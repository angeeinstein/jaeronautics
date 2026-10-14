"""One device for everything (docs/devices-plan.md): pairing by a code on the
device's screen, the device saying what it is and can be, its settings
opened by an admin's card or a backup code, and firmware updates
(services/device_pairing.py, services/device_firmware.py,
services/firmware_image.py, api/devices.py).

A device is driven here the way its ESP32 drives it: plain HTTP, its key as a
bearer token once it has one, the usual headers saying what it is.
"""
import hashlib
import struct
from datetime import timedelta
from pathlib import Path

import pytest

from api_helpers import send, signed_in
from conftest import db, make_member
from aeronautics_members.db_models import Device, DevicePairing, DeviceRequest, FirmwareRelease
from aeronautics_members.services import cards, device_firmware, device_pairing, devices, firmware_image
from aeronautics_members.services.clock import get_now_utc
from test_admin_reviews import _staff

ESPTOOL_IMAGE = Path(__file__).parent / "fixtures" / "firmware-esptool-esp32s3.bin"
BOARD = {"X-Device-Hardware": "98:a3:16:e5:06:3c", "X-Device-Firmware": "0.2.0",
         "X-Device-Board": "jae-terminal-s3", "X-Device-Types": "sales_point, test"}


def _image(version="0.3.0", project="jae-terminal", payload=b"\x5a" * 300):
    """An ESP-IDF app image as the build writes it (the layout esptool reads;
    tests/fixtures holds one esptool wrote): header, one segment opening with
    the app description, the checksum ending a 16-byte block, the SHA-256."""
    desc = struct.pack("<IIII", firmware_image.APP_DESC_MAGIC, 0, 0, 0)
    desc += version.encode().ljust(32, b"\0") + project.encode().ljust(32, b"\0")
    desc += b"09:12:44".ljust(16, b"\0") + b"Oct 14 2026".ljust(16, b"\0") + b"v5.3.1".ljust(32, b"\0")
    segment = desc.ljust(firmware_image.APP_DESC_SIZE, b"\0") + payload
    header = bytearray(24)
    header[0], header[1] = firmware_image.IMAGE_MAGIC, 1
    struct.pack_into("<H", header, 12, 0x0009)
    header[23] = 1
    data = bytes(header) + struct.pack("<II", 0x3C000020, len(segment)) + segment
    checksum = firmware_image.CHECKSUM_SEED
    for byte in segment:
        checksum ^= byte
    data += b"\0" * (15 - len(data) % 16) + bytes([checksum])
    return data + hashlib.sha256(data).digest()


@pytest.fixture
def admin(app):
    return _staff("admin@example.org", "admin")


def _start(client, headers=BOARD):
    response = client.post("/api/v1/device/pair/start", headers=headers)
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def _poll(client, pairing_id):
    return client.post("/api/v1/device/pair/poll", json={"pairing_id": pairing_id})


def _later(seconds=6):
    """Pairings polled a moment ago: as if ``seconds`` had passed since."""
    for pairing in db.session.query(DevicePairing):
        if pairing.last_poll_at is not None:
            pairing.last_poll_at = pairing.last_poll_at - timedelta(seconds=seconds)
    db.session.commit()


def _paired(app, client, admin, **fields):
    """A device paired as a new test device: its key."""
    device_client = app.test_client()
    started = _start(device_client)
    signed_in(client, admin)
    approved = send(client, "POST", f"/api/v1/admin/devices/pairings/{started['code']}",
                    {"type": "test", "owner": "association", **fields})
    assert approved.status_code == 201, approved.get_json()
    paired = _poll(device_client, started["pairing_id"]).get_json()
    assert paired["status"] == "paired"
    return paired["key"], approved.get_json()


def _hello(app, key, **headers):
    return app.test_client().get("/api/v1/device", headers={"Authorization": f"Bearer {key}", **headers})


