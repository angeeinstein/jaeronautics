# ruff: noqa: F811 -- the fixtures imported below are taken as arguments
"""Email through Microsoft 365 (services/microsoft_mail.py, services/mail_replies.py,
docs/microsoft-mail-plan.md): mailboxes reached through Microsoft Graph beside
SMTP accounts, the portal signing in as its app with a certificate it made
itself, and replies to answers from Admin › Messages brought back into them.
Microsoft itself is a stand-in here (``FakeMicrosoft``).
"""
import base64
import email
import json
from datetime import datetime, timedelta
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from api_helpers import send, signed_in
from conftest import db
from aeronautics_members.db_models import AuditLog, ContactReply, MailAccount, MicrosoftConnection
from aeronautics_members.mail_utils import send_mail
from aeronautics_members.services import mail_replies, mail_senders, messages, microsoft_mail, settings_sections
from test_api_admin_mail import API, _boss
from test_messages import FORM, admins  # noqa: F401
from test_teams_foundation import _association_member

TENANT = "contoso.onmicrosoft.com"
APP_ID = "11111111-2222-3333-4444-555555555555"


class FakeMicrosoft:
    """Microsoft's sign-in and Graph, as far as the portal uses them: every
    call kept; answers by (method, path start), else 200 with nothing."""

    def __init__(self):
        self.calls = []
        self.answers = {}
        self.refuse_sign_in = None
        self.inbox = {}
        #: A token Graph turns away, as one issued before a permission was granted.
        self.stale_token = None
        #: The Graph permissions granted in Entra: the token then names them, as Microsoft's does.
        self.roles = None

    def answer(self, method, path, status=200, body=None):
        self.answers[(method, path)] = (status, body or {})

    def __call__(self, method, url, *, data=None, headers=None):
        parts = urlsplit(url)
        self.calls.append({"method": method, "url": url, "path": unquote(parts.path), "query": unquote(parts.query),
                           "data": data, "headers": headers or {}})
        if parts.netloc == "login.microsoftonline.com":
            if self.refuse_sign_in:
                return 401, {}, json.dumps({"error": "invalid_client",
                                            "error_description": f"{self.refuse_sign_in}: no."}).encode()
            token = f"token-{len(self.sign_ins())}"
            if self.roles is not None:
                claims = base64.urlsafe_b64encode(json.dumps({"roles": self.roles}).encode()).decode().rstrip("=")
                token = f"header.{claims}.{token}"
            return 200, {}, json.dumps({"access_token": token, "expires_in": 3600}).encode()
        if self.stale_token and (headers or {}).get("Authorization") == f"Bearer {self.stale_token}":
            return 403, {}, json.dumps({"error": {"code": "ErrorAccessDenied", "message": "Access is denied."}}).encode()
        path = unquote(parts.path).removeprefix("/v1.0")
        for (method_, start), (status, body) in self.answers.items():
            if method == method_ and path.startswith(start):
                return status, {}, json.dumps(body).encode()
        return 200 if method != "POST" else 202, {}, b""

    def graph_calls(self, method=None):
        return [call for call in self.calls if "graph.microsoft.com" in call["url"]
                and (method is None or call["method"] == method)]

    def sign_ins(self):
        return [call for call in self.calls if "login.microsoftonline.com" in call["url"]]


@pytest.fixture
def microsoft(app, monkeypatch):
    fake = FakeMicrosoft()
    monkeypatch.setattr(microsoft_mail, "http", fake)
    microsoft_mail._forget_token()

    def at_once(_app, work, *args):
        work(*args)
        db.session.commit()

    monkeypatch.setattr(mail_replies, "start_now", at_once)
    return fake


def _connected():
    microsoft_mail.save_app(None, tenant_id=TENANT, client_id=APP_ID)
    microsoft_mail.make_certificate(None)
    db.session.commit()
    return microsoft_mail.connection()


