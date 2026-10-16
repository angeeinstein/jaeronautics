"""A fresh board flashed from the browser (docs/devices-plan.md, "A fresh board
flashed from the portal"): the factory image -- bootloader, partition table
and app in one file -- read from the file itself, kept one per chip, and
handed to the page that writes it (services/firmware_image.py,
``read_factory``; services/device_factory.py; api/device_firmware.py).
"""
import hashlib
import struct
from io import BytesIO
from pathlib import Path

import pytest

from api_helpers import signed_in
from conftest import db, make_member
from aeronautics_members.db_models import FactoryImage
from aeronautics_members.services import device_factory, device_firmware, firmware_image
from test_admin_reviews import _staff

ESPTOOL_APP = Path(__file__).parent / "fixtures" / "firmware-esptool-esp32s3.bin"
API = "/api/v1/admin/devices/firmware/factory"


@pytest.fixture
def admin(app):
    return _staff("admin@example.org", "admin")


def _upload(client, data, name="factory.bin"):
    return client.post(API, data={"file": (BytesIO(data), name)}, content_type="multipart/form-data")


class TestReading:
    def test_the_app_inside_names_what_it_is(self):
        """Around an app esptool wrote, laid out as idf.py merge-bin lays it out."""
        app = ESPTOOL_APP.read_bytes()
        data = firmware_image.factory_example(app=app)

        image = firmware_image.read_factory(data)

        inner = firmware_image.read(app)
        assert (image.version, image.project, image.chip) == (inner.version, inner.project, "esp32s3")
        assert image.app_offset == 0x10000
        assert image.sha256 == hashlib.sha256(data).hexdigest() and image.size == len(data)

    def test_an_esp32_starts_its_bootloader_at_4_kb(self):
        assert firmware_image.read_factory(firmware_image.factory_example(chip_id=0x0000)).chip == "esp32"

    def test_a_bootloader_where_the_chip_does_not_look_is_refused(self):
        """An ESP32 image built to be written from 0x1000, not from 0."""
        data = firmware_image.factory_example(chip_id=0x0000)[0x1000:]
        with pytest.raises(firmware_image.NotAnImage, match="not where the esp32 looks"):
            firmware_image.read_factory(data)

    def test_an_update_file_is_told_apart(self):
        with pytest.raises(firmware_image.NotAnImage, match="update file"):
            firmware_image.read_factory(firmware_image.example())

    def test_without_a_partition_table_it_is_refused(self):
        data = bytearray(firmware_image.factory_example())
        data[0x8000:0x8002] = b"\xff\xff"
        with pytest.raises(firmware_image.NotAnImage, match="no partition table"):
            firmware_image.read_factory(bytes(data))

    def test_a_table_moved_on_for_a_larger_bootloader_is_found(self):
        data = bytearray(firmware_image.factory_example())
        table = data[0x8000:0x8000 + 4 * 32]
        data[0x8000:0x8000 + len(table)] = b"\xff" * len(table)
        data[0x9000:0x9000 + len(table)] = table
        assert firmware_image.read_factory(bytes(data)).version == "1.0.0"

    def test_a_factory_app_partition_comes_before_the_ota_slots(self):
        data = bytearray(firmware_image.factory_example())
        # The first OTA slot's entry made the factory app's (subtype 0).
        entry = 0x8000 + 2 * 32
        assert data[entry + 3] == firmware_image.OTA_0_SUBTYPE
        data[entry + 3] = firmware_image.FACTORY_SUBTYPE
        assert firmware_image.read_factory(bytes(data)).app_offset == 0x10000

    def test_without_the_app_it_is_refused(self):
        data = firmware_image.factory_example()[:0x10000]
        with pytest.raises(firmware_image.NotAnImage, match="no app in it"):
            firmware_image.read_factory(data)

    def test_a_damaged_app_is_named(self):
        data = bytearray(firmware_image.factory_example())
        data[-40] ^= 0xFF
        with pytest.raises(firmware_image.NotAnImage, match="^The app in this factory image: this firmware file is damaged"):
            firmware_image.read_factory(bytes(data))

    def test_an_app_for_another_chip_than_its_bootloader_is_refused(self):
        app = firmware_image.example(chip_id=0x0005)  # an ESP32-C3's
        with pytest.raises(firmware_image.NotAnImage, match="for the esp32c3, its bootloader for the esp32s3"):
            firmware_image.read_factory(firmware_image.factory_example(app=app))

    def test_an_unknown_chip_is_refused(self):
        data = bytearray(firmware_image.factory_example())
        struct.pack_into("<H", data, 12, 0x7777)
        with pytest.raises(firmware_image.NotAnImage, match="does not know"):
            firmware_image.read_factory(bytes(data))


class TestUploading:
    def test_it_is_kept_with_what_it_is(self, app, client, admin):
        signed_in(client, admin)
        data = firmware_image.factory_example(version="1.2.0")

        response = _upload(client, data, name="jae-terminal-factory.bin")

        assert response.status_code == 201, response.get_json()
        (image,) = response.get_json()["images"]
        assert image["chip"] == "esp32s3" and image["version"] == "1.2.0" and image["project"] == "jae-terminal"
        assert image["file_name"] == "jae-terminal-factory.bin" and image["uploaded_by"] == "admin@example.org"
        stored = db.session.get(FactoryImage, image["id"])
        assert device_factory.path_of(stored).read_bytes() == data

    def test_a_new_one_replaces_the_old_for_its_chip(self, app, client, admin):
        signed_in(client, admin)
        _upload(client, firmware_image.factory_example(version="1.0.0"))
        old = device_factory.path_of(db.session.query(FactoryImage).one())

        images = _upload(client, firmware_image.factory_example(version="1.1.0")).get_json()["images"]

        assert [image["version"] for image in images] == ["1.1.0"]
        assert not old.exists()
        # Another chip's stands beside it.
        images = _upload(client, firmware_image.factory_example(chip_id=0x0000)).get_json()["images"]
        assert [image["chip"] for image in images] == ["esp32", "esp32s3"]

    def test_its_app_must_be_of_the_update_files_project(self, app, client, admin):
        signed_in(client, admin)
        device_firmware.upload(admin, firmware_image.example(version="0.3.0", project="jae-terminal"))
        db.session.commit()

        response = _upload(client, firmware_image.factory_example(project="someone-else"))

        assert response.status_code == 400
        assert "not of \"jae-terminal\"" in response.get_json()["error"]["fields"]["file"]
        assert db.session.query(FactoryImage).count() == 0

    def test_an_update_file_is_refused_with_why(self, app, client, admin):
        signed_in(client, admin)
        response = _upload(client, firmware_image.example())
        assert response.status_code == 400
        assert "update file" in response.get_json()["error"]["fields"]["file"]

    def test_the_page_gets_the_file_and_it_can_be_removed(self, app, client, admin):
        signed_in(client, admin)
        data = firmware_image.factory_example()
        (image,) = _upload(client, data).get_json()["images"]

        response = client.get(f"{API}/{image['id']}/file")
        assert response.status_code == 200 and response.data == data
        assert "no-store" in response.headers["Cache-Control"]

        assert client.delete(f"{API}/{image['id']}").get_json()["images"] == []
        assert not list(device_factory.factory_dir().glob("*.bin"))
        assert client.get(f"{API}/{image['id']}/file").status_code == 404

    def test_only_who_manages_devices(self, app, client):
        signed_in(client, make_member(email="member@example.org").user)
        assert client.get(API).status_code == 403
        assert _upload(client, firmware_image.factory_example()).status_code == 403
