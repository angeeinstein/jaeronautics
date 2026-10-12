"""Devices: their management, their keys and the API they talk to
(services/devices.py, api/devices.py, docs/devices-plan.md).

A device is driven here the way an ESP32 will drive it: plain HTTP with its
key as a bearer token, no session, no cookies, no CSRF token.
"""
from datetime import timedelta

import pytest

from api_helpers import send, signed_in
from conftest import db, make_member
from aeronautics_members.db_models import AuditLog, Device, DeviceEvent
from aeronautics_members.services import devices
from aeronautics_members.services.clock import get_now_utc
from test_admin_reviews import _staff


@pytest.fixture
def admin(app):
    return _staff("admin@example.org", "admin")


def _add(client, admin, name="Fridge reader", **fields):
    signed_in(client, admin)
    response = send(client, "POST", "/api/v1/admin/devices",
                    {"name": name, "type": "test", "place": "Workshop", **fields})
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def _as_device(app, key, firmware="0.1.0"):
    """A client of its own, as a device has: nothing signed in, no cookies."""
    client = app.test_client()
    headers = {"Authorization": f"Bearer {key}"}
    if firmware:
        headers["X-Device-Firmware"] = firmware
    return client, headers


class TestManaging:
    def test_adding_shows_the_key_once_and_keeps_only_its_hash(self, app, client, admin):
        added = _add(client, admin)

        key = added["key"]
        assert key.startswith("jadev_") and added["configuration"]["key"] == key
        assert added["configuration"]["server"] == "https://members.test/api/v1/device"
        stored = db.session.get(Device, added["device"]["id"])
        assert key.split("_", 2)[2] not in (stored.key_hash, stored.key_id)
        assert added["device"]["key_hint"] == f"jadev_{stored.key_id}"
        listed = client.get("/api/v1/admin/devices").get_json()
        assert "key" not in listed["items"][0] and listed["types"][0]["slug"] == "test"
        assert db.session.query(AuditLog).filter_by(event_type="device_added").count() == 1

    def test_the_type_settings_default_and_are_checked(self, app, client, admin):
        assert _add(client, admin)["device"]["settings"] == {"greeting": "Hello"}

        refused = send(client, "POST", "/api/v1/admin/devices",
                       {"name": "Other", "type": "test", "settings": {"colour": "red"}})
        assert refused.status_code == 400 and refused.get_json()["error"]["code"] == "device_setting_unknown"
        unknown_type = send(client, "POST", "/api/v1/admin/devices", {"name": "Other", "type": "toaster"})
        assert unknown_type.get_json()["error"]["fields"] == {"type": "Choose a type."}

    def test_names_are_unique(self, app, client, admin):
        _add(client, admin, name="Reader")
        again = send(client, "POST", "/api/v1/admin/devices", {"name": "reader", "type": "test"})
        assert again.status_code == 409

    def test_only_with_the_permission(self, app, client):
        member = make_member(email="member@example.com")
        signed_in(client, member.user)
        assert client.get("/api/v1/admin/devices").status_code == 403

    def test_a_new_key_replaces_the_old_one_at_once(self, app, client, admin):
        added = _add(client, admin)
        old = added["key"]
        new = send(client, "POST", f"/api/v1/admin/devices/{added['device']['id']}/key").get_json()["key"]

        device_client, headers = _as_device(app, old)
        assert device_client.get("/api/v1/device", headers=headers).status_code == 401
        device_client, headers = _as_device(app, new)
        assert device_client.get("/api/v1/device", headers=headers).status_code == 200

    def test_switched_off_it_is_refused(self, app, client, admin):
        added = _add(client, admin)
        send(client, "PATCH", f"/api/v1/admin/devices/{added['device']['id']}", {"enabled": False})

        device_client, headers = _as_device(app, added["key"])
        answer = device_client.get("/api/v1/device", headers=headers)
        assert answer.status_code == 403 and answer.get_json()["error"]["code"] == "device_off"

    def test_removing_takes_its_events_with_it(self, app, client, admin):
        added = _add(client, admin)
        device_id = added["device"]["id"]
        assert send(client, "DELETE", f"/api/v1/admin/devices/{device_id}").status_code == 204
        assert db.session.get(Device, device_id) is None
        assert db.session.query(DeviceEvent).count() == 0