def _mailbox(key="support", address="support@example.org"):
    account = MailAccount(account_key=key, kind=MailAccount.MICROSOFT, from_email=address,
                          from_name="Joanneum Aeronautics")
    db.session.add(account)
    db.session.commit()
    return account


def _b64decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class TestTheCertificate:
    def test_the_first_is_in_use_a_later_one_waits(self, app, microsoft):
        first = _connected()
        thumbprint = first.thumbprint

        microsoft_mail.make_certificate(None)

        found = microsoft_mail.connection()
        assert found.thumbprint == thumbprint and found.new_thumbprint not in (None, thumbprint)
        assert len(thumbprint) == 40 and thumbprint == thumbprint.upper()
        assert found.certificate_expires_at - datetime.utcnow() > timedelta(days=3 * 365 - 2)

    def test_the_file_is_the_certificate_alone_never_the_key(self, app, microsoft):
        _connected()

        name, data = microsoft_mail.certificate_file("current")

        assert name.endswith(".cer") and b"BEGIN CERTIFICATE" in data and b"PRIVATE KEY" not in data
        loaded = x509.load_pem_x509_certificate(data)
        assert loaded.fingerprint(hashes.SHA1()).hex().upper() == microsoft_mail.connection().thumbprint

    def test_a_new_one_takes_over_once_it_signs_in(self, app, microsoft):
        _connected()
        microsoft_mail.make_certificate(None)
        waiting = microsoft_mail.connection().new_thumbprint

        ok, message = microsoft_mail.check()

        assert ok and "new certificate" in message
        found = microsoft_mail.connection()
        assert found.thumbprint == waiting and found.new_thumbprint is None

    def test_one_not_uploaded_yet_waits_and_the_old_one_goes_on(self, app, microsoft):
        _connected()
        in_use = microsoft_mail.connection().thumbprint
        microsoft_mail.make_certificate(None)
        microsoft.refuse_sign_in = "AADSTS700027"

        ok, message = microsoft_mail.check()

        assert not ok and "upload it to the app registration" in message
        assert microsoft_mail.connection().thumbprint == in_use


class TestSigningIn:
    def test_a_jwt_signed_with_the_certificate_names_the_app_and_its_thumbprint(self, app, microsoft):
        found = _connected()

        microsoft_mail.token()

        [call] = microsoft.sign_ins()
        assert call["url"] == f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
        form = parse_qs(call["data"].decode())
        assert form["scope"] == ["https://graph.microsoft.com/.default"]
        assert form["grant_type"] == ["client_credentials"] and form["client_id"] == [APP_ID]
        header, claims, signature = form["client_assertion"][0].split(".")
        header, claims = json.loads(_b64decode(header)), json.loads(_b64decode(claims))
        certificate = x509.load_pem_x509_certificate(found.certificate.encode())
        assert _b64decode(header["x5t"]) == certificate.fingerprint(hashes.SHA1())
        assert claims["iss"] == claims["sub"] == APP_ID and claims["aud"] == call["url"]
        signed = f"{form['client_assertion'][0].rsplit('.', 1)[0]}".encode()
        certificate.public_key().verify(_b64decode(signature), signed, padding.PKCS1v15(), hashes.SHA256())

    def test_the_token_is_kept_while_it_is_good(self, app, microsoft):
        _connected()

        microsoft_mail.token()
        microsoft_mail.token()

        assert len(microsoft.sign_ins()) == 1

    def test_what_microsoft_says_is_said_plainly(self, app, microsoft):
        _connected()
        microsoft.refuse_sign_in = "AADSTS700016"

        ok, message = microsoft_mail.check()

        assert not ok and message == "There is no app with this application (client) ID in that tenant."

    def test_a_tenant_and_an_app_id_are_checked(self, app):
        with pytest.raises(microsoft_mail.ValidationError):
            microsoft_mail.save_app(None, tenant_id=TENANT, client_id="not-an-id")
        with pytest.raises(microsoft_mail.ValidationError):
            microsoft_mail.save_app(None, tenant_id="has spaces", client_id=APP_ID)


