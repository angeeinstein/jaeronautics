"""Settings -> Mail accounts and the test email: the accounts the portal sends
from -- SMTP servers, whose passwords are never sent to the browser, only in an
export that asks for the current password, and Microsoft 365 mailboxes -- the
connection to Microsoft those sign in with (its app registration and
certificate), and one email from a template, to see it as a member would. The
rules are in services/mail_accounts.py and services/microsoft_mail.py. Drawn by
frontend/src/pages/admin/settings/MailAccounts.tsx and TestEmail.tsx.
"""

import json
from datetime import datetime, timezone
from typing import Literal

from flask import current_app
from flask_login import current_user
from pydantic import Field

from ..app import limiter
from ..config import RATELIMIT_ADMIN_EMAIL
from ..db_models import db
from ..permissions import Permission
from ..services import mail_accounts as mail
from ..services import mail_replies
from ..services import microsoft_mail as microsoft
from ..services import settings_sections as sections
from ._core import Model, UtcDateTime, endpoint

TAG = "Admin"
CREDENTIALS = [Permission.SETTINGS_CREDENTIALS]


class MailAccountOut(Model):
    id: int
    #: How the account is chosen elsewhere, e.g. as the welcome email's sender.
    key: str
    #: An SMTP server, or a Microsoft 365 mailbox (through Microsoft Graph).
    kind: Literal["smtp", "microsoft"]
    #: An SMTP account's server and login; none for a Microsoft one.
    host: str | None
    port: int | None
    username: str | None
    #: STARTTLS on the port; otherwise SSL/TLS from the start.
    starttls: bool
    #: A Microsoft account's mailbox; for SMTP, where the login is not the address.
    from_email: str | None
    from_name: str | None


class CertificateOut(Model):
    #: SHA-1 in hex, as Entra ID shows it under Certificates & secrets.
    thumbprint: str
    expires_at: UtcDateTime


class MicrosoftOut(Model):
    tenant_id: str | None
    client_id: str | None
    #: The certificate the portal signs in with.
    certificate: CertificateOut | None
    #: A newer one, waiting until it signs in; then it is the one in use.
    new_certificate: CertificateOut | None
    #: Tenant, app and a certificate: the portal can try to sign in.
    ready: bool


class MailOut(Model):
    accounts: list[MailAccountOut]
    microsoft: MicrosoftOut


def _out(account):
    return MailAccountOut(id=account.id, key=account.account_key, kind=account.kind or "smtp", host=account.host,
                          port=account.port, username=account.username, starttls=bool(account.starttls),
                          from_email=account.from_email, from_name=account.from_name)


def _microsoft_out():
    found = microsoft.connection()

    def certificate(thumbprint, expires):
        return CertificateOut(thumbprint=thumbprint, expires_at=expires) if thumbprint else None

    if found is None:
        return MicrosoftOut(tenant_id=None, client_id=None, certificate=None, new_certificate=None, ready=False)
    return MicrosoftOut(tenant_id=found.tenant_id, client_id=found.client_id,
                        certificate=certificate(found.thumbprint, found.certificate_expires_at),
                        new_certificate=certificate(found.new_thumbprint, found.new_certificate_expires_at),
                        ready=microsoft.ready())


def _mail_out():
    return MailOut(accounts=[_out(account) for account in mail.accounts()], microsoft=_microsoft_out())


def watch_soon():
    """The replies' mailbox watched, or not, as the accounts and senders now
    say -- beside the request: making Graph's subscription has Microsoft call
    this portal back."""
    if microsoft.ready():
        mail_replies.start_now(current_app._get_current_object(), mail_replies.keep_watching)


@endpoint("GET", "/admin/settings/mail", response=MailOut, permissions=CREDENTIALS, tag=TAG)
def admin_mail_accounts():
    """The accounts the portal sends email from, without their passwords, and the connection to Microsoft."""
    return _mail_out()


class MailAccountIn(Model):
    key: str = Field(max_length=80)
    kind: Literal["smtp", "microsoft"] = "smtp"
    #: SMTP: the server, port and login. Not used for a Microsoft account.
    host: str | None = Field(None, max_length=255)
    port: int | None = None
    username: str | None = Field(None, max_length=255)
    #: Needed for a new SMTP account; empty keeps the one an account has.
    password: str | None = Field(None, max_length=255)
    starttls: bool = True
    #: A Microsoft account's mailbox; for SMTP, where the login is not an address (Brevo, say).
    from_email: str | None = Field(None, max_length=255)
    from_name: str | None = Field(None, max_length=120)


