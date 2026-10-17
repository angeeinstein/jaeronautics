"""Email through Microsoft 365 (docs/microsoft-mail-plan.md; setting it up:
docs/microsoft-mail-setup.md).

The portal is an app registered in the association's Entra ID and signs in as
that app -- no person signs in -- with a certificate it made itself: the
private key stays here, the certificate is uploaded to the app registration.
What it may reach is set in Exchange Online: the mailboxes listed there, no
others. A Microsoft mail account (``MailAccount.kind`` "microsoft") is one of
those mailboxes; it sends through Microsoft Graph the same email an SMTP
account sends (``mail_utils.send_mail``), and the one chosen for replies is
read for answers to answers (services/mail_replies.py).

A new certificate waits beside the one in use until it signs in; then it
takes over. Renewing never stops the email.
"""

import base64
import hashlib
import json
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from ..db_models import MailAccount, MicrosoftConnection, db
from . import NotFoundError, ValidationError
from .audit import log_audit_event
from .clock import get_now_utc

LOGIN = "https://login.microsoftonline.com"
GRAPH = "https://graph.microsoft.com/v1.0"
SCOPE = "https://graph.microsoft.com/.default"
#: How long a certificate the portal makes is valid.
CERTIFICATE_DAYS = 3 * 365
#: When System health starts to warn that it runs out.
WARN_BEFORE = timedelta(days=30)
#: How long one call to Microsoft may wait, as for an SMTP server.
TIMEOUT_SECONDS = 20

_TENANT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,99}$")
_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class MicrosoftError(Exception):
    """Microsoft said no, or could not be reached. ``str()`` says what to do."""

    def __init__(self, message, *, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


# --- The connection: the app registration and its certificate ------------------------------


def connection():
    """The app registration's row, or None before anything was entered."""
    return db.session.execute(db.select(MicrosoftConnection).order_by(MicrosoftConnection.id)).scalars().first()


def _connection_for_change():
    found = connection()
    if found is None:
        found = MicrosoftConnection()
        db.session.add(found)
    return found


def ready():
    """Whether the portal can sign in: tenant, app and a certificate in use."""
    found = connection()
    return bool(found and found.tenant_id and found.client_id and found.certificate)


def _snapshot(found):
    if found is None:
        return None
    return {"tenant_id": found.tenant_id, "client_id": found.client_id, "thumbprint": found.thumbprint,
            "new_thumbprint": found.new_thumbprint}


def save_app(actor, *, tenant_id, client_id):
    """The tenant (its ID, or a domain of it) and the app registration's ID."""
    tenant_id, client_id = (tenant_id or "").strip(), (client_id or "").strip().lower()
    if not _TENANT.fullmatch(tenant_id):
        raise ValidationError("Enter the directory (tenant) ID.", code="microsoft_invalid",
                              details={"fields": {"tenant_id": "Enter the directory (tenant) ID."}})
    if not _GUID.fullmatch(client_id):
        message = "Enter the application (client) ID: 8-4-4-4-12 characters."
        raise ValidationError(message, code="microsoft_invalid", details={"fields": {"client_id": message}})
    found = _connection_for_change()
    before = _snapshot(found) if found.id else None
    found.tenant_id, found.client_id = tenant_id, client_id
    _forget_token()
    db.session.flush()
    log_audit_event("settings", "microsoft_mail_app_saved", actor_user=actor, target_user=actor, before=before,
                    after=_snapshot(found))
    return found


def _thumbprint(certificate):
    return certificate.fingerprint(hashes.SHA1()).hex().upper()  # noqa: S303 -- what Entra ID shows and expects


def _common_name():
    from flask import current_app

    host = urlsplit(current_app.config.get("PUBLIC_BASE_URL") or "").hostname or "localhost"
    return f"{host} mail"


def make_certificate(actor, now=None):
    """A new key pair and certificate, valid three years. The first one is in
    use at once; a later one waits beside it until it signs in (``check``)."""
    now = (now or get_now_utc()).replace(tzinfo=timezone.utc)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, _common_name())])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=CERTIFICATE_DAYS))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    certificate_pem = certificate.public_bytes(serialization.Encoding.PEM).decode()
    expires = certificate.not_valid_after_utc.replace(tzinfo=None)
    found = _connection_for_change()
    if found.certificate:
        found.new_private_key, found.new_certificate = key_pem, certificate_pem
        found.new_thumbprint, found.new_certificate_expires_at = _thumbprint(certificate), expires
    else:
        found.private_key, found.certificate = key_pem, certificate_pem
        found.thumbprint, found.certificate_expires_at = _thumbprint(certificate), expires
    db.session.flush()
    log_audit_event("settings", "microsoft_mail_certificate_made", actor_user=actor, target_user=actor,
                    after=_snapshot(found))
    return found


