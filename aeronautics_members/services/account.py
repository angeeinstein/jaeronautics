"""My Account: what a member changes about themselves, and what they start
from there -- their contact details, a request to change their name or kind
of membership, paying, rejoining, deleting the account, the password.

Each takes values already checked against the form rules (api/_forms.py) and
says what happened as messages ``(tone, text)`` for the page to show; what
cannot be done is a ServiceError. Drawn by api/account.py.
"""

import stripe
from flask import current_app

from ..db_models import MemberProfileChangeRequest, User, db
from ..notification_service import ADMIN_GENERAL_CHANNEL
from . import (
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    PermissionError_,
    ServiceError,
    ValidationError,
)
from .audit import log_audit_event, snapshot_member_for_audit, snapshot_user_for_audit
from .billing import (
    backfill_member_stripe_references,
    billing_portal_session,
    can_rejoin,
    can_resume_payment,
    create_checkout_session_for_member,
    find_live_stripe_subscription,
    sync_member_subscription_state_from_subscription,
)
from .clock import get_now_utc
from .forum import get_forum_service, sync_member_forum_state
from .identity import (
    read_token,
    send_email_verification_email,
    send_work_email_verification_email,
)
from .locking import lock_administration, locked
from .members import (
    DIRECT_MEMBER_PROFILE_FIELDS,
    IDENTITY_MEMBER_FIELDS,
    apply_member_profile,
    normalize_optional_member_value,
)
from .membership import member_has_active_access
from .notifications import flush_marked_notification_channels, queue_curated_admin_notification
from .outbox import enqueue_forum_sync
from .privacy import (
    INITIATED_BY_MEMBER,
    TOKEN_MAX_AGE_ACCOUNT_DELETION,
    account_deletion_claims_match,
    describe_deletion_impact,
    erase_account,
    send_account_deletion_email,
)
from .signup import begin_membership, chosen_payment_method
from .workflows import sync_member_primary_email

NO_EMAIL = "Email isn't set up yet. Please contact us."


def _has_forum_account(member):
    return member.user is not None and (member.user.forum_account is not None or member_has_active_access(member))


# --- Contact details -------------------------------------------------------------------


def save_contact_details(user, member, values):
    """Address, phones and both email addresses: changed at once, no review.
    A new private address is the login, and waits to be confirmed; a new
    university address too. Returns the messages for the page."""
    has_forum_account = _has_forum_account(member)
    new_email = (values.get("email_private") or "").strip().lower()
    if has_forum_account and new_email and new_email != (member.email_private or "").strip().lower():
        # Checked before anything is saved: the forum refuses an address
        # another of its accounts has, and taking it here anyway left the
        # portal on the new address and the forum on the old one. Not knowing
        # (forum unreachable) lets the change through; the sync then reports
        # its problem where admins see it.
        if get_forum_service().address_taken_by_another_forum_account(user, new_email):
            raise ValidationError(
                "Please check the form.",
                details={"fields": {"email_private": (
                    "This email address already belongs to another account on the forum. Please choose a "
                    "different address, or contact us if that forum account is yours.")}},
            )

    before_user = snapshot_user_for_audit(user)
    before_member = snapshot_member_for_audit(member, fields=DIRECT_MEMBER_PROFILE_FIELDS)
    try:
        email_changed = sync_member_primary_email(member, values.get("email_private"))
    except ValueError as exc:
        raise ValidationError("Please check the form.", details={"fields": {"email_private": str(exc)}}) from None

    # Through apply_member_profile rather than field by field, because that is
    # what drops the university address's confirmation when the address
    # changes -- and a confirmed university address is what gives a returning
    # student their old forum account and its posts.
    previous_email_work = (member.email_work or "").strip().lower()
    contact_fields = tuple(f for f in DIRECT_MEMBER_PROFILE_FIELDS if f != "email_private")
    apply_member_profile(member, {f: values.get(f) for f in contact_fields}, fields=contact_fields)
    work_email_changed = bool(member.email_work) and member.email_work.strip().lower() != previous_email_work

    log_audit_event(
        category="profile",
        event_type="contact_details_updated",
        actor_user=user,
        target_user=user,
        target_member=member,
        before={"user": before_user, "member": before_member},
        after={"user": snapshot_user_for_audit(user),
               "member": snapshot_member_for_audit(member, fields=DIRECT_MEMBER_PROFILE_FIELDS)},
        metadata={"email_changed": email_changed},
    )
    forum_result = None
    if has_forum_account:
        forum_result, _forum_service = sync_member_forum_state(member)
    db.session.commit()

    app = current_app._get_current_object()
    messages = []
    if email_changed:
        try:
            send_email_verification_email(app, user)
            messages.append(("success", "Saved. Please confirm your new email address with the link we sent you."))
            if has_forum_account:
                # The forum now has the new address and waits for it to be
                # confirmed, so the account there is paused until then.
                messages.append(("info", "Your forum access is paused until you confirm the new address."))
        except Exception as exc:  # noqa: BLE001 -- the reason belongs in the log
            current_app.logger.warning(
                "Could not send verification email after profile update for user_id=%s: %s", user.id, exc)
            messages.append(("success", "Saved."))
    else:
        messages.append(("success", "Saved."))
    if work_email_changed:
        try:
            if send_work_email_verification_email(app, member):
                db.session.commit()  # the link's nonce
                messages.append(("info", f"Please confirm {member.email_work} with the link we sent there."))
        except Exception as exc:  # noqa: BLE001 -- the reason belongs in the log, not the page
            db.session.rollback()
            current_app.logger.warning(
                "Could not send the work email verification after a profile update for member_id=%s: %s",
                member.id, exc)
    if forum_result and forum_result.error:
        messages.append(("warning", "Your forum profile could not be updated right now. We will try again."))
    return messages


