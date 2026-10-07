"""My Account's routes. The page itself is the app's (api/account.py); here
remain the data download, the deletion link's page from the email, and the
membership form for an account without one (until the signup moves, step 7).
"""

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_babel import _
from flask_login import current_user, login_required, logout_user

from ..app import limiter
from ..config import RATELIMIT_ACCOUNT_DELETION, RATELIMIT_DATA_EXPORT
from ..db_models import Member, User, db
from ..forms import CreateMembershipProfileForm
from ..services import ServiceError
from ..services.account import resume_payment_url
from ..services.audit import log_audit_event, snapshot_member_for_audit, snapshot_user_for_audit
from ..services.billing import can_resume_payment
from ..services.clock import get_now_utc
from ..services.forum import generate_unique_forum_username
from ..services.identity import read_token
from ..services.locking import lock_administration, locked
from ..services.members import apply_member_profile
from ..services.privacy import (
    INITIATED_BY_MEMBER,
    TOKEN_MAX_AGE_ACCOUNT_DELETION,
    account_deletion_claims_match,
    describe_deletion_impact,
    erase_account,
    export_account_data,
    export_filename_for,
)
from ..services.signup import chosen_payment_method, invoice_payments_allowed
from ._responses import json_download_response
from ._signup import start_membership
from .app_shell import app_shell

account_bp = Blueprint("account", __name__)


@account_bp.route("/account", methods=["GET"])
@login_required
def account():
    """My Account, drawn by the app (api/account.py)."""
    return app_shell()


@account_bp.route("/account/create-membership", methods=["GET", "POST"])
@login_required
def create_membership_profile():
    if current_user.member is not None:
        # The form sent twice -- a double click while Stripe is asked for the
        # payment page -- finds the profile the first one made. Carry on to
        # that payment page rather than to the account page in its place.
        if (
            request.method == "POST"
            and can_resume_payment(current_user.member)
            and chosen_payment_method(request.form.get("payment_method", "checkout")) == "checkout"
        ):
            try:
                return redirect(resume_payment_url(current_user.member), code=303)
            except ServiceError as exc:
                flash(exc.message, "warning")
        return redirect(url_for("account.account"))

    form = CreateMembershipProfileForm()
    if request.method == "GET":
        form.email_private.data = current_user.email

    if form.validate_on_submit():
        form_data = form.data
        form_data.pop("csrf_token", None)
        form_data.pop("submit", None)

        payment_method = chosen_payment_method(form_data.pop("payment_method", "checkout"))

        member_email = (current_user.email or "").strip().lower()
        existing_member = db.session.execute(db.select(Member).filter_by(email_private=member_email)).scalar_one_or_none()
        if existing_member is not None:
            if existing_member.user_id == current_user.id:
                return redirect(url_for("account.account"))
            flash(_("A membership profile with this email address already exists. Please contact the club so we can resolve it."), "warning")
            return redirect(url_for("admin.admin_dashboard" if current_user.has_role("admin") else "public.index"))

        member = Member(
            created_at=get_now_utc(),
            payment_status="pending_checkout",
            is_active=False,
            pending_checkout_started_at=get_now_utc(),
        )
        apply_member_profile(member, {**form_data, "email_private": member_email, "terms_accepted": True})
        # Added before anything queries: the username check below would
        # otherwise autoflush a membership the session does not hold yet.
        db.session.add(member)
        member.user = current_user
        current_user.email = member_email
        if not current_user.forum_username:
            current_user.forum_username = generate_unique_forum_username(
                member.first_name,
                member.last_name,
                member.year_group,
                exclude_user_id=current_user.id,
            )

        before_user = snapshot_user_for_audit(current_user)
        db.session.add(member)
        db.session.flush()
        log_audit_event(
            category="membership",
            event_type="linked_membership_created",
            actor_user=current_user,
            target_user=current_user,
            target_member=member,
            before={"user": before_user, "member": None},
            after={"user": snapshot_user_for_audit(current_user), "member": snapshot_member_for_audit(member)},
            metadata={"payment_method": payment_method},
        )
        db.session.commit()

        return start_membership(member, payment_method, what="profile")

    return render_template(
        "account/create_membership.html",
        form=form,
        invoice_payments_enabled=invoice_payments_allowed(),
    )


@account_bp.route("/account/data-export", methods=["GET"])
@login_required
@limiter.limit(RATELIMIT_DATA_EXPORT)
def export_my_data():
    """Download everything the association holds about you, as JSON.

    GDPR Art. 15 and 20. Self-service because the alternative -- emailing an
    administrator who then assembles it by hand -- is slower and reliably
    incomplete.
    """
    payload = export_account_data(current_user)

    log_audit_event(
        category="privacy",
        event_type="data_exported",
        actor_user=current_user,
        target_user=current_user,
        target_member=current_user.member,
        metadata={"self_service": True, "sections": sorted(payload)},
    )
    db.session.commit()

    return json_download_response(payload, export_filename_for(current_user))


@account_bp.route("/account/delete/<token>", methods=["GET", "POST"])
@login_required
@limiter.limit(RATELIMIT_ACCOUNT_DELETION, methods=["POST"])
def confirm_account_deletion(token):
    """Show what deletion will do, then -- on POST -- do it.

    The GET deliberately changes nothing. Mail clients, link scanners and
    chat previews fetch URLs in emails without being asked, and an account that
    erased itself because a spam filter opened the link would be unrecoverable.
    """
    try:
        token_data = read_token(token, "delete-account", TOKEN_MAX_AGE_ACCOUNT_DELETION)
    except Exception:
        flash(_("This deletion link is invalid or has expired. Please start again."), "warning")
        return redirect(url_for("account.account"))

    if not account_deletion_claims_match(token_data, current_user):
        flash(_("This deletion link does not belong to the account you are signed in to."), "warning")
        return redirect(url_for("account.account"))

    if request.method == "POST":
        # Read afresh under the lock: an admin may be erasing this account, or
        # taking away the other admin, in the same moment.
        lock_administration()
        db.session.execute(locked(db.select(User).filter_by(id=current_user.id))).scalar_one()
    impact = describe_deletion_impact(current_user, actor_user=current_user)

    if request.method == "GET":
        return render_template(
            "account_delete_confirm.html",
            token=token,
            impact=impact,
            member=current_user.member,
        )

    if "last_admin" in impact["blockers"]:
        flash(
            _("You are the only administrator. Give someone else admin access "
              "before deleting your account."),
            "warning",
        )
        return redirect(url_for("account.account"))

    email_for_message = current_user.email
    try:
        erase_account(current_user, actor_user=current_user, initiated_by=INITIATED_BY_MEMBER)
    except ServiceError as exc:
        db.session.rollback()
        flash(exc.message, "warning" if exc.http_status < 500 else "danger")
        return redirect(url_for("account.account"))

    db.session.commit()
    current_app.logger.info("Account erased on member request (previously %s).", email_for_message)

    # Sign out last: the session belongs to an account that no longer exists.
    logout_user()
    flash(
        _("Your account and personal data have been deleted. Thank you for having been a member."),
        "success",
    )
    return redirect(url_for("public.index"))
