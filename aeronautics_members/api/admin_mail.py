"""Settings -> Mail accounts and the test email: the accounts the portal sends
from -- their passwords never sent to the browser, only in an export that asks
for the current password -- and one email from a template, to see it as a
member would. The rules are in services/mail_accounts.py. Drawn by
frontend/src/pages/admin/settings/MailAccounts.tsx and TestEmail.tsx.
"""

import json
from datetime import datetime, timezone

from flask import current_app
from flask_login import current_user
from pydantic import Field

from ..app import limiter
from ..config import RATELIMIT_ADMIN_EMAIL
from ..db_models import db
from ..permissions import Permission
from ..services import mail_accounts as mail
from ..services import settings_sections as sections
from ._core import Model, endpoint

TAG = "Admin"
CREDENTIALS = [Permission.SETTINGS_CREDENTIALS]


class MailAccountOut(Model):
    id: int
    #: How the account is chosen elsewhere, e.g. as the welcome email's sender.
    key: str
    host: str
    port: int
    username: str
    #: STARTTLS on the port; otherwise SSL/TLS from the start.
    starttls: bool
    from_email: str | None
    from_name: str | None


class MailOut(Model):
    accounts: list[MailAccountOut]


def _out(account):
    return MailAccountOut(id=account.id, key=account.account_key, host=account.host, port=account.port,
                          username=account.username, starttls=bool(account.starttls),
                          from_email=account.from_email, from_name=account.from_name)


@endpoint("GET", "/admin/settings/mail", response=MailOut, permissions=CREDENTIALS, tag=TAG)
def admin_mail_accounts():
    """The accounts the portal sends email from, without their passwords."""
    return MailOut(accounts=[_out(account) for account in mail.accounts()])


class MailAccountIn(Model):
    key: str = Field(max_length=80)
    host: str = Field(max_length=255)
    port: int
    username: str = Field(max_length=255)
    #: Needed for a new account; empty keeps the one an account has.
    password: str | None = Field(None, max_length=255)
    starttls: bool = True
    #: Where the login is not an address (Brevo, say): the address emails come from.
    from_email: str | None = Field(None, max_length=255)
    from_name: str | None = Field(None, max_length=120)


def _save(account_id, body):
    account = mail.save(current_user, account_id, key=body.key, host=body.host, port=body.port,
                        username=body.username, password=body.password, starttls=body.starttls,
                        from_email=body.from_email, from_name=body.from_name)
    db.session.commit()
    return _out(account)


@endpoint("POST", "/admin/settings/mail/accounts", response=MailAccountOut, body=MailAccountIn,
          permissions=CREDENTIALS, status=201, tag=TAG)
def admin_mail_account_add(body):
    """Add an account to send from."""
    return _save(None, body)


@endpoint("PUT", "/admin/settings/mail/accounts/<int:account_id>", response=MailAccountOut, body=MailAccountIn,
          permissions=CREDENTIALS, tag=TAG)
def admin_mail_account_change(account_id, body):
    """Change an account. An empty password keeps the one it has."""
    return _save(account_id, body)


class DeletedOut(Model):
    #: It was the welcome email's sender: no welcome email until another is chosen.
    removed_welcome_sender: bool


@endpoint("DELETE", "/admin/settings/mail/accounts/<int:account_id>", response=DeletedOut,
          permissions=CREDENTIALS, tag=TAG)
def admin_mail_account_delete(account_id):
    """Remove an account."""
    removed = mail.delete(current_user, account_id)
    db.session.commit()
    return DeletedOut(removed_welcome_sender=removed)


class ConnectionOut(Model):
    ok: bool
    message: str


@endpoint("POST", "/admin/settings/mail/accounts/<int:account_id>/test", response=ConnectionOut,
          permissions=CREDENTIALS, tag=TAG)
@limiter.limit(RATELIMIT_ADMIN_EMAIL)
def admin_mail_account_test(account_id):
    """Sign in to the account's server. Sends nothing."""
    ok, message = mail.test_connection(current_user, account_id)
    db.session.commit()
    return ConnectionOut(ok=ok, message=message)


class ImportQuery(Model):
    #: Replace an account whose key is already there; otherwise it is kept and skipped.
    overwrite: bool = False


class ImportOut(Model):
    created: int
    updated: int
    #: Keys that were already there and kept.
    skipped: list[str]


@endpoint("POST", "/admin/settings/mail/import", response=ImportOut, query=ImportQuery, uploads={"file": True},
          permissions=CREDENTIALS, tag=TAG)
def admin_mail_import(query, files):
    """Accounts from a JSON file: this portal's export, the older mapping, or a list of accounts."""
    found = mail.import_file(current_user, files["file"].read(), overwrite=query.overwrite)
    db.session.commit()
    return ImportOut(**found)


class ExportIn(Model):
    #: The current password: the file has every account's password in it.
    password: str = Field(max_length=255)


@endpoint("POST", "/admin/settings/mail/export", body=ExportIn, permissions=CREDENTIALS,
          produces="application/json", tag=TAG)
def admin_mail_export(body):
    """Every account as a JSON file, passwords included -- with the current password confirmed."""
    payload = mail.export(current_user, body.password)
    db.session.commit()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    response = current_app.response_class(json.dumps(payload, indent=2), mimetype="application/json")
    response.headers["Content-Disposition"] = f'attachment; filename="jaeronautics-mail-accounts-{stamp}.json"'
    response.headers["Cache-Control"] = "no-store, private"
    return response


# --- The test email ---------------------------------------------------------------------


class TestEmailOut(Model):
    senders: list[str]
    templates: list[str]


@endpoint("GET", "/admin/settings/test-email", response=TestEmailOut,
          permissions=[Permission.NOTIFICATIONS_MANAGE], tag=TAG)
def admin_test_email():
    """What a test email can be sent from, and with."""
    return TestEmailOut(senders=sections.sender_accounts(), templates=sections.email_templates())


class TestEmailIn(Model):
    sender: str = Field(max_length=100)
    recipient: str = Field(max_length=255)
    template: str = Field(max_length=200)


class SentOut(Model):
    ok: bool


@endpoint("POST", "/admin/settings/test-email", response=SentOut, body=TestEmailIn,
          permissions=[Permission.NOTIFICATIONS_MANAGE], tag=TAG)
@limiter.limit(RATELIMIT_ADMIN_EMAIL)
def admin_test_email_send(body):
    """Send one email from a template, filled in as a member's would be. Whether the server took it."""
    return SentOut(ok=mail.send_test_email(body.sender, body.recipient, body.template))
