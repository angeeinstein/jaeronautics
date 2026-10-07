"""Settings, section by section: each read and saved on its own, and a save
changes only its own section. General and Notifications for whoever may
change the general settings; the Membership fee and the Forum, which hold
third-party credentials, only for whoever may see those -- and the secrets
themselves are never sent back, only whether one is set.

The rules are in services/settings_sections.py. Drawn by
frontend/src/pages/admin/settings/.
"""

from typing import Literal

from flask import current_app
from flask_login import current_user
from pydantic import Field

from ..db_models import db
from ..forum_service import ForumProviderError, member_category_groups, normalize_forum_settings
from ..member_categories import CATEGORY_ORDER, category_label
from ..notification_service import NotificationService, normalize_notification_settings
from ..permissions import Permission
from ..security_utils import build_public_url
from ..services import settings as settings_service
from ..services import settings_sections as sections
from ..services.audit import log_audit_event
from ..services.forum import get_forum_service, get_forum_settings_map
from ..services.institutional_email import get_institutional_domains
from ..services.notifications import get_notification_settings_map
from .admin_mail import ConnectionOut
from ._core import Model, UtcDateTime, endpoint

TAG = "Admin"
GENERAL = [Permission.SETTINGS_GENERAL]
CREDENTIALS = [Permission.SETTINGS_CREDENTIALS]


class SettingsSavedOut(Model):
    #: The settings that changed; empty when nothing did.
    changed: list[str]