# --- Name and kind of membership: asked for, decided by an admin -----------------------


def has_identity_changes(member, values):
    """Whether the request asks for anything: a field that differs, or a note."""
    for field_name in IDENTITY_MEMBER_FIELDS:
        requested = normalize_optional_member_value(field_name, values.get(field_name))
        if getattr(member, field_name) != requested:
            return True
    return bool((values.get("member_note") or "").strip())


def _new_identity_request(member, user, values):
    if member.open_identity_change_request is not None:
        raise ConflictError("You already have a change request waiting for review.", code="request_pending")
    record = MemberProfileChangeRequest(
        member=member,
        requested_by=user,
        requested_salutation=values["salutation"],
        requested_title=normalize_optional_member_value("title", values.get("title")),
        requested_first_name=values["first_name"],
        requested_last_name=values["last_name"],
        requested_member_category=values["member_category"],
        requested_year_group=normalize_optional_member_value("year_group", values.get("year_group")),
        member_note=(values.get("member_note") or "").strip() or None,
        status="pending",
    )
    db.session.add(record)
    return record


def request_identity_change(user, member, values, note):
    """Ask for a new name, salutation, kind of membership or year group."""
    data = {**values, "member_note": note}
    if not has_identity_changes(member, data):
        raise ValidationError("Nothing is different from what we have.", code="nothing_changed")
    record = _new_identity_request(member, user, data)
    db.session.flush()
    log_audit_event(
        category="profile_change_request",
        event_type="identity_request_submitted",
        actor_user=user,
        target_user=user,
        target_member=member,
        before=snapshot_member_for_audit(member, fields=IDENTITY_MEMBER_FIELDS),
        after={
            "requested_salutation": record.requested_salutation,
            "requested_title": record.requested_title,
            "requested_first_name": record.requested_first_name,
            "requested_last_name": record.requested_last_name,
            "requested_year_group": record.requested_year_group,
        },
        metadata={"request_id": record.id, "member_note": record.member_note},
    )
    queue_curated_admin_notification(
        ADMIN_GENERAL_CHANNEL,
        "identity_change_request_created",
        f"{member.email_private} asked for a profile change (name, membership type or year group).",
        payload={"member_email": member.email_private, "request_id": record.id, "member_note": record.member_note},
        target_user=user,
        target_member=member,
        object_type="member_profile_change_request",
        object_id=record.id,
    )
    db.session.commit()
    flush_marked_notification_channels()
    return record


