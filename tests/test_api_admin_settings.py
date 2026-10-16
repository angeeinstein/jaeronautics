"""Settings, section by section (api/admin_settings.py, services/settings.py;
the pages are frontend/src/pages/admin/settings/).

Each section is saved on its own and changes only its own settings; a save
that changes something is one log entry, secrets only as configured or
changed; the secrets themselves never leave the server.
"""
from api_helpers import send, signed_in
from conftest import app_module, db
from aeronautics_members.db_models import AuditLog, MailAccount, Setting
from test_admin_reviews import _staff

API = "/api/v1/admin/settings"
GENERAL = {"invoice_payments": False}
SENDING = {"automatic_emails": False, "legal_pdfs_in_welcome_emails": False, "automatic_email_template": None,
           "admin_general": True, "admin_error": True, "user_status": True, "per_hour": 100, "per_day": 300}


def _stored(key):
    setting = db.session.get(Setting, key)
    return setting.value if setting is not None else None


def _superadmin(client):
    signed_in(client, _staff("boss@example.org", "superadmin"))


def test_signed_out(client):
    assert client.get(f"{API}/general").status_code == 401


class TestGeneral:
    def test_read_with_the_choices_and_the_domains_in_force(self, client):
        _superadmin(client)

        body = client.get(f"{API}/general").get_json()

        assert body["invoice_payments"] is False and body["institutional_email_domains"] is None
        assert "edu.fh-joanneum.at" in body["domains_in_use"]
        assert "welcome_email_sender" not in body, "email is on Settings › Email"

    def test_saved_and_logged_once_with_what_changed(self, client):
        _superadmin(client)

        body = send(client, "PUT", f"{API}/general", {**GENERAL, "invoice_payments": True}).get_json()

        assert "invoice_payments_enabled" in body["changed"]
        assert _stored("invoice_payments_enabled") == "True"
        entry = db.session.query(AuditLog).filter_by(event_type="settings_updated").one()
        assert entry.event_metadata["section"] == "general"

    def test_saved_again_unchanged_changes_and_logs_nothing(self, client):
        _superadmin(client)
        send(client, "PUT", f"{API}/general", GENERAL)
        before = db.session.query(AuditLog).filter_by(event_type="settings_updated").count()

        body = send(client, "PUT", f"{API}/general", GENERAL).get_json()

        assert body == {"changed": []}
        assert db.session.query(AuditLog).filter_by(event_type="settings_updated").count() == before

    def test_it_touches_no_other_section(self, client):
        app_module.set_setting_value("notification_admin_error_enabled", "False")
        app_module.set_setting_value("stripe_price_id", "price_kept")
        db.session.commit()
        _superadmin(client)

        send(client, "PUT", f"{API}/general", GENERAL)

        assert _stored("notification_admin_error_enabled") == "False" and _stored("stripe_price_id") == "price_kept"


def _accounts():
    db.session.add_all([
        MailAccount(account_key="office", host="h", port=465, username="office@example.org", password="p"),
        MailAccount(account_key="support", host="h", port=465, username="u-1", password="p",
                    from_email="support@example.org"),
    ])
    db.session.commit()


class TestEmailSenders:
    def test_a_default_and_each_kind_empty_is_the_default(self, app, client):
        from aeronautics_members.services import mail_senders

        _accounts()
        _superadmin(client)
        body = client.get(f"{API}/email/senders").get_json()
        assert (body["default"], body["default_in_use"], body["accounts"]) == ("", "office", ["office", "support"])

        saved = send(client, "PUT", f"{API}/email/senders", {"default": "office", "replies": "support",
                                                               "reply_copy": True})

        assert saved.status_code == 200, saved.get_json()
        assert [mail_senders.account_for(kind) for kind in ("welcome", "notifications", "mailings", "replies")] == [
            "office", "office", "office", "support"]
        body = client.get(f"{API}/email/senders").get_json()
        assert (body["replies"], body["reply_copy"], body["reply_address"]) == ("support", True, "support@example.org")
        entry = db.session.query(AuditLog).filter_by(event_type="settings_updated").one()
        assert entry.event_metadata["section"] == "email_senders"

    def test_an_account_that_does_not_exist_is_said_at_its_field(self, client):
        _accounts()
        _superadmin(client)

        response = send(client, "PUT", f"{API}/email/senders", {"welcome": "nobody"})

        assert response.status_code == 400 and "welcome" in response.get_json()["error"]["fields"]

    def test_an_account_removed_hands_what_it_sent_to_the_default(self, app):
        from aeronautics_members.services import mail_senders, settings_sections

        _accounts()
        settings_sections.save_senders(None, default="office", welcome="support", notifications="",
                                       mailings="support", replies="", reply_copy=False)
        db.session.commit()

        assert mail_senders.forget_account("support") == ["welcome", "mailings"]
        db.session.commit()
        assert mail_senders.account_for("welcome") == "office"


