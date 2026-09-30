"""Account blueprint.

Route handlers moved verbatim out of app.py (dedented; @app.route ->
@account_bp.route; app.logger -> current_app.logger). Helpers are imported
from the app module, which is fully initialized before this is imported.
"""

from flask import Blueprint, current_app

from ..services.audit import (
    log_audit_event,
    snapshot_member_for_audit,
    snapshot_user_for_audit,
)
from ..services.billing import (
    backfill_member_stripe_references,
    can_rejoin,
    create_checkout_session_for_member,
    find_live_stripe_subscription,
    sync_member_subscription_state_from_subscription,
)
from ..services.clock import (
    get_now_utc,
)
from ..services.forum import (
    generate_unique_forum_username,
    get_forum_service,
    sync_member_forum_state,
)
from ..config import (
    RATELIMIT_ACCOUNT_DELETION,
    RATELIMIT_DATA_EXPORT,
    RATELIMIT_EMAIL_RESEND,
)
from ..services import (
    ServiceError,
)
from ..services.identity import (
    read_token,
    send_email_verification_email,
    send_work_email_verification_email,
)
from ..services.privacy import (
    INITIATED_BY_MEMBER,
    TOKEN_MAX_AGE_ACCOUNT_DELETION,
    account_deletion_claims_match,
    describe_deletion_impact,
    erase_account,
    export_account_data,
    export_filename_for,
    send_account_deletion_email,
)
from ._responses import json_download_response
from ..services.members import (
    DIRECT_MEMBER_PROFILE_FIELDS,
    IDENTITY_MEMBER_FIELDS,
    apply_member_profile,
)
from ..services.membership import (
    member_has_active_access,
)
from ..services.outbox import enqueue_forum_sync
from ..services.notifications import (
    flush_marked_notification_channels,
    queue_curated_admin_notification,
)
from ..services.signup import (
    chosen_payment_method,
    invoice_payments_allowed,
)
from ._email_cooldown import remember_sent, sent_just_now
from ._signup import start_membership
from ..services.workflows import (
    sync_member_primary_email,
)
import stripe
from datetime import (
    datetime,
    timezone,
)
from flask import (
    flash,
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
    login_required,
    logout_user,
)
from ..db_models import (
    Member,
    MemberProfileChangeRequest,
    User,
    db,
)
from ..services.locking import lock_administration, locked
from ..forms import (
    CreateMembershipProfileForm,
    IdentityChangeRequestForm,
    MemberProfileForm,
)
from ..notification_service import (
    ADMIN_GENERAL_CHANNEL,
)
from ..app import (
    limiter,
    can_resume_payment,
    create_identity_change_request,
    get_current_member_for_user,
    get_portal_session,
    has_identity_changes,
    render_account_dashboard,
)

account_bp = Blueprint("account", __name__)


@account_bp.route("/account", methods=["GET"])
@login_required
def account():
    if not request.args.get("rt"):
        redirect_args = {"rt": str(int(datetime.now(timezone.utc).timestamp() * 1000))}
        if request.args.get("refresh_billing") == "1":
            redirect_args["refresh_billing"] = "1"
        return redirect(url_for("account.account", **redirect_args))
    return render_account_dashboard()


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
            return resume_member_payment()
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