class TestTheDeviceApi:
    def test_a_sign_of_life(self, app, client, admin):
        added = _add(client, admin)
        device_client, headers = _as_device(app, added["key"])

        answer = device_client.get("/api/v1/device", headers=headers)

        assert answer.status_code == 200
        body = answer.get_json()
        assert body["name"] == "Fridge reader" and body["type"] == "test"
        assert body["settings"] == {"greeting": "Hello"}
        assert body["resources"] == ["hello"] and body["reports"] == ["ping"]
        assert body["server_time"].endswith("Z")
        device = db.session.get(Device, added["device"]["id"])
        db.session.refresh(device)
        assert device.last_seen_at is not None and device.firmware == "0.1.0"
        assert devices.is_online(device)

    def test_no_session_and_no_csrf_token_needed(self, app, admin, client):
        added = _add(client, admin)
        app.config["WTF_CSRF_ENABLED"] = True
        try:
            device_client, headers = _as_device(app, added["key"])
            answer = device_client.post("/api/v1/device/events", headers=headers,
                                        json={"event_id": "1", "kind": "ping", "data": {"n": 1}})
        finally:
            app.config["WTF_CSRF_ENABLED"] = False
        assert answer.status_code == 200, answer.get_json()

    @pytest.mark.parametrize("authorization, code", [
        (None, "device_key_missing"),
        ("Bearer nonsense", "device_key_missing"),
        ("Basic jadev_aaaaaaaaaaaa_x", "device_key_missing"),
        ("Bearer jadev_aaaaaaaaaaaa_secret", "device_key_unknown"),
    ])
    def test_without_a_known_key_it_is_refused(self, app, authorization, code):
        client = app.test_client()
        answer = client.get("/api/v1/device", headers={"Authorization": authorization} if authorization else {})
        assert answer.status_code == 401 and answer.get_json()["error"]["code"] == code

    def test_a_wrong_secret_for_a_known_device_is_recorded(self, app, client, admin):
        added = _add(client, admin)
        key_id = added["device"]["key_hint"].split("_")[1]
        device_client, headers = _as_device(app, f"jadev_{key_id}_wrong")

        assert device_client.get("/api/v1/device", headers=headers).status_code == 401
        assert db.session.query(DeviceEvent).filter_by(kind="refused").count() == 1

    def test_a_resource_of_its_type(self, app, client, admin):
        added = _add(client, admin, settings={"greeting": "Servus"})
        device_client, headers = _as_device(app, added["key"])

        answer = device_client.get("/api/v1/device/resources/hello", headers=headers).get_json()
        assert answer == {"resource": "hello", "data": {"greeting": "Servus", "device": "Fridge reader"}}
        missing = device_client.get("/api/v1/device/resources/items", headers=headers)
        assert missing.status_code == 404 and missing.get_json()["error"]["code"] == "device_resource_unknown"

    def test_a_report_is_answered_once_however_often_it_is_sent(self, app, client, admin):
        added = _add(client, admin)
        device_client, headers = _as_device(app, added["key"])
        report = {"event_id": "boot-7", "kind": "ping", "data": {"n": 1}}

        first = device_client.post("/api/v1/device/events", headers=headers, json=report).get_json()
        again = device_client.post("/api/v1/device/events", headers=headers,
                                   json={**report, "data": {"n": 2}}).get_json()

        assert first == {"event_id": "boot-7", "answer": {"result": "ok", "message": "Hello", "echo": {"n": 1}}}
        assert again == first, "the first answer, not a second one"
        assert db.session.query(DeviceEvent).filter_by(kind="reported").count() == 1

    def test_a_report_its_type_does_not_know(self, app, client, admin):
        added = _add(client, admin)
        device_client, headers = _as_device(app, added["key"])
        answer = device_client.post("/api/v1/device/events", headers=headers,
                                    json={"event_id": "1", "kind": "sale", "data": {}})
        assert answer.status_code == 404 and answer.get_json()["error"]["code"] == "device_report_unknown"

    def test_last_seen_is_not_written_on_every_request(self, app, client, admin):
        added = _add(client, admin)
        device = db.session.get(Device, added["device"]["id"])
        now = get_now_utc()
        devices.seen(device, address="10.0.0.5", now=now)
        devices.seen(device, address="10.0.0.5", now=now + timedelta(seconds=5))
        assert device.last_seen_at == now
        devices.seen(device, address="10.0.0.5", now=now + timedelta(seconds=40))
        assert device.last_seen_at == now + timedelta(seconds=40)


class TestTryingFromItsPage:
    def test_what_the_device_would_be_answered_without_marking_it_seen(self, app, client, admin):
        added = _add(client, admin)
        url = f"/api/v1/admin/devices/{added['device']['id']}/try"

        hello = send(client, "POST", url, {"what": "hello"}).get_json()["answer"]
        ping = send(client, "POST", url, {"what": "report", "name": "ping", "data": {"x": 1}}).get_json()["answer"]

        assert hello["name"] == "Fridge reader" and ping["echo"] == {"x": 1}
        assert db.session.get(Device, added["device"]["id"]).last_seen_at is None
        detail = client.get(f"/api/v1/admin/devices/{added['device']['id']}").get_json()
        assert [event["kind"] for event in detail["events"]][:2] == ["tried", "tried"]


def test_events_are_kept_90_days(app, client, admin):
    added = _add(client, admin)
    device = db.session.get(Device, added["device"]["id"])
    db.session.add(DeviceEvent(device=device, at=get_now_utc() - timedelta(days=91), kind="reported"))
    db.session.commit()

    assert devices.forget_old() == 1


def test_the_pages_are_the_apps_for_admins_only(app, client, admin):
    added = _add(client, admin)
    assert client.get("/admin/settings/devices").status_code == 200
    assert client.get(f"/admin/settings/devices/{added['device']['id']}").status_code == 200

    from flask import g

    member = make_member(email="nosy@example.com")
    g.pop("_login_user", None)  # Flask-Login keeps the first one on the open app context
    other = signed_in(app.test_client(), member.user)
    assert other.get("/admin/settings/devices").status_code in (302, 403)