class TestEmailSending:
    def test_the_welcome_email_the_notifications_and_the_pace_saved_together(self, client):
        _superadmin(client)

        saved = send(client, "PUT", f"{API}/email/sending",
                     {**SENDING, "automatic_emails": True, "admin_error": False, "per_hour": 50})

        assert saved.status_code == 200, saved.get_json()
        body = client.get(f"{API}/email/sending").get_json()
        assert (body["automatic_emails"], body["admin_error"], body["per_hour"], body["per_day"]) == (
            True, False, 50, 300)
        assert [channel["channel"] for channel in body["health"]] == ["admin_general", "admin_error", "user_status"]
        assert body["health"][1]["enabled"] is False
        assert isinstance(body["templates"], list)

    def test_the_pace_checked(self, client):
        _superadmin(client)

        response = send(client, "PUT", f"{API}/email/sending", {**SENDING, "per_hour": 500, "per_day": 300})

        assert response.status_code == 400 and "per_hour" in response.get_json()["error"]["fields"]


class TestBilling:
    def test_a_secret_left_empty_stays_and_a_new_one_is_logged_as_changed(self, client):
        app_module.set_setting_value("stripe_secret_key", "sk_test_old")
        db.session.commit()
        _superadmin(client)

        send(client, "PUT", f"{API}/billing", {"publishable_key": "pk_test_1"})
        assert _stored("stripe_secret_key") == "sk_test_old"

        send(client, "PUT", f"{API}/billing", {"publishable_key": "pk_test_1", "secret_key": "sk_test_new"})

        assert _stored("stripe_secret_key") == "sk_test_new"
        entry = db.session.query(AuditLog).filter_by(event_type="settings_updated").order_by(AuditLog.id.desc()).first()
        assert entry.event_metadata["changed_keys"] == ["stripe_secret_key"]
        assert "sk_test" not in str(entry.before_state) + str(entry.after_state), "the secret itself is never logged"


class TestForum:
    def test_a_box_per_kind_and_the_secrets_kept(self, client):
        app_module.set_setting_value("discourse_api_key", "kept-key")
        db.session.commit()
        _superadmin(client)

        send(client, "PUT", f"{API}/forum", {
            "enabled": False, "manage_staff_flags": False, "base_url": " https://forum.example.org ",
            "category_groups": {"student": "students", "staff": ""}, "avatar_allowed_types": ["JPG", ".png"],
        })

        body = client.get(f"{API}/forum").get_json()
        assert body["base_url"] == "https://forum.example.org" and body["api_key_set"] is True
        assert {item["kind"]: item["group"] for item in body["category_groups"]}["student"] == "students"
        assert body["avatar_allowed_types"] == ["jpg", "png"]
        assert _stored("discourse_api_key") == "kept-key"
        endpoints = body["endpoints"]
        assert all(endpoints[name].startswith("http") for name in ("entry", "connect", "logout"))

    def test_a_size_that_is_no_size(self, client):
        _superadmin(client)

        response = send(client, "PUT", f"{API}/forum", {"enabled": False, "manage_staff_flags": False,
                                                         "avatar_max_bytes": 0})

        assert response.status_code == 400 and "avatar_max_bytes" in response.get_json()["error"]["fields"]

    def test_the_connection_test_says_how_it_went_and_is_logged(self, client):
        _superadmin(client)

        body = send(client, "POST", f"{API}/forum/test").get_json()

        assert body["ok"] is False and body["message"]
        assert db.session.query(AuditLog).filter_by(event_type="forum_connection_tested").count() == 1


def test_the_pages_are_the_apps(client):
    _superadmin(client)

    for section in ("general", "email", "billing", "forum"):
        assert client.get(f"/admin/settings/{section}").status_code == 200
    # The pages Settings › Email took over lead to its tab.
    for old, tab in (("notifications", "sending"), ("mailings", "sending"), ("mail", "accounts"),
                     ("test-email", "test")):
        response = client.get(f"/admin/settings/{old}")
        assert response.status_code == 302 and response.headers["Location"].endswith(f"/admin/settings/email#{tab}")