class TestFirmwareImage:
    def test_one_esptool_wrote_is_read(self):
        image = firmware_image.read(ESPTOOL_IMAGE.read_bytes())

        assert (image.version, image.project, image.chip) == ("0.3.0", "jae-terminal", "esp32s3")
        assert image.built == "Oct 14 2026 09:12:44" and image.idf_version == "v5.3.1"
        # Where a firmware's own marks start: right after the app description.
        assert ESPTOOL_IMAGE.read_bytes()[image.custom_offset:image.custom_offset + 6] == b"JAEFW1"

    @pytest.mark.parametrize("damage, message", [
        (lambda data: b"PK\x03\x04" + data[4:], "not an ESP-IDF firmware"),
        (lambda data: data[:400], "cut off"),
        (lambda data: data[:500] + bytes([data[500] ^ 1]) + data[501:], "checksum"),
        (lambda data: data[:-1] + bytes([data[-1] ^ 1]), "SHA-256"),
    ])
    def test_what_is_not_a_whole_app_is_refused(self, damage, message):
        with pytest.raises(firmware_image.NotAnImage, match=message):
            firmware_image.read(damage(_image()))

    def test_a_bootloader_is_not_an_app(self):
        data = bytearray(_image())
        # Without the app description's mark (and the checksum made to match).
        segment_start = 32
        old = data[segment_start:segment_start + 4]
        data[segment_start:segment_start + 4] = b"\0\0\0\0"
        position = len(data) - 33
        for byte in old:
            data[position] ^= byte
        data[-32:] = hashlib.sha256(bytes(data[:-32])).digest()
        with pytest.raises(firmware_image.NotAnImage, match="no app description"):
            firmware_image.read(bytes(data))


def _upload(client, data, name="app.bin", notes=None):
    from io import BytesIO

    fields = {"file": (BytesIO(data), name)}
    if notes:
        fields["notes"] = notes
    return client.post("/api/v1/admin/devices/firmware", data=fields, content_type="multipart/form-data")