def _is_true(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


# --- General ----------------------------------------------------------------------------


class GeneralIn(Model):
    invoice_payments: bool
    automatic_emails: bool
    #: The legal texts the member accepted, as PDFs, in the welcome email.
    legal_pdfs_in_welcome_emails: bool
    #: The mail account welcome emails come from; empty for none.
    welcome_email_sender: str | None = Field(None, max_length=100)
    automatic_email_template: str | None = Field(None, max_length=200)
    #: The domains a student's address must be on, as typed: commas, spaces or lines.
    institutional_email_domains: str | None = Field(None, max_length=2000)


class GeneralOut(GeneralIn):
    #: The domains in force: the built-in list while the setting is empty.
    domains_in_use: list[str]
    senders: list[str]
    templates: list[str]


@endpoint("GET", "/admin/settings/general", response=GeneralOut, permissions=GENERAL, tag=TAG)
def admin_settings_general():
    """Invoices, automatic emails, the welcome email and the students' email domains."""
    stored = settings_service.get_settings_map([
        "invoice_payments_enabled", "automatic_emails_enabled", "legal_pdfs_in_welcome_emails",
        "welcome_email_sender", "automatic_email_template", "institutional_email_domains"])
    return GeneralOut(
        invoice_payments=_is_true(stored.get("invoice_payments_enabled")),
        automatic_emails=_is_true(stored.get("automatic_emails_enabled")),
        legal_pdfs_in_welcome_emails=_is_true(stored.get("legal_pdfs_in_welcome_emails")),
        welcome_email_sender=stored.get("welcome_email_sender") or None,
        automatic_email_template=stored.get("automatic_email_template") or None,
        institutional_email_domains=stored.get("institutional_email_domains") or None,
        domains_in_use=list(get_institutional_domains()),
        senders=sections.sender_accounts(),
        templates=sections.email_templates(),
    )


@endpoint("PUT", "/admin/settings/general", response=SettingsSavedOut, body=GeneralIn, permissions=GENERAL, tag=TAG)
def admin_settings_general_save(body):
    """Save the general settings: only these, whatever else there is."""
    changed = sections.save_general(
        current_user, invoice_payments=body.invoice_payments, automatic_emails=body.automatic_emails,
        legal_pdfs_in_welcome_emails=body.legal_pdfs_in_welcome_emails,
        welcome_email_sender=body.welcome_email_sender, automatic_email_template=body.automatic_email_template,
        institutional_email_domains=body.institutional_email_domains)
    db.session.commit()
    return SettingsSavedOut(changed=changed)


# --- Notifications ----------------------------------------------------------------------


class NotificationsIn(Model):
    #: The digest of what waits for review, to the admins.
    admin_general: bool
    #: Application errors, to the admins.
    admin_error: bool
    #: Emails to members about their membership.
    user_status: bool
    #: The mail account they come from; empty for the welcome email's.
    sender: str | None = Field(None, max_length=100)


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


class NotificationsOut(NotificationsIn):
    senders: list[str]
    health: list[ChannelHealth]


@endpoint("GET", "/admin/settings/notifications", response=NotificationsOut, permissions=GENERAL, tag=TAG)
def admin_settings_notifications():
    """Which notifications go out, from which account, and how each channel stands."""
    values = normalize_notification_settings(get_notification_settings_map())
    health = NotificationService(current_app._get_current_object()).get_health_snapshot()
    return NotificationsOut(
        admin_general=values["notification_admin_general_enabled"],
        admin_error=values["notification_admin_error_enabled"],
        user_status=values["notification_user_status_enabled"],
        sender=values["notification_sender"] or None,
        senders=sections.sender_accounts(),
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


@endpoint("PUT", "/admin/settings/notifications", response=SettingsSavedOut, body=NotificationsIn, permissions=GENERAL,
          tag=TAG)
def admin_settings_notifications_save(body):
    """Save which notifications go out, and from which account."""
    changed = sections.save_notifications(current_user, admin_general=body.admin_general,
                                                  admin_error=body.admin_error, user_status=body.user_status,
                                                  sender=body.sender)
    db.session.commit()
    return SettingsSavedOut(changed=changed)


# --- Membership fee (Stripe) -------------------------------------------------------------


class BillingIn(Model):
    publishable_key: str | None = Field(None, max_length=255)
    #: The membership's price (price_...). A new one is checked with Stripe first.
    price_id: str | None = Field(None, max_length=255)
    #: Empty keeps the one set.
    secret_key: str | None = Field(None, max_length=255)
    #: Empty keeps the one set.
    webhook_secret: str | None = Field(None, max_length=255)


class BillingOut(Model):
    publishable_key: str | None
    price_id: str | None
    secret_key_set: bool
    webhook_secret_set: bool


class BillingSavedOut(SettingsSavedOut):
    #: Running subscriptions moving to a new price from their next renewal.
    moving: int


@endpoint("GET", "/admin/settings/billing", response=BillingOut, permissions=CREDENTIALS, tag=TAG)
def admin_settings_billing():
    """The Stripe keys and the membership's price; of the secrets only whether one is set."""
    values = settings_service.get_stripe_settings_map()
    return BillingOut(publishable_key=values.get("stripe_publishable_key") or None,
                      price_id=values.get("stripe_price_id") or None,
                      secret_key_set=bool(values.get("stripe_secret_key")),
                      webhook_secret_set=bool(values.get("stripe_webhook_secret")))


@endpoint("PUT", "/admin/settings/billing", response=BillingSavedOut, body=BillingIn, permissions=CREDENTIALS,
          tag=TAG)
def admin_settings_billing_save(body):
    """Save the keys and the price. A new price moves every running membership to it from its
    next renewal, each member emailed two weeks before."""
    changed, moving = sections.save_billing(current_user, publishable_key=body.publishable_key,
                                                    price_id=body.price_id, secret_key=body.secret_key,
                                                    webhook_secret=body.webhook_secret)
    db.session.commit()
    return BillingSavedOut(changed=changed, moving=moving)


# --- Forum ------------------------------------------------------------------------------


class CategoryGroup(Model):
    kind: str
    label: str
    #: Empty: no such group for this kind of member.
    group: str


class ForumIn(Model):
    enabled: bool
    base_url: str | None = Field(None, max_length=255)
    api_username: str | None = Field(None, max_length=100)
    #: Empty keeps the one set.
    api_key: str | None = Field(None, max_length=255)
    #: Empty keeps the one set.
    connect_secret: str | None = Field(None, max_length=255)
    onboarding_group: str | None = Field(None, max_length=100)
    member_group: str | None = Field(None, max_length=100)
    inactive_group: str | None = Field(None, max_length=100)
    staff_group: str | None = Field(None, max_length=100)
    #: The portal decides who is admin or moderator on the forum.
    manage_staff_flags: bool
    #: ``{kind of member: group}``.
    category_groups: dict[str, str] = Field(default_factory=dict)
    lecture_groups: str | None = Field(None, max_length=500)
    archive_groups: str | None = Field(None, max_length=500)
    #: Where members land on the forum after signing in.
    onboarding_path: str | None = Field(None, max_length=255)
    avatar_max_bytes: int | None = None
    avatar_allowed_types: list[str] = Field(default_factory=list)


class ForumEndpoints(Model):
    """The portal's addresses the forum is set up with."""

    public_base_url: str | None
    entry: str
    connect: str
    logout: str


class ForumSettingsOut(Model):
    enabled: bool
    base_url: str
    api_username: str
    api_key_set: bool
    connect_secret_set: bool
    onboarding_group: str
    member_group: str
    inactive_group: str
    staff_group: str
    manage_staff_flags: bool
    category_groups: list[CategoryGroup]
    lecture_groups: str
    archive_groups: str
    onboarding_path: str
    avatar_max_bytes: int
    avatar_allowed_types: list[str]
    endpoints: ForumEndpoints
    #: What is still missing while the integration is switched on.
    missing: list[str]


@endpoint("GET", "/admin/settings/forum", response=ForumSettingsOut, permissions=CREDENTIALS, tag=TAG)
def admin_settings_forum():
    """The forum's address, credentials and groups; of the secrets only whether one is set."""
    values = normalize_forum_settings(get_forum_settings_map())
    groups = member_category_groups(values)
    service = get_forum_service()
    return ForumSettingsOut(
        enabled=values["forum_integration_enabled"], base_url=values["forum_base_url"],
        api_username=values["discourse_api_username"], api_key_set=bool(values["discourse_api_key"]),
        connect_secret_set=bool(values["discourse_connect_secret"]),
        onboarding_group=values["forum_onboarding_group"], member_group=values["forum_member_group"],
        inactive_group=values["forum_inactive_group"], staff_group=values["forum_staff_group"],
        manage_staff_flags=values["forum_manage_staff_flags"],
        category_groups=[CategoryGroup(kind=kind, label=str(category_label(kind)), group=groups.get(kind, ""))
                         for kind in CATEGORY_ORDER],
        lecture_groups=values["forum_lecture_groups"], archive_groups=values["forum_archive_groups"],
        onboarding_path=values["forum_onboarding_path"], avatar_max_bytes=values["forum_avatar_max_bytes"],
        avatar_allowed_types=values["forum_avatar_allowed_types"],
        endpoints=ForumEndpoints(public_base_url=current_app.config.get("PUBLIC_BASE_URL") or None,
                                 entry=build_public_url("forum.forum_entry"),
                                 connect=build_public_url("forum.forum_discourse_connect"),
                                 logout=build_public_url("forum.forum_logout")),
        missing=list(service.config_errors) if values["forum_integration_enabled"] else [],
    )


@endpoint("PUT", "/admin/settings/forum", response=SettingsSavedOut, body=ForumIn, permissions=CREDENTIALS, tag=TAG)
def admin_settings_forum_save(body):
    """Save the forum's settings. A secret left empty stays as it is."""
    changed = sections.save_forum(
        current_user, enabled=body.enabled, base_url=body.base_url, api_username=body.api_username,
        api_key=body.api_key, connect_secret=body.connect_secret, onboarding_group=body.onboarding_group,
        member_group=body.member_group, inactive_group=body.inactive_group, staff_group=body.staff_group,
        manage_staff_flags=body.manage_staff_flags, category_groups=body.category_groups,
        lecture_groups=body.lecture_groups, archive_groups=body.archive_groups,
        onboarding_path=body.onboarding_path, avatar_max_bytes=body.avatar_max_bytes,
        avatar_allowed_types=body.avatar_allowed_types)
    db.session.commit()
    return SettingsSavedOut(changed=changed)


@endpoint("POST", "/admin/settings/forum/test", response=ConnectionOut, permissions=CREDENTIALS, tag=TAG)
def admin_settings_forum_test():
    """Check that the forum answers with these settings. Grants nobody anything."""
    service = get_forum_service()
    try:
        ok, message = service.test_connection()
    except ForumProviderError as exc:
        ok, message = False, str(exc)
    log_audit_event("forum", "forum_connection_tested", actor_user=current_user, target_user=current_user,
                    after={"ready": service.is_ready(), "enabled": service.is_enabled()},
                    metadata={"success": ok, "message": message})
    db.session.commit()
    return ConnectionOut(ok=bool(ok), message=message)
