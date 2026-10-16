"""Admin › Settings › Email (docs/frontend-structure.md, "Settings"): everything
about sending email on one page, in tabs --

- **Senders**: which mail account sends what (services/mail_senders.py);
- **What goes out**: the welcome email, the notifications and how each
  channel stands, the mailings' pace;
- **Accounts** (api/admin_mail.py) and **Test** (the test email), as before.

Drawn by frontend/src/pages/admin/settings/Email.tsx.
"""

from typing import Literal

from flask import current_app
from flask_login import current_user
from pydantic import Field

from ..db_models import db
from ..notification_service import NotificationService, normalize_notification_settings
from ..permissions import Permission
from ..services import mail_senders
from ..services import mailings as mailings_service
from ..services import settings as settings_service
from ..services import settings_sections as sections
from ..services.notifications import get_notification_settings_map
from ._core import Model, UtcDateTime, endpoint

TAG = "Admin"
GENERAL = [Permission.SETTINGS_GENERAL]


class SettingsChangedOut(Model):
    changed: list[str]


# --- Senders ------------------------------------------------------------------------------


class SendersIn(Model):
    #: Everything not chosen otherwise, and the account emails; empty: the first account.
    default: str = Field("", max_length=100)
    #: Each empty: the default.
    welcome: str = Field("", max_length=100)
    notifications: str = Field("", max_length=100)
    mailings: str = Field("", max_length=100)
    replies: str = Field("", max_length=100)
    #: Each reply also goes, as a blind copy, to its account's own address.
    reply_copy: bool = False


class SendersOut(Model):
    default: str
    welcome: str
    notifications: str
    mailings: str
    replies: str
    reply_copy: bool
    accounts: list[str]
    #: What the default is now: the one chosen, or the first account.
    default_in_use: str
    #: The address replies go out from, and the copy goes to.
    reply_address: str


def _senders_out():
    chosen = mail_senders.stored()
    return SendersOut(
        default=chosen["default"], welcome=chosen["welcome"], notifications=chosen["notifications"],
        mailings=chosen["mailings"], replies=chosen["replies"], reply_copy=chosen["reply_copy"],
        accounts=mail_senders.accounts(), default_in_use=mail_senders.default_account(),
        reply_address=mail_senders.address_of(mail_senders.account_for("replies")),
    )


@endpoint("GET", "/admin/settings/email/senders", response=SendersOut, permissions=GENERAL, tag=TAG)
def admin_email_senders():
    """Which mail account sends what."""
    return _senders_out()


@endpoint("PUT", "/admin/settings/email/senders", response=SettingsChangedOut, body=SendersIn, permissions=GENERAL,
          tag=TAG)
def admin_email_senders_save(body):
    """Choose the default account, and an account of its own for each kind of email."""
    changed = mail_senders.save(current_user, default=body.default, welcome=body.welcome,
                                notifications=body.notifications, mailings=body.mailings, replies=body.replies,
                                reply_copy=body.reply_copy)
    db.session.commit()
    return SettingsChangedOut(changed=changed)


# --- What goes out ------------------------------------------------------------------------------


ChannelKey = Literal["admin_general", "admin_error", "user_status"]
CHANNEL_LABELS = {"admin_general": "Admin review queue", "admin_error": "Admin error queue",
                  "user_status": "Member status emails"}


class ChannelHealth(Model):
    channel: ChannelKey
    label: str
    enabled: bool
    pending: int
    #: Kinds held back by their cooldown; empty when everything goes out at once.
    held_back: list[str]
    #: ``None``: now.
    next_send_at: UtcDateTime | None
    #: After failures, nothing is tried before this.
    backoff_until: UtcDateTime | None
    last_failure: str | None


class SendingIn(Model):
    #: The welcome email sent by itself once somebody is a member.
    automatic_emails: bool
    #: The legal texts the member accepted, as PDFs, in the welcome email.
    legal_pdfs_in_welcome_emails: bool
    automatic_email_template: str | None = Field(None, max_length=200)
    #: The digest of what waits for review, to the admins.
    admin_general: bool
    #: Application errors, to the admins.
    admin_error: bool
    #: Emails to members about their membership.
    user_status: bool
    #: Announcements and team mailings: at most this many an hour, and a day.
    per_hour: int = Field(ge=1)
    per_day: int = Field(ge=1)


class SendingOut(SendingIn):
    templates: list[str]
    health: list[ChannelHealth]


def _is_true(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@endpoint("GET", "/admin/settings/email/sending", response=SendingOut, permissions=GENERAL, tag=TAG)
def admin_email_sending():
    """The welcome email, which notifications go out and how each channel stands, the mailings' pace."""
    stored = settings_service.get_settings_map([
        "automatic_emails_enabled", "legal_pdfs_in_welcome_emails", "automatic_email_template"])
    notifications = normalize_notification_settings(get_notification_settings_map())
    limits = mailings_service.settings()
    health = NotificationService(current_app._get_current_object()).get_health_snapshot()
    return SendingOut(
        automatic_emails=_is_true(stored.get("automatic_emails_enabled")),
        legal_pdfs_in_welcome_emails=_is_true(stored.get("legal_pdfs_in_welcome_emails")),
        automatic_email_template=stored.get("automatic_email_template") or None,
        admin_general=notifications["notification_admin_general_enabled"],
        admin_error=notifications["notification_admin_error_enabled"],
        user_status=notifications["notification_user_status_enabled"],
        per_hour=limits["per_hour"], per_day=limits["per_day"],
        templates=sections.email_templates(),
        health=[
            ChannelHealth(channel=channel, label=label, enabled=bool(state.get("enabled")),
                          pending=state.get("pending_count") or 0,
                          held_back=list(state.get("throttled_kinds") or []),
                          next_send_at=state.get("next_allowed_at"),
                          backoff_until=state.get("failure_backoff_until"),
                          last_failure=(state.get("last_failure_message") or "")[:300] or None)
            for channel, label in CHANNEL_LABELS.items()
            for state in [health.get(channel, {})]
        ],
    )


@endpoint("PUT", "/admin/settings/email/sending", response=SettingsChangedOut, body=SendingIn, permissions=GENERAL,
          tag=TAG)
def admin_email_sending_save(body):
    """Save what goes out: the welcome email, the notifications, the mailings' pace."""
    changed = sections.save_sending(
        current_user, automatic_emails=body.automatic_emails,
        legal_pdfs_in_welcome_emails=body.legal_pdfs_in_welcome_emails,
        automatic_email_template=body.automatic_email_template, admin_general=body.admin_general,
        admin_error=body.admin_error, user_status=body.user_status, per_hour=body.per_hour, per_day=body.per_day)
    db.session.commit()
    return SettingsChangedOut(changed=changed)