def cancel_identity_change(user, member, request_id):
    """Take back a request not decided yet."""
    # Locked, as an admin's decision on it is: whichever comes second finds it settled.
    record = db.session.execute(
        locked(db.select(MemberProfileChangeRequest).where(MemberProfileChangeRequest.id == request_id))
    ).scalar_one_or_none()
    if record is None or record.member_id != member.id:
        raise NotFoundError("There is no such request.")
    if record.status != "pending":
        raise ConflictError("This request has already been decided.", code="already_decided")
    record.status = "canceled"
    record.reviewed_by = user
    record.reviewed_at = get_now_utc()
    log_audit_event(
        category="profile_change_request",
        event_type="identity_request_canceled",
        actor_user=user,
        target_user=user,
        target_member=member,
        before={"request_id": record.id, "status": "pending"},
        after={"request_id": record.id, "status": record.status},
        metadata={"member_note": record.member_note},
    )
    db.session.commit()


# --- Confirming the email addresses ----------------------------------------------------


def send_private_confirmation(user):
    """The link confirming the login address, again. Returns whether it went."""
    if user.email_is_verified:
        raise ConflictError("Your email address is already confirmed.", code="already_confirmed")
    try:
        sent = send_email_verification_email(current_app._get_current_object(), user)
    except Exception as exc:  # noqa: BLE001 -- the reason belongs in the log
        current_app.logger.warning("Could not resend verification email for user_id=%s: %s", user.id, exc)
        raise ExternalServiceError("We could not send the email right now. Please try again later.") from None
    if not sent:
        raise ExternalServiceError(NO_EMAIL, code="email_not_set_up")


def send_work_confirmation(member):
    """The link confirming the university or company address, again.

    For a returning student that address is the only evidence that can give
    them their old forum account, so there has to be a way to ask for it.
    """
    if member is None or not (member.email_work or "").strip():
        raise ConflictError("No university or company email address is on file.", code="no_address")
    if member.email_work_is_verified:
        raise ConflictError("Your university or company email address is already confirmed.",
                            code="already_confirmed")
    try:
        sent = send_work_email_verification_email(current_app._get_current_object(), member)
        if sent:
            db.session.commit()
    except Exception as exc:  # noqa: BLE001 -- the reason belongs in the log, not the page
        db.session.rollback()
        current_app.logger.warning(
            "Could not resend the work email verification for member_id=%s: %s", member.id, exc)
        raise ExternalServiceError("We could not send the email right now. Please try again later.") from None
    if not sent:
        raise ExternalServiceError(NO_EMAIL, code="email_not_set_up")


# --- Paying ----------------------------------------------------------------------------


def billing_url(member):
    """Stripe's billing page: payment method, invoices, cancelling."""
    try:
        return billing_portal_session(member).url
    except ValueError as exc:
        raise ConflictError(str(exc), code="no_billing") from None
    except stripe.StripeError as exc:
        current_app.logger.error("Could not create Stripe portal session for member_id=%s: %s", member.id, exc)
        raise ExternalServiceError("The billing page is unavailable. Please try again later.") from None


def resume_payment_url(member):
    """Stripe's payment page again, for a membership whose payment was not finished."""
    if not can_resume_payment(member):
        raise ConflictError("There is no unfinished payment to resume.", code="nothing_to_resume")
    try:
        session, _cycle = create_checkout_session_for_member(member)
        db.session.commit()
        return session.url
    except stripe.StripeError as exc:
        db.session.rollback()
        current_app.logger.error("Could not resume Checkout for member_id=%s: %s", member.id, exc)
    except Exception:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.exception("Unexpected error while resuming payment for member_id=%s", member.id)
    raise ExternalServiceError("The payment page is unavailable. Please try again later.")


