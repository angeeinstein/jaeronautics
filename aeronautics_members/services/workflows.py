"""Operations that span more than one domain.

Each function here coordinates services that should not know about each other: a
welcome email needs both the mail machinery and the forum handoff; refreshing a
member's state touches billing, membership and the forum. Keeping the
coordination in its own module is what stops, say, the billing service from
importing the forum service and creating a cycle.

These are the natural entry points for a route -- HTML today, JSON later -- since
they express a complete operation rather than a step of one.
"""

import os

import stripe
from flask import current_app
from flask_babel import _
from sqlalchemy import or_

from ..db_models import EmailDeliveryJob, ExternalWorkItem, Member, User, db
from . import ExternalServiceError
from ..mail_utils import send_mail
from ..notification_service import ADMIN_ERROR_CHANNEL
from .billing import (
    apply_runtime_stripe_config,
    get_latest_stripe_subscription_for_member,
    sync_member_subscription_state_from_subscription,
)
from .clock import get_now_utc
from .forum import (
    build_forum_entry_url,
    generate_suggested_username,
    get_forum_service,
    sync_member_forum_state,
)
from .identity import rotate_email_verification_nonce
from .membership import sync_member_active_state
from .outbox import register_handler
from .notifications import (
    EMAIL_JOB_STATUS_CANCELED,
    EMAIL_JOB_STATUS_EXHAUSTED,
    EMAIL_JOB_STATUS_PENDING,
    EMAIL_JOB_STATUS_SENT,
    EMAIL_JOB_TYPE_WELCOME,
    WELCOME_EMAIL_RETRY_DELAYS,
    mark_email_delivery_jobs_sent,
    queue_curated_admin_notification,
    queue_welcome_email_retry_job,
)
from .settings import get_settings_map




def send_member_welcome_email(app, member, force_send=False, notify_on_failure=True, queue_retry_on_failure=None, return_error=False):
    settings = get_settings_map()
    if queue_retry_on_failure is None:
        queue_retry_on_failure = not force_send

    if not force_send and settings.get("automatic_emails_enabled") != "True":
        return (False, _("Automatic welcome emails are disabled.")) if return_error else False

    sender_account = settings.get("welcome_email_sender")
    template_name = settings.get("automatic_email_template")
    if not sender_account or not template_name:
        error_message = _("Email sender or template is not configured in the admin settings.")
        if force_send:
            raise ValueError(error_message)
        if queue_retry_on_failure:
            queue_welcome_email_retry_job(member, error_message=error_message)
        if notify_on_failure:
            queue_curated_admin_notification(
                ADMIN_ERROR_CHANNEL,
                "welcome_email_failed",
                _("A welcome email could not be sent because the sender or template is not configured."),
                payload={
                    "recipient": member.email_private,
                    "sender_account": sender_account or None,
                    "template_name": template_name or None,
                    "error": error_message,
                },
                target_user=member.user,
                target_member=member,
                commit=True,
            )
        return (False, error_message) if return_error else False

    suggested_username = member.user.forum_username if member.user and member.user.forum_username else generate_suggested_username(member)
    logo_path = os.path.join(app.root_path, "static", "logo_joanneum_aeronautics_negativ.png")
    attachments = [{"path": logo_path, "cid": "logo"}] if os.path.exists(logo_path) else None

    forum_service = get_forum_service()
    forum_entry_url = None
    if forum_service.is_enabled() and member.user is not None:
        forum_entry_url = build_forum_entry_url(member.user, include_token=True)

    success, error_message = send_mail(
        from_account=sender_account,
        to_email=member.email_private,
        subject=_("Welcome to Joanneum Aeronautics!"),
        template_name=template_name,
        attachments=attachments,
        first_name=member.first_name,
        suggested_username=suggested_username,
        membership_starts_on=member.membership_starts_on,
        membership_ends_on=member.membership_ends_on,
        renewal_due_on=member.renewal_due_on,
        forum_integration_enabled=forum_service.is_enabled(),
        forum_entry_url=forum_entry_url,
        now=get_now_utc(),
        return_error=True,
    )
    if success:
        mark_email_delivery_jobs_sent(EMAIL_JOB_TYPE_WELCOME, target_member=member)
        return (True, None) if return_error else True

    if queue_retry_on_failure:
        queue_welcome_email_retry_job(member, error_message=error_message)
    if notify_on_failure:
        queue_curated_admin_notification(
            ADMIN_ERROR_CHANNEL,
            "welcome_email_failed",
            _("A welcome email could not be sent."),
            payload={
                "recipient": member.email_private,
                "sender_account": sender_account,
                "template_name": template_name,
                "error": error_message,
            },
            target_user=member.user,
            target_member=member,
            commit=True,
        )
    return (False, error_message) if return_error else False


