from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import os
import re

from flask import current_app
from flask_babel import _
from sqlalchemy import func

try:
    from .db_models import (
        MailAccount,
        NotificationBatch,
        NotificationChannelState,
        NotificationEvent,
        Role,
        Setting,
        User,
        db,
    )
    from .mail_utils import send_mail
    from .security_utils import build_public_url
except ImportError:
    from db_models import (
        MailAccount,
        NotificationBatch,
        NotificationChannelState,
        NotificationEvent,
        Role,
        Setting,
        User,
        db,
    )
    from mail_utils import send_mail
    from security_utils import build_public_url

ADMIN_GENERAL_CHANNEL = "admin_general"
ADMIN_ERROR_CHANNEL = "admin_error"
USER_STATUS_CHANNEL = "user_status"
NOTIFICATION_CHANNELS = (
    ADMIN_GENERAL_CHANNEL,
    ADMIN_ERROR_CHANNEL,
    USER_STATUS_CHANNEL,
)
NOTIFICATION_SETTING_KEYS = (
    "notification_admin_general_enabled",
    "notification_admin_error_enabled",
    "notification_user_status_enabled",
    "notification_sender",
)
DEFAULT_NOTIFICATION_SETTINGS = {
    "notification_admin_general_enabled": "True",
    "notification_admin_error_enabled": "True",
    "notification_user_status_enabled": "True",
    "notification_sender": "",
}
CHANNEL_COOLDOWN_LADDERS = {
    ADMIN_GENERAL_CHANNEL: [0, 60, 240, 720],
    ADMIN_ERROR_CHANNEL: [0, 15, 60, 240],
    USER_STATUS_CHANNEL: [0],
}
CHANNEL_DAILY_CAPS = {
    ADMIN_GENERAL_CHANNEL: 3,
    ADMIN_ERROR_CHANNEL: 4,
    USER_STATUS_CHANNEL: None,
}
CHANNEL_AUDIENCE = {
    ADMIN_GENERAL_CHANNEL: "admin",
    ADMIN_ERROR_CHANNEL: "admin",
    USER_STATUS_CHANNEL: "user",
}
FAILURE_BACKOFF_LADDER = [30, 120, 720]
QUIET_RESET_WINDOW = timedelta(hours=24)
ADMIN_EVENT_LIST_LIMIT = 12
SENSITIVE_NOTIFICATION_FIELD_NAMES = {
    "password",
    "pass",
    "secret",
    "smtp_password",
    "stripe_secret_key",
    "stripe_webhook_secret",
    "discourse_api_key",
    "discourse_connect_secret",
}


def normalize_notification_settings(settings_map):
    values = dict(DEFAULT_NOTIFICATION_SETTINGS)
    values.update(settings_map or {})
    values["notification_admin_general_enabled"] = normalize_bool(values.get("notification_admin_general_enabled"))
    values["notification_admin_error_enabled"] = normalize_bool(values.get("notification_admin_error_enabled"))
    values["notification_user_status_enabled"] = normalize_bool(values.get("notification_user_status_enabled"))
    values["notification_sender"] = (values.get("notification_sender") or "").strip()
    return values



def normalize_bool(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}



def serialize_notification_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: serialize_notification_value(inner_value) for key, inner_value in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [serialize_notification_value(inner_value) for inner_value in value]
    return value



def is_sensitive_notification_field(field_name):
    normalized_name = str(field_name or "").strip().lower()
    if not normalized_name:
        return False
    if normalized_name in SENSITIVE_NOTIFICATION_FIELD_NAMES:
        return True
    return any(token in normalized_name for token in ("secret", "password", "api_key", "webhook_secret"))



def redact_notification_value(value, placeholder="<configured>"):
    serialized = serialize_notification_value(value)
    if isinstance(serialized, dict):
        redacted = {}
        for key, inner_value in serialized.items():
            if is_sensitive_notification_field(key):
                has_secret_value = inner_value not in {None, "", [], {}}
                redacted[key] = placeholder if has_secret_value else None
            else:
                redacted[key] = redact_notification_value(inner_value, placeholder=placeholder)
        return redacted
    if isinstance(serialized, list):
        return [redact_notification_value(item, placeholder=placeholder) for item in serialized]
    return serialized


def sanitize_notification_error(message):
    text = str(message or "Notification delivery failed.")
    text = re.sub(r'(?i)\b(password|secret|api[_ -]?key|webhook[_ -]?secret)\b\s*[:=]\s*[^,\s]+', r'\1=<redacted>', text)
    return text[:4000]



