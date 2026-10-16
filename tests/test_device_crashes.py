"""Crash reports from the devices (docs/devices-plan.md, "Crash reports";
docs/device-protocol.md, 6.5): the summary ESP-IDF read from the core dump,
then the dump itself, kept whole and shown only to admins on the device's
page (services/device_crashes.py, api/devices.py).

A device is driven here the way its firmware drives it: plain HTTP, its key
as a bearer token.
"""
import hashlib
from datetime import timedelta

import pytest

from api_helpers import signed_in
from conftest import db, make_member
from aeronautics_members.db_models import DeviceCrash
from aeronautics_members.services import device_crashes, device_firmware, devices, firmware_image
from aeronautics_members.services.clock import get_now_utc
from test_admin_reviews import _staff


def _dump(seed=b"1"):
    """A core dump as ESP-IDF writes one: an ELF file (here only its start and some bytes)."""
    return b"\x7fELF\x01\x01\x01" + b"\0" * 9 + seed * 400


def _summary(dump, **fields):
    return {
        "crash_id": hashlib.sha256(dump).hexdigest(), "build": "72ce52965e229fed", "task": "portal",
        "pc": "0x4209f87c", "backtrace": ["0x4209F879", "0x4209FB05", "0x4209D2EA", "0x42008809"],
        "backtrace_corrupted": False, "reset_reason": "panic", "size": len(dump), **fields,
    }


@pytest.fixture
def reader(app):
    device, key = devices.add(None, name="Fridge reader", type="test")
    db.session.commit()
    return device, {"Authorization": f"Bearer {key}", "X-Device-Firmware": "0.4.3"}


@pytest.fixture
def admin(app):
    return _staff("admin@example.org", "admin")


def _send(client, headers, dump, **fields):
    return client.post("/api/v1/device/crash", json=_summary(dump, **fields), headers=headers)


def _upload(client, headers, dump, crash_id=None):
    crash_id = crash_id or hashlib.sha256(dump).hexdigest()
    return client.put(f"/api/v1/device/crash/{crash_id}/file", data=dump, headers=headers,
                      content_type="application/octet-stream")


class TestTheDevice:
    def test_a_crash_reported_then_its_dump_sent(self, app, client, reader):
        device, headers = reader
        dump = _dump()

        response = _send(client, headers, dump)
        assert response.status_code == 201 and response.get_json() == {"upload": True}
        assert _upload(client, headers, dump).status_code == 204

        crash = db.session.query(DeviceCrash).one()
        assert (crash.device_id, crash.task, crash.pc, crash.reset_reason) == (device.id, "portal", "0x4209F87C",
                                                                              "panic")
        assert crash.reported_by == "0.4.3" and crash.file_received_at is not None
        assert device_crashes.path_of(crash).read_bytes() == dump

    def test_the_same_crash_again_is_answered_as_before(self, app, client, reader):
        _device, headers = reader
        dump = _dump()
        assert _send(client, headers, dump).get_json() == {"upload": True}
        # The dump not sent yet (a lost connection): it is still wanted.
        assert _send(client, headers, dump).get_json() == {"upload": True}
        _upload(client, headers, dump)

        response = _send(client, headers, dump)

        assert response.status_code == 200 and response.get_json() == {"upload": False}
        assert db.session.query(DeviceCrash).count() == 1
        # The dump sent twice is harmless too.
        assert _upload(client, headers, dump).status_code == 204

    def test_a_dump_without_its_summary_is_refused(self, app, client, reader):
        _device, headers = reader
        response = _upload(client, headers, _dump())
        assert response.status_code == 404 and response.get_json()["error"]["code"] == "crash_unknown"

    def test_a_dump_must_be_the_one_reported_and_an_elf(self, app, client, reader):
        _device, headers = reader
        dump = _dump()
        _send(client, headers, dump)

        other = _dump(b"2")
        response = _upload(client, headers, other, crash_id=hashlib.sha256(dump).hexdigest())
        assert response.status_code == 400 and response.get_json()["error"]["code"] == "crash_file_mismatch"

        not_elf = b"MZ" + b"\0" * 100
        _send(client, headers, not_elf)
        response = _upload(client, headers, not_elf)
        assert response.status_code == 400 and response.get_json()["error"]["code"] == "crash_file_not_elf"

    def test_what_is_not_a_summary_is_refused_with_its_field(self, app, client, reader):
        _device, headers = reader
        for field, value in (("pc", "here"), ("backtrace", ["0x1"] * 17), ("reset_reason", "brownout"),
                             ("build", "xyz"), ("crash_id", "1234")):
            response = _send(client, headers, _dump(), **{field: value})
            assert response.status_code == 400, field
        assert db.session.query(DeviceCrash).count() == 0

    def test_a_dump_too_large_is_refused(self, app, client, reader):
        _device, headers = reader
        big = b"\x7fELF" + b"\0" * device_crashes.MAX_SIZE
        response = _upload(client, headers, big)
        assert response.status_code == 400 and response.get_json()["error"]["code"] == "crash_file_too_large"

    def test_without_a_key_nothing(self, app, client):
        assert client.post("/api/v1/device/crash", json=_summary(_dump())).status_code == 401


