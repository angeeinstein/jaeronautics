from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import re

from flask import current_app
from flask_babel import _
from sqlalchemy import func

from .permissions import Permission, roles_with

try:
    from .db_models import (
        ForumAvatarSubmission,
        MailAccount,
        MemberProfileChangeRequest,
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
        ForumAvatarSubmission,
        MailAccount,
        MemberProfileChangeRequest,
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
# Members' own emails go out at once. Admin emails are throttled per kind of
# event rather than per channel: one error that keeps coming back must not hold
# up a different one, and a hundred members hitting the same problem are one
# kind, not a hundred -- the kind is the event_type, never the message text,
# which names the member.
CHANNEL_COOLDOWN_LADDERS = {
    USER_STATUS_CHANNEL: [0],
}
CHANNEL_DAILY_CAPS = {}
# The first two of a kind go out at once: the second is what says it is
# recurring. After that they are collected and summarised, further apart each
# time, and a kind that has been quiet for a day starts afresh.
IMMEDIATE_EMAILS_PER_KIND = 2
KIND_SUMMARY_MINUTES = {
    ADMIN_ERROR_CHANNEL: [60, 240, 1440],
    # Review items are members waiting on a decision; they are also read on
    # the reviews page, so every half hour is enough.
    ADMIN_GENERAL_CHANNEL: [30],
}
# However many kinds are firing at once -- the forum down, say -- no more than
# this many admin emails an hour. What does not fit goes into the next one.
ADMIN_EMAILS_PER_HOUR = 10
ADMIN_CHANNELS = (ADMIN_GENERAL_CHANNEL, ADMIN_ERROR_CHANNEL)
# Review items wait this long before the email, so ten photos uploaded in the
# same minute arrive as one email rather than ten. Errors do not wait. The
# notification timer, every two minutes, sends what the wait held back.
KIND_COLLECT_WINDOW = {
    ADMIN_GENERAL_CHANNEL: timedelta(minutes=1),
}
# A photo or change request left undecided this long gets a daily reminder.
REVIEW_REMINDER_AFTER = timedelta(hours=24)
REVIEW_REMINDER_EVENT = "reviews_still_waiting"
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
        if channel in ADMIN_CHANNELS:
            self._note_kind_activity(channel, event_type, now)
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
        if ADMIN_GENERAL_CHANNEL in selected_channels:
            self._queue_review_reminder(now)
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
            throttled = self._throttled_kinds(channel, now) if channel in ADMIN_CHANNELS else {}
            if throttled:
                next_gate = max(next_gate, min(throttled.values()))
            health[channel] = {
                "enabled": self.is_enabled(channel),
                "pending_count": pending_counts.get(channel, 0),
                "cooldown_stage": stage,
                "cooldown_minutes": ladder[stage],
                "throttled_kinds": sorted(kind.replace("_", " ").capitalize() for kind in throttled),
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

        # The channel's own gate is only a mail server that just failed.
        state, next_gate = self._refresh_channel_state(channel, now)
        if next_gate > now or self._hourly_admin_gate(now) > now:
            return {"deferred": True}

        collect = KIND_COLLECT_WINDOW.get(channel)
        oldest = {}
        for event in pending_events:
            queued = ensure_utc_datetime(event.queued_at) or now
            oldest[event.event_type] = min(oldest.get(event.event_type, queued), queued)
        due_kinds = {
            kind for kind, first_queued in oldest.items()
            if self._kind_is_due(channel, kind, now)
            and (collect is None or first_queued <= now - collect)
        }
        if not due_kinds:
            return {"deferred": True}
        pending_events = [event for event in pending_events if event.event_type in due_kinds]

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
            for kind in due_kinds:
                self._record_kind_sent(channel, kind, now)
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
            heading = _("Something needs an admin's attention")
            intro = _("The portal ran into problems it could not solve on its own.")
            action_url = build_public_url("admin.admin_logs")
            action_label = _("Open Audit Logs")
        else:
            subject = _(
                "Joanneum Aeronautics: %(count)s admin item(s) need attention",
                count=len(events),
            )
            heading = _("New items to review")
            intro = _("Members are waiting on the committee, for example for a profile picture or a change request.")
            action_url = build_public_url("admin.admin_dashboard")
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
                    # Some events say what to do about them; that is the part
                    # an admin reading this on the phone needs most.
                    "what_to_do": (event.payload or {}).get("what_to_do"),
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
        first_name = payload.get("first_name")
        greeting = _("Hello %(name)s,", name=first_name) if first_name else _("Hello,")
        note = (payload.get("review_note") or payload.get("admin_note") or "").strip()
        account_url = build_public_url("account.account")

        if event.event_type == "forum_avatar_approved":
            return (
                _("Your forum access is complete"),
                {
                    "preview_text": _("Your profile picture was approved."),
                    "action_url": build_public_url("forum.forum_entry"),
                    "action_label": _("Open Forum"),
                    "heading": _("Your profile picture was approved"),
                    "body_lines": [
                        greeting,
                        _("Your profile picture was approved, so your forum access is now "
                          "complete: you can read and write in the members' area."),
                    ],
                },
            )
        if event.event_type == "forum_avatar_replaced":
            return (
                _("Your new profile picture is live"),
                {
                    "preview_text": _("Your new profile picture was approved."),
                    "action_url": build_public_url("forum.forum_entry"),
                    "action_label": _("Open Forum"),
                    "heading": _("Your new profile picture was approved"),
                    "body_lines": [
                        greeting,
                        _("It now shows on the forum."),
                    ],
                },
            )
        if event.event_type == "forum_avatar_rejected":
            return (
                _("Please upload a new profile picture"),
                {
                    "preview_text": _("Your profile picture could not be approved."),
                    "action_url": account_url,
                    "action_label": _("Upload a New Picture"),
                    "heading": _("Your profile picture could not be approved"),
                    "body_lines": [
                        greeting,
                        _("We could not approve the profile picture you uploaded for the forum."),
                        _("Reason: %(note)s", note=note) if note else None,
                        _("Please upload a new one on your account page. Once it is approved, "
                          "your forum access is complete."),
                    ],
                },
            )
        if event.event_type == "identity_request_approved":
            return (
                _("Your profile change was approved"),
                {
                    "preview_text": _("Your requested profile change has been approved."),
                    "action_url": account_url,
                    "action_label": _("Open My Account"),
                    "heading": _("Your profile change was approved"),
                    "body_lines": [
                        greeting,
                        _("The change you requested to your name, membership type or year group was "
                          "approved, and your profile now shows it."),
                        _("Note from the committee: %(note)s", note=note) if note else None,
                    ],
                },
            )
        if event.event_type == "identity_request_rejected":
            return (
                _("Your profile change was not approved"),
                {
                    "preview_text": _("Your requested profile change was not approved."),
                    "action_url": account_url,
                    "action_label": _("Open My Account"),
                    "heading": _("Your profile change was not approved"),
                    "body_lines": [
                        greeting,
                        _("The change you requested to your name, membership type or year group was "
                          "not approved, so your profile stays as it was."),
                        _("Note from the committee: %(note)s", note=note) if note
                        else _("If you have questions about it, please get in touch with us."),
                    ],
                },
            )
        team_message = self._build_team_message(event, payload, greeting)
        if team_message is not None:
            return team_message
        return (
            _("An update on your Joanneum Aeronautics account"),
            {
                "preview_text": event.summary,
                "action_url": account_url,
                "action_label": _("Open My Account"),
                "heading": _("An update on your account"),
                "body_lines": [greeting, event.summary],
            },
        )

    def _build_team_message(self, event, payload, greeting):
        """Emails about teams: to the person, or to the team's leads."""
        team = payload.get("team_name") or _("your team")
        slug = payload.get("team_slug")
        if not event.event_type.startswith("team_") or not slug:
            return None
        teams_url = build_public_url("teams.teams_home")
        manage_url = build_public_url("teams.team_manage", slug=slug)

        to_person = {
            "team_invited": (
                _("Invitation from %(team)s", team=team),
                _("The leads of %(team)s would like to meet you.", team=team),
                [_("Thanks for applying to %(team)s. The leads would like to meet you:", team=team),
                 payload.get("meeting_details")],
                teams_url, _("Open Teams"),
            ),
            "team_approved": (
                _("Welcome to %(team)s", team=team),
                _("You are now a member of %(team)s.", team=team),
                [_("You are now a member of %(team)s.", team=team)],
                build_public_url("teams.team_page", slug=slug), _("Open Team Page"),
            ),
            "team_rejected": (
                _("Your application to %(team)s", team=team),
                _("Your application was not accepted."),
                [_("Your application to %(team)s was not accepted this time.", team=team)],
                teams_url, _("Open Teams"),
            ),
            "team_removed": (
                _("Your membership in %(team)s has ended", team=team),
                _("Your membership in %(team)s has ended.", team=team),
                [_("Your membership in %(team)s has ended. If you think this is a mistake, "
                   "please contact the team's leads.", team=team)],
                teams_url, _("Open Teams"),
            ),
        }
        if event.event_type in to_person:
            subject, preview, lines, url, label = to_person[event.event_type]
        elif event.event_type in {"team_application_received", "team_member_joined",
                                  "team_member_left", "team_members_lapsed"}:
            subject = {
                "team_application_received": _("New application for %(team)s", team=team),
                "team_member_joined": _("New member in %(team)s", team=team),
                "team_member_left": _("A member left %(team)s", team=team),
                "team_members_lapsed": _("Members left %(team)s", team=team),
            }[event.event_type]
            preview, lines, url, label = event.summary, [event.summary], manage_url, _("Open Team Management")
        else:
            return None
        return (
            subject,
            {
                "preview_text": preview,
                "action_url": url,
                "action_label": label,
                "heading": subject,
                "body_lines": [greeting, *lines],
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

        primary_recipient = recipients[0]
        blind_copies = recipients[1:] or None
        return send_mail(
            from_account=sender_account,
            to_email=primary_recipient,
            bcc_emails=blind_copies,
            subject=subject,
            template_name="admin_notification_digest.html",
            return_error=True,
            **template_vars,
        )

    def _send_user_status_mail(self, event, subject, template_vars):
        sender_account = self.get_sender_account()
        if not sender_account:
            return False, "No notification sender account is configured."

        return send_mail(
            from_account=sender_account,
            to_email=event.recipient_email,
            subject=subject,
            template_name="member_account_action.html",
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

    # -- Throttling per kind of admin event --------------------------------

    @staticmethod
    def _kind_key(channel, kind):
        return f"{channel}:{kind or 'other'}"[:80]

    def _note_kind_activity(self, channel, kind, now):
        """An event of this kind happened. A kind quiet for a day starts afresh."""
        state = self._get_or_create_channel_state(self._kind_key(channel, kind))
        last = ensure_utc_datetime(state.last_activity_at)
        if last is None or last <= now - QUIET_RESET_WINDOW:
            state.cooldown_stage = 0
            state.next_allowed_at = None
        state.last_activity_at = now

    def _kind_is_due(self, channel, kind, now):
        state = db.session.get(NotificationChannelState, self._kind_key(channel, kind))
        if state is None or (state.cooldown_stage or 0) < IMMEDIATE_EMAILS_PER_KIND:
            return True
        gate = ensure_utc_datetime(state.next_allowed_at)
        return gate is None or gate <= now

    def _record_kind_sent(self, channel, kind, now):
        state = self._get_or_create_channel_state(self._kind_key(channel, kind))
        state.cooldown_stage = (state.cooldown_stage or 0) + 1
        state.last_sent_at = now
        if state.cooldown_stage < IMMEDIATE_EMAILS_PER_KIND:
            state.next_allowed_at = None
        else:
            ladder = KIND_SUMMARY_MINUTES[channel]
            step = min(state.cooldown_stage - IMMEDIATE_EMAILS_PER_KIND, len(ladder) - 1)
            state.next_allowed_at = now + timedelta(minutes=ladder[step])

    def _throttled_kinds(self, channel, now):
        """Kinds with something waiting for their next summary, and when it goes."""
        waiting = {
            kind for kind in db.session.execute(
                db.select(NotificationEvent.event_type).where(
                    NotificationEvent.channel == channel, NotificationEvent.status == "pending"
                )
            ).scalars()
        }
        throttled = {}
        for kind in waiting:
            if not self._kind_is_due(channel, kind, now):
                state = db.session.get(NotificationChannelState, self._kind_key(channel, kind))
                throttled[kind] = ensure_utc_datetime(state.next_allowed_at)
        return throttled

    def _hourly_admin_gate(self, now):
        """When the next admin email may go, under the overall hourly limit."""
        window_start = now - timedelta(hours=1)
        sent = [
            ensure_utc_datetime(sent_at)
            for sent_at in db.session.execute(
                db.select(NotificationBatch.sent_at)
                .where(
                    NotificationBatch.channel.in_(ADMIN_CHANNELS),
                    NotificationBatch.status == "sent",
                    NotificationBatch.sent_at.is_not(None),
                    NotificationBatch.sent_at >= window_start,
                )
                .order_by(NotificationBatch.sent_at.asc())
            ).scalars().all()
        ]
        if len(sent) < ADMIN_EMAILS_PER_HOUR:
            return now
        return sent[-ADMIN_EMAILS_PER_HOUR] + timedelta(hours=1)

    def _queue_review_reminder(self, now):
        """Once a day, while a photo or change request has waited over a day."""
        cutoff = now - REVIEW_REMINDER_AFTER
        waiting = (
            db.session.scalar(
                db.select(func.count(ForumAvatarSubmission.id)).where(
                    ForumAvatarSubmission.status == "pending",
                    ForumAvatarSubmission.uploaded_at <= cutoff,
                )
            ) or 0
        ) + (
            db.session.scalar(
                db.select(func.count(MemberProfileChangeRequest.id)).where(
                    MemberProfileChangeRequest.status == "pending",
                    MemberProfileChangeRequest.created_at <= cutoff,
                )
            ) or 0
        )
        if not waiting:
            return
        last = db.session.scalar(
            db.select(func.max(NotificationEvent.queued_at)).where(
                NotificationEvent.event_type == REVIEW_REMINDER_EVENT
            )
        )
        last = ensure_utc_datetime(last)
        if last is not None and last > now - REVIEW_REMINDER_AFTER:
            return
        self.queue_admin_general(
            REVIEW_REMINDER_EVENT,
            _("%(count)s item(s) waiting for more than a day.", count=waiting),
        )
        db.session.commit()

    def get_admin_recipient_emails(self):
        """Who the admin digests go to.

        Asked as a capability, not as ``Role.slug == "admin"``. That literal was
        wrong the moment an account could hold super admin without the admin row
        beside it: it silently stopped being told about errors and review tasks,
        which is a failure that announces itself by nothing happening.

        Erased accounts are excluded; their address is a placeholder on a domain
        that cannot resolve, so every digest to one would bounce.
        """
        slugs = roles_with(Permission.NOTIFICATIONS_RECEIVE)
        if not slugs:
            return []
        return db.session.execute(
            db.select(User.email)
            .where(
                User.email_verified_at.is_not(None),
                User.deleted_at.is_(None),
                User.roles.any(Role.slug.in_(slugs)),
            )
            .order_by(User.email.asc())
        ).scalars().all()