class TestFirmware:
    def test_an_upload_is_read_from_the_file_and_kept(self, app, client, admin):
        signed_in(client, admin)
        response = _upload(client, _image("0.3.0"), notes="Faster taps")

        assert response.status_code == 201, response.get_json()
        release = response.get_json()["releases"][0]
        assert (release["version"], release["project"], release["chip"]) == ("0.3.0", "jae-terminal", "esp32s3")
        assert release["notes"] == "Faster taps" and release["uploaded_by"] == "admin@example.org"
        stored = db.session.get(FirmwareRelease, release["id"])
        assert device_firmware.path_of(stored).read_bytes() == _image("0.3.0")

    def test_what_is_refused(self, app, client, admin):
        signed_in(client, admin)
        _upload(client, _image("0.3.0"))

        assert _upload(client, b"not firmware at all" * 30).get_json()["error"]["code"] == "firmware_invalid"
        assert _upload(client, _image("0.3.0")).get_json()["error"]["code"] == "firmware_known"
        assert _upload(client, _image("0.3.0", payload=b"\x11" * 64)).get_json()["error"]["code"] == \
            "firmware_version_taken"
        other = _upload(client, _image("1.0.0", project="someone-else"))
        assert other.get_json()["error"]["code"] == "firmware_not_ours"
        assert db.session.query(FirmwareRelease).count() == 1

    def test_only_admins(self, app, client):
        signed_in(client, make_member(email="member@example.com").user)
        assert _upload(client, _image()).status_code == 403

    def test_a_device_is_offered_what_every_device_runs_and_downloads_it(self, app, client, admin):
        key, _added = _paired(app, client, admin)
        _upload(client, _image("0.3.0"))
        release_id = client.get("/api/v1/admin/devices/firmware").get_json()["releases"][0]["id"]
        assert _hello(app, key).get_json()["firmware"] is None  # none chosen yet

        send(client, "PUT", "/api/v1/admin/devices/firmware/chosen", {"release_id": release_id})

        offer = _hello(app, key, **{"X-Device-Firmware": "0.2.0"}).get_json()["firmware"]
        assert offer["version"] == "0.3.0" and offer["sha256"] == hashlib.sha256(_image("0.3.0")).hexdigest()
        info = app.test_client().get("/api/v1/device/firmware", headers={"Authorization": f"Bearer {key}"}).get_json()
        assert info["install"] and info["wanted"]["url"] == "https://members.test/api/v1/device/firmware/file"
        file = app.test_client().get("/api/v1/device/firmware/file", headers={"Authorization": f"Bearer {key}"})
        assert file.status_code == 200 and file.data == _image("0.3.0")
        assert file.headers["X-Firmware-Version"] == "0.3.0"
        # A download cut off is resumed.
        rest = app.test_client().get("/api/v1/device/firmware/file",
                                     headers={"Authorization": f"Bearer {key}", "Range": "bytes=100-"})
        assert rest.status_code == 206 and rest.data == _image("0.3.0")[100:]

        # Installed: it says so with its next request, and nothing is offered.
        assert _hello(app, key, **{"X-Device-Firmware": "0.3.0"}).get_json()["firmware"] is None
        row = client.get("/api/v1/admin/devices/firmware").get_json()
        assert row["devices"][0]["state"] == "current" and row["releases"][0]["running"] == 1

    def test_a_version_it_could_not_install_is_not_offered_again_until_asked(self, app, client, admin):
        key, added = _paired(app, client, admin)
        _upload(client, _image("0.3.0"))
        release_id = client.get("/api/v1/admin/devices/firmware").get_json()["releases"][0]["id"]
        send(client, "PUT", "/api/v1/admin/devices/firmware/chosen", {"release_id": release_id})

        failed = app.test_client().post("/api/v1/device/firmware", headers={"Authorization": f"Bearer {key}"},
                                        json={"version": "0.3.0", "result": "failed", "detail": "Image invalid"})
        assert failed.status_code == 200 and failed.get_json()["install"] is False
        assert _hello(app, key).get_json()["firmware"] is None
        page = client.get(f"/api/v1/admin/devices/{added['id']}").get_json()["device"]
        assert page["firmware_state"] == "failed" and page["firmware_failed_detail"] == "Image invalid"

        send(client, "POST", f"/api/v1/admin/devices/{added['id']}/firmware/retry")
        assert _hello(app, key).get_json()["firmware"]["version"] == "0.3.0"

    def test_one_device_first(self, app, client, admin):
        first_key, first = _paired(app, client, admin, name="First")
        other_key, _other = _paired(app, client, admin, name="Other")
        _upload(client, _image("0.3.0"))
        release_id = client.get("/api/v1/admin/devices/firmware").get_json()["releases"][0]["id"]

        chosen = send(client, "PUT", f"/api/v1/admin/devices/{first['id']}/firmware", {"release_id": release_id})

        assert chosen.get_json()["firmware_release_id"] == release_id
        assert _hello(app, first_key).get_json()["firmware"]["version"] == "0.3.0"
        assert _hello(app, other_key).get_json()["firmware"] is None
        # A version a device is to run cannot be deleted.
        refused = send(client, "DELETE", f"/api/v1/admin/devices/firmware/{release_id}")
        assert refused.get_json()["error"]["code"] == "firmware_in_use"
        send(client, "PUT", f"/api/v1/admin/devices/{first['id']}/firmware", {"release_id": None})
        assert send(client, "DELETE", f"/api/v1/admin/devices/firmware/{release_id}").status_code == 200
        assert not list(device_firmware.firmware_dir().glob("*.bin"))

    def test_no_firmware_to_download(self, app, client, admin):
        key, _added = _paired(app, client, admin)
        file = app.test_client().get("/api/v1/device/firmware/file", headers={"Authorization": f"Bearer {key}"})
        assert file.status_code == 404 and file.get_json()["error"]["code"] == "firmware_none"
        assert app.test_client().get("/api/v1/device/firmware/file").status_code == 401