class TestKeeping:
    def test_twenty_dumps_a_device_the_oldest_file_going(self, app, client, reader):
        device, headers = reader
        dumps = [_dump(bytes([n + 1])) for n in range(device_crashes.FILES_KEPT + 1)]
        for n, dump in enumerate(dumps):
            _send(client, headers, dump)
            crash = db.session.query(DeviceCrash).filter_by(crash_id=hashlib.sha256(dump).hexdigest()).one()
            crash.received_at = crash.received_at + timedelta(seconds=n)  # in the order sent
            db.session.commit()
            _upload(client, headers, dump)

        crashes = device_crashes.crashes(device)
        assert len(crashes) == device_crashes.FILES_KEPT + 1
        oldest = crashes[-1]
        assert oldest.crash_id == hashlib.sha256(dumps[0]).hexdigest()
        assert not device_crashes.has_file(oldest) and not device_crashes.path_of(oldest).exists()
        assert all(device_crashes.has_file(crash) for crash in crashes[:-1])

    def test_ninety_days_then_gone_with_the_dump(self, app, client, reader):
        _device, headers = reader
        dump = _dump()
        _send(client, headers, dump)
        _upload(client, headers, dump)
        crash = db.session.query(DeviceCrash).one()
        path = device_crashes.path_of(crash)

        devices.forget_old(get_now_utc() + timedelta(days=91))
        db.session.commit()

        assert db.session.query(DeviceCrash).count() == 0 and not path.exists()

    def test_removing_the_device_removes_its_crashes(self, app, client, reader):
        device, headers = reader
        dump = _dump()
        _send(client, headers, dump)
        _upload(client, headers, dump)
        folder = device_crashes.path_of(db.session.query(DeviceCrash).one()).parent

        devices.remove(None, device)
        db.session.commit()

        assert db.session.query(DeviceCrash).count() == 0 and not folder.exists()


class TestTheAdminsPage:
    def test_listed_newest_first_with_the_crashed_version(self, app, client, reader, admin):
        device, headers = reader
        app_file = firmware_image.example("0.4.2")
        release = device_firmware.upload(admin, app_file)
        db.session.commit()
        dump = _dump()
        _send(client, headers, dump, build=release.elf_sha256[:16])
        _upload(client, headers, dump)
        _send(client, headers, _dump(b"9"), build="0123456789abcdef", reset_reason="task_watchdog")
        signed_in(client, admin)

        crashes = client.get(f"/api/v1/admin/devices/{device.id}/crashes").get_json()["crashes"]

        assert [(c["version"], c["build"], c["reset_reason"], c["has_file"]) for c in reversed(crashes)] == [
            ("0.4.2", release.elf_sha256[:16], "panic", True),
            (None, "0123456789abcdef", "task_watchdog", False),
        ]
        assert crashes[-1]["backtrace"] == ["0x4209F879", "0x4209FB05", "0x4209D2EA", "0x42008809"]

    def test_a_version_uploaded_before_the_hash_was_kept_is_found_too(self, app, client, reader, admin):
        device, headers = reader
        release = device_firmware.upload(admin, firmware_image.example("0.4.1"))
        build = release.elf_sha256[:16]
        release.elf_sha256 = None
        db.session.commit()
        _send(client, headers, _dump(), build=build)
        signed_in(client, admin)

        (crash,) = client.get(f"/api/v1/admin/devices/{device.id}/crashes").get_json()["crashes"]

        assert crash["version"] == "0.4.1"
        db.session.refresh(release)
        assert release.elf_sha256 is not None

    def test_the_dump_downloaded_whole_and_a_report_deleted(self, app, client, reader, admin):
        device, headers = reader
        dump = _dump()
        _send(client, headers, dump)
        _upload(client, headers, dump)
        signed_in(client, admin)
        (crash,) = client.get(f"/api/v1/admin/devices/{device.id}/crashes").get_json()["crashes"]

        response = client.get(f"/api/v1/admin/devices/{device.id}/crashes/{crash['id']}/file")
        assert response.status_code == 200 and response.data == dump
        assert response.headers["Content-Disposition"].startswith(f"attachment; filename=crash-{device.code}-")

        answer = client.delete(f"/api/v1/admin/devices/{device.id}/crashes/{crash['id']}").get_json()
        assert answer == {"crashes": []}
        assert not list(device_crashes.crash_dir().rglob("*.elf"))

    def test_only_who_manages_devices(self, app, client, reader):
        device, _headers = reader
        signed_in(client, make_member(email="member@example.org").user)
        assert client.get(f"/api/v1/admin/devices/{device.id}/crashes").status_code == 403