class TestSending:
    def test_the_same_email_goes_out_whole_through_graph(self, app, microsoft):
        _connected()
        _mailbox()

        sent, error = send_mail(from_account="support", to_email="anna@example.com", subject="Hello",
                                body="<p>Hi Anna</p>", bcc_emails=["support@example.org"],
                                headers={"List-Unsubscribe": "<https://members.test/u/1>"}, return_error=True)

        assert sent and error is None
        [call] = microsoft.graph_calls("POST")
        assert call["path"] == "/v1.0/users/support@example.org/sendMail"
        assert call["headers"]["Content-Type"] == "text/plain" and call["headers"]["Authorization"] == "Bearer token-1"
        mime = email.message_from_bytes(base64.b64decode(call["data"]))
        assert mime["Subject"] == "Hello" and mime["To"] == "anna@example.com"
        assert mime["From"] == "Joanneum Aeronautics <support@example.org>"
        assert mime["Bcc"] == "support@example.org" and mime["List-Unsubscribe"] == "<https://members.test/u/1>"
        assert mime["Message-ID"].endswith("@example.org>")

    def test_a_mailbox_the_app_may_not_use_says_where_to_allow_it(self, app, microsoft):
        _connected()
        _mailbox()
        microsoft.answer("POST", "/users/support@example.org/sendMail", 403,
                         {"error": {"code": "ErrorAccessDenied", "message": "Access is denied."}})

        sent, error = send_mail(from_account="support", to_email="anna@example.com", subject="Hello",
                                body="<p>Hi</p>", return_error=True)

        assert not sent and "may not" not in error and "does not let the app send from support@example.org" in error

    def test_a_test_email_that_is_refused_says_why(self, client, microsoft):
        _connected()
        _mailbox()
        microsoft.answer("POST", "/users/support@example.org/sendMail", 403,
                         {"error": {"code": "ErrorAccessDenied", "message": "Access is denied."}})
        _boss(client)

        answer = send(client, "POST", "/api/v1/admin/settings/test-email",
                      {"sender": "support", "recipient": "anna@example.com", "template": "test_email.html"}).get_json()

        assert answer["ok"] is False and "does not let the app send from support@example.org" in answer["error"]

    def test_a_token_from_before_the_permission_was_granted_is_replaced(self, app, microsoft):
        """Microsoft's token carries the permissions of when it was issued, and is kept an hour."""
        _connected()
        _mailbox()
        microsoft_mail.token()
        microsoft.stale_token = "token-1"

        sent, error = send_mail(from_account="support", to_email="anna@example.com", subject="Hello",
                                body="<p>Hi</p>", return_error=True)

        assert sent and error is None
        assert [call["headers"]["Authorization"] for call in microsoft.graph_calls("POST")] == [
            "Bearer token-1", "Bearer token-2"]

    def test_checking_the_sign_in_says_what_entra_granted(self, app, microsoft):
        _connected()
        microsoft.roles = ["Mail.Send", "Mail.Read", "User.Read.All"]
        assert microsoft_mail.check() == (
            True, "Signed in to Microsoft. Granted in Entra for every mailbox: Mail.Read, Mail.Send.")

        microsoft.roles = []
        ok, message = microsoft_mail.check()
        assert ok and "No mail permission granted in Entra" in message

    def test_checking_the_sign_in_starts_with_a_new_token(self, app, microsoft):
        _connected()
        microsoft_mail.token()
        signed_in = len(microsoft.sign_ins())

        microsoft_mail.check()
        microsoft_mail.token()

        assert len(microsoft.sign_ins()) == signed_in + 2

    def test_not_set_up_yet_is_an_error_not_a_crash(self, app, microsoft):
        _mailbox()

        sent, error = send_mail(from_account="support", to_email="anna@example.com", subject="Hello",
                                body="<p>Hi</p>", return_error=True)

        assert not sent and "not set up" in error and not microsoft.calls


