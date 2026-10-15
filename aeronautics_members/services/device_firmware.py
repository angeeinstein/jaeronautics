"""Firmware updates for the devices (docs/devices-plan.md, "Firmware updates").

One firmware runs on every device, whatever it is used for. An admin uploads
a file under Admin › Settings › Devices › Firmware; what it is -- version,
project, when it was built, the chip -- is read from the file itself
(services/firmware_image.py), and it is refused unless it is a whole ESP-IDF
app of the same project as the files before it.

Which version the devices run is chosen there, for all of them, and on a
device's page for one -- to try a version on one device first. A device
learns of it with its sign of life, downloads it signed in with its key,
checks it, installs it when it is not in use, and says so with its next
request (``X-Device-Firmware``). A version it reports it could not install is
not offered to it again until an admin asks (*Try again*), so a device does
not try the same broken file over and over.
"""

import os
from pathlib import Path

from flask import current_app

from ..db_models import Device, FirmwareRelease, db
from . import ConflictError, NotFoundError, ValidationError
from . import firmware_image
from .audit import log_audit_event
from .settings import get_settings_map, set_setting_value

#: The release every device runs, unless one is chosen for it.
SETTING_RELEASE = "device_firmware_release"
#: A firmware file is at most this big: an app partition of a 16 MB flash.
MAX_SIZE = 16 * 1024 * 1024
NOTES_MAX = 300
DETAIL_MAX = 255


def firmware_dir():
    configured = current_app.config.get("FIRMWARE_DIR")
    return Path(configured) if configured else Path(current_app.root_path).parent / "storage" / "firmware"


def path_of(release):
    return firmware_dir() / f"{release.sha256}.bin"


def releases():
    """Every uploaded firmware, the newest first."""
    return db.session.execute(
        db.select(FirmwareRelease).order_by(FirmwareRelease.uploaded_at.desc(), FirmwareRelease.id.desc())
    ).scalars().all()


def release_of(release_id):
    release = db.session.get(FirmwareRelease, release_id)
    if release is None:
        raise NotFoundError("No such firmware.", code="firmware_unknown")
    return release


def project():
    """The project every firmware must be of: that of the newest one; any, before the first."""
    newest = releases()
    return newest[0].project if newest else None


def _unlike_the_newest(image):
    """What makes a file unlike the firmware uploaded before: problems, in
    words. Checked against the newest one, by what ESP-IDF puts in every build
    -- its project and its chip; nothing of the firmware's own is needed
    (docs/device-protocol.md, 6.3)."""
    newest = releases()
    if not newest:
        return []
    if image.project != newest[0].project:
        return [f"This is firmware of \"{image.project}\", not of \"{newest[0].project}\"."]
    if newest[0].chip and image.chip != newest[0].chip:
        return [f"This firmware is for the {image.chip or 'unknown chip'}, not the {newest[0].chip}."]
    return []


def upload(actor, data, *, file_name=None, notes=None):
    """A firmware file, checked and kept. Returns the :class:`FirmwareRelease`."""
    if len(data) > MAX_SIZE:
        raise ValidationError(f"A firmware file is at most {MAX_SIZE // (1024 * 1024)} MB.", code="firmware_too_large",
                              details={"fields": {"file": "Too large."}})
    try:
        image = firmware_image.read(data)
    except firmware_image.NotAnImage as exc:
        raise ValidationError(str(exc), code="firmware_invalid", details={"fields": {"file": str(exc)}}) from exc
    problems = _unlike_the_newest(image)
    if problems:
        raise ValidationError(problems[0], code="firmware_not_ours", details={"fields": {"file": problems[0]}})
    same = db.session.execute(db.select(FirmwareRelease).filter_by(sha256=image.sha256)).scalar_one_or_none()
    if same is not None:
        raise ConflictError(f"This file is uploaded already, as version {same.version}.", code="firmware_known",
                            details={"fields": {"file": f"Uploaded already, as version {same.version}."}})
    if db.session.execute(db.select(FirmwareRelease.id).filter_by(version=image.version[:32])).first() is not None:
        message = f"Version {image.version} is uploaded already: build this one with a new version."
        raise ConflictError(message, code="firmware_version_taken", details={"fields": {"file": message}})
    notes = " ".join((notes or "").split())
    if len(notes) > NOTES_MAX:
        raise ValidationError(f"At most {NOTES_MAX} characters.", details={"fields": {"notes": f"At most {NOTES_MAX} characters."}})
    release = FirmwareRelease(
        version=image.version[:32], project=image.project[:32], built=image.built[:40] or None,
        idf_version=image.idf_version[:32] or None, chip=image.chip, sha256=image.sha256, size=image.size,
        file_name=os.path.basename(file_name or "")[:200] or None,
        notes=notes or None, uploaded_by_user_id=actor.id if actor is not None else None,
    )
    directory = firmware_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = path_of(release)
    partial = target.with_suffix(".part")
    partial.write_bytes(data)
    partial.replace(target)
    db.session.add(release)
    db.session.flush()
    log_audit_event("devices", "firmware_uploaded", actor_user=actor,
                    metadata={"release_id": release.id, "version": release.version, "sha256": release.sha256})
    return release