def rejoin(user, member, payment_method, *, sent=None):
    """Start the membership again after it has ended. Returns where to pay,
    or None with a message when Stripe still runs it after all.

    Stripe cannot restart a cancelled subscription -- "canceled" is final
    there -- so this is a new one, on the same Stripe customer.
    """
    if not can_rejoin(member):
        raise ConflictError("Your membership has not ended, so there is nothing to restart.", code="not_ended")
    try:
        live = find_live_stripe_subscription(member)
    except stripe.StripeError as exc:
        current_app.logger.error(
            "Could not check Stripe for a running subscription before a rejoin for member_id=%s: %s",
            member.id, exc)
        raise ExternalServiceError("We could not check your billing with Stripe right now. "
                                   "Please try again later.") from None
    if live is not None:
        # The portal was behind Stripe, not the membership over. Catch up
        # instead of starting a second subscription that would charge twice.
        backfill_member_stripe_references(member, subscription_id=live.get("id"))
        sync_member_subscription_state_from_subscription(member, live)
        enqueue_forum_sync(member, reason="Found running while rejoining.")
        db.session.commit()
        return None, ("Your membership is still running in Stripe, so there is nothing to restart. "
                      "If a payment is outstanding, you can settle it under Manage billing.")

    payment_method = chosen_payment_method(payment_method)
    log_audit_event(
        category="membership",
        event_type="membership_rejoin_started",
        actor_user=user,
        target_user=user,
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
    return begin_membership(member, payment_method, what="rejoin", sent=sent), None


# --- Password --------------------------------------------------------------------------


def change_password(user, current_password, new_password):
    if not user.check_password(current_password):
        raise ValidationError("Please check the form.",
                              details={"fields": {"current_password": "This is not your current password."}})
    user.set_password(new_password)
    db.session.commit()


# --- Deleting the account --------------------------------------------------------------


def request_deletion(user):
    """Email the link that deletes the account. Nothing is erased here:
    the mailbox as well as the session means a borrowed laptop cannot
    destroy an account, and it gives a moment to change one's mind."""
    try:
        sent = send_account_deletion_email(current_app._get_current_object(), user)
    except Exception as exc:  # noqa: BLE001 -- the reason belongs in the log
        current_app.logger.warning("Could not send account deletion email for user_id=%s: %s", user.id, exc)
        raise ExternalServiceError("We could not send the confirmation email right now. "
                                   "Please try again later.") from None
    if not sent:
        raise ExternalServiceError(NO_EMAIL, code="email_not_set_up")
    log_audit_event(
        category="privacy",
        event_type="account_deletion_requested",
        actor_user=user,
        target_user=user,
        target_member=user.member,
    )
    db.session.commit()


def _deletion_token(user, token):
    try:
        data = read_token(token, "delete-account", TOKEN_MAX_AGE_ACCOUNT_DELETION)
    except Exception:  # noqa: BLE001 -- expired, altered or not a token at all
        raise ValidationError("This deletion link is invalid or has expired. Please start again.",
                              code="link_invalid") from None
    if not account_deletion_claims_match(data, user):
        raise PermissionError_("This deletion link does not belong to the account you are signed in to.",
                               code="link_other_account")


def deletion_impact(user, token):
    """What deleting will do. Changes nothing: mail clients and link scanners
    open links in emails without being asked."""
    _deletion_token(user, token)
    return describe_deletion_impact(user, actor_user=user)


def delete_account(user, token):
    """Erase the account now. The caller signs the person out."""
    _deletion_token(user, token)
    # Read afresh under the lock: an admin may be erasing this account, or
    # taking away the other admin, in the same moment.
    lock_administration()
    db.session.execute(locked(db.select(User).filter_by(id=user.id))).scalar_one()
    impact = describe_deletion_impact(user, actor_user=user)
    if "last_admin" in impact["blockers"]:
        raise ConflictError("You are the only administrator. Give someone else admin access before deleting "
                            "your account.", code="last_admin")
    email = user.email
    try:
        erase_account(user, actor_user=user, initiated_by=INITIATED_BY_MEMBER)
    except ServiceError:
        db.session.rollback()
        raise
    db.session.commit()
    current_app.logger.info("Account erased on member request (previously %s).", email)
