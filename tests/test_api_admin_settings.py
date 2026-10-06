"""Settings, section by section (api/admin_settings.py, services/settings.py;
the pages are frontend/src/pages/admin/settings/).

Each section is saved on its own and changes only its own settings; a save
that changes something is one log entry, secrets only as configured or
changed; the secrets themselves never leave the server.
"""
from api_helpers import send, signed_in
from conftest import app_module, db
from aeronautics_members.db_models import AuditLog, Setting
from test_admin_reviews import _staff

API = "/api/v1/admin/settings"
GENERAL = {"invoice_payments": False, "automatic_emails": False, "legal_pdfs_in_welcome_emails": False}


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
        assert isinstance(body["senders"], list) and isinstance(body["templates"], list)

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

    def test_a_sender_that_does_not_exist_is_said_at_its_field(self, client):
        _superadmin(client)

        response = send(client, "PUT", f"{API}/general", {**GENERAL, "welcome_email_sender": "nobody"})

        assert response.status_code == 400
        assert response.get_json()["error"]["fields"] == {"welcome_email_sender": "That sender account does not exist."}

    def test_it_touches_no_other_section(self, client):
        app_module.set_setting_value("notification_admin_error_enabled", "False")
        app_module.set_setting_value("stripe_price_id", "price_kept")
        db.session.commit()
        _superadmin(client)

        send(client, "PUT", f"{API}/general", GENERAL)

        assert _stored("notification_admin_error_enabled") == "False" and _stored("stripe_price_id") == "price_kept"


class TestNotifications:
    def test_the_switches_and_each_channel(self, client):
        _superadmin(client)
        send(client, "PUT", f"{API}/notifications",
             {"admin_general": True, "admin_error": False, "user_status": True, "sender": None})

        body = client.get(f"{API}/notifications").get_json()

        assert (body["admin_general"], body["admin_error"], body["user_status"]) == (True, False, True)
        assert [channel["channel"] for channel in body["health"]] == ["admin_general", "admin_error", "user_status"]
        assert body["health"][1]["enabled"] is False


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

    for section in ("general", "notifications", "billing", "forum"):
        assert client.get(f"/admin/settings/{section}").status_code == 200
