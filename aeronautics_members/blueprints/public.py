"""Public blueprint.

Route handlers moved verbatim out of app.py (dedented; @app.route ->
@public_bp.route; app.logger -> current_app.logger). Helpers are imported
from the app module, which is fully initialized before this is imported.
"""

import stripe
from flask import Blueprint, current_app
from sqlalchemy import text

from ..config import (
    RATELIMIT_MEMBERSHIP,
    STRIPE_PUBLISHABLE_KEY,
)
from ..services.audit import (
    log_audit_event,
    snapshot_member_for_audit,
    snapshot_user_for_audit,
)
from ..services.billing import (
    create_checkout_session_for_member,
)
from ..services.clock import (
    get_now_utc,
)
from ..services.forum import (
    generate_unique_forum_username,
)
from ..services.members import (
    apply_member_profile,
)
from ..services.membership import (
    sync_member_active_state,
)
from ..services.settings import (
    get_stripe_settings_map,
)
from ..services.signup import (
    chosen_payment_method,
    invoice_payments_allowed,
)
from ._signup import start_membership
from datetime import (
    datetime,
    timezone,
)
from flask import (
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_babel import (
    _,
)
from flask_login import (
    current_user,
    login_user,
)
from ..db_models import (
    Member,
    User,
    db,
)
from ..forms import (
    MembershipForm,
)
from ..app import (
    can_resume_payment,
    get_member_portal_target,
    limiter,
)

public_bp = Blueprint("public", __name__)


@public_bp.route("/", methods=["GET"])
def index():
    """The front door: what this is, and the way in -- joining or logging in.

    Somebody already signed in has no use for either, so they go straight to
    where logging in would have taken them.
    """
    if current_user.is_authenticated:
        return redirect(url_for(get_member_portal_target(current_user)))
    return render_template("landing.html")


@public_bp.route("/join", methods=["GET"])
def join():
    """The membership signup form, at an address that can go on a poster.

    Signed in, there is nothing to sign up for here: members have their
    account, and a login without a membership starts one from My Account.
    """
    if current_user.is_authenticated:
        return redirect(url_for("account.account"))
    form = MembershipForm()
    return render_template(
        "join.html",
        form=form,
        invoice_payments_enabled=invoice_payments_allowed(),
        stripe_key=get_stripe_settings_map().get("stripe_publishable_key") or STRIPE_PUBLISHABLE_KEY,
    )


@public_bp.route("/process-membership", methods=["POST"])
@limiter.limit(RATELIMIT_MEMBERSHIP)
def process_membership():
    form = MembershipForm()

    if form.validate_on_submit():
        form_data = form.data
        form_data.pop("csrf_token", None)
        form_data.pop("submit", None)
        password = form_data.pop("password")
        form_data.pop("confirm_password", None)

        payment_method = form_data.pop("payment_method", "checkout")
        form_data["email_private"] = form_data["email_private"].strip().lower()
        email_address = form_data["email_private"]

        existing_member = db.session.execute(db.select(Member).filter_by(email_private=email_address)).scalar_one_or_none()
        existing_user = db.session.execute(db.select(User).filter_by(email=email_address)).scalar_one_or_none()

        if existing_member and sync_member_active_state(existing_member):
            db.session.commit()

        if existing_member is not None:
            if existing_member.user_id:
                continued = _continue_unfinished_signup(existing_member, password, payment_method)
                if continued is not None:
                    return continued
                flash(_("An account with this email address already exists. Please log in to manage or resume your membership."), "warning")
                return redirect(url_for("auth.login"))
            flash(_("A membership profile with this email address already exists without a linked login. Please contact the club so we can resolve it."), "warning")
            return redirect(url_for("public.join"))

        if existing_user is not None:
            flash(_("An account with this email address already exists. Please log in instead."), "warning")
            return redirect(url_for("auth.login"))

        payment_method = chosen_payment_method(payment_method)

        member = Member(
            created_at=get_now_utc(),
            payment_status="pending_checkout",
            is_active=False,
            pending_checkout_started_at=get_now_utc(),
        )
        apply_member_profile(member, {**form_data, "terms_accepted": True})

        user = User(
            email=email_address,
            forum_username=generate_unique_forum_username(
                member.first_name,
                member.last_name,
                member.year_group,
            ),
        )
        user.set_password(password)
        member.user = user

        db.session.add(user)
        db.session.add(member)
        db.session.flush()
        log_audit_event(
            category="membership",
            event_type="public_membership_signup_started",
            actor_user=user,
            target_user=user,
            target_member=member,
            before=None,
            after={"user": snapshot_user_for_audit(user), "member": snapshot_member_for_audit(member)},
            metadata={"payment_method": payment_method},
        )
        db.session.commit()
        login_user(user)

        return start_membership(member, payment_method, what="account")

    current_app.logger.warning(f"Form validation failed. Errors: {form.errors}")
    flash(_("Please correct the errors below and try again."), "danger")
    return render_template(
        "join.html",
        form=form,
        invoice_payments_enabled=invoice_payments_allowed(),
        stripe_key=get_stripe_settings_map().get("stripe_publishable_key") or STRIPE_PUBLISHABLE_KEY,
    )


def _continue_unfinished_signup(member, password, requested_method):
    """Carry on to payment for a signup that never got that far. Or None.

    The same form sent twice -- a double click while Stripe is being asked for
    a payment page, or filling it in again after cancelling the payment --
    found the account the first one made and answered "already exists, please
    log in", often in place of the payment page. With the right password that
    is somebody the login form would let in anyway, so they are logged in and
    sent on, to the payment page already open for them where there is one.

    Only for an account whose payment has not started at Stripe, and only for
    Checkout: paying by invoice creates a subscription outright, and a second
    would be a second invoice.
    """
    user = member.user
    if user is None or user.is_disabled or not user.check_password(password):
        return None
    if not can_resume_payment(member) or chosen_payment_method(requested_method) != "checkout":
        return None

    login_user(user)
    try:
        checkout, _cycle = create_checkout_session_for_member(member)
        db.session.commit()
        return redirect(checkout.url, code=303)
    except stripe.StripeError as exc:
        db.session.rollback()
        current_app.logger.error(
            "Could not continue an unfinished signup to Checkout for member_id=%s: %s", member.id, exc
        )
        flash(_("The payment page could not be opened right now. You can continue from your account."), "warning")
        return redirect(url_for("account.account"))


@public_bp.route("/thank-you")
def thank_you():
    method = request.args.get("method", "checkout")
    phase = request.args.get("phase", "prorated")
    return render_template("thank_you.html", method=method, phase=phase)


@public_bp.route("/cancel")
def cancel():
    return render_template("cancel.html")


@public_bp.route("/legal")
def legal_texts():
    return render_template("legal_texts.html")


@public_bp.route("/__health", methods=["GET"])
def health_check():
    checks = {"app": "ok", "database": "ok"}
    status_code = 200
    try:
        db.session.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - exercised only when DB is down
        checks["database"] = "error"
        status_code = 503
        current_app.logger.error("Health check database probe failed: %s", exc)
    return (
        jsonify(
            {
                "status": "ok" if status_code == 200 else "degraded",
                "app": "jaeronautics",
                "checks": checks,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "host": request.host,
            }
        ),
        status_code,
    )
