"""Notifications, transactional email and the mail-account configuration.

Delivery is queued rather than sent inline: an SMTP server that is slow or down
must not make a member's signup fail, and a message that could not be sent needs
to be retried rather than lost. ``EmailDeliveryJob`` rows carry that state, which
is why queueing and sending are separate steps here.

Admin notifications are "curated": they are grouped into channels so a burst of
related failures does not turn into a burst of identical emails.
"""

import os
from datetime import datetime, timedelta, timezone

from flask import current_app
from flask_babel import _

from ..db_models import EmailDeliveryJob, MailAccount, Setting, db
from ..mail_utils import load_mail_accounts_config, send_mail
from ..notification_service import (
    ADMIN_ERROR_CHANNEL,
    ADMIN_GENERAL_CHANNEL,
    NOTIFICATION_SETTING_KEYS,
    NotificationService,
)
from .clock import get_now_utc
from .settings import get_settings_map

EMAIL_JOB_TYPE_WELCOME = "welcome_email"
EMAIL_JOB_STATUS_PENDING = "pending"
EMAIL_JOB_STATUS_SENT = "sent"
EMAIL_JOB_STATUS_EXHAUSTED = "exhausted"
EMAIL_JOB_STATUS_CANCELED = "canceled"
# A failed welcome email is retried twice, soon and then the next day, before it
# is marked exhausted and surfaced to an administrator.
WELCOME_EMAIL_RETRY_DELAYS = (
    timedelta(minutes=15),
    timedelta(hours=24),
)




def get_notification_settings_map():
    return get_settings_map(NOTIFICATION_SETTING_KEYS)


def get_notification_service():
    return NotificationService(current_app._get_current_object())


def flush_marked_notification_channels():
    channels = sorted(db.session.info.pop("notification_channels_to_flush", set()))
    if not channels:
        return {}
    try:
        return get_notification_service().deliver_pending_notifications(channels=channels)
    except Exception as exc:
        current_app.logger.error("Could not flush queued notification emails: %s", exc)
        return {}


def queue_curated_admin_notification(channel, event_type, summary, payload=None, target_user=None, target_member=None, object_type=None, object_id=None, severity="error", commit=False):
    if channel not in {ADMIN_GENERAL_CHANNEL, ADMIN_ERROR_CHANNEL}:
        return None
    try:
        service = get_notification_service()
        if channel == ADMIN_GENERAL_CHANNEL:
            event = service.queue_admin_general(
                event_type=event_type,
                summary=summary,
                payload=payload,
                target_user=target_user,
                target_member=target_member,
                object_type=object_type,
                object_id=object_id,
            )
        else:
            event = service.queue_admin_error(
                event_type=event_type,
                summary=summary,
                payload=payload,
                target_user=target_user,
                target_member=target_member,
                object_type=object_type,
                object_id=object_id,
                severity=severity,
            )
        if commit and event is not None:
            db.session.commit()
            flush_marked_notification_channels()
        return event
    except Exception as exc:
        current_app.logger.error("Could not queue admin notification '%s': %s", event_type, exc)
        if commit:
            db.session.rollback()
        return None


def queue_user_status_notification(event_type, summary, recipient_email, payload=None, target_user=None, target_member=None, object_type=None, object_id=None):
    try:
        return get_notification_service().queue_user_status(
            event_type=event_type,
            summary=summary,
            recipient_email=recipient_email,
            payload=payload,
            target_user=target_user,
            target_member=target_member,
            object_type=object_type,
            object_id=object_id,
        )
    except Exception as exc:
        current_app.logger.error("Could not queue user notification '%s': %s", event_type, exc)
        return None


def get_default_sender_account():
    settings = {s.key: s.value for s in Setting.query.all()}
    preferred_sender = settings.get("welcome_email_sender")
    if preferred_sender:
        return preferred_sender

    try:
        mail_accounts = load_mail_accounts_config()
        return next(iter(mail_accounts.keys()), None)
    except Exception:
        return None