class TestPairing:
    def test_a_code_for_people_and_an_id_for_the_device(self, app, client, admin):
        started = _start(app.test_client())

        assert len(started["code"]) == 7 and started["code"][3] == "-"
        assert started["url"] == f"https://members.test/admin/settings/devices/pair?code={started['code']}"
        assert started["expires_in"] == 600 and started["poll_every"] == 5
        stored = db.session.query(DevicePairing).one()
        # Only its hash is kept.
        assert started["pairing_id"] not in (stored.secret_hash, stored.code)
        assert stored.hardware_id == "98:A3:16:E5:06:3C" and stored.supported_types == ["sales_point", "test"]
        signed_in(client, admin)
        waiting = client.get("/api/v1/admin/devices").get_json()["pairings"]
        assert [p["code"] for p in waiting] == [started["code"]]

    def test_approved_the_device_gets_its_key_once(self, app, client, admin):
        device_client = app.test_client()
        started = _start(device_client)
        assert _poll(device_client, started["pairing_id"]).get_json()["status"] == "waiting"
        _later()
        signed_in(client, admin)
        approved = send(client, "POST", f"/api/v1/admin/devices/pairings/{started['code'].lower()}",
                        {"type": "test", "owner": "association", "name": "Workshop"})
        assert approved.status_code == 201
        assert "key" not in approved.get_json()

        paired = _poll(device_client, started["pairing_id"]).get_json()

        assert paired["status"] == "paired" and paired["key"].startswith("jadev_")
        assert paired["device"]["name"] == "Workshop" and paired["device"]["type"] == "test"
        assert _hello(app, paired["key"]).status_code == 200
        device = db.session.get(Device, approved.get_json()["id"])
        assert (device.hardware_id, device.board, device.firmware) == ("98:A3:16:E5:06:3C", "jae-terminal-s3", "0.2.0")
        # Once: the pairing is gone.
        assert _poll(device_client, started["pairing_id"]).get_json()["error"]["code"] == "pairing_unknown"

    def test_asking_too_often(self, app):
        device_client = app.test_client()
        started = _start(device_client)
        _poll(device_client, started["pairing_id"])
        again = _poll(device_client, started["pairing_id"])
        assert again.status_code == 429 and again.get_json()["error"]["code"] == "pairing_too_fast"

    def test_turned_away(self, app, client, admin):
        device_client = app.test_client()
        started = _start(device_client)
        signed_in(client, admin)
        assert send(client, "DELETE", f"/api/v1/admin/devices/pairings/{started['code']}").status_code == 204

        assert _poll(device_client, started["pairing_id"]).get_json()["status"] == "refused"
        assert db.session.query(DevicePairing).count() == 0

    def test_a_code_lasts_ten_minutes(self, app, client, admin):
        device_client = app.test_client()
        started = _start(device_client)
        db.session.query(DevicePairing).one().expires_at = get_now_utc() - timedelta(seconds=1)
        db.session.commit()

        signed_in(client, admin)
        assert client.get(f"/api/v1/admin/devices/pairings/{started['code']}").status_code == 404
        assert _poll(device_client, started["pairing_id"]).get_json()["error"]["code"] == "pairing_expired"

    def test_only_a_type_its_firmware_can_be(self, app, client, admin):
        started = _start(app.test_client(), {**BOARD, "X-Device-Types": "test"})
        signed_in(client, admin)
        refused = send(client, "POST", f"/api/v1/admin/devices/pairings/{started['code']}",
                       {"type": "sales_point", "owner": "association"})
        assert refused.get_json()["error"]["code"] == "device_type_unsupported"

    def test_a_new_board_replacing_one(self, app, client, admin):
        old_key, old = _paired(app, client, admin, name="Fridge")
        device_client = app.test_client()
        started = _start(device_client, {**BOARD, "X-Device-Hardware": "24:6f:28:aa:bb:cc"})
        replaced = send(client, "POST", f"/api/v1/admin/devices/pairings/{started['code']}", {"replaces": old["id"]})

        assert replaced.get_json()["id"] == old["id"] and replaced.get_json()["hardware_id"] == "24:6F:28:AA:BB:CC"
        # The old board's key stops at once.
        assert _hello(app, old_key).status_code == 401
        new_key = _poll(device_client, started["pairing_id"]).get_json()["key"]
        assert _hello(app, new_key).get_json()["name"] == "Fridge"

    def test_not_too_many_at_once(self, app, monkeypatch):
        monkeypatch.setattr(device_pairing, "MOST_WAITING", 2)
        device_client = app.test_client()
        _start(device_client)
        _start(device_client)
        busy = device_client.post("/api/v1/device/pair/start", headers=BOARD)
        assert busy.status_code == 429 and busy.get_json()["error"]["code"] == "pairing_busy"

    def test_the_approval_page_needs_an_admin(self, app, client):
        started = _start(app.test_client())
        signed_in(client, make_member(email="member@example.com").user)
        assert client.get(f"/api/v1/admin/devices/pairings/{started['code']}").status_code == 403
        assert client.get("/admin/settings/devices/pair").status_code in (302, 403)


