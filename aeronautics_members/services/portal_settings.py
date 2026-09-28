"""Everything an administrator typed, as a file, so it can be typed once.

A fresh install starts with a blank settings page, and filling it in is forty
minutes of copying values out of screenshots -- Stripe keys, the forum's base
URL and API credentials, the group names, the mail accounts, the institutional
domains. Every one of them has to be exactly right and none of them announces
itself when it is wrong: a missing lecture group means an import that refuses,
a missing Connect secret means a login that silently does not work.

So they travel as a file. That makes rebuilding the portal -- for this
migration, for the move to a cloud host, for a machine that has to be replaced
in a hurry -- a restore rather than a reconstruction from memory.

**With ``--with-secrets`` the file is a password list.** It holds the Stripe
secret key, the Stripe webhook secret, the Discourse API key, the Connect
secret and every SMTP password. It is written ``0600`` and it is not encrypted;
anywhere it is copied inherits that, which is the same warning the database
dump carries and for the same reason.

Deliberately not included: anything in ``.env``. ``SECRET_KEY`` and the database
password belong to the machine rather than to the configuration, and restoring
an old database password onto a new install would break the thing it was
restoring.
"""

from ..config import STRIPE_SETTING_KEYS
from ..db_models import MailAccount, Setting, db
from ..forum_service import FORUM_SETTING_KEYS
from ..notification_service import NOTIFICATION_SETTING_KEYS
from ..services.institutional_email import SETTING_KEY as INSTITUTIONAL_EMAIL_SETTING_KEY

FORMAT = 1

#: The settings an administrator edits, by the page they are edited on. Listed
#: rather than "every row in the table" so that a key some future feature keeps
#: there for its own bookkeeping is not quietly restored onto a new machine.
GENERAL_SETTING_KEYS = (
    "invoice_payments_enabled",
    "automatic_emails_enabled",
    "welcome_email_sender",
    "automatic_email_template",
    INSTITUTIONAL_EMAIL_SETTING_KEY,
)

SECTIONS = {
    "general": GENERAL_SETTING_KEYS,
    "notifications": NOTIFICATION_SETTING_KEYS,
    "forum": FORUM_SETTING_KEYS,
    "stripe": STRIPE_SETTING_KEYS,
}

#: Values that are credentials rather than configuration. Left out unless they
#: are asked for by name, because a file that is merely useful and a file that
#: is a password list should not be the same file by accident.
SECRET_KEYS = frozenset({
    "stripe_secret_key",
    "stripe_webhook_secret",
    "discourse_api_key",
    "discourse_connect_secret",
})

REDACTED = "<not exported>"


def export_settings(*, with_secrets=False):
    """Everything an administrator typed, as a dictionary ready to be JSON."""
    stored = {
        setting.key: setting.value
        for setting in db.session.execute(db.select(Setting)).scalars()
    }

    sections, held_back = {}, []
    for section, keys in SECTIONS.items():
        values = {}
        for key in keys:
            if key not in stored:
                continue
            if key in SECRET_KEYS and not with_secrets:
                held_back.append(key)
                values[key] = REDACTED
                continue
            values[key] = stored[key]
        sections[section] = values

    accounts = []
    for account in db.session.execute(
        db.select(MailAccount).order_by(MailAccount.account_key.asc())
    ).scalars():
        row = {
            "account_key": account.account_key,
            "host": account.host,
            "port": account.port,
            "username": account.username,
            "starttls": bool(account.starttls),
        }
        if with_secrets:
            row["password"] = account.password
        else:
            held_back.append(f"mail account {account.account_key}")
        accounts.append(row)

    return {
        "format": FORMAT,
        "with_secrets": bool(with_secrets),
        "settings": sections,
        "mail_accounts": accounts,
        "held_back": sorted(set(held_back)),
    }


def _known_keys():
    return {key for keys in SECTIONS.values() for key in keys}


def import_settings(payload, *, dry_run=False):
    """Put them back. Returns what changed, what was already right, what was not.

    Never removes anything. A setting the file does not mention is left as it
    is, because the file is a record of one machine rather than a description of
    every machine, and a restore that silently blanked what it did not know
    about would be one nobody could run twice.
    """
    if not isinstance(payload, dict) or "settings" not in payload:
        raise ValueError(
            "That file has no settings in it. Export one with "
            "dump-portal-settings."
        )
    if payload.get("format") != FORMAT:
        raise ValueError(
            f"That file is format {payload.get('format')!r} and this "
            f"understands {FORMAT}."
        )

    known = _known_keys()
    report = {"set": [], "already": [], "skipped": [], "accounts": [],
              "problems": []}

    stored = {
        setting.key: setting for setting in
        db.session.execute(db.select(Setting)).scalars()
    }

    for section, values in (payload.get("settings") or {}).items():
        for key, value in (values or {}).items():
            if key not in known:
                report["problems"].append(
                    f"{section}.{key} is not a setting this portal has; ignored"
                )
                continue
            if value == REDACTED:
                # Exported without secrets. Writing the placeholder would be
                # worse than leaving the box empty, because an empty box says
                # what it is and "<not exported>" reads like a value.
                report["skipped"].append(key)
                continue
            row = stored.get(key)
            if row is not None and row.value == value:
                report["already"].append(key)
                continue
            report["set"].append(key)
            if dry_run:
                continue
            if row is None:
                db.session.add(Setting(key=key, value=value))
            else:
                row.value = value

    for row in payload.get("mail_accounts") or []:
        key = (row.get("account_key") or "").strip()
        if not key:
            continue
        password = row.get("password")
        account = db.session.execute(
            db.select(MailAccount).where(MailAccount.account_key == key)
        ).scalar_one_or_none()
        if account is None and not password:
            # A mail account cannot exist without one, and inventing a blank
            # would produce an account that fails at the first send rather than
            # one that is visibly missing.
            report["problems"].append(
                f"mail account {key} was exported without its password and "
                f"does not exist here; add it by hand"
            )
            continue
        report["accounts"].append(key)
        if dry_run:
            continue
        if account is None:
            account = MailAccount(account_key=key)
            db.session.add(account)
        account.host = row.get("host") or ""
        account.port = int(row.get("port") or 587)
        account.username = row.get("username") or ""
        account.starttls = bool(row.get("starttls"))
        if password:
            account.password = password

    return report
