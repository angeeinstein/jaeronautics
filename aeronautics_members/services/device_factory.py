"""Factory images: what a fresh board is flashed with from the browser
(docs/devices-plan.md, "A fresh board flashed from the portal").

A board without firmware can neither pair nor update. An admin uploads a
factory image -- bootloader, partition table and app in one file, built with
``idf.py merge-bin`` -- under Admin › Settings › Devices › Firmware, *New
device*; the page writes it to a board over USB (Web Serial), and the board
then pairs and updates as any other. What the file is, is read from it
(services/firmware_image.py, ``read_factory``), and it is refused unless its
app is of the same project as the update files. One per chip: a new one
replaces the old.
"""

import os

from ..db_models import FactoryImage, db, utcnow
from . import NotFoundError, ValidationError
from . import device_firmware, firmware_image
from .audit import log_audit_event

#: A factory image is at most a whole 16 MB flash.
MAX_SIZE = 16 * 1024 * 1024


def factory_dir():
    return device_firmware.firmware_dir() / "factory"


def path_of(image):
    return factory_dir() / f"{image.sha256}.bin"


def images():
    """Every factory image, by chip."""
    return db.session.execute(db.select(FactoryImage).order_by(FactoryImage.chip)).scalars().all()


def builds():
    """``{program file SHA-256: version}`` of the factory images' apps, as device_firmware.builds."""
    known = {}
    for image in images():
        if image.elf_sha256 is None:
            try:
                image.elf_sha256 = firmware_image.read_factory(path_of(image).read_bytes()).elf_sha256
            except (OSError, firmware_image.NotAnImage):
                continue
        known[image.elf_sha256] = image.version
    return known


def image_of(image_id):
    image = db.session.get(FactoryImage, image_id)
    if image is None:
        raise NotFoundError("No such factory image.", code="factory_image_unknown")
    return image


def _invalid(message, code):
    return ValidationError(message, code=code, details={"fields": {"file": message}})


def upload(actor, data, *, file_name=None):
    """A factory image, checked and kept; it replaces the one for its chip. Returns the :class:`FactoryImage`."""
    if len(data) > MAX_SIZE:
        raise _invalid(f"A factory image is at most {MAX_SIZE // (1024 * 1024)} MB.", "factory_image_too_large")
    try:
        found = firmware_image.read_factory(data)
    except firmware_image.NotAnImage as exc:
        raise _invalid(str(exc), "factory_image_invalid") from exc
    project = device_firmware.project()
    if project and found.project != project:
        raise _invalid(f"The app in it is of \"{found.project}\", not of \"{project}\".", "factory_image_not_ours")

    image = db.session.execute(db.select(FactoryImage).filter_by(chip=found.chip)).scalar_one_or_none()
    old_path = path_of(image) if image is not None else None
    if image is None:
        image = FactoryImage(chip=found.chip)
        db.session.add(image)
    image.version, image.project = found.version[:32], found.project[:32]
    image.built, image.idf_version = found.built[:40] or None, found.idf_version[:32] or None
    image.sha256, image.size, image.elf_sha256 = found.sha256, found.size, found.elf_sha256
    image.file_name = os.path.basename(file_name or "")[:200] or None
    image.uploaded_at = utcnow()
    image.uploaded_by_user_id = actor.id if actor is not None else None

    directory = factory_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{found.sha256}.bin"
    partial = target.with_suffix(".part")
    partial.write_bytes(data)
    partial.replace(target)
    db.session.flush()
    if old_path is not None and old_path != target:
        old_path.unlink(missing_ok=True)
    log_audit_event("devices", "factory_image_uploaded", actor_user=actor,
                    metadata={"chip": image.chip, "version": image.version, "sha256": image.sha256})
    return image


def remove(actor, image):
    """A factory image deleted: boards of its chip can no longer be flashed from the portal."""
    path = path_of(image)
    log_audit_event("devices", "factory_image_removed", actor_user=actor,
                    metadata={"chip": image.chip, "version": image.version, "sha256": image.sha256})
    db.session.delete(image)
    db.session.flush()
    path.unlink(missing_ok=True)