class TestTheSettingsPage:
    def test_a_microsoft_account_needs_only_its_mailbox(self, client, microsoft):
        _boss(client)

        response = send(client, "POST", f"{API}/accounts",
                        {"key": "noreply", "kind": "microsoft", "from_email": "NoReply@Example.org",
                         "from_name": "Joanneum Aeronautics"})

        assert response.status_code == 201, response.get_json()
        added = response.get_json()
        assert (added["kind"], added["from_email"], added["host"], added["username"]) == (
            "microsoft", "noreply@example.org", None, None)
        assert mail_senders.address_of("noreply") == "noreply@example.org"

    def test_without_its_mailbox_it_is_refused(self, client, microsoft):
        _boss(client)

        response = send(client, "POST", f"{API}/accounts", {"key": "noreply", "kind": "microsoft"})

        assert response.status_code == 400 and "from_email" in response.get_json()["error"]["fields"]

    def test_both_kinds_side_by_side_each_a_sender(self, client, microsoft):
        _boss(client)
        send(client, "POST", f"{API}/accounts", {"key": "brevo", "host": "smtp-relay.brevo.com", "port": 587,
                                                 "username": "u-1", "password": "x", "from_email": "news@example.org"})
        send(client, "POST", f"{API}/accounts", {"key": "support", "kind": "microsoft",
                                                 "from_email": "support@example.org"})

        listed = client.get(API).get_json()

        assert [(row["key"], row["kind"]) for row in listed["accounts"]] == [("brevo", "smtp"), ("support", "microsoft")]
        assert sorted(settings_sections.sender_accounts()) == ["brevo", "support"]

    def test_the_connection_from_nothing_to_signed_in(self, client, microsoft):
        _boss(client)
        assert client.get(API).get_json()["microsoft"] == {"tenant_id": None, "client_id": None, "certificate": None,
                                                           "new_certificate": None, "ready": False}

        saved = send(client, "PUT", f"{API}/microsoft", {"tenant_id": TENANT, "client_id": APP_ID.upper()})
        made = send(client, "POST", f"{API}/microsoft/certificate")
        file = client.get(f"{API}/microsoft/certificate?which=current")
        checked = send(client, "POST", f"{API}/microsoft/check")

        assert saved.status_code == 200 and saved.get_json()["client_id"] == APP_ID
        assert made.status_code == 201 and made.get_json()["ready"] is True
        assert made.get_json()["certificate"]["thumbprint"] == microsoft_mail.connection().thumbprint
        assert file.status_code == 200 and b"BEGIN CERTIFICATE" in file.data
        assert "attachment" in file.headers["Content-Disposition"]
        assert checked.get_json()["ok"] is True
        logged = {entry.event_type for entry in db.session.query(AuditLog)}
        assert {"microsoft_mail_app_saved", "microsoft_mail_certificate_made"} <= logged
        assert "PRIVATE KEY" not in json.dumps([entry.after_state for entry in db.session.query(AuditLog)])

    def test_a_new_certificate_can_be_dropped(self, client, microsoft):
        _boss(client)
        _connected()
        send(client, "POST", f"{API}/microsoft/certificate")

        dropped = send(client, "DELETE", f"{API}/microsoft/certificate/new")

        assert dropped.get_json()["new_certificate"] is None and dropped.get_json()["certificate"] is not None

    def test_the_test_button_signs_in_and_reads_without_sending(self, client, microsoft):
        _boss(client)
        _connected()
        account = _mailbox()

        result = send(client, "POST", f"{API}/accounts/{account.id}/test").get_json()

        assert result["ok"] and "can read the mailbox" in result["message"]
        assert not microsoft.graph_calls("POST")

    def test_exported_and_imported_without_a_password(self, client, microsoft):
        boss = _boss(client)
        _mailbox()

        exported = send(client, "POST", f"{API}/export", {"password": "current-password"}).get_json()
        [row] = exported["mail_accounts"]
        assert row["kind"] == "microsoft" and row["password"] is None
        db.session.query(MailAccount).delete()
        db.session.commit()

        from aeronautics_members.services import mail_accounts

        found = mail_accounts.import_file(boss, json.dumps(exported).encode(), overwrite=False)

        assert found["created"] == 1
        account = db.session.query(MailAccount).one()
        assert account.is_microsoft and account.from_email == "support@example.org"


