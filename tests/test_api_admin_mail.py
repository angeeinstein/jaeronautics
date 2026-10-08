"""Settings -> Mail accounts and the test email (api/admin_mail.py,
services/mail_accounts.py; the pages are frontend/src/pages/admin/settings/).

The passwords never reach the browser but in an export, which asks for the
current password; every change is in the log, without them.
"""
import io
import json

from api_helpers import send, signed_in
from conftest import app_module, db
from aeronautics_members.db_models import AuditLog, MailAccount, Setting
from test_admin_reviews import _staff

API = "/api/v1/admin/settings/mail"
OFFICE = {"key": "office", "host": "smtp.example.org", "port": 587, "username": "office@example.org",
          "password": "first", "starttls": True}


def _boss(client):
    boss = _staff("boss@example.org", "superadmin")
    boss.set_password("current-password")
    db.session.commit()
    signed_in(client, boss)
    return boss


def _add(client, **fields):
    response = send(client, "POST", f"{API}/accounts", {**OFFICE, **fields})
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def test_only_with_the_credentials_permission(client):
    signed_in(client, _staff("plain@example.org", "admin"))

    assert client.get(API).status_code == 403
    assert send(client, "POST", f"{API}/accounts", OFFICE).status_code == 403


class TestAccounts:
    def test_added_listed_without_its_password_and_logged_without_it(self, client):
        _boss(client)

        added = _add(client)

        assert added["key"] == "office" and "password" not in added
        assert client.get(API).get_json()["accounts"] == [added]
        entry = db.session.query(AuditLog).filter_by(event_type="mail_account_created").one()
        assert "first" not in json.dumps([entry.before_state, entry.after_state, entry.event_metadata])

    def test_a_new_one_needs_its_password(self, client):
        _boss(client)

        response = send(client, "POST", f"{API}/accounts", {**OFFICE, "password": None})

        assert response.status_code == 400 and "password" in response.get_json()["error"]["fields"]

    def test_changed_with_an_empty_password_keeps_it(self, client):
        _boss(client)
        account_id = _add(client)["id"]

        send(client, "PUT", f"{API}/accounts/{account_id}", {**OFFICE, "host": "smtp2.example.org", "password": None})

        account = db.session.get(MailAccount, account_id)
        assert account.host == "smtp2.example.org" and account.password == "first"

    def test_a_key_that_is_taken(self, client):
        _boss(client)
        _add(client)
        other = _add(client, key="noreply")

        response = send(client, "PUT", f"{API}/accounts/{other['id']}", {**OFFICE, "key": "office"})

        assert response.status_code == 409 and response.get_json()["error"]["fields"] == {
            "key": "A mail account with this key already exists."}

    def test_a_key_with_spaces(self, client):
        _boss(client)

        response = send(client, "POST", f"{API}/accounts", {**OFFICE, "key": "the office"})

        assert response.status_code == 400 and "key" in response.get_json()["error"]["fields"]

    def test_removing_the_welcome_emails_sender_says_so(self, client):
        _boss(client)
        account_id = _add(client)["id"]
        app_module.set_setting_value("welcome_email_sender", "office")
        db.session.commit()

        body = send(client, "DELETE", f"{API}/accounts/{account_id}").get_json()

        assert body == {"removed_welcome_sender": True}
        assert db.session.get(Setting, "welcome_email_sender") is None
        assert db.session.get(MailAccount, account_id) is None

    def test_the_connection_test_says_how_it_went_and_is_logged(self, client, monkeypatch):
        from aeronautics_members import mail_utils

        monkeypatch.setattr(mail_utils, "probe_mail_account_connection",
                            lambda config: (False, "Authentication failed."))
        _boss(client)
        account_id = _add(client)["id"]

        body = send(client, "POST", f"{API}/accounts/{account_id}/test").get_json()

        assert body == {"ok": False, "message": "Authentication failed."}
        assert db.session.query(AuditLog).filter_by(event_type="mail_account_connection_tested").count() == 1


class TestImportAndExport:
    def _import(self, client, payload, overwrite=False):
        data = {"file": (io.BytesIO(json.dumps(payload).encode()), "accounts.json")}
        return client.post(f"{API}/import?overwrite={'true' if overwrite else 'false'}", data=data,
                           content_type="multipart/form-data")

    def test_the_export_asks_for_the_current_password(self, client):
        _boss(client)
        _add(client)

        refused = send(client, "POST", f"{API}/export", {"password": "wrong"})
        exported = send(client, "POST", f"{API}/export", {"password": "current-password"})

        assert refused.status_code == 403 and "password" in refused.get_json()["error"]["fields"]
        assert exported.mimetype == "application/json"
        assert "attachment" in exported.headers["Content-Disposition"]
        assert exported.get_json()["mail_accounts"][0]["password"] == "first"

    def test_what_is_exported_imports_again_keeping_what_is_there(self, client):
        _boss(client)
        _add(client)
        exported = send(client, "POST", f"{API}/export", {"password": "current-password"}).get_json()
        exported["mail_accounts"].append({**exported["mail_accounts"][0], "account_key": "noreply"})

        body = self._import(client, exported).get_json()

        assert body == {"created": 1, "updated": 0, "skipped": ["office"]}
        assert {account.account_key for account in db.session.query(MailAccount)} == {"office", "noreply"}

    def test_replacing_what_is_there_when_asked(self, client):
        _boss(client)
        _add(client)

        body = self._import(client, {"mail_accounts": [{**OFFICE, "account_key": "office", "host": "new.example"}]},
                            overwrite=True).get_json()

        assert body == {"created": 0, "updated": 1, "skipped": []}

    def test_a_file_that_is_no_json(self, client):
        _boss(client)

        response = client.post(f"{API}/import", data={"file": (io.BytesIO(b"not json"), "a.json")},
                               content_type="multipart/form-data")

        assert response.status_code == 400 and response.get_json()["error"]["fields"] == {
            "file": "The file is not valid JSON."}


class TestTheTestEmail:
    def test_what_it_can_be_sent_from_and_with(self, client):
        _boss(client)

        body = client.get("/api/v1/admin/settings/test-email").get_json()

        assert "welcome_email.html" in body["templates"] and isinstance(body["senders"], list)

    def test_a_template_that_does_not_exist(self, client):
        _boss(client)

        response = send(client, "POST", "/api/v1/admin/settings/test-email",
                        {"sender": "office", "recipient": "me@example.org", "template": "nope.html"})

        assert response.status_code == 400 and set(response.get_json()["error"]["fields"]) <= {"sender", "template"}

    def test_only_for_whoever_may_manage_notifications(self, client):
        signed_in(client, _staff("money@example.org", "treasurer"))

        assert client.get("/api/v1/admin/settings/test-email").status_code == 403


def test_the_pages_are_the_apps(client):
    _boss(client)

    assert client.get("/admin/settings/mail").status_code == 200
    assert client.get("/admin/settings/test-email").status_code == 200