def chosen():
    """The firmware every device runs, unless one is chosen for it; None: none chosen."""
    value = get_settings_map([SETTING_RELEASE]).get(SETTING_RELEASE)
    return db.session.get(FirmwareRelease, int(value)) if value and value.isdigit() else None


def choose_for_all(actor, release):
    """Every device runs ``release`` (None: no update is offered)."""
    before = chosen()
    set_setting_value(SETTING_RELEASE, release.id if release is not None else None)
    log_audit_event("devices", "firmware_chosen", actor_user=actor,
                    metadata={"version": release.version if release else None,
                              "before": before.version if before else None})


def choose_for_device(actor, device, release):
    """``device`` runs ``release`` instead of what every device runs (None: the same)."""
    from .devices import record_event

    if (release.id if release else None) == device.firmware_release_id:
        return
    device.firmware_release = release
    device.firmware_release_id = release.id if release is not None else None
    record_event(device, "firmware", detail=(f"To run firmware {release.version}" if release
                                        else "To run the firmware every device runs"))
    log_audit_event("devices", "device_firmware_chosen", actor_user=actor,
                    metadata={"device_id": device.id, "version": release.version if release else None})


def remove(actor, release):
    """An uploaded firmware deleted, unless a device is to run it."""
    current = chosen()
    used = db.session.execute(db.select(Device.id).filter_by(firmware_release_id=release.id)).first() is not None
    if (current is not None and current.id == release.id) or used:
        raise ConflictError("Devices are to run this firmware: choose another one first.", code="firmware_in_use")
    path = path_of(release)
    log_audit_event("devices", "firmware_removed", actor_user=actor,
                    metadata={"version": release.version, "sha256": release.sha256})
    db.session.delete(release)
    db.session.flush()
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def wanted(device):
    """The firmware ``device`` should run: its own choice, else every device's."""
    return device.firmware_release or chosen()


def offer(device):
    """The firmware to offer ``device`` now, or None: none chosen, it runs it
    already, or it said it could not install it."""
    release = wanted(device)
    if release is None or device.firmware == release.version or device.firmware_failed_version == release.version:
        return None
    return release


def state(device):
    """In a word: "none" (no firmware chosen), "current", "waiting" (offered,
    not installed yet) or "failed" (it could not install what it should run)."""
    release = wanted(device)
    if release is None:
        return "none"
    if device.firmware == release.version:
        return "current"
    if device.firmware_failed_version == release.version:
        return "failed"
    return "waiting"


def ran(device, version):
    """The device runs ``version`` now (its sign of life said so): an earlier failure with it is over."""
    if device.firmware_failed_version and device.firmware_failed_version == version:
        device.firmware_failed_version = device.firmware_failed_detail = None


def downloading(device, release):
    from .devices import record_event

    record_event(device, "firmware", detail=f"Downloading firmware {release.version}")


#: What a device may report about an update.
RESULTS = ("started", "failed")


def reported(device, *, result, version, detail=None):
    """The device says how its update goes: "started" (installing), or "failed"
    -- then that version is not offered to it again until an admin asks."""
    from .devices import record_event

    if result not in RESULTS:
        raise ValidationError("Report \"started\" or \"failed\".", code="firmware_result_unknown")
    version = (version or "").strip()[:32]
    detail = " ".join((detail or "").split())[:DETAIL_MAX] or None
    if not version:
        raise ValidationError("Say which version.", code="firmware_version_missing")
    if result == "failed":
        device.firmware_failed_version, device.firmware_failed_detail = version, detail
        record_event(device, "firmware", detail=f"Could not install firmware {version}" + (f": {detail}" if detail else ""))
    else:
        record_event(device, "firmware", detail=f"Installing firmware {version}")


def try_again(actor, device):
    """Offer the version it could not install once more."""
    from .devices import record_event

    if device.firmware_failed_version is None:
        return
    record_event(device, "firmware", detail=f"Firmware {device.firmware_failed_version} offered again")
    log_audit_event("devices", "device_firmware_retry", actor_user=actor,
                    metadata={"device_id": device.id, "version": device.firmware_failed_version})
    device.firmware_failed_version = device.firmware_failed_detail = None