def refresh_member_billing_state(member, force_stripe_sync=False, sync_forum=False, on_date=None):
    if member is None:
        return False, None, None

    changed = False
    stripe_subscription = None
    has_stripe_reference = bool(member.stripe_customer_id or member.stripe_subscription_id)

    if has_stripe_reference and force_stripe_sync:
        stripe_subscription = get_latest_stripe_subscription_for_member(member)
        if stripe_subscription and sync_member_subscription_state_from_subscription(member, stripe_subscription):
            changed = True
        # The lookup may have cleared a dead subscription/customer reference.
        if bool(member.stripe_customer_id or member.stripe_subscription_id) != has_stripe_reference:
            changed = True

    if sync_member_active_state(member, on_date=on_date):
        changed = True

    forum_result = None
    forum_service = get_forum_service()
    if sync_forum and member.user is not None and (forum_service.is_enabled() or member.user.forum_account is not None):
        forum_result, _forum_service = sync_member_forum_state(member)
        if forum_result and forum_result.changed:
            changed = True

    return changed, stripe_subscription, forum_result


def sync_member_primary_email(member, new_email):
    new_email = (new_email or "").strip().lower()
    if not new_email:
        raise ValueError(_("The private email address is required."))

    existing_member = db.session.execute(
        db.select(Member).filter(Member.email_private == new_email, Member.id != member.id)
    ).scalar_one_or_none()
    if existing_member is not None:
        raise ValueError(_("A membership profile with this email address already exists."))

    if member.user is not None:
        existing_user = db.session.execute(
            db.select(User).filter(User.email == new_email, User.id != member.user.id)
        ).scalar_one_or_none()
        if existing_user is not None:
            raise ValueError(_("An account with this email address already exists."))

    email_changed = member.email_private != new_email
    member.email_private = new_email

    if member.user is not None and member.user.email != new_email:
        member.user.email = new_email
        member.user.email_verified_at = None
        # Invalidate any verification/forum link issued for the previous address.
        rotate_email_verification_nonce(member.user)

    if email_changed and member.stripe_customer_id:
        try:
            apply_runtime_stripe_config()
            stripe.Customer.modify(member.stripe_customer_id, email=new_email)
        except Exception as exc:
            current_app.logger.warning(
                "Could not sync Stripe customer email for member_id=%s customer_id=%s: %s",
                member.id,
                member.stripe_customer_id,
                exc,
            )

    return email_changed