# --- Replies -----------------------------------------------------------------------------


@pytest.fixture
def mailboxes(app, microsoft):
    _connected()
    support = _mailbox()
    _mailbox("noreply", "noreply@example.org")
    settings_sections.save_senders(None, default="noreply", welcome="", notifications="", mailings="",
                                   replies="support", reply_copy=True)
    db.session.commit()
    return support


@pytest.fixture
def answered(app, admins, mailboxes, microsoft):  # noqa: F811
    """Anna's message, answered from Admin › Messages through the support mailbox."""
    anna = _association_member("anna@example.com")
    message = messages.send_contact(user=anna, topic=FORM["topic"], subject=FORM["subject"], body=FORM["message"],
                                    seconds=FORM["seconds"])
    answer = messages.reply(admins[0], message, "It was paid on Monday.")
    db.session.commit()
    return message, answer


def _incoming(internet_id, *, replying_to=None, graph_id="AAMk-1", attachments=False):
    headers = []
    if replying_to:
        headers = [{"name": "In-Reply-To", "value": replying_to}, {"name": "References", "value": replying_to}]
    return {"id": graph_id, "internetMessageId": internet_id, "internetMessageHeaders": headers,
            # A minute after the answer it replies to.
            "receivedDateTime": f"{datetime.utcnow() + timedelta(minutes=1):%Y-%m-%dT%H:%M:%SZ}",
            "hasAttachments": attachments}


class TestTheAnswerGoesOut:
    def test_with_an_id_of_its_own_kept_for_the_reply(self, app, answered, microsoft):
        _message, answer = answered

        mime = email.message_from_bytes(base64.b64decode(microsoft.graph_calls("POST")[-1]["data"]))

        assert answer.email_message_id and mime["Message-ID"] == answer.email_message_id
        assert mime["Bcc"] == "support@example.org"


class TestWatchingTheMailbox:
    def test_saving_the_senders_subscribes_to_the_replies_inbox(self, client, mailboxes, microsoft):
        _boss(client)

        send(client, "PUT", "/api/v1/admin/settings/email/senders",
             {"default": "noreply", "welcome": "", "notifications": "", "mailings": "", "replies": "support",
              "reply_copy": True})

        [made] = [call for call in microsoft.graph_calls("POST") if call["path"] == "/v1.0/subscriptions"]
        body = json.loads(made["data"])
        assert body["notificationUrl"] == "https://members.test/webhooks/microsoft-mail"
        assert body["resource"] == "users/support@example.org/mailFolders('inbox')/messages"
        assert body["changeType"] == "created" and len(body["clientState"]) > 30
        assert mailboxes.inbox_client_state == body["clientState"]

    def test_renewed_before_it_runs_out_and_dropped_when_no_longer_wanted(self, app, mailboxes, microsoft):
        microsoft.answer("POST", "/subscriptions", 201, {"id": "sub-1", "expirationDateTime": "2026-10-22T00:00:00Z"})
        mail_replies.keep_watching(datetime(2026, 10, 16))
        assert mailboxes.inbox_subscription_id == "sub-1"

        mail_replies.keep_watching(datetime(2026, 10, 18))
        assert not microsoft.graph_calls("PATCH")
        mail_replies.keep_watching(datetime(2026, 10, 20, 12))
        [renewed] = microsoft.graph_calls("PATCH")
        assert renewed["path"] == "/v1.0/subscriptions/sub-1"

        settings_sections.save_senders(None, default="noreply", welcome="", notifications="", mailings="",
                                       replies="noreply", reply_copy=False)
        mail_replies.keep_watching(datetime(2026, 10, 21))
        assert [call["path"] for call in microsoft.graph_calls("DELETE")] == ["/v1.0/subscriptions/sub-1"]
        assert mailboxes.inbox_subscription_id is None

    def test_graph_checking_the_address_gets_its_token_back(self, client):
        response = client.post("/webhooks/microsoft-mail?validationToken=abc%20123")

        assert response.status_code == 200 and response.data == b"abc 123"
        assert response.mimetype == "text/plain"