class TestWhatTheDeviceSays:
    def test_its_board_and_what_it_can_be(self, app, client, admin):
        key, added = _paired(app, client, admin)
        _hello(app, key, **{"X-Device-Board": "jae-terminal-s3-v2", "X-Device-Types": "test"})

        device = db.session.get(Device, added["id"])
        assert device.board == "jae-terminal-s3-v2" and device.supported_types == ["test"]
        refused = send(client, "PATCH", f"/api/v1/admin/devices/{added['id']}", {"type": "sales_point"})
        assert refused.get_json()["error"]["code"] == "device_type_unsupported"

    def test_its_type_changed_in_the_portal(self, app, client, admin):
        key, added = _paired(app, client, admin)
        changed = send(client, "PATCH", f"/api/v1/admin/devices/{added['id']}", {"type": "sales_point"})

        assert changed.get_json()["type"] == "sales_point" and changed.get_json()["settings"] == {"item": None}
        assert _hello(app, key).get_json()["type"] == "sales_point"

    def test_nothing_new_is_a_short_answer(self, app, client, admin):
        key, added = _paired(app, client, admin)
        first = _hello(app, key)
        tag = first.headers["ETag"]

        again = _hello(app, key, **{"If-None-Match": tag})
        assert again.status_code == 304 and again.data == b""
        send(client, "PATCH", f"/api/v1/admin/devices/{added['id']}", {"place": "Office"})
        changed = _hello(app, key, **{"If-None-Match": tag})
        assert changed.status_code == 200 and changed.get_json()["place"] == "Office"
        # The 304s are not listed with its requests: they would push out what happened.
        statuses = [r.status for r in db.session.query(DeviceRequest).filter_by(device_id=added["id"])]
        assert 304 not in statuses and statuses.count(200) == 2

    def test_its_backup_code(self, app, client, admin):
        key, added = _paired(app, client, admin)
        device = db.session.get(Device, added["id"])
        code = device.backup_code
        assert len(code) == 6 and code.isdigit() and added["backup_code"] == code
        expected = hashlib.sha256(f"{device.code}:{code}".encode()).hexdigest()
        assert _hello(app, key).get_json()["backup_code_hash"] == expected

        renewed = send(client, "POST", f"/api/v1/admin/devices/{added['id']}/backup-code").get_json()
        assert renewed["backup_code"] != code
        assert _hello(app, key).get_json()["backup_code_hash"] != expected


class TestSettingsOpenedByACard:
    def _ask(self, app, key, card):
        return app.test_client().post("/api/v1/device/manager", headers={"Authorization": f"Bearer {key}"},
                                      json={"card": card}).get_json()

    def test_an_admins_card_opens_them_and_roles_count_at_once(self, app, client, admin):
        key, _added = _paired(app, client, admin)
        person = make_member(email="anna@example.com", first_name="Anna")
        cards.link(person.user, "04A1B2C3D4E5F6", by=None, how=cards.BY_PHONE)
        db.session.commit()

        assert self._ask(app, key, "04A1B2C3D4E5F6") == {"allowed": False, "name": None}
        from aeronautics_members.db_models import Role

        person.user.roles.append(db.session.query(Role).filter_by(slug="admin").one())
        db.session.commit()
        assert self._ask(app, key, "04a1b2c3d4e5f6") == {"allowed": True, "name": "Anna"}

        cards.disable(None, cards.find("04A1B2C3D4E5F6"))
        db.session.commit()
        assert self._ask(app, key, "04A1B2C3D4E5F6")["allowed"] is False

    def test_an_unknown_card(self, app, client, admin):
        key, _added = _paired(app, client, admin)
        assert self._ask(app, key, "04FFEEDDCCBB11") == {"allowed": False, "name": None}


def test_old_pairings_go_with_the_nightly_clean_up(app):
    device_pairing.start(now=get_now_utc() - timedelta(minutes=30))
    db.session.commit()
    devices.forget_old()
    assert db.session.query(DevicePairing).count() == 0