@account_bp.route("/account/profile", methods=["POST"])
@login_required
def save_member_profile():
    member = get_current_member_for_user(current_user)
    if member is None:
        flash(_("No membership profile is linked to this account yet."), "warning")
        return redirect(url_for("public.index"))

    profile_form = MemberProfileForm(prefix="profile")
    profile_form.member_category_value = member.member_category
    identity_form = IdentityChangeRequestForm(prefix="identity")
    if profile_form.validate_on_submit():
        before_user = snapshot_user_for_audit(current_user)
        before_member = snapshot_member_for_audit(member, fields=DIRECT_MEMBER_PROFILE_FIELDS)
        has_forum_account = member.user is not None and (
            member.user.forum_account is not None or member_has_active_access(member)
        )
        new_email = (profile_form.email_private.data or "").strip().lower()
        if has_forum_account and new_email and new_email != (member.email_private or "").strip().lower():
            # Checked before anything is saved: the forum refuses an address
            # another of its accounts has, and taking it here anyway left the
            # portal on the new address and the forum on the old one. Not
            # knowing (forum unreachable) lets the change through; the sync
            # then reports its problem where admins see it.
            if get_forum_service().address_taken_by_another_forum_account(member.user, new_email):
                flash(
                    _("This email address already belongs to another account on the forum, so it cannot be used here. Please choose a different address, or contact us if that forum account is yours."),
                    "danger",
                )
                return render_account_dashboard(profile_form=profile_form, identity_form=identity_form)
        try:
            email_changed = sync_member_primary_email(member, profile_form.email_private.data)
        except ValueError as exc:
            flash(str(exc), "danger")
            return render_account_dashboard(profile_form=profile_form, identity_form=identity_form)

        # Through apply_member_profile rather than field by field, because that
        # is what drops the university address's confirmation when the address
        # changes. Setting the fields directly kept the tick on whatever was
        # typed next -- and a confirmed university address is what gives a
        # returning student their old forum account and its posts.
        previous_email_work = (member.email_work or "").strip().lower()
        contact_fields = tuple(f for f in DIRECT_MEMBER_PROFILE_FIELDS if f != "email_private")
        apply_member_profile(
            member,
            {f: getattr(profile_form, f).data for f in contact_fields},
            fields=contact_fields,
        )
        work_email_changed = bool(member.email_work) and (
            member.email_work.strip().lower() != previous_email_work
        )

        log_audit_event(
            category="profile",
            event_type="contact_details_updated",
            actor_user=current_user,
            target_user=current_user,
            target_member=member,
            before={"user": before_user, "member": before_member},
            after={"user": snapshot_user_for_audit(current_user), "member": snapshot_member_for_audit(member, fields=DIRECT_MEMBER_PROFILE_FIELDS)},
            metadata={"email_changed": email_changed},
        )
        forum_result = None
        if has_forum_account:
            forum_result, _forum_service = sync_member_forum_state(member)
        db.session.commit()
        if email_changed:
            try:
                send_email_verification_email(current_app._get_current_object(), current_user)
                flash(_("Your profile was updated. Please verify your new email address using the link we sent you."), "success")
                if has_forum_account:
                    # The forum now has the new address and waits for it to be
                    # confirmed, so the account there is paused until then.
                    flash(_("Your forum access is paused until you confirm the new address."), "info")
            except Exception as exc:
                current_app.logger.warning("Could not send verification email after profile update for user_id=%s: %s", current_user.id, exc)
                flash(_("Your profile was updated."), "success")
        else:
            flash(_("Your profile was updated."), "success")
        if work_email_changed:
            try:
                if send_work_email_verification_email(current_app._get_current_object(), member):
                    db.session.commit()  # the link's nonce
                    flash(
                        _("Please confirm %(address)s using the link we sent there.", address=member.email_work),
                        "info",
                    )
            except Exception as exc:  # noqa: BLE001 -- the reason belongs in the log, not the page
                db.session.rollback()
                current_app.logger.warning(
                    "Could not send the work email verification after a profile update for member_id=%s: %s",
                    member.id,
                    exc,
                )
        if forum_result and forum_result.error:
            flash(_("Your forum profile could not be synchronized right now. Please try again later."), "warning")
        return redirect(url_for("account.account"))

    flash(_("Please correct the profile form and try again."), "danger")
    return render_account_dashboard(profile_form=profile_form, identity_form=identity_form)


@account_bp.route("/account/identity-request", methods=["POST"])
@login_required
def submit_identity_change_request():
    member = get_current_member_for_user(current_user)
    if member is None:
        flash(_("No membership profile is linked to this account yet."), "warning")
        return redirect(url_for("public.index"))

    identity_form = IdentityChangeRequestForm(prefix="identity")
    profile_form = MemberProfileForm(prefix="profile")
    profile_form.member_category_value = member.member_category
    if identity_form.validate_on_submit():
        form_data = identity_form.data
        form_data.pop("csrf_token", None)
        form_data.pop("submit", None)

        if not has_identity_changes(member, form_data):
            flash(_("There are no identity changes to request."), "warning")
            return redirect(url_for("account.account"))

        try:
            request_record = create_identity_change_request(member, current_user, form_data)
            db.session.flush()
            log_audit_event(
                category="profile_change_request",
                event_type="identity_request_submitted",
                actor_user=current_user,
                target_user=current_user,
                target_member=member,
                before=snapshot_member_for_audit(member, fields=IDENTITY_MEMBER_FIELDS),
                after={
                    "requested_salutation": request_record.requested_salutation,
                    "requested_title": request_record.requested_title,
                    "requested_first_name": request_record.requested_first_name,
                    "requested_last_name": request_record.requested_last_name,
                    "requested_year_group": request_record.requested_year_group,
                },
                metadata={"request_id": request_record.id, "member_note": request_record.member_note},
            )
            queue_curated_admin_notification(
                ADMIN_GENERAL_CHANNEL,
                "identity_change_request_created",
                _("%(email)s asked for a profile change (name, membership type or year group).", email=member.email_private),
                payload={
                    "member_email": member.email_private,
                    "request_id": request_record.id,
                    "member_note": request_record.member_note,
                },
                target_user=current_user,
                target_member=member,
                object_type="member_profile_change_request",
                object_id=request_record.id,
            )
            db.session.commit()
            flush_marked_notification_channels()
            flash(_("Your identity change request has been submitted for admin review."), "success")
            return redirect(url_for("account.account"))
        except ValueError as exc:
            flash(str(exc), "warning")
            return render_account_dashboard(profile_form=profile_form, identity_form=identity_form)

    flash(_("Please correct the identity change form and try again."), "danger")
    return render_account_dashboard(profile_form=profile_form, identity_form=identity_form)