class TestARepliesComesBack:
    def _notify(self, client, account, graph_id="AAMk-1", state=None):
        return client.post("/webhooks/microsoft-mail", json={"value": [{
            "subscriptionId": account.inbox_subscription_id, "clientState": state or account.inbox_client_state,
            "changeType": "created", "resourceData": {"id": graph_id}}]})

    def _subscribed(self, account, microsoft):
        microsoft.answer("POST", "/subscriptions", 201, {"id": "sub-1", "expirationDateTime": "2026-10-22T00:00:00Z"})
        mail_replies.keep_watching()
        db.session.commit()

    def test_into_the_thread_and_the_message_is_open_again(self, client, answered, mailboxes, microsoft):
        message, answer = answered
        self._subscribed(mailboxes, microsoft)
        microsoft.answer("GET", "/users/support@example.org/messages/AAMk-1",
                         body={**_incoming("<r1@anna.example>", replying_to=answer.email_message_id),
                               "uniqueBody": {"content": "Thanks, all good.\r\n"}})
        assert message.done_at is not None

        response = self._notify(client, mailboxes)

        assert response.status_code == 202
        reply = db.session.query(ContactReply).filter_by(incoming=True).one()
        assert (reply.body, reply.message_id, reply.author_user_id) == ("Thanks, all good.", message.id, None)
        assert reply.email_message_id == "<r1@anna.example>" and reply.account_key == "support"
        assert db.session.get(type(message), message.id).done_at is None
        [fetch, *_] = [call for call in microsoft.graph_calls("GET") if "uniqueBody" in call["query"]]
        assert fetch["headers"]["Prefer"] == 'outlook.body-content-type="text"'

    def test_shown_in_admin_messages_as_theirs(self, client, answered, mailboxes, microsoft, admins):  # noqa: F811
        message, answer = answered
        microsoft.answer("GET", "/users/support@example.org/messages/AAMk-1",
                         body={**_incoming("<r1@anna.example>", replying_to=answer.email_message_id,
                                           attachments=True), "uniqueBody": {"content": "See attached."}})
        mail_replies.take_notified(mailboxes.id, "AAMk-1")
        db.session.commit()
        signed_in(client, admins[0])

        [shown] = client.get("/api/v1/admin/messages?state=all").get_json()["items"]

        assert [(row["incoming"], row["has_attachments"]) for row in shown["replies"]] == [(False, False),
                                                                                        (True, True)]
        assert shown["done_at"] is None

    def test_a_notification_without_the_secret_is_ignored(self, client, answered, mailboxes, microsoft):
        self._subscribed(mailboxes, microsoft)

        self._notify(client, mailboxes, state="guessed")

        assert not [call for call in microsoft.graph_calls("GET") if "/messages/" in call["path"]]
        assert not db.session.query(ContactReply).filter_by(incoming=True).count()

    def test_other_mail_and_its_own_copy_are_left_alone(self, app, answered, mailboxes, microsoft):
        _message, answer = answered
        for graph_id, item in (("AAMk-2", _incoming("<news@else.example>", graph_id="AAMk-2")),
                               ("AAMk-3", _incoming(answer.email_message_id, graph_id="AAMk-3"))):
            microsoft.answer("GET", f"/users/support@example.org/messages/{graph_id}", body=item)
            assert mail_replies.take_notified(mailboxes.id, graph_id) is None

        assert not db.session.query(ContactReply).filter_by(incoming=True).count()

    def test_taken_once_even_when_told_twice(self, app, answered, mailboxes, microsoft):
        _message, answer = answered
        microsoft.answer("GET", "/users/support@example.org/messages/AAMk-1",
                         body={**_incoming("<r1@anna.example>", replying_to=answer.email_message_id),
                               "uniqueBody": {"content": "Thanks"}})

        mail_replies.take_notified(mailboxes.id, "AAMk-1")
        mail_replies.take_notified(mailboxes.id, "AAMk-1")

        assert db.session.query(ContactReply).filter_by(incoming=True).count() == 1

    def test_its_own_copy_is_left_alone_whatever_its_id(self, app, answered, mailboxes, microsoft):
        """Exchange may give the copy a Message-ID of its own; it is from the portal's mailbox still."""
        _message, answer = answered
        copy = {**_incoming("<exchange-made@example.org>", replying_to=answer.email_message_id),
                "from": {"emailAddress": {"address": "Support@example.org"}}}
        microsoft.answer("GET", "/users/support@example.org/messages/AAMk-1", body=copy)

        assert mail_replies.take_notified(mailboxes.id, "AAMk-1") is None
        assert not db.session.query(ContactReply).filter_by(incoming=True).count()

    def test_the_answers_id_is_kept_before_it_is_sent(self, app, admins, mailboxes, microsoft, monkeypatch):  # noqa: F811
        """Graph's notification of the copy can come before the request that sent it has ended."""
        from sqlalchemy import text

        seen = []
        sending = messages.send_mail

        def looking(**kwargs):
            with db.engine.connect() as other:
                seen.extend(other.execute(text("SELECT email_message_id FROM contact_replies")).scalars())
            return sending(**kwargs)

        monkeypatch.setattr(messages, "send_mail", looking)
        anna = _association_member("anna@example.com")
        message = messages.send_contact(user=anna, topic=FORM["topic"], subject=FORM["subject"],
                                        body=FORM["message"], seconds=FORM["seconds"])

        answer = messages.reply(admins[0], message, "It was paid on Monday.")

        assert seen == [answer.email_message_id]

    def test_taken_once_when_both_ways_take_it_at_the_same_moment(self, app, answered, mailboxes, microsoft,
                                                                monkeypatch):
        from sqlalchemy import text

        _message, answer = answered
        microsoft.answer("GET", "/users/support@example.org/messages/AAMk-1",
                         body={**_incoming("<r1@anna.example>", replying_to=answer.email_message_id),
                               "uniqueBody": {"content": "Thanks"}})
        finding = mail_replies._thread_of

        def meanwhile_the_other_took_it(item):
            found = finding(item)
            with db.engine.begin() as other:
                other.execute(text("INSERT INTO contact_replies (message_id, body, account_key, created_at, "
                                   "attempts, incoming, email_message_id, has_attachments) VALUES "
                                   "(:message, 'Thanks', 'support', CURRENT_TIMESTAMP, 0, 1, '<r1@anna.example>', 0)"),
                              {"message": found.id})
            return found

        monkeypatch.setattr(mail_replies, "_thread_of", meanwhile_the_other_took_it)

        assert mail_replies.take_notified(mailboxes.id, "AAMk-1") is None
        db.session.commit()
        assert db.session.query(ContactReply).filter_by(incoming=True).count() == 1

    def test_one_missed_is_found_by_the_look_through(self, app, answered, mailboxes, microsoft):
        _message, answer = answered
        microsoft.answer("GET", "/users/support@example.org/mailFolders/inbox/messages", body={"value": [
            _incoming("<news@else.example>", graph_id="AAMk-2"),
            _incoming("<r1@anna.example>", replying_to=answer.email_message_id),
        ]})
        microsoft.answer("GET", "/users/support@example.org/messages/AAMk-1", body={"uniqueBody": {"content": "Ok"}})

        assert mail_replies.run() == 1

        [look] = [call for call in microsoft.graph_calls("GET") if "mailFolders/inbox/messages" in call["path"]]
        assert "receivedDateTime ge" in look["query"]
        assert mailboxes.inbox_checked_at is not None

    def test_a_later_answer_continues_the_thread(self, app, answered, mailboxes, microsoft, admins):  # noqa: F811
        message, answer = answered
        microsoft.answer("GET", "/users/support@example.org/messages/AAMk-1",
                         body={**_incoming("<r1@anna.example>", replying_to=answer.email_message_id),
                               "uniqueBody": {"content": "One more question."}})
        mail_replies.take_notified(mailboxes.id, "AAMk-1")

        messages.reply(admins[0], message, "Here is the answer.")

        mime = email.message_from_bytes(base64.b64decode(microsoft.graph_calls("POST")[-1]["data"]))
        assert mime["In-Reply-To"] == "<r1@anna.example>"
        assert answer.email_message_id in mime["References"]

    def test_their_replies_are_in_their_data_export(self, app, answered, mailboxes, microsoft):
        message, answer = answered
        microsoft.answer("GET", "/users/support@example.org/messages/AAMk-1",
                         body={**_incoming("<r1@anna.example>", replying_to=answer.email_message_id),
                               "uniqueBody": {"content": "Thanks"}})
        mail_replies.take_notified(mailboxes.id, "AAMk-1")

        [sent] = messages.sent_by(message.sender)

        assert [row["answer"] for row in sent["answers"]] == ["It was paid on Monday."]
        assert [row["reply"] for row in sent["your_replies"]] == ["Thanks"]