def ensure_utc_datetime(value):
    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class NotificationService:
    def __init__(self, app=None):
        self.app = app or current_app._get_current_object()

    def get_settings(self):
        rows = db.session.execute(
            db.select(Setting).where(Setting.key.in_(NOTIFICATION_SETTING_KEYS))
        ).scalars().all()
        return normalize_notification_settings({row.key: row.value for row in rows})

    def is_enabled(self, channel):
        settings = self.get_settings()
        if channel == ADMIN_GENERAL_CHANNEL:
            return settings["notification_admin_general_enabled"]
        if channel == ADMIN_ERROR_CHANNEL:
            return settings["notification_admin_error_enabled"]
        if channel == USER_STATUS_CHANNEL:
            return settings["notification_user_status_enabled"]
        return False

    def get_sender_account(self):
        settings = self.get_settings()
        if settings["notification_sender"]:
            return settings["notification_sender"]
        fallback = db.session.get(Setting, "welcome_email_sender")
        return (fallback.value if fallback is not None else "") or ""

    def queue_admin_general(self, event_type, summary, payload=None, target_user=None, target_member=None, object_type=None, object_id=None):
        return self.queue_event(
            channel=ADMIN_GENERAL_CHANNEL,
            severity="info",
            event_type=event_type,
            summary=summary,
            payload=payload,
            target_user=target_user,
            target_member=target_member,
            object_type=object_type,
            object_id=object_id,
        )

    def queue_admin_error(self, event_type, summary, payload=None, target_user=None, target_member=None, object_type=None, object_id=None, severity="error"):
        return self.queue_event(
            channel=ADMIN_ERROR_CHANNEL,
            severity=severity,
            event_type=event_type,
            summary=summary,
            payload=payload,
            target_user=target_user,
            target_member=target_member,
            object_type=object_type,
            object_id=object_id,
        )

    def queue_user_status(self, event_type, summary, recipient_email, payload=None, target_user=None, target_member=None, object_type=None, object_id=None):
        if not recipient_email:
            return None
        return self.queue_event(
            channel=USER_STATUS_CHANNEL,
            severity="info",
            event_type=event_type,
            summary=summary,
            payload=payload,
            target_user=target_user,
            target_member=target_member,
            recipient_email=recipient_email,
            object_type=object_type,
            object_id=object_id,
        )

    def queue_event(self, channel, severity, event_type, summary, payload=None, target_user=None, target_member=None, recipient_email=None, object_type=None, object_id=None):
        if channel not in NOTIFICATION_CHANNELS or not self.is_enabled(channel):
            return None

        event = NotificationEvent(
            channel=channel,
            audience=CHANNEL_AUDIENCE[channel],
            severity=(severity or "info").strip().lower(),
            event_type=event_type,
            summary=(summary or "").strip()[:255],
            payload=redact_notification_value(payload) if payload is not None else None,
            target_user=target_user,
            target_member=target_member,
            recipient_email=(recipient_email or "").strip().lower() or None,
            object_type=(object_type or "").strip() or None,
            object_id=int(object_id) if object_id is not None else None,
        )
        db.session.add(event)
        state = self._get_or_create_channel_state(channel)
        now = datetime.now(timezone.utc)
        state.last_activity_at = now
        db.session.info.setdefault("notification_channels_to_flush", set()).add(channel)
        return event

    def deliver_pending_notifications(self, channels=None):
        now = datetime.now(timezone.utc)
        summary = {
            "sent_batches": 0,
            "failed_batches": 0,
            "sent_events": 0,
            "failed_events": 0,
            "deferred_channels": [],
        }
        selected_channels = list(channels or NOTIFICATION_CHANNELS)
        for channel in selected_channels:
            if channel == USER_STATUS_CHANNEL:
                channel_result = self._deliver_user_status_events(now)
            else:
                channel_result = self._deliver_admin_digest(channel, now)
            summary["sent_batches"] += channel_result.get("sent_batches", 0)
            summary["failed_batches"] += channel_result.get("failed_batches", 0)
            summary["sent_events"] += channel_result.get("sent_events", 0)
            summary["failed_events"] += channel_result.get("failed_events", 0)
            if channel_result.get("deferred"):
                summary["deferred_channels"].append(channel)
        return summary

    def get_health_snapshot(self):
        pending_counts = {
            channel: count
            for channel, count in db.session.execute(
                db.select(NotificationEvent.channel, func.count(NotificationEvent.id))
                .where(NotificationEvent.status == "pending")
                .group_by(NotificationEvent.channel)
            ).all()
        }
        health = {}
        now = datetime.now(timezone.utc)
        for channel in NOTIFICATION_CHANNELS:
            state, next_gate = self._refresh_channel_state(channel, now)
            ladder = CHANNEL_COOLDOWN_LADDERS.get(channel, [0])
            stage = state.cooldown_stage or 0
            stage = min(stage, len(ladder) - 1)
            failure_backoff_until = ensure_utc_datetime(state.failure_backoff_until)
            last_sent_at = ensure_utc_datetime(state.last_sent_at)
            health[channel] = {
                "enabled": self.is_enabled(channel),
                "pending_count": pending_counts.get(channel, 0),
                "cooldown_stage": stage,
                "cooldown_minutes": ladder[stage],
                "next_allowed_at": next_gate if next_gate > now else None,
                "rolling_sent_count": state.rolling_sent_count,
                "daily_cap": CHANNEL_DAILY_CAPS.get(channel),
                "failure_backoff_until": failure_backoff_until if failure_backoff_until and failure_backoff_until > now else None,
                "last_failure_message": state.last_failure_message,
                "last_sent_at": last_sent_at,
            }
        return health

    def _deliver_admin_digest(self, channel, now):
        pending_events = self._get_pending_events(channel)
        if not pending_events or not self.is_enabled(channel):
            return {}

        state, next_gate = self._refresh_channel_state(channel, now)
        if next_gate > now:
            return {"deferred": True}

        recipients = self.get_admin_recipient_emails()
        if not recipients:
            return {"deferred": True}

        batch = NotificationBatch(
            channel=channel,
            status="failed",
            recipient_scope="verified_admins",
            recipient_count=len(recipients),
            event_count=len(pending_events),
        )
        db.session.add(batch)
        subject, template_vars = self._build_admin_digest_message(channel, pending_events, now)
        batch.subject = subject

        success, error = self._send_admin_digest_mail(recipients, subject, template_vars)
        if success:
            batch.status = "sent"
            batch.sent_at = now
            for event in pending_events:
                event.batch = batch
                event.status = "sent"
                event.last_attempted_at = now
                event.processed_at = now
                event.delivery_error = None
            self._record_success(state, channel, now)
            db.session.commit()
            return {"sent_batches": 1, "sent_events": len(pending_events)}

        batch.error_text = sanitize_notification_error(error)
        for event in pending_events:
            event.last_attempted_at = now
            event.delivery_error = error
        self._record_failure(state, channel, now, error)
        db.session.commit()
        return {"failed_batches": 1, "failed_events": len(pending_events)}

    def _deliver_user_status_events(self, now):
        if not self.is_enabled(USER_STATUS_CHANNEL):
            return {}

        pending_events = self._get_pending_events(USER_STATUS_CHANNEL)
        if not pending_events:
            return {}

        sent_batches = 0
        sent_events = 0
        failed_batches = 0
        failed_events = 0

        for event in pending_events:
            state, next_gate = self._refresh_channel_state(USER_STATUS_CHANNEL, now)
            if next_gate > now:
                return {
                    "sent_batches": sent_batches,
                    "sent_events": sent_events,
                    "failed_batches": failed_batches,
                    "failed_events": failed_events,
                    "deferred": True,
                }

            batch = NotificationBatch(
                channel=USER_STATUS_CHANNEL,
                status="failed",
                recipient_scope=event.recipient_email or "single_user",
                recipient_count=1,
                event_count=1,
            )
            db.session.add(batch)
            subject, template_vars = self._build_user_status_message(event)
            batch.subject = subject

            success, error = self._send_user_status_mail(event, subject, template_vars)
            if success:
                batch.status = "sent"
                batch.sent_at = now
                event.batch = batch
                event.status = "sent"
                event.last_attempted_at = now
                event.processed_at = now
                event.delivery_error = None
                self._record_success(state, USER_STATUS_CHANNEL, now)
                db.session.commit()
                sent_batches += 1
                sent_events += 1
                continue

            batch.error_text = sanitize_notification_error(error)
            event.last_attempted_at = now
            event.delivery_error = error
            self._record_failure(state, USER_STATUS_CHANNEL, now, error)
            db.session.commit()
            failed_batches += 1
            failed_events += 1
            break

        return {
            "sent_batches": sent_batches,
            "sent_events": sent_events,
            "failed_batches": failed_batches,
            "failed_events": failed_events,
        }

    def _build_admin_digest_message(self, channel, events, now):
        newest_events = sorted(events, key=lambda event: event.queued_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        visible_events = newest_events[:ADMIN_EVENT_LIST_LIMIT]
        event_counts = Counter(event.event_type for event in events)
        count_summary = [
            {
                "event_type": event_type.replace("_", " ").title(),
                "count": count,
            }
            for event_type, count in sorted(event_counts.items())
        ]
        if channel == ADMIN_ERROR_CHANNEL:
            subject = _(
                "Joanneum Aeronautics: %(count)s admin error notification(s)",
                count=len(events),
            )
            heading = _("Admin error notifications")
            intro = _("High-signal application issues were recorded and may require attention.")
            action_url = build_public_url("admin_logs")
            action_label = _("Open Audit Logs")
        else:
            subject = _(
                "Joanneum Aeronautics: %(count)s admin item(s) need attention",
                count=len(events),
            )
            heading = _("Admin notifications")
            intro = _("New review tasks or other admin-relevant events were recorded.")
            action_url = build_public_url("admin_dashboard")
            action_label = _("Open Admin Workspace")

        template_vars = {
            "heading": heading,
            "intro": intro,
            "event_counts": count_summary,
            "events": [
                {
                    "summary": event.summary,
                    "queued_at": event.queued_at,
                    "severity": event.severity,
                }
                for event in visible_events
            ],
            "omitted_count": max(0, len(events) - len(visible_events)),
            "action_url": action_url,
            "action_label": action_label,
            "now": now,
            "channel": channel,
        }
        return subject, template_vars

    def _build_user_status_message(self, event):
        payload = event.payload or {}
        recipient_name = payload.get("first_name") or _("member")
        if event.event_type == "forum_avatar_rejected":
            return (
                _("Your forum profile picture needs attention"),
                {
                    "preview_text": _("Your forum profile picture was reviewed and needs to be replaced."),
                    "action_url": build_public_url("forum_entry"),
                    "action_label": _("Upload a New Picture"),
                    "heading": _("Your profile picture needs to be replaced"),
                    "body_lines": [
                        _("Hello %(name)s, your forum profile picture was rejected during review.", name=recipient_name),
                        payload.get("review_note") or _("Please upload a new picture to continue with forum onboarding."),
                    ],
                },
            )
        if event.event_type == "identity_request_approved":
            return (
                _("Your identity change request was approved"),
                {
                    "preview_text": _("Your identity change request has been approved."),
                    "action_url": build_public_url("account"),
                    "action_label": _("Open My Account"),
                    "heading": _("Identity change approved"),
                    "body_lines": [
                        _("Hello %(name)s, your identity change request was approved.", name=recipient_name),
                        payload.get("admin_note") or _("Your member profile now reflects the approved identity details."),
                    ],
                },
            )
        if event.event_type == "identity_request_rejected":
            return (
                _("Your identity change request was reviewed"),
                {
                    "preview_text": _("Your identity change request was rejected."),
                    "action_url": build_public_url("account"),
                    "action_label": _("Open My Account"),
                    "heading": _("Identity change rejected"),
                    "body_lines": [
                        _("Hello %(name)s, your identity change request was rejected.", name=recipient_name),
                        payload.get("admin_note") or _("Please review the note and submit a new request if needed."),
                    ],
                },
            )
        return (
            _("An update is available for your account"),
            {
                "preview_text": event.summary,
                "action_url": build_public_url("account"),
                "action_label": _("Open My Account"),
                "heading": _("Account update"),
                "body_lines": [event.summary],
            },
        )

    def _send_admin_digest_mail(self, recipients, subject, template_vars):
        sender_account = self.get_sender_account()
        if not sender_account:
            return False, "No notification sender account is configured."

        sender_record = db.session.execute(
            db.select(MailAccount).filter_by(account_key=sender_account)
        ).scalar_one_or_none()
        if sender_record is None:
            return False, f"Notification sender account '{sender_account}' does not exist."

        logo_path = os.path.join(self.app.root_path, "static", "Logo_Aeronautics_signature-logo.png")
        attachments = [{"path": logo_path, "cid": "logo"}] if os.path.exists(logo_path) else None
        primary_recipient = recipients[0]
        blind_copies = recipients[1:] or None
        return send_mail(
            from_account=sender_account,
            to_email=primary_recipient,
            bcc_emails=blind_copies,
            subject=subject,
            template_name="admin_notification_digest.html",
            attachments=attachments,
            return_error=True,
            **template_vars,
        )

    def _send_user_status_mail(self, event, subject, template_vars):
        sender_account = self.get_sender_account()
        if not sender_account:
            return False, "No notification sender account is configured."

        logo_path = os.path.join(self.app.root_path, "static", "Logo_Aeronautics_signature-logo.png")
        attachments = [{"path": logo_path, "cid": "logo"}] if os.path.exists(logo_path) else None
        return send_mail(
            from_account=sender_account,
            to_email=event.recipient_email,
            subject=subject,
            template_name="member_account_action.html",
            attachments=attachments,
            return_error=True,
            **template_vars,
        )

    def _get_pending_events(self, channel):
        return db.session.execute(
            db.select(NotificationEvent)
            .where(NotificationEvent.channel == channel, NotificationEvent.status == "pending")
            .order_by(NotificationEvent.queued_at.asc(), NotificationEvent.id.asc())
        ).scalars().all()

    def _get_or_create_channel_state(self, channel):
        state = db.session.get(NotificationChannelState, channel)
        if state is None:
            state = NotificationChannelState(channel=channel)
            db.session.add(state)
            db.session.flush()
        return state

    def _refresh_channel_state(self, channel, now):
        state = self._get_or_create_channel_state(channel)
        state.last_activity_at = ensure_utc_datetime(state.last_activity_at)
        state.last_sent_at = ensure_utc_datetime(state.last_sent_at)
        state.last_failure_at = ensure_utc_datetime(state.last_failure_at)
        state.next_allowed_at = ensure_utc_datetime(state.next_allowed_at)
        state.failure_backoff_until = ensure_utc_datetime(state.failure_backoff_until)

        quiet_reference = state.last_activity_at or state.last_sent_at or state.last_failure_at
        if quiet_reference is None or quiet_reference <= now - QUIET_RESET_WINDOW:
            state.cooldown_stage = 0
            state.rolling_sent_count = 0
            if not state.failure_backoff_until or state.failure_backoff_until <= now:
                state.failure_stage = 0
                state.failure_backoff_until = None
                state.last_failure_message = None

        sent_count, oldest_sent_at = self._get_rolling_sent_window(channel, now)
        state.rolling_sent_count = sent_count

        gates = [now]
        if state.next_allowed_at and state.next_allowed_at > now:
            gates.append(state.next_allowed_at)
        if state.failure_backoff_until and state.failure_backoff_until > now:
            gates.append(state.failure_backoff_until)

        daily_cap = CHANNEL_DAILY_CAPS.get(channel)
        if daily_cap and sent_count >= daily_cap and oldest_sent_at is not None:
            gates.append(oldest_sent_at + QUIET_RESET_WINDOW)

        next_gate = max(gates)
        if next_gate > now:
            state.next_allowed_at = next_gate
        elif channel == USER_STATUS_CHANNEL:
            state.next_allowed_at = now
        return state, next_gate

    def _get_rolling_sent_window(self, channel, now):
        window_start = now - QUIET_RESET_WINDOW
        sent_batches = [
            ensure_utc_datetime(sent_at)
            for sent_at in db.session.execute(
                db.select(NotificationBatch.sent_at)
                .where(
                    NotificationBatch.channel == channel,
                    NotificationBatch.status == "sent",
                    NotificationBatch.sent_at.is_not(None),
                    NotificationBatch.sent_at >= window_start,
                )
                .order_by(NotificationBatch.sent_at.asc())
            ).scalars().all()
        ]
        return len(sent_batches), (sent_batches[0] if sent_batches else None)

    def _record_success(self, state, channel, now):
        state.last_sent_at = now
        state.last_activity_at = now
        state.failure_stage = 0
        state.failure_backoff_until = None
        state.last_failure_at = None
        state.last_failure_message = None
        ladder = CHANNEL_COOLDOWN_LADDERS.get(channel, [0])
        if len(ladder) == 1:
            state.cooldown_stage = 0
            state.next_allowed_at = now
        else:
            state.cooldown_stage = min((state.cooldown_stage or 0) + 1, len(ladder) - 1)
            state.next_allowed_at = now + timedelta(minutes=ladder[state.cooldown_stage])
        sent_count, _ = self._get_rolling_sent_window(channel, now)
        state.rolling_sent_count = sent_count

    def _record_failure(self, state, channel, now, message):
        state.last_activity_at = now
        state.last_failure_at = now
        state.last_failure_message = sanitize_notification_error(message)
        next_stage = min((state.failure_stage or 0), len(FAILURE_BACKOFF_LADDER) - 1)
        wait_minutes = FAILURE_BACKOFF_LADDER[next_stage]
        state.failure_stage = min(next_stage + 1, len(FAILURE_BACKOFF_LADDER) - 1)
        state.failure_backoff_until = now + timedelta(minutes=wait_minutes)
        if state.next_allowed_at is None or state.next_allowed_at < state.failure_backoff_until:
            state.next_allowed_at = state.failure_backoff_until

    def get_admin_recipient_emails(self):
        return db.session.execute(
            db.select(User.email)
            .where(User.email_verified_at.is_not(None), User.roles.any(Role.slug == "admin"))
            .order_by(User.email.asc())
        ).scalars().all()