def certificate_file(which):
    """``(file name, PEM bytes)`` of the certificate in use (``"current"``) or the new one."""
    found = connection()
    pem, thumbprint = ((found.new_certificate, found.new_thumbprint) if which == "new"
                       else (found.certificate, found.thumbprint)) if found else (None, None)
    if not pem:
        raise NotFoundError("There is no such certificate.", code="microsoft_no_certificate")
    return f"portal-mail-{thumbprint[:8].lower()}.cer", pem.encode()


def discard_new(actor):
    """Drop the new certificate that waits; the one in use goes on."""
    found = connection()
    if found is None or not found.new_certificate:
        return
    before = _snapshot(found)
    found.new_private_key = found.new_certificate = found.new_thumbprint = None
    found.new_certificate_expires_at = None
    log_audit_event("settings", "microsoft_mail_certificate_discarded", actor_user=actor, target_user=actor,
                    before=before, after=_snapshot(found))


def _promote_new(found):
    found.private_key, found.certificate = found.new_private_key, found.new_certificate
    found.thumbprint, found.certificate_expires_at = found.new_thumbprint, found.new_certificate_expires_at
    found.new_private_key = found.new_certificate = found.new_thumbprint = None
    found.new_certificate_expires_at = None
    log_audit_event("settings", "microsoft_mail_certificate_in_use", after=_snapshot(found))


def check(actor=None):
    """Sign in once: ``(ok, message)``. A new certificate that waits is tried
    first, and takes over when it works."""
    found = connection()
    if not (found and found.tenant_id and found.client_id):
        return False, "Enter the tenant and the app first."
    if not (found.certificate or found.new_certificate):
        return False, "Make a certificate and upload it to the app registration first."
    if found.new_certificate:
        try:
            _fetch_token(found, found.new_private_key, found.new_certificate)
        except MicrosoftError as exc:
            if not found.certificate:
                return False, str(exc)
            ok, message = _check_current(found)
            note = f"The new certificate does not sign in yet: {exc}"
            return ok, f"{message} {note}" if ok else note
        _promote_new(found)
        _forget_token()
        return True, "Signed in to Microsoft with the new certificate; it is in use now."
    return _check_current(found)


def _check_current(found):
    try:
        _fetch_token(found, found.private_key, found.certificate)
    except MicrosoftError as exc:
        return False, str(exc)
    return True, "Signed in to Microsoft."


# --- Signing in -------------------------------------------------------------------------------


_tokens = {}
_tokens_lock = threading.Lock()


def _forget_token():
    with _tokens_lock:
        _tokens.clear()


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _assertion(found, key_pem, certificate_pem):
    """A JWT signed with the certificate's key: who the app is, for the token endpoint."""
    certificate = x509.load_pem_x509_certificate(certificate_pem.encode())
    der = certificate.public_bytes(serialization.Encoding.DER)
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT", "x5t": _b64(hashlib.sha1(der).digest())}  # noqa: S324
    claims = {"aud": f"{LOGIN}/{found.tenant_id}/oauth2/v2.0/token", "iss": found.client_id,
              "sub": found.client_id, "jti": str(uuid.uuid4()), "nbf": now - 60, "iat": now, "exp": now + 600}
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}"
    key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    signature = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64(signature)}"


#: What the token endpoint's AADSTS codes mean for whoever sets it up.
_SIGN_IN_ERRORS = {
    "AADSTS700027": "Microsoft does not know this certificate: upload it to the app registration "
                    "(Certificates & secrets).",
    "AADSTS700024": "The certificate is not valid now: make a new one.",
    "AADSTS700016": "There is no app with this application (client) ID in that tenant.",
    "AADSTS90002": "There is no tenant with this ID.",
    "AADSTS900023": "That is not a valid tenant ID.",
    "AADSTS7000229": "The app has no service principal in the tenant yet: open it once under "
                     "Enterprise applications, or follow the setup guide.",
}


def _fetch_token(found, key_pem, certificate_pem):
    body = urlencode({
        "client_id": found.client_id, "scope": SCOPE, "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": _assertion(found, key_pem, certificate_pem),
    }).encode()
    status, _headers, answer = http("POST", f"{LOGIN}/{quote(found.tenant_id, safe='')}/oauth2/v2.0/token", data=body,
                                    headers={"Content-Type": "application/x-www-form-urlencoded"})
    data = _json(answer)
    if status != 200 or "access_token" not in data:
        described = str(data.get("error_description") or data.get("error") or f"HTTP {status}")
        code = described.split(":", 1)[0].strip()
        raise MicrosoftError(_SIGN_IN_ERRORS.get(code) or described.splitlines()[0][:300], status=status,
                             code=code)
    return data["access_token"], int(data.get("expires_in") or 3600)


