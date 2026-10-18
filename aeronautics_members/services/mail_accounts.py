"""The accounts the portal sends email from: adding, changing, removing,
testing, importing and exporting them -- and a test email from one.

An account is an SMTP server, or a Microsoft 365 mailbox reached through
Microsoft Graph (services/microsoft_mail.py), which has no server, login or
password of its own. SMTP passwords are kept for sending and leave the server
only in an export, which asks for the current password first. Every change is
in the log, without the password.
"""

import json
import re

from sqlalchemy.exc import IntegrityError

from ..db_models import MailAccount, db
from . import ConflictError, NotFoundError, PermissionError_, ValidationError
from .audit import log_audit_event, snapshot_mail_account_for_audit
from .notifications import (
    build_mail_accounts_export_payload,
    get_db_mail_accounts,
    normalize_imported_mail_accounts_payload,
)

KEY_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# A mailbox goes into Microsoft Graph's paths: what an Exchange address holds,
# nothing that could mean something else there (/, ?, #, %).
_MAILBOX = re.compile(r"^[a-z0-9._'+-]+@[a-z0-9-]+(\.[a-z0-9-]+)+$")


def accounts():
    return get_db_mail_accounts()


def _invalid(field, message):
    return ValidationError(message, code="mail_account_invalid", details={"fields": {field: message}})


def _get(account_id):
    account = db.session.get(MailAccount, account_id)
    if account is None:
        raise NotFoundError("That mail account does not exist.")
    return account


def save(actor, account_id, *, key, host=None, port=None, username=None, password=None, starttls=True,
         from_email=None, from_name=None, kind=MailAccount.SMTP):
    """Add an account (``account_id`` None) or change one. An SMTP account
    needs its server, login and -- when new -- password (an empty one keeps
    the one it has); a Microsoft account only its mailbox, ``from_email``."""
    key, host, username = (key or "").strip(), (host or "").strip(), (username or "").strip()
    from_email = (from_email or "").strip().lower() or None
    microsoft = kind == MailAccount.MICROSOFT
    if kind not in (MailAccount.SMTP, MailAccount.MICROSOFT):
        raise _invalid("kind", "Choose SMTP or Microsoft 365.")
    if not KEY_PATTERN.fullmatch(key):
        raise _invalid("key", "Use only letters, digits, dashes and underscores.")
    if microsoft:
        if not from_email:
            raise _invalid("from_email", "Enter the mailbox's address.")
        if not _MAILBOX.fullmatch(from_email):
            raise _invalid("from_email", "That does not look like a mailbox's address.")
    else:
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
    account = MailAccount() if account_id is None else _get(account_id)
    if not microsoft and not password and not account.password:
        raise _invalid("password", "A new account needs its password.")
    if account_id is None:
        db.session.add(account)
    before = snapshot_mail_account_for_audit(account) if account_id is not None else None
    account.account_key, account.kind = key, kind
    if microsoft:
        account.host = account.port = account.username = account.password = None
        account.starttls = False
    else:
        account.host, account.port, account.username = host, int(port), username
        if password:
            account.password = password
        account.starttls = bool(starttls)
        _stop_watching(account)
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


def _stop_watching(account):
    """No longer a Microsoft account, or removed: Graph's subscription to its
    inbox ends (it would run out by itself within days)."""
    if account.inbox_subscription_id:
        from .mail_replies import unsubscribe

        unsubscribe(account)


def delete(actor, account_id):
    """Remove an account. What it was chosen to send (Settings › Email, Senders)
    goes out from the default from now on; those kinds are answered."""
    from .mail_senders import forget_account

    account = _get(account_id)
    cleared = forget_account(account.account_key)
    _stop_watching(account)
    log_audit_event("settings", "mail_account_deleted", actor_user=actor, target_user=actor,
                    before=snapshot_mail_account_for_audit(account),
                    metadata={"mail_account_id": account.id, "account_key": account.account_key,
                              "cleared_senders": cleared})
    db.session.delete(account)
    return cleared


def test_connection(actor, account_id):
    """Sign in to the account's server: ``(ok, message)``. Sends nothing."""
    from ..mail_utils import probe_mail_account_connection

    account = _get(account_id)
    if account.is_microsoft:
        from .microsoft_mail import probe

        ok, message = probe(account)
    else:
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

    for record in records:
        if record["kind"] == MailAccount.MICROSOFT and not _MAILBOX.fullmatch(record["from_email"] or ""):
            raise _invalid("file", f"{record['account_key']}: that does not look like a mailbox's address.")
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
        for field in ("account_key", "kind", "host", "port", "username", "password", "starttls", "from_email",
                      "from_name"):
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
    """One email from ``template`` as a member would get it, filled in with
    sample values: ``(sent, why not)`` -- the reason is for the admin testing."""
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
    sent, error = send_mail(from_account=sender, to_email=recipient.strip(), subject=f"Test: {subject}",
                            template_name=template, return_error=True, **values)
    return bool(sent), None if sent else (error or "The server did not take it.")
