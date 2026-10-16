"""Which mail account sends what (Admin › Settings › Email, *Senders*): one
default account, and for each kind of email an account of its own -- or,
left empty, the default.

- **Default** (``mail_default_sender``): everything not chosen otherwise, and
  the account emails (password reset, confirming an address, deletion
  links); with none chosen, the first mail account.
- **Welcome emails** (``welcome_email_sender``).
- **Notifications** (``notification_sender``): to the admins, and to members
  about their membership.
- **Announcements and team mailings** (``mailings_sender``), and the contact
  form's messages to the admins.
- **Replies to contact messages** (``contact_reply_sender``), with
  ``contact_reply_copy``: each reply also goes, as a blind copy, to that
  account's own address.

Until 2026-10 there was no default: the welcome email's sender served as one,
and each other kind fell back to another. The migration ``a4c7e2f9b1d6``
made that sender the default.
"""

from ..db_models import Setting, db
from ..mail_utils import load_mail_accounts_config
from . import ValidationError
from .settings import get_settings_map

DEFAULT = "mail_default_sender"
#: A kind of email, and the setting that names its own account.
KINDS = {
    "welcome": "welcome_email_sender",
    "notifications": "notification_sender",
    "mailings": "mailings_sender",
    "replies": "contact_reply_sender",
}
REPLY_COPY = "contact_reply_copy"
KEYS = (DEFAULT, *KINDS.values(), REPLY_COPY)


def accounts():
    """The mail accounts, by their key."""
    try:
        return sorted(load_mail_accounts_config())
    except Exception:  # noqa: BLE001 -- no accounts is an answer too
        return []


def stored():
    """What is chosen: ``{"default": "office", "welcome": "", ..., "reply_copy": False}``."""
    values = get_settings_map(KEYS)
    chosen = {"default": (values.get(DEFAULT) or "").strip()}
    chosen.update({kind: (values.get(key) or "").strip() for kind, key in KINDS.items()})
    chosen["reply_copy"] = (values.get(REPLY_COPY) or "") == "1"
    return chosen


def default_account():
    """The default account: the one chosen, else the first there is; "" with none."""
    chosen = stored()["default"]
    if chosen:
        return chosen
    available = accounts()
    return available[0] if available else ""


def account_for(kind):
    """The account an email of ``kind`` goes out from: its own, else the default."""
    return stored()[kind] or default_account()


def address_of(key):
    """The address an account sends as: its sender address, else its login."""
    config = load_mail_accounts_config().get(key) or {}
    return (config.get("from") or config.get("user") or "").strip()


def save(actor, *, default, welcome, notifications, mailings, replies, reply_copy):
    """Choose the accounts. Each must exist; empty is the default (for the default: the first account)."""
    from .settings_sections import _write

    available = set(accounts())
    chosen = {"default": default, "welcome": welcome, "notifications": notifications, "mailings": mailings,
              "replies": replies}
    for field, value in chosen.items():
        value = (value or "").strip()
        if value and value not in available:
            message = "No such mail account (the Accounts tab)."
            raise ValidationError(message, code="settings_invalid", details={"fields": {field: message}})
        chosen[field] = value
    values = {DEFAULT: chosen["default"] or None}
    values.update({key: chosen[kind] or None for kind, key in KINDS.items()})
    values[REPLY_COPY] = "1" if reply_copy else None
    return _write(actor, "email_senders", values)


def forget_account(key):
    """An account removed: whatever it sent now goes out from the default. The kinds it was chosen for."""
    cleared = []
    for name, setting_key in ((None, DEFAULT), *KINDS.items()):
        setting = db.session.get(Setting, setting_key)
        if setting is not None and setting.value == key:
            db.session.delete(setting)
            cleared.append(name or "default")
    return cleared