@account_bp.route("/account/identity-request/<int:request_id>/cancel", methods=["POST"])
@login_required
def cancel_identity_change_request(request_id):
    member = get_current_member_for_user(current_user)
    # Locked, as an admin's decision on it is: whichever comes second finds it settled.
    request_record = db.session.execute(
        locked(db.select(MemberProfileChangeRequest).where(MemberProfileChangeRequest.id == request_id))
    ).scalar_one_or_none()
    if request_record is None or member is None or request_record.member_id != member.id or request_record.status != "pending":
        flash(_("The selected change request could not be canceled."), "warning")
        return redirect(url_for("account.account"))

    request_record.status = "canceled"
    request_record.reviewed_by = current_user
    request_record.reviewed_at = get_now_utc()
    log_audit_event(
        category="profile_change_request",
        event_type="identity_request_canceled",
        actor_user=current_user,
        target_user=current_user,
        target_member=member,
        before={"request_id": request_record.id, "status": "pending"},
        after={"request_id": request_record.id, "status": request_record.status},
        metadata={"member_note": request_record.member_note},
    )
    db.session.commit()
    flash(_("Your pending identity change request was canceled."), "success")
    return redirect(url_for("account.account"))


@account_bp.route("/account/billing", methods=["POST"])
@login_required
def manage_member_billing():
    member = get_current_member_for_user(current_user)
    if member is None:
        flash(_("No membership profile is linked to this account yet."), "warning")
        return redirect(url_for("public.index"))

    try:
        portal_session = get_portal_session(member)
        return redirect(portal_session.url, code=303)
    except ValueError as exc:
        flash(str(exc), "warning")
    except stripe.StripeError as exc:
        current_app.logger.error("Could not create Stripe portal session for member_id=%s: %s", member.id, exc)
        flash(_("Billing page unavailable. Please try again later."), "danger")
    return redirect(url_for("account.account"))


@account_bp.route("/account/resume-payment", methods=["POST"])
@login_required
def resume_member_payment():
    member = get_current_member_for_user(current_user)
    if member is None:
        flash(_("No membership profile is linked to this account yet."), "warning")
        return redirect(url_for("public.index"))
    if not can_resume_payment(member):
        flash(_("This membership cannot be resumed from here. Use billing management instead if a Stripe customer already exists."), "warning")
        return redirect(url_for("account.account"))

    try:
        session, _cycle = create_checkout_session_for_member(member)
        db.session.commit()
        return redirect(session.url, code=303)
    except stripe.StripeError as exc:
        current_app.logger.error("Could not resume Checkout for member_id=%s: %s", member.id, exc)
        flash(_("Payment page unavailable. Please try again later."), "danger")
    except Exception:
        current_app.logger.exception("Unexpected error while resuming payment for member_id=%s", member.id)
        flash(_("Could not restart the membership payment right now."), "danger")
    return redirect(url_for("account.account"))


@account_bp.route("/account/rejoin", methods=["POST"])
@login_required
def rejoin_membership():
    """Start a membership again after it has ended.

    Stripe cannot restart a cancelled subscription -- "canceled" is final
    there -- so this is a new one, on the same Stripe customer. Without it
    somebody whose membership lapsed had no way back: the signup page sends
    them to log in, the create-profile page sees they already have one, and the
    billing portal only reopens a subscription that has not ended yet.
    """
    member = get_current_member_for_user(current_user)
    if member is None:
        flash(_("No membership profile is linked to this account yet."), "warning")
        return redirect(url_for("public.index"))
    if not can_rejoin(member):
        flash(_("Your membership has not ended, so there is nothing to restart."), "info")
        return redirect(url_for("account.account"))

    try:
        live = find_live_stripe_subscription(member)
    except stripe.StripeError as exc:
        current_app.logger.error(
            "Could not check Stripe for a running subscription before a rejoin for member_id=%s: %s",
            member.id, exc,
        )
        flash(_("We could not check your billing with Stripe right now. Please try again later."), "danger")
        return redirect(url_for("account.account"))

    if live is not None:
        # The portal was behind Stripe, not the membership over. Catch up
        # instead of starting a second subscription that would charge twice.
        backfill_member_stripe_references(member, subscription_id=live.get("id"))
        sync_member_subscription_state_from_subscription(member, live)
        enqueue_forum_sync(member, reason="Found running while rejoining.")
        db.session.commit()
        flash(
            _("Your membership is still running in Stripe, so there is nothing to restart. "
              "If a payment is outstanding, you can settle it under Manage Billing."),
            "info",
        )
        return redirect(url_for("account.account"))

    payment_method = chosen_payment_method(request.form.get("payment_method", "checkout"))
    log_audit_event(
        category="membership",
        event_type="membership_rejoin_started",
        actor_user=current_user,
        target_user=current_user,
        target_member=member,
        before=snapshot_member_for_audit(member),
        after=None,
        metadata={
            "payment_method": payment_method,
            "previous_status": member.payment_status,
            "previous_subscription_id": member.stripe_subscription_id,
        },
    )
    db.session.commit()
    return start_membership(member, payment_method, what="rejoin")