def process_email_delivery_jobs(app):
    now = get_now_utc()
    summary = {
        "processed": 0,
        "sent": 0,
        "exhausted": 0,
        "canceled": 0,
        "failed": 0,
    }
    jobs = db.session.execute(
        db.select(EmailDeliveryJob)
        .where(
            EmailDeliveryJob.status == EMAIL_JOB_STATUS_PENDING,
            or_(EmailDeliveryJob.next_attempt_at.is_(None), EmailDeliveryJob.next_attempt_at <= now),
        )
        .order_by(EmailDeliveryJob.next_attempt_at.asc(), EmailDeliveryJob.id.asc())
    ).scalars().all()
    if not jobs:
        return summary

    automatic_emails_enabled = get_settings_map().get("automatic_emails_enabled") == "True"

    for job in jobs:
        summary["processed"] += 1
        job.last_attempted_at = now

        if job.email_type != EMAIL_JOB_TYPE_WELCOME:
            job.status = EMAIL_JOB_STATUS_CANCELED
            job.next_attempt_at = None
            job.last_error = _("This queued email type is no longer supported.")
            summary["canceled"] += 1
            continue

        member = db.session.get(Member, job.target_member_id) if job.target_member_id else None
        if member is None:
            job.status = EMAIL_JOB_STATUS_CANCELED
            job.next_attempt_at = None
            job.last_error = _("The linked member profile no longer exists.")
            summary["canceled"] += 1
            continue

        job.recipient_email = member.email_private

        if not automatic_emails_enabled:
            job.status = EMAIL_JOB_STATUS_CANCELED
            job.next_attempt_at = None
            job.last_error = _("Automatic emails were disabled before this retry could be sent.")
            summary["canceled"] += 1
            continue

        success, error_message = send_member_welcome_email(
            app,
            member,
            force_send=False,
            notify_on_failure=False,
            queue_retry_on_failure=False,
            return_error=True,
        )
        if success:
            mark_email_delivery_jobs_sent(EMAIL_JOB_TYPE_WELCOME, target_member=member)
            job.status = EMAIL_JOB_STATUS_SENT
            job.sent_at = now
            job.next_attempt_at = None
            job.last_error = None
            summary["sent"] += 1
            continue

        summary["failed"] += 1
        job.last_error = (error_message or _("The welcome email could not be sent."))[:4000]
        job.retry_count += 1
        if job.retry_count >= len(WELCOME_EMAIL_RETRY_DELAYS):
            job.status = EMAIL_JOB_STATUS_EXHAUSTED
            job.next_attempt_at = None
            summary["exhausted"] += 1
            queue_curated_admin_notification(
                ADMIN_ERROR_CHANNEL,
                "welcome_email_retry_exhausted",
                _("A welcome email could not be delivered after automatic retries."),
                payload={
                    "recipient": member.email_private,
                    "last_error": job.last_error,
                    "retry_count": job.retry_count,
                },
                target_user=member.user,
                target_member=member,
                commit=False,
            )
            continue

        job.next_attempt_at = now + WELCOME_EMAIL_RETRY_DELAYS[job.retry_count]

    return summary


def _handle_forum_sync_work(item):
    """Outbox handler: bring one member's Discourse state up to date.

    Raises on a reported sync error so the item is retried with backoff rather
    than being marked done while the two systems still disagree.
    """
    member = item.member
    if member is None or member.user is None:
        # The member was deleted after the work was queued; nothing left to do.
        return
    if member.deleted_at is not None:
        # Erased between queueing and running. Syncing now would push the
        # placeholder profile to Discourse and undo the anonymisation.
        return
    result, _service = sync_member_forum_state(member)
    db.session.commit()
    if result is not None and result.error:
        raise ExternalServiceError(f"Forum sync failed for member_id={member.id}: {result.error}")


def _handle_forum_anonymise_work(item):
    """Outbox handler: retry a Discourse anonymisation an erasure could not do.

    The local data is already gone by the time this runs, so there is nothing to
    fall back to -- this has to keep trying until Discourse accepts it. Raising
    on failure is what puts it back in the queue with backoff.
    """
    user = item.user
    if user is None or user.forum_account is None:
        return
    anonymised, error = get_forum_service().anonymize_user(user)
    db.session.commit()
    if error:
        raise ExternalServiceError(f"Forum anonymisation failed for user_id={user.id}: {error}")
    if not anonymised:
        # No remote account to anonymise (already gone, or the integration is
        # switched off). Nothing further will change that, so stop retrying.
        current_app.logger.info(
            "No Discourse account to anonymise for user_id=%s; marking the work done.", user.id
        )


register_handler(ExternalWorkItem.KIND_FORUM_SYNC, _handle_forum_sync_work)
register_handler(ExternalWorkItem.KIND_FORUM_ANONYMISE, _handle_forum_anonymise_work)