def test_the_connection_is_one_row(app, microsoft):
    _connected()
    _connected()
    assert db.session.query(MicrosoftConnection).count() == 1


class TestSystemHealth:
    def _health(self):
        from aeronautics_members.services.diagnostics import collect_system_health

        return collect_system_health(check_pages=False)

    def test_quiet_without_microsoft_accounts(self, app, microsoft):
        health = self._health()
        assert not [line for line in health["problems"] + health["warnings"] if "Microsoft" in line]

    def test_accounts_without_the_connection_are_a_problem(self, app, microsoft):
        _mailbox()
        assert any("not set up" in line for line in self._health()["problems"])

    def test_a_certificate_running_out_is_a_warning_then_a_problem(self, app, microsoft):
        found = _connected()
        _mailbox()
        found.certificate_expires_at = datetime.utcnow() + timedelta(days=10)
        db.session.commit()
        assert any("runs out on" in line for line in self._health()["warnings"])

        found.certificate_expires_at = datetime.utcnow() - timedelta(days=1)
        db.session.commit()
        assert any("has run out" in line for line in self._health()["problems"])

    def test_replies_without_notifications_are_a_warning(self, app, mailboxes, microsoft):
        assert any("every 15 minutes only" in line for line in self._health()["warnings"])


class TestAMailboxAddress:
    def test_is_quoted_into_graphs_paths(self):
        assert microsoft_mail.mailbox_path("support@example.org") == "/users/support@example.org"
        assert microsoft_mail.mailbox_path("a/../../me?x@example.org") == "/users/a%2F..%2F..%2Fme%3Fx@example.org"

    @pytest.mark.parametrize("address", ["a/b@example.org", "a?b@example.org", "a#b@example.org", "a%2f@example.org"])
    def test_with_what_could_mean_something_else_is_refused(self, app, address):
        from aeronautics_members.services import ValidationError, mail_accounts

        with pytest.raises(ValidationError):
            mail_accounts.save(None, None, key="odd", from_email=address, kind=MailAccount.MICROSOFT)