@account_bp.route("/account/resend-verification", methods=["POST"])
@login_required
@limiter.limit(RATELIMIT_EMAIL_RESEND, methods=["POST"])
def resend_verification_email():
    if current_user.email_is_verified:
        flash(_("Your email address is already verified."), "info")
        return redirect(url_for("account.account"))
    if sent_just_now("verify-email", current_user.email):
        flash(_("We sent it a moment ago. Please check your inbox and your spam folder."), "info")
        return redirect(url_for("account.account"))

    try:
        if send_email_verification_email(current_app._get_current_object(), current_user):
            remember_sent("verify-email", current_user.email)
            flash(_("We sent you a new confirmation email. Not in your inbox? Please check your spam folder."), "success")
        else:
            flash(_("Email isn't set up yet. Please contact us."), "warning")
    except Exception as exc:
        current_app.logger.warning("Could not resend verification email for user_id=%s: %s", current_user.id, exc)
        flash(_("We could not send a verification email right now."), "danger")
    return redirect(url_for("account.account"))


@account_bp.route("/account/resend-work-verification", methods=["POST"])
@login_required
@limiter.limit(RATELIMIT_EMAIL_RESEND, methods=["POST"])
def resend_work_email_verification():
    """Send the university-address confirmation again.

    There was no way to ask for this. Somebody who lost the mail, or who only
    confirmed the private address and stopped, had no route back -- and for a
    returning student the university address is the *only* evidence that can
    give them their old forum account, so there is nothing else they can try.
    """
    member = current_user.member
    if member is None or not (member.email_work or "").strip():
        flash(_("No university or company email address is on file."), "info")
        return redirect(url_for("account.account"))
    if member.email_work_is_verified:
        flash(_("Your university or company email address is already confirmed."), "info")
        return redirect(url_for("account.account"))
    if sent_just_now("verify-work-email", member.email_work):
        flash(_("We sent it a moment ago. Please check your inbox and your spam folder."), "info")
        return redirect(url_for("account.account"))

    try:
        if send_work_email_verification_email(current_app._get_current_object(), member):
            db.session.commit()
            remember_sent("verify-work-email", member.email_work)
            flash(
                _("We sent a new confirmation email to %(address)s. Not in your inbox? Please check your spam folder.",
                  address=member.email_work),
                "success",
            )
        else:
            flash(_("Email isn't set up yet. Please contact us."), "warning")
    except Exception as exc:  # noqa: BLE001 -- the reason belongs in the log, not the page
        db.session.rollback()
        current_app.logger.warning(
            "Could not resend the work email verification for member_id=%s: %s", member.id, exc
        )
        flash(_("We could not send a confirmation email right now."), "danger")
    return redirect(url_for("account.account"))


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


@account_bp.route("/account/delete", methods=["POST"])
@login_required
@limiter.limit(RATELIMIT_ACCOUNT_DELETION)
def request_account_deletion():
    """Start a member-initiated erasure by emailing a confirmation link.

    Nothing is erased here. Requiring the mailbox as well as the session means a
    borrowed laptop or a hijacked session cannot destroy an account, and it
    gives the member a moment to change their mind.
    """
    try:
        sent = send_account_deletion_email(current_app._get_current_object(), current_user)
    except Exception as exc:
        current_app.logger.warning(
            "Could not send account deletion email for user_id=%s: %s", current_user.id, exc
        )
        flash(_("We could not send the confirmation email right now. Please try again later."), "danger")
        return redirect(url_for("account.account"))

    if not sent:
        flash(
            _("Email isn't set up yet. Please contact us."),
            "warning",
        )
        return redirect(url_for("account.account"))

    log_audit_event(
        category="privacy",
        event_type="account_deletion_requested",
        actor_user=current_user,
        target_user=current_user,
        target_member=current_user.member,
    )
    db.session.commit()

    flash(
        _("We sent a confirmation link to %(email)s. Your account will be deleted "
          "once you open it. The link is valid for one hour.", email=current_user.email),
        "info",
    )
    return redirect(url_for("account.account"))


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