def send_account_action_email(
    app,
    to_email,
    subject,
    preview_text,
    action_url,
    action_label,
    heading,
    body_lines,
    failure_event_type="account_action_email_failed",
    failure_summary=None,
    failure_payload=None,
    target_user=None,
    target_member=None,
    notify_on_failure=True,
):
    sender_account = get_default_sender_account()
    failure_summary = failure_summary or _("An account-related email could not be sent.")
    payload = {
        "recipient": to_email,
        "subject": subject,
        "sender_account": sender_account or None,
        **(failure_payload or {}),
    }
    if not sender_account:
        app.logger.warning("Could not send account email to %s because no sender account is configured.", to_email)
        if notify_on_failure:
            queue_curated_admin_notification(
                ADMIN_ERROR_CHANNEL,
                failure_event_type,
                failure_summary,
                payload=payload,
                target_user=target_user,
                target_member=target_member,
                commit=True,
            )
        return False

    logo_path = os.path.join(app.root_path, "static", "logo_joanneum_aeronautics_negativ.png")
    attachments = [{"path": logo_path, "cid": "logo"}] if os.path.exists(logo_path) else None
    success, error_message = send_mail(
        from_account=sender_account,
        to_email=to_email,
        subject=subject,
        template_name="member_account_action.html",
        attachments=attachments,
        preview_text=preview_text,
        action_url=action_url,
        action_label=action_label,
        heading=heading,
        body_lines=body_lines,
        now=get_now_utc(),
        return_error=True,
    )
    if not success and notify_on_failure:
        queue_curated_admin_notification(
            ADMIN_ERROR_CHANNEL,
            failure_event_type,
            failure_summary,
            payload={**payload, "error": error_message},
            target_user=target_user,
            target_member=target_member,
            commit=True,
        )
    return success


def queue_email_delivery_job(email_type, recipient_email=None, target_user=None, target_member=None, payload=None, initial_delay=None, error_message=None):
    normalized_recipient = (recipient_email or "").strip().lower() or None
    if initial_delay is None:
        initial_delay = timedelta()

    query = db.select(EmailDeliveryJob).where(
        EmailDeliveryJob.email_type == email_type,
        EmailDeliveryJob.status == EMAIL_JOB_STATUS_PENDING,
    )
    if target_member is not None and target_member.id is not None:
        query = query.where(EmailDeliveryJob.target_member_id == target_member.id)
    elif target_user is not None and target_user.id is not None:
        query = query.where(EmailDeliveryJob.target_user_id == target_user.id)
    elif normalized_recipient:
        query = query.where(EmailDeliveryJob.recipient_email == normalized_recipient)
    else:
        return None, False

    existing_job = db.session.execute(
        query.order_by(EmailDeliveryJob.created_at.asc(), EmailDeliveryJob.id.asc())
    ).scalars().first()
    if existing_job is not None:
        if normalized_recipient:
            existing_job.recipient_email = normalized_recipient
        if payload is not None:
            existing_job.payload = payload
        if error_message:
            existing_job.last_error = str(error_message)[:4000]
        return existing_job, False

    job = EmailDeliveryJob(
        email_type=email_type,
        recipient_email=normalized_recipient,
        target_user_id=target_user.id if target_user is not None else None,
        target_member_id=target_member.id if target_member is not None else None,
        payload=payload,
        status=EMAIL_JOB_STATUS_PENDING,
        retry_count=0,
        next_attempt_at=get_now_utc() + initial_delay,
        last_error=str(error_message)[:4000] if error_message else None,
    )
    db.session.add(job)
    return job, True


def queue_welcome_email_retry_job(member, error_message=None):
    if member is None:
        return None, False
    return queue_email_delivery_job(
        EMAIL_JOB_TYPE_WELCOME,
        recipient_email=member.email_private,
        target_user=member.user,
        target_member=member,
        initial_delay=WELCOME_EMAIL_RETRY_DELAYS[0],
        error_message=error_message,
    )


def mark_email_delivery_jobs_sent(email_type, target_user=None, target_member=None):
    query = db.select(EmailDeliveryJob).where(
        EmailDeliveryJob.email_type == email_type,
        EmailDeliveryJob.status == EMAIL_JOB_STATUS_PENDING,
    )
    if target_member is not None and target_member.id is not None:
        query = query.where(EmailDeliveryJob.target_member_id == target_member.id)
    elif target_user is not None and target_user.id is not None:
        query = query.where(EmailDeliveryJob.target_user_id == target_user.id)
    else:
        return 0

    jobs = db.session.execute(query).scalars().all()
    if not jobs:
        return 0

    now = get_now_utc()
    for job in jobs:
        job.status = EMAIL_JOB_STATUS_SENT
        job.sent_at = now
        job.next_attempt_at = None
        job.last_error = None
    return len(jobs)




