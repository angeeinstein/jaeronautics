"""The devices' firmware (services/device_firmware.py, docs/devices-plan.md,
"Firmware updates"): Admin › Settings › Devices › Firmware. Upload a file,
choose which version every device runs, and see where each device stands.
What a device asks and downloads is in api/devices.py with the rest of the
device API; choosing a version for one device is on its page.
"""

from pathlib import Path
from typing import Literal

from flask import Response, current_app, request, send_file
from flask_login import current_user

from ..db_models import db
from ..services import NotFoundError, device_factory, device_firmware
from ..services import devices as devices_service
from ..security_utils import build_public_url, get_public_base_url
from ..services.clock import get_now_utc
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


@endpoint("GET", "/admin/devices/protocol", permissions=MANAGE, tag="Admin", produces="text/markdown")
def admin_device_protocol():
    """The device protocol (docs/device-protocol.md), everything firmware needs
    to know, as Markdown: with this portal's address, for whoever writes it."""
    text = (Path(current_app.root_path).parent / "docs" / "device-protocol.md").read_text(encoding="utf-8")
    title, _, rest = text.partition("\n")
    note = (f"> Downloaded from {get_public_base_url()} on "
            f"{get_now_utc():%d.%m.%Y}. This portal's device API: `{build_public_url('api.device_hello')}`.")
    response = Response(f"{title}\n\n{note}\n{rest}", mimetype="text/markdown")
    response.headers["Content-Disposition"] = 'attachment; filename="device-protocol.md"'
    response.headers["Cache-Control"] = "no-store"
    return response


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


# --- Factory images: a fresh board flashed from the browser ------------------------------------
# (services/device_factory.py; the Firmware page's *New device* tab.)


class FactoryImageOut(Model):
    id: int
    #: The chip it is for: "esp32s3". The page flashes it only to a board of this chip.
    chip: str
    #: The app in it, as its app description names it.
    version: str
    project: str
    built: str | None
    idf_version: str | None
    sha256: str
    size: int
    file_name: str | None
    uploaded_at: UtcDateTime
    uploaded_by: str | None


class FactoryImagesOut(Model):
    images: list[FactoryImageOut]
    #: The project the app in an upload must be of; none before the first update file.
    project: str | None
    #: The largest file accepted, in bytes.
    max_size: int


def _factory_out():
    return FactoryImagesOut(
        images=[FactoryImageOut(
            id=image.id, chip=image.chip, version=image.version, project=image.project, built=image.built,
            idf_version=image.idf_version, sha256=image.sha256, size=image.size, file_name=image.file_name,
            uploaded_at=image.uploaded_at,
            uploaded_by=image.uploaded_by.email if image.uploaded_by is not None else None,
        ) for image in device_factory.images()],
        project=device_firmware.project(), max_size=device_factory.MAX_SIZE,
    )


@endpoint("GET", "/admin/devices/firmware/factory", response=FactoryImagesOut, permissions=MANAGE, tag="Admin")
def admin_factory_images():
    """The factory images, one per chip: what a fresh board is flashed with."""
    return _factory_out()


@endpoint("POST", "/admin/devices/firmware/factory", response=FactoryImagesOut, permissions=MANAGE, status=201,
          tag="Admin", uploads={"file": True})
def admin_factory_image_upload(files):
    """Upload a factory image (``idf.py merge-bin``: bootloader, partition table
    and app in one file). It replaces the one for its chip."""
    upload = files["file"]
    device_factory.upload(current_user, upload.read(device_factory.MAX_SIZE + 1), file_name=upload.filename)
    db.session.commit()
    return _factory_out()


@endpoint("DELETE", "/admin/devices/firmware/factory/<int:image_id>", response=FactoryImagesOut,
          permissions=MANAGE, tag="Admin")
def admin_factory_image_remove(image_id):
    """Delete a factory image."""
    device_factory.remove(current_user, device_factory.image_of(image_id))
    db.session.commit()
    return _factory_out()


@endpoint("GET", "/admin/devices/firmware/factory/<int:image_id>/file", permissions=MANAGE, tag="Admin",
          produces="application/octet-stream")
def admin_factory_image_file(image_id):
    """The factory image itself, for the page to write to a board."""
    image = device_factory.image_of(image_id)
    path = device_factory.path_of(image)
    if not path.is_file():
        raise NotFoundError("The file of this factory image is missing: upload it again.",
                            code="factory_image_file_missing")
    response = send_file(path, mimetype="application/octet-stream", etag=image.sha256, max_age=0,
                         download_name=f"factory-{image.chip}-{image.version}.bin")
    response.headers["Cache-Control"] = "no-store"
    return response
