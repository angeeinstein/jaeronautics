"""Public blueprint.

Route handlers moved verbatim out of app.py (dedented; @app.route ->
@public_bp.route; app.logger -> current_app.logger). Helpers are imported
from the app module, which is fully initialized before this is imported.
"""

from flask import Blueprint, current_app
from sqlalchemy import text

from ..app import (
    Member,
    MembershipForm,
    RATELIMIT_MEMBERSHIP,
    STRIPE_PUBLISHABLE_KEY,
    User,
    _,
    apply_member_profile,
    create_checkout_session_for_member,
    create_invoice_membership_for_member,
    datetime,
    db,
    flash,
    generate_unique_forum_username,
    get_now_utc,
    get_settings_map,
    get_stripe_settings_map,
    jsonify,
    limiter,
    log_audit_event,
    login_user,
    redirect,
    render_template,
    request,
    send_email_verification_email,
    send_member_welcome_email,
    session,
    snapshot_member_for_audit,
    snapshot_user_for_audit,
    stripe,
    sync_member_active_state,
    sync_member_forum_state,
    timezone,
    url_for,
)

public_bp = Blueprint("public", __name__)


@public_bp.route("/", methods=["GET"])
def index():
    form = MembershipForm()
    public_settings = get_settings_map(["invoice_payments_enabled"])
    return render_template(
        "index.html",
        form=form,
        invoice_payments_enabled=public_settings.get("invoice_payments_enabled") == "True",
        stripe_key=get_stripe_settings_map().get("stripe_publishable_key") or STRIPE_PUBLISHABLE_KEY,
    )


@public_bp.route("/process-membership", methods=["POST"])
@limiter.limit(RATELIMIT_MEMBERSHIP)
def process_membership():
    form = MembershipForm()
    settings = get_settings_map(["invoice_payments_enabled"])

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
                flash(_("An account with this email address already exists. Please log in to manage or resume your membership."), "warning")
                return redirect(url_for("auth.login"))
            flash(_("A membership profile with this email address already exists without a linked login. Please contact the club so we can resolve it."), "warning")
            return redirect(url_for("public.index"))

        if existing_user is not None:
            flash(_("An account with this email address already exists. Please log in instead."), "warning")
            return redirect(url_for("auth.login"))

        if settings.get("invoice_payments_enabled") != "True":
            payment_method = "checkout"

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

        try:
            try:
                send_email_verification_email(current_app._get_current_object(), user)
            except Exception as email_exc:
                current_app.logger.warning("Could not send verification email for user_id=%s: %s", user.id, email_exc)

            if payment_method == "checkout":
                session, _cycle = create_checkout_session_for_member(member)
                db.session.commit()
                return redirect(session.url, code=303)

            if payment_method == "invoice":
                _subscription, cycle = create_invoice_membership_for_member(member)
                forum_result = None
                if cycle["free_period"]:
                    forum_result, _forum_service = sync_member_forum_state(member)
                db.session.commit()
                if cycle["free_period"]:
                    send_member_welcome_email(current_app._get_current_object(), member)
                    if forum_result and forum_result.error:
                        current_app.logger.warning("Forum sync reported an issue after invoice activation for member_id=%s: %s", member.id, forum_result.error)
                return redirect(
                    url_for(
                        "public.thank_you",
                        method="invoice",
                        phase=cycle["thank_you_phase"],
                    )
                )

        except stripe.StripeError as e:
            error_body = getattr(e, "json_body", {}) or {}
            error_details = error_body.get("error", {}) if isinstance(error_body, dict) else {}
            current_app.logger.error(
                "Stripe Error during membership signup: type=%s message=%s user_message=%s code=%s param=%s request_id=%s http_status=%s payment_method=%s email=%s member_id=%s",
                type(e).__name__,
                str(e),
                error_details.get("message"),
                error_details.get("code"),
                error_details.get("param"),
                getattr(e, "request_id", None),
                getattr(e, "http_status", None),
                payment_method,
                email_address,
                member.id,
            )
            flash(_("Your account was created, but payment could not be started. Please log in and resume your membership from your account page."), "warning")
        except Exception:
            current_app.logger.exception(
                "Unexpected error during membership signup for email=%s payment_method=%s member_id=%s",
                email_address,
                payment_method,
                member.id,
            )
            flash(_("Your account was created, but an unexpected error occurred while starting billing. Please log in and resume your membership from your account page."), "warning")

        return redirect(url_for("account.account"))

    current_app.logger.warning(f"Form validation failed. Errors: {form.errors}")
    flash(_("Please correct the errors below and try again."), "danger")
    return render_template(
        "index.html",
        form=form,
        invoice_payments_enabled=get_settings_map(["invoice_payments_enabled"]).get("invoice_payments_enabled") == "True",
        stripe_key=get_stripe_settings_map().get("stripe_publishable_key") or STRIPE_PUBLISHABLE_KEY,
    )


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
