"""The devices' firmware (services/device_firmware.py, docs/devices-plan.md,
"Firmware updates"): Admin › Settings › Devices › Firmware. Upload a file,
choose which version every device runs, and see where each device stands.
What a device asks and downloads is in api/devices.py with the rest of the
device API; choosing a version for one device is on its page.
"""

from typing import Literal

from flask import request
from flask_login import current_user

from ..db_models import db
from ..services import device_firmware
from ..services import devices as devices_service
from ._core import Model, UtcDateTime, endpoint
from .devices import MANAGE


class FirmwareReleaseOut(Model):
    id: int
    #: As the file's app description names them.
    version: str
    project: str
    built: str | None
    idf_version: str | None
    #: The chip it was built for: "esp32s3".
    chip: str | None
    sha256: str
    size: int
    file_name: str | None
    notes: str | None
    uploaded_at: UtcDateTime
    uploaded_by: str | None
    #: Every device runs it, unless one is chosen for a device.
    chosen: bool
    #: Devices it is chosen for alone.
    chosen_for: int
    #: Devices that run it now.
    running: int


class FirmwareDeviceOut(Model):
    id: int
    code: str
    label: str
    online: bool
    #: What it said it runs.
    running: str | None
    #: What it should run.
    wanted: str | None
    #: ``wanted`` is chosen for this device alone.
    own: bool
    state: Literal["none", "current", "waiting", "failed"]
    #: Why it could not install ``wanted``, as it said.
    failed_detail: str | None


class FirmwareOut(Model):
    releases: list[FirmwareReleaseOut]
    #: The firmware every device runs; none: no update is offered.
    chosen_id: int | None
    #: The project every upload must be of; none before the first.
    project: str | None
    #: The largest file accepted, in bytes.
    max_size: int
    devices: list[FirmwareDeviceOut]


class FirmwareChoiceIn(Model):
    #: The firmware every device runs; none: no update is offered.
    release_id: int | None = None


def _out():
    chosen = device_firmware.chosen()
    devices = devices_service.devices()
    out = []
    for release in device_firmware.releases():
        out.append(FirmwareReleaseOut(
            id=release.id, version=release.version, project=release.project, built=release.built,
            idf_version=release.idf_version, chip=release.chip, sha256=release.sha256, size=release.size,
            file_name=release.file_name, notes=release.notes, uploaded_at=release.uploaded_at,
            uploaded_by=release.uploaded_by.email if release.uploaded_by is not None else None,
            chosen=chosen is not None and chosen.id == release.id,
            chosen_for=sum(1 for device in devices if device.firmware_release_id == release.id),
            running=sum(1 for device in devices if device.firmware == release.version),
        ))
    rows = []
    for device in devices:
        wanted = device_firmware.wanted(device)
        state = device_firmware.state(device)
        rows.append(FirmwareDeviceOut(
            id=device.id, code=device.code, label=devices_service.label(device),
            online=devices_service.is_online(device), running=device.firmware,
            wanted=wanted.version if wanted is not None else None, own=device.firmware_release_id is not None,
            state=state, failed_detail=device.firmware_failed_detail if state == "failed" else None,
        ))
    return FirmwareOut(releases=out, chosen_id=chosen.id if chosen is not None else None,
                       project=device_firmware.project(), max_size=device_firmware.MAX_SIZE, devices=rows)


@endpoint("GET", "/admin/devices/firmware", response=FirmwareOut, permissions=MANAGE, tag="Admin")
def admin_firmware():
    """The uploaded firmware, which every device runs, and where each device stands."""
    return _out()


@endpoint("POST", "/admin/devices/firmware", response=FirmwareOut, permissions=MANAGE, status=201, tag="Admin",
          uploads={"file": True})
def admin_firmware_upload(files):
    """Upload a firmware file (the app's .bin), with ``notes`` as a form field. It
    is checked: a whole ESP-IDF app, of the same project as the files before."""
    upload = files["file"]
    device_firmware.upload(current_user, upload.read(device_firmware.MAX_SIZE + 1), file_name=upload.filename,
                           notes=request.form.get("notes"))
    db.session.commit()
    return _out()


@endpoint("PUT", "/admin/devices/firmware/chosen", response=FirmwareOut, body=FirmwareChoiceIn, permissions=MANAGE,
          tag="Admin")
def admin_firmware_choose(body):
    """Which firmware every device runs (none: no update is offered)."""
    release = device_firmware.release_of(body.release_id) if body.release_id is not None else None
    device_firmware.choose_for_all(current_user, release)
    db.session.commit()
    return _out()


@endpoint("DELETE", "/admin/devices/firmware/<int:release_id>", response=FirmwareOut, permissions=MANAGE,
          tag="Admin")
def admin_firmware_remove(release_id):
    """Delete an uploaded firmware no device is to run."""
    device_firmware.remove(current_user, device_firmware.release_of(release_id))
    db.session.commit()
    return _out()