def _save(account_id, body):
    account = mail.save(current_user, account_id, key=body.key, kind=body.kind, host=body.host, port=body.port,
                        username=body.username, password=body.password, starttls=body.starttls,
                        from_email=body.from_email, from_name=body.from_name)
    db.session.commit()
    watch_soon()
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
    #: What it was chosen to send (Settings › Email, Senders): "default", "welcome",
    #: "notifications", "mailings", "replies". That goes out from the default now.
    cleared_senders: list[str]


@endpoint("DELETE", "/admin/settings/mail/accounts/<int:account_id>", response=DeletedOut,
          permissions=CREDENTIALS, tag=TAG)
def admin_mail_account_delete(account_id):
    """Remove an account."""
    cleared = mail.delete(current_user, account_id)
    db.session.commit()
    watch_soon()
    return DeletedOut(cleared_senders=cleared)


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


# --- Microsoft 365: the app registration and its certificate --------------------------------


class MicrosoftAppIn(Model):
    #: The directory (tenant) ID, or a domain of the tenant.
    tenant_id: str = Field(max_length=100)
    #: The app registration's application (client) ID.
    client_id: str = Field(max_length=64)


@endpoint("PUT", "/admin/settings/mail/microsoft", response=MicrosoftOut, body=MicrosoftAppIn,
          permissions=CREDENTIALS, tag=TAG)
def admin_mail_microsoft_save(body):
    """The tenant and the app registration the Microsoft accounts sign in with."""
    microsoft.save_app(current_user, tenant_id=body.tenant_id, client_id=body.client_id)
    db.session.commit()
    return _microsoft_out()


@endpoint("POST", "/admin/settings/mail/microsoft/certificate", response=MicrosoftOut,
          permissions=CREDENTIALS, status=201, tag=TAG)
def admin_mail_microsoft_certificate():
    """Make a certificate, valid three years. With one in use, the new one waits
    until it signs in (after it is uploaded); then it takes over."""
    microsoft.make_certificate(current_user)
    db.session.commit()
    return _microsoft_out()


class CertificateQuery(Model):
    #: The one in use, or the new one that waits.
    which: Literal["current", "new"] = "current"


@endpoint("GET", "/admin/settings/mail/microsoft/certificate", query=CertificateQuery, permissions=CREDENTIALS,
          produces="application/x-x509-ca-cert", tag=TAG)
def admin_mail_microsoft_certificate_file(query):
    """The certificate, to upload to the app registration. Only the public part: the key stays here."""
    name, data = microsoft.certificate_file(query.which)
    response = current_app.response_class(data, mimetype="application/x-x509-ca-cert")
    response.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


@endpoint("DELETE", "/admin/settings/mail/microsoft/certificate/new", response=MicrosoftOut,
          permissions=CREDENTIALS, tag=TAG)
def admin_mail_microsoft_certificate_discard():
    """Drop the new certificate that waits; the one in use goes on."""
    microsoft.discard_new(current_user)
    db.session.commit()
    return _microsoft_out()


class MicrosoftCheckOut(Model):
    ok: bool
    message: str
    microsoft: MicrosoftOut


@endpoint("POST", "/admin/settings/mail/microsoft/check", response=MicrosoftCheckOut, permissions=CREDENTIALS,
          tag=TAG)
@limiter.limit(RATELIMIT_ADMIN_EMAIL)
def admin_mail_microsoft_check():
    """Sign in to Microsoft once. A new certificate that waits is tried first, and is used from then on if it works."""
    ok, message = microsoft.check(current_user)
    db.session.commit()
    if ok:
        watch_soon()
    return MicrosoftCheckOut(ok=ok, message=message, microsoft=_microsoft_out())


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
    #: Why not, as the mail server or Microsoft said it.
    error: str | None = None


@endpoint("POST", "/admin/settings/test-email", response=SentOut, body=TestEmailIn,
          permissions=[Permission.NOTIFICATIONS_MANAGE], tag=TAG)
@limiter.limit(RATELIMIT_ADMIN_EMAIL)
def admin_test_email_send(body):
    """Send one email from a template, filled in as a member's would be. Whether the server took it."""
    sent, error = mail.send_test_email(body.sender, body.recipient, body.template)
    return SentOut(ok=sent, error=error)
