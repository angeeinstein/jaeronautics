"""Admin -> Settings, section by section: each saved on its own, changing only
its own settings; what is not sent is not touched. Every save that changes
something is one entry in the log, secrets shown only as configured. Above
services/settings.py, which only reads and writes the values, so that one can
stay at the bottom of the services everybody uses.
"""

from ..db_models import db
from .settings import get_settings_map, set_setting_value


def _snapshot(keys):
    stored = get_settings_map(keys)
    return {key: stored.get(key) for key in keys}


def _flag(value):
    return "True" if value else "False"


def _write(actor, section, values):
    """Store ``values`` and log what changed; the keys that did."""
    from .audit import log_audit_event, redact_settings_states_for_audit

    db.session.flush()
    before = _snapshot(list(values))
    for key, value in values.items():
        set_setting_value(key, value)
    db.session.flush()
    after = _snapshot(list(values))
    changed = sorted(key for key in values if before.get(key) != after.get(key))
    if changed:
        logged_before, logged_after = redact_settings_states_for_audit(before, after)
        log_audit_event("settings", "settings_updated", actor_user=actor, target_user=actor,
                        before=logged_before, after=logged_after,
                        metadata={"section": section, "changed_keys": changed})
    return changed


def sender_accounts():
    """The mail accounts emails can be sent from, by their key."""
    from flask import current_app

    from ..mail_utils import load_mail_accounts_config

    try:
        return sorted(load_mail_accounts_config().keys())
    except Exception as exc:  # noqa: BLE001 -- a broken mail setup must not hide the settings
        current_app.logger.error("Could not load the mail accounts for the settings: %s", exc)
        return []


def email_templates():
    from flask import current_app

    from .notifications import get_email_template_choices

    return [name for name, _label in get_email_template_choices(current_app._get_current_object())]


def _choice(value, allowed, field, message):
    from . import ValidationError

    value = (value or "").strip()
    if value and value not in allowed:
        raise ValidationError(message, code="settings_invalid", details={"fields": {field: message}})
    return value or None


def save_general(actor, *, invoice_payments, automatic_emails, legal_pdfs_in_welcome_emails, welcome_email_sender,
                 automatic_email_template, institutional_email_domains, staff_email_domains=None):
    sender = _choice(welcome_email_sender, sender_accounts(), "welcome_email_sender",
                     "That sender account does not exist.")
    template = _choice(automatic_email_template, email_templates(), "automatic_email_template",
                       "That email template does not exist.")
    return _write(actor, "general", {
        "invoice_payments_enabled": _flag(invoice_payments),
        "automatic_emails_enabled": _flag(automatic_emails),
        "legal_pdfs_in_welcome_emails": _flag(legal_pdfs_in_welcome_emails),
        "welcome_email_sender": sender,
        "automatic_email_template": template,
        # Stored as typed; institutional_email.py makes sense of commas, newlines and stray @ signs.
        "institutional_email_domains": (institutional_email_domains or "").strip() or None,
        "staff_email_domains": (staff_email_domains or "").strip() or None,
    })


def save_notifications(actor, *, admin_general, admin_error, user_status, sender):
    sender = _choice(sender, sender_accounts(), "sender", "That sender account does not exist.")
    return _write(actor, "notifications", {
        "notification_admin_general_enabled": _flag(admin_general),
        "notification_admin_error_enabled": _flag(admin_error),
        "notification_user_status_enabled": _flag(user_status),
        "notification_sender": sender,
    })


def save_billing(actor, *, publishable_key, price_id, secret_key=None, webhook_secret=None):
    """The Stripe keys and the membership price. A new price is checked with Stripe
    and every running subscription moves to it from its next renewal; how many
    is answered with the keys that changed. A secret left out stays as it is."""
    from . import ValidationError
    from .billing import change_membership_price

    price_id = (price_id or "").strip()
    try:
        moving = change_membership_price(actor, price_id) if price_id else 0
    except ValidationError as exc:
        exc.details = {**exc.details, "fields": {"price_id": exc.message}}  # said at the price
        raise
    values = {"stripe_publishable_key": (publishable_key or "").strip() or None, "stripe_price_id": price_id or None}
    if (secret_key or "").strip():
        values["stripe_secret_key"] = secret_key.strip()
    if (webhook_secret or "").strip():
        values["stripe_webhook_secret"] = webhook_secret.strip()
    return _write(actor, "billing", values), moving


def save_forum(actor, *, enabled, base_url, api_username, onboarding_group, member_group, inactive_group,
               staff_group, manage_staff_flags, category_groups, lecture_groups, archive_groups, onboarding_path,
               avatar_max_bytes, avatar_allowed_types, api_key=None, connect_secret=None):
    """The forum's address, credentials and groups. ``category_groups`` is
    ``{kind of member: group}``; a secret left out stays as it is."""
    from ..member_categories import CATEGORY_ORDER
    from . import ValidationError

    if avatar_max_bytes is not None and avatar_max_bytes <= 0:
        message = "The size limit must be a positive number of bytes."
        raise ValidationError(message, code="settings_invalid", details={"fields": {"avatar_max_bytes": message}})
    # Stored as the lines the rest of the portal reads (forum_service.member_category_groups).
    lines = "\n".join(f"{kind} = {(category_groups or {}).get(kind, '').strip()}" for kind in CATEGORY_ORDER
                      if (category_groups or {}).get(kind, "").strip())
    values = {
        "forum_integration_enabled": _flag(enabled),
        "forum_provider": "discourse",
        "forum_auth_strategy": "discourse_connect",
        "forum_base_url": (base_url or "").strip() or None,
        "discourse_api_username": (api_username or "").strip() or None,
        "forum_onboarding_group": (onboarding_group or "").strip() or None,
        "forum_member_group": (member_group or "").strip() or None,
        "forum_inactive_group": (inactive_group or "").strip() or None,
        "forum_staff_group": (staff_group or "").strip() or None,
        "forum_manage_staff_flags": _flag(manage_staff_flags),
        "forum_category_groups": lines or None,
        "forum_lecture_groups": (lecture_groups or "").strip() or None,
        "forum_archive_groups": (archive_groups or "").strip() or None,
        "forum_onboarding_path": (onboarding_path or "").strip() or "/",
        "forum_avatar_max_bytes": str(avatar_max_bytes) if avatar_max_bytes else None,
        "forum_avatar_allowed_types": ",".join(t.strip().lower().lstrip(".") for t in avatar_allowed_types
                                                if t.strip()) or None,
    }
    if (api_key or "").strip():
        values["discourse_api_key"] = api_key.strip()
    if (connect_secret or "").strip():
        values["discourse_connect_secret"] = connect_secret.strip()
    return _write(actor, "forum", values)