def token():
    """An access token for Graph, from memory while it is good for another five minutes."""
    found = connection()
    if not (found and found.tenant_id and found.client_id and found.certificate):
        raise MicrosoftError("Microsoft 365 is not set up (Settings › Email, Accounts).")
    key = (found.tenant_id, found.client_id, found.thumbprint)
    with _tokens_lock:
        held = _tokens.get(key)
    if held and held[1] > time.time() + 300:
        return held[0]
    value, lasts = _fetch_token(found, found.private_key, found.certificate)
    with _tokens_lock:
        _tokens.clear()
        _tokens[key] = (value, time.time() + lasts)
    return value


# --- Talking to Microsoft ---------------------------------------------------------------------


def http(method, url, *, data=None, headers=None):
    """One HTTPS call: ``(status, headers, body bytes)``. Replaced in tests."""
    request = Request(url, data=data, method=method, headers=headers or {})
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 -- fixed https hosts
            return response.status, dict(response.headers), response.read()
    except HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read() or b""
    except (URLError, OSError) as exc:
        raise MicrosoftError(f"Microsoft could not be reached: {getattr(exc, 'reason', exc)}") from exc


def _json(answer):
    try:
        data = json.loads(answer or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def graph(method, path, *, body=None, data=None, content_type=None, headers=None):
    """A Graph call with the app's token: ``(status, JSON answer)``. Raises
    ``MicrosoftError`` for anything but 2xx."""
    extra = {"Authorization": f"Bearer {token()}", **(headers or {})}
    if body is not None:
        data, content_type = json.dumps(body).encode(), "application/json"
    if content_type:
        extra["Content-Type"] = content_type
    status, answer_headers, answer = http(method, f"{GRAPH}{path}", data=data, headers=extra)
    parsed = _json(answer)
    if 200 <= status < 300:
        return status, parsed
    error = parsed.get("error") if isinstance(parsed.get("error"), dict) else {}
    code, said = error.get("code") or "", error.get("message") or f"HTTP {status}"
    if status == 429 or status >= 500:
        wait = answer_headers.get("Retry-After") or answer_headers.get("retry-after")
        said = f"Microsoft is busy ({status}); tried again later." + (f" It asked to wait {wait} s." if wait else "")
    raise MicrosoftError(said, status=status, code=code)


def mailbox_path(mailbox):
    """Graph's path to a mailbox. Quoted, a slash too: an address must not be
    able to reach another part of the API. "@" may stand in a path as it is."""
    return f"/users/{quote(mailbox, safe='@')}"


def send_mime(mailbox, mime):
    """Send a finished email (MIME bytes) from ``mailbox``; it is kept in the
    mailbox's Sent Items. Raises ``MicrosoftError``."""
    try:
        graph("POST", f"{mailbox_path(mailbox)}/sendMail", data=base64.b64encode(mime),
              content_type="text/plain")
    except MicrosoftError as exc:
        if exc.status == 403:
            raise MicrosoftError(f"Microsoft does not let the app send from {mailbox}: add the mailbox to the "
                                 "app's mailboxes in Exchange Online (the setup guide, step 5). A change there "
                                 "can take up to two hours.", status=403, code=exc.code) from exc
        if exc.status == 404:
            raise MicrosoftError(f"There is no mailbox {mailbox} in the tenant.", status=404,
                                 code=exc.code) from exc
        raise


def probe(account):
    """Test a Microsoft account without sending: ``(ok, message)``."""
    mailbox = account.from_email or ""
    try:
        token()
    except MicrosoftError as exc:
        return False, str(exc)
    reads = True
    try:
        graph("GET", f"{mailbox_path(mailbox)}/mailFolders/inbox?$select=id")
    except MicrosoftError as exc:
        if exc.status == 404:
            return False, f"There is no mailbox {mailbox} in the tenant."
        reads = False
    message = f"Signed in to Microsoft. A test email shows whether it may send from {mailbox}."
    return True, message + (" It can read the mailbox, so replies can come back." if reads else "")


def microsoft_accounts():
    return db.session.execute(
        db.select(MailAccount).where(MailAccount.kind == MailAccount.MICROSOFT)
        .order_by(MailAccount.account_key)).scalars().all()


def certificate_state(now=None):
    """For System health: ``(ok, message)``, or None when no Microsoft account is there."""
    if not microsoft_accounts():
        return None
    found = connection()
    if not ready():
        return False, "Microsoft 365 accounts are there, but the connection is not set up."
    now = (now or get_now_utc()).replace(tzinfo=None)
    expires = found.certificate_expires_at
    if expires <= now:
        return False, "The certificate has run out: make a new one and upload it."
    if expires - now <= WARN_BEFORE:
        waiting = " A new one waits to be uploaded." if found.new_certificate else ""
        return False, f"The certificate runs out on {expires:%d.%m.%Y}: make a new one and upload it.{waiting}"
    return True, f"Certificate valid until {expires:%d.%m.%Y}."


def as_utc(moment):
    return moment.replace(tzinfo=timezone.utc) if moment is not None and moment.tzinfo is None else moment


def utc_from_graph(value):
    """Graph's "2026-10-16T22:05:11Z" (or with fractions) as a stored UTC time."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None
