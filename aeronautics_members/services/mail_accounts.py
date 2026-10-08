"""The accounts the portal sends email from: adding, changing, removing,
testing, importing and exporting them -- and a test email from one.

Their passwords are kept for sending and leave the server only in an export,
which asks for the current password first. Every change is in the log,
without the password.
"""

import json
import re

from sqlalchemy.exc import IntegrityError

from ..db_models import MailAccount, Setting, db
from . import ConflictError, NotFoundError, PermissionError_, ValidationError
from .audit import log_audit_event, snapshot_mail_account_for_audit
from .notifications import (
    build_mail_accounts_export_payload,
    get_db_mail_accounts,
    normalize_imported_mail_accounts_payload,
)

KEY_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def accounts():
    return get_db_mail_accounts()


def _invalid(field, message):
    return ValidationError(message, code="mail_account_invalid", details={"fields": {field: message}})


def _get(account_id):
    account = db.session.get(MailAccount, account_id)
    if account is None:
        raise NotFoundError("That mail account does not exist.")
    return account


def save(actor, account_id, *, key, host, port, username, password, starttls, from_email, from_name):
    """Add an account (``account_id`` None; a password is needed) or change one
    (an empty password keeps the one it has)."""
    key, host, username = (key or "").strip(), (host or "").strip(), (username or "").strip()
    from_email = (from_email or "").strip().lower() or None
    if not KEY_PATTERN.fullmatch(key):
        raise _invalid("key", "Use only letters, digits, dashes and underscores.")
    if not host:
        raise _invalid("host", "Enter the SMTP server.")
    if not username:
        raise _invalid("username", "Enter the SMTP username.")
    if not 1 <= int(port or 0) <= 65535:
        raise _invalid("port", "Enter a port between 1 and 65535.")
    if from_email and not _EMAIL.fullmatch(from_email):
        raise _invalid("from_email", "That does not look like an email address.")
    clash = db.session.execute(db.select(MailAccount).filter_by(account_key=key)).scalar_one_or_none()
    if clash is not None and clash.id != account_id:
        raise ConflictError("A mail account with this key already exists.", code="mail_account_key_taken",
                            details={"fields": {"key": "A mail account with this key already exists."}})
    if account_id is None:
        if not password:
            raise _invalid("password", "A new account needs its password.")
        account = MailAccount()
        db.session.add(account)
    else:
        account = _get(account_id)
    before = snapshot_mail_account_for_audit(account) if account_id is not None else None
    account.account_key, account.host, account.port, account.username = key, host, int(port), username
    if password:
        account.password = password
    account.starttls = bool(starttls)
    account.from_email = from_email
    account.from_name = (from_name or "").strip() or None
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        raise ConflictError("A mail account with this key already exists.", code="mail_account_key_taken") from None
    log_audit_event("settings", "mail_account_created" if before is None else "mail_account_updated",
                    actor_user=actor, target_user=actor, before=before, after=snapshot_mail_account_for_audit(account),
                    metadata={"mail_account_id": account.id, "account_key": account.account_key})
    return account


def delete(actor, account_id):
    """Remove an account. Welcome emails sent from it stop until another sender is chosen;
    whether that happened is answered."""
    account = _get(account_id)
    welcome = db.session.get(Setting, "welcome_email_sender")
    removed_welcome_sender = welcome is not None and welcome.value == account.account_key
    if removed_welcome_sender:
        db.session.delete(welcome)
    log_audit_event("settings", "mail_account_deleted", actor_user=actor, target_user=actor,
                    before=snapshot_mail_account_for_audit(account),
                    metadata={"mail_account_id": account.id, "account_key": account.account_key,
                              "removed_welcome_sender": removed_welcome_sender})
    db.session.delete(account)
    return removed_welcome_sender


def test_connection(actor, account_id):
    """Sign in to the account's server: ``(ok, message)``. Sends nothing."""
    from ..mail_utils import probe_mail_account_connection

    account = _get(account_id)
    ok, message = probe_mail_account_connection(account.to_config())
    log_audit_event("settings", "mail_account_connection_tested", actor_user=actor, target_user=actor,
                    before=snapshot_mail_account_for_audit(account),
                    metadata={"mail_account_id": account.id, "account_key": account.account_key,
                              "success": ok, "message": message})
    return bool(ok), message


def import_file(actor, data, *, overwrite):
    """Accounts from a JSON file: this portal's export, the older mapping, or a list.
    An existing key is kept unless ``overwrite``. ``{created, updated, skipped}``."""
    try:
        records = normalize_imported_mail_accounts_payload(json.loads(data.decode("utf-8-sig")))
    except UnicodeDecodeError:
        raise _invalid("file", "The file is not UTF-8 text.") from None
    except json.JSONDecodeError:
        raise _invalid("file", "The file is not valid JSON.") from None
    except ValueError as exc:
        raise _invalid("file", str(exc)) from None

    created, updated, skipped = 0, 0, []
    for record in records:
        account = db.session.execute(
            db.select(MailAccount).filter_by(account_key=record["account_key"])).scalar_one_or_none()
        if account is not None and not overwrite:
            skipped.append(record["account_key"])
            continue
        before = snapshot_mail_account_for_audit(account)
        if account is None:
            account = MailAccount()
            db.session.add(account)
        for field in ("account_key", "host", "port", "username", "password", "starttls", "from_email", "from_name"):
            setattr(account, field, record[field])
        db.session.flush()
        log_audit_event("settings", "mail_account_created" if before is None else "mail_account_updated",
                        actor_user=actor, target_user=actor, before=before,
                        after=snapshot_mail_account_for_audit(account),
                        metadata={"mail_account_id": account.id, "account_key": account.account_key,
                                  "source": "json_import", "overwrite_existing": overwrite})
        if before is None:
            created += 1
        else:
            updated += 1
    log_audit_event("settings", "mail_accounts_imported", actor_user=actor, target_user=actor,
                    after={"created": created, "updated": updated, "skipped": len(skipped)},
                    metadata={"overwrite_existing": overwrite, "skipped_keys": skipped,
                              "imported_keys": [record["account_key"] for record in records]})
    return {"created": created, "updated": updated, "skipped": skipped}


def export(actor, password):
    """Every account, passwords included -- only with the current password confirmed."""
    if not actor.check_password(password or ""):
        raise PermissionError_("Your current password is needed to export the accounts with their passwords.",
                               code="password_wrong",
                               details={"fields": {"password": "That is not your current password."}})
    payload = build_mail_accounts_export_payload()
    log_audit_event("settings", "mail_accounts_exported", actor_user=actor, target_user=actor,
                    metadata={"count": len(payload["mail_accounts"]), "format": payload["format"],
                              "version": payload["version"]})
    return payload


def send_test_email(sender, recipient, template):
    """One email from ``template`` as a member would get it, filled in with sample values."""
    from flask import current_app

    from ..mail_utils import load_mail_accounts_config, send_mail
    from .notifications import get_email_template_choices, sample_email_for

    if sender not in load_mail_accounts_config():
        raise _invalid("sender", "That sender account does not exist.")
    if template not in {name for name, _label in get_email_template_choices(current_app._get_current_object())}:
        raise _invalid("template", "That email template does not exist.")
    if not _EMAIL.fullmatch((recipient or "").strip()):
        raise _invalid("recipient", "That does not look like an email address.")
    subject, values = sample_email_for(template)
    return bool(send_mail(from_account=sender, to_email=recipient.strip(), subject=f"Test: {subject}",
                          template_name=template, **values))
