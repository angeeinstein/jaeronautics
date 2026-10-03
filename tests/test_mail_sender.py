"""A mail account's sender, when it is not the login.

Most providers sign in with the address they send from. A relay such as Brevo
signs in with an account id of its own -- bc6766001@smtp-brevo.com -- and
refuses a message whose sender is that id: it must come from an address
verified with Brevo. So an account can name its sender, and a name to go with
it; without one, the login is the sender, as it always was.
"""
import email
import json

import pytest

from conftest import db
from aeronautics_members import mail_utils
from aeronautics_members.db_models import MailAccount
from test_admin_reviews import _login, _staff


class RecordingSMTP:
    sent = []
    logins = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, **k):
        pass

    def login(self, user, password):
        RecordingSMTP.logins.append(user)

    def sendmail(self, sender, recipients, raw):
        RecordingSMTP.sent.append((sender, recipients, email.message_from_string(raw)))


@pytest.fixture
def smtp(app, monkeypatch):
    RecordingSMTP.sent, RecordingSMTP.logins = [], []
    monkeypatch.setattr(mail_utils.smtplib, "SMTP", RecordingSMTP)
    monkeypatch.setattr(mail_utils.smtplib, "SMTP_SSL", RecordingSMTP)
    return RecordingSMTP


def _account(**fields):
    account = MailAccount(account_key="brevo", host="smtp-relay.brevo.com", port=587,
                          username="bc6766001@smtp-brevo.com", password="key", starttls=True, **fields)
    db.session.add(account)
    db.session.commit()
    return account


def _send():
    with mail_utils.current_app.test_request_context():
        return mail_utils.send_mail("brevo", "anna@example.com", "Hello", body="<p>Hi</p>", return_error=True)


def test_the_login_signs_in_and_the_sender_sends(app, smtp):
    _account(from_email="noreply@joanneum-aeronautics.at", from_name="Joanneum Aeronautics")

    assert _send() == (True, None)

    assert smtp.logins == ["bc6766001@smtp-brevo.com"]
    envelope_sender, _recipients, message = smtp.sent[-1]
    assert envelope_sender == "noreply@joanneum-aeronautics.at"
    assert message["From"] == "Joanneum Aeronautics <noreply@joanneum-aeronautics.at>"


def test_a_name_with_umlauts_is_encoded(app, smtp):
    _account(from_email="noreply@joanneum-aeronautics.at", from_name="Verein für Luftfahrt")

    _send()

    header = smtp.sent[-1][2]["From"]
    assert "noreply@joanneum-aeronautics.at" in header
    assert str(email.header.make_header(email.header.decode_header(header))).startswith("Verein für Luftfahrt")


def test_without_a_sender_the_login_sends_as_before(app, smtp):
    _account()

    _send()

    envelope_sender, _recipients, message = smtp.sent[-1]
    assert envelope_sender == message["From"] == "bc6766001@smtp-brevo.com"


class TestTheSettingsPage:
    def _admin(self, client):
        _login(client, _staff("super@example.com", "superadmin").id)

    def test_saved_and_shown(self, app, client):
        self._admin(client)

        client.post("/admin/settings/mail-accounts", data={
            "mail-account_key": "brevo", "mail-host": "smtp-relay.brevo.com", "mail-port": "587",
            "mail-username": "bc6766001@smtp-brevo.com", "mail-password": "key", "mail-starttls": "y",
            "mail-from_email": "NoReply@joanneum-aeronautics.at", "mail-from_name": "Joanneum Aeronautics",
        })

        account = db.session.query(MailAccount).filter_by(account_key="brevo").one()
        assert (account.from_email, account.from_name) == ("noreply@joanneum-aeronautics.at", "Joanneum Aeronautics")
        body = client.get("/admin/settings").get_data(as_text=True)
        assert "sends as Joanneum Aeronautics &lt;noreply@joanneum-aeronautics.at&gt;" in body

    def test_not_an_address_is_refused(self, app, client):
        self._admin(client)

        client.post("/admin/settings/mail-accounts", data={
            "mail-account_key": "brevo", "mail-host": "h", "mail-port": "587",
            "mail-username": "u", "mail-password": "p", "mail-from_email": "noreply at example",
        })

        assert db.session.query(MailAccount).count() == 0

    def test_travels_with_the_export_and_import(self, app):
        from aeronautics_members.services.notifications import (
            build_mail_accounts_export_payload,
            normalize_imported_mail_accounts_payload,
        )

        _account(from_email="noreply@joanneum-aeronautics.at", from_name="Joanneum Aeronautics")

        exported = build_mail_accounts_export_payload()
        [record] = normalize_imported_mail_accounts_payload(json.loads(json.dumps(exported)))

        assert (record["from_email"], record["from_name"]) == ("noreply@joanneum-aeronautics.at", "Joanneum Aeronautics")


def test_unused_names_stay_out_of_the_config(app):
    assert "from" not in _account().to_config()