def get_db_mail_accounts():
    try:
        return db.session.execute(
            db.select(MailAccount).order_by(MailAccount.account_key.asc())
        ).scalars().all()
    except Exception:
        return []


def get_email_template_choices(app):
    template_choices = []
    email_template_dir = os.path.join(app.root_path, "templates", "emails")
    if os.path.isdir(email_template_dir):
        template_choices = [(f, f) for f in os.listdir(email_template_dir) if f.endswith(".html")]
    return template_choices


def normalize_mail_account_key(raw_key):
    if raw_key is None:
        return ""
    normalized = "".join(
        character if (character.isalnum() or character in {"-", "_"}) else "_"
        for character in str(raw_key).strip()
    )
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized.strip("_")


def parse_imported_starttls(value, security_hint=None):
    if value is not None:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "starttls"}
        return bool(value)

    security_value = (security_hint or "").strip().lower()
    if security_value in {"starttls", "tls-starttls", "smtp-starttls", "explicit_tls"}:
        return True
    if security_value in {"ssl", "ssl/tls", "tls", "implicit_tls"}:
        return False
    return False


def normalize_imported_mail_account_record(raw_record, fallback_key=None):
    if not isinstance(raw_record, dict):
        raise ValueError("Each imported mail account entry must be a JSON object.")

    account_key = normalize_mail_account_key(
        raw_record.get("account_key")
        or raw_record.get("key")
        or raw_record.get("name")
        or fallback_key
    )
    host = (raw_record.get("host") or raw_record.get("smtp_host") or raw_record.get("server") or "").strip()
    username = (
        raw_record.get("username")
        or raw_record.get("user")
        or raw_record.get("email")
        or raw_record.get("login")
        or ""
    ).strip()
    password = (
        raw_record.get("password")
        or raw_record.get("pass")
        or raw_record.get("secret")
        or raw_record.get("smtp_password")
        or ""
    )
    port_value = raw_record.get("port") or raw_record.get("smtp_port")
    security_hint = raw_record.get("security") or raw_record.get("encryption") or raw_record.get("transport_security")
    starttls = parse_imported_starttls(raw_record.get("starttls"), security_hint=security_hint)

    if not account_key:
        raise ValueError("Every imported mail account needs a valid account key.")
    if not host:
        raise ValueError(f"Mail account '{account_key}' is missing the SMTP host.")
    if not username:
        raise ValueError(f"Mail account '{account_key}' is missing the SMTP username.")
    if not password:
        raise ValueError(f"Mail account '{account_key}' is missing the SMTP password.")
    if port_value in (None, ""):
        raise ValueError(f"Mail account '{account_key}' is missing the SMTP port.")

    try:
        port = int(port_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Mail account '{account_key}' has an invalid SMTP port.") from exc

    if port < 1 or port > 65535:
        raise ValueError(f"Mail account '{account_key}' has an invalid SMTP port.")

    return {
        "account_key": account_key,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "starttls": starttls,
    }


def normalize_imported_mail_accounts_payload(payload):
    raw_records = []

    if isinstance(payload, dict) and isinstance(payload.get("mail_accounts"), list):
        raw_records = [(None, entry) for entry in payload.get("mail_accounts", [])]
    elif isinstance(payload, list):
        raw_records = [(None, entry) for entry in payload]
    elif isinstance(payload, dict):
        raw_records = [
            (key, value)
            for key, value in payload.items()
            if isinstance(value, dict)
        ]
    else:
        raise ValueError("The uploaded JSON must be a Jaeronautics export, a legacy mail-account mapping, or a list of mail account objects.")

    if not raw_records:
        raise ValueError("The uploaded file does not contain any mail accounts.")

    normalized_records = []
    seen_keys = set()
    for fallback_key, raw_record in raw_records:
        normalized = normalize_imported_mail_account_record(raw_record, fallback_key=fallback_key)
        if normalized["account_key"] in seen_keys:
            raise ValueError(f"The uploaded file contains the account key '{normalized['account_key']}' more than once.")
        seen_keys.add(normalized["account_key"])
        normalized_records.append(normalized)

    return normalized_records


def build_mail_accounts_export_payload():
    return {
        "format": "jaeronautics_mail_accounts",
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "mail_accounts": [
            {
                "account_key": mail_account.account_key,
                "host": mail_account.host,
                "port": mail_account.port,
                "username": mail_account.username,
                "password": mail_account.password,
                "starttls": mail_account.starttls,
            }
            for mail_account in get_db_mail_accounts()
        ],
    }
