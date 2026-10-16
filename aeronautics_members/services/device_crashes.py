"""Crash reports from the devices (docs/devices-plan.md, "Crash reports";
docs/device-protocol.md, section 6.5).

On a crash ESP-IDF keeps a core dump and the board restarts. The device then
sends what ESP-IDF read from it -- the task, where it stopped, the call path,
its build -- and, when asked, the dump itself: an ELF, decoded on a computer
with the firmware's program file (``espcoredump.py info_corefile``). Kept
whole, as sent. Seen only by admins, on the device's page, *Crashes*.

``crash_id`` is the dump's SHA-256: the same crash sent again is answered as
the first time. Kept 90 days, with the device's events; at most
:data:`FILES_KEPT` dump files a device -- a new one lets the oldest go, its
summary staying. Which version crashed (its ``build``) is matched in the API
(device_firmware.builds, device_factory.builds), so this service needs
neither.
"""

import hashlib
import re
import shutil
from datetime import timedelta
from pathlib import Path

from flask import current_app

from ..db_models import DeviceCrash, db, utcnow
from . import NotFoundError, ValidationError
from .audit import log_audit_event
from .clock import get_now_utc

#: A core dump is at most the 128 KB flash area it is kept in.
MAX_SIZE = 128 * 1024
FILES_KEPT = 20
KEPT = timedelta(days=90)
REASONS = ("panic", "task_watchdog", "interrupt_watchdog")
MAX_BACKTRACE = 16

_HEX64 = re.compile(r"[0-9a-f]{64}")
_BUILD = re.compile(r"[0-9a-f]{16}")
_ADDRESS = re.compile(r"0x[0-9A-Fa-f]{1,8}")
ELF_MAGIC = b"\x7fELF"


def crash_dir():
    configured = current_app.config.get("DEVICE_CRASH_DIR")
    return Path(configured) if configured else Path(current_app.root_path).parent / "storage" / "devices" / "crashes"


def path_of(crash):
    return crash_dir() / str(crash.device_id) / f"{crash.crash_id}.elf"


def _invalid(field, message, code="validation_error"):
    return ValidationError(message, code=code, details={"fields": {field: message}})


def report(device, *, crash_id, build, task, pc, backtrace, backtrace_corrupted, reset_reason, size,
           firmware=None):
    """A crash the device reports. Returns ``(crash, upload)``: whether to send
    the dump now. The same ``crash_id`` again is the same crash."""
    crash_id, build = (crash_id or "").strip().lower(), (build or "").strip().lower()
    if not _HEX64.fullmatch(crash_id):
        raise _invalid("crash_id", "The dump's SHA-256, 64 hex characters.")
    existing = db.session.execute(
        db.select(DeviceCrash).filter_by(device_id=device.id, crash_id=crash_id)).scalar_one_or_none()
    if existing is not None:
        return existing, _wants_file(existing)
    if not _BUILD.fullmatch(build):
        raise _invalid("build", "The first 16 hex characters of the program file's SHA-256.")
    if not _ADDRESS.fullmatch(pc or ""):
        raise _invalid("pc", "An address: 0x4209F87C.")
    if len(backtrace) > MAX_BACKTRACE or not all(_ADDRESS.fullmatch(address or "") for address in backtrace):
        raise _invalid("backtrace", f"At most {MAX_BACKTRACE} addresses: 0x4209F879.")
    if reset_reason not in REASONS:
        raise _invalid("reset_reason", f"One of {', '.join(REASONS)}.")
    task = " ".join((task or "").split())
    if not task or len(task) > 32:
        raise _invalid("task", "The task's name, at most 32 characters.")
    if not 0 < size <= MAX_SIZE:
        raise _invalid("size", f"The dump's size, at most {MAX_SIZE} bytes.")
    crash = DeviceCrash(
        device_id=device.id, crash_id=crash_id, received_at=utcnow(), reported_by=(firmware or "")[:32] or None,
        build=build, task=task, pc=pc.upper().replace("0X", "0x"),
        backtrace=[address.upper().replace("0X", "0x") for address in backtrace],
        backtrace_corrupted=bool(backtrace_corrupted), reset_reason=reset_reason, size=size,
    )
    db.session.add(crash)
    db.session.flush()
    return crash, True


def _wants_file(crash):
    return crash.file_received_at is None or not path_of(crash).is_file()


def store_file(device, crash_id, data):
    """The dump of a crash reported before: checked by its SHA-256 and its ELF header, and kept."""
    crash = db.session.execute(
        db.select(DeviceCrash).filter_by(device_id=device.id, crash_id=(crash_id or "").lower())
    ).scalar_one_or_none()
    if crash is None:
        raise NotFoundError("Report the crash first: POST /api/v1/device/crash.", code="crash_unknown")
    if len(data) > MAX_SIZE:
        raise ValidationError(f"A core dump is at most {MAX_SIZE} bytes.", code="crash_file_too_large")
    if hashlib.sha256(data).hexdigest() != crash.crash_id:
        raise ValidationError("This is not the dump of this crash: its SHA-256 is another.",
                              code="crash_file_mismatch")
    if not data.startswith(ELF_MAGIC):
        raise ValidationError("A core dump is an ELF file; this one does not start like one.",
                              code="crash_file_not_elf")
    if not _wants_file(crash):
        return crash
    target = path_of(crash)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    partial.write_bytes(data)
    partial.replace(target)
    crash.file_received_at = utcnow()
    _keep_newest_files(device)
    return crash


def _keep_newest_files(device):
    """At most :data:`FILES_KEPT` dumps: the oldest go, their summaries stay."""
    with_file = db.session.execute(
        db.select(DeviceCrash).where(DeviceCrash.device_id == device.id, DeviceCrash.file_received_at.isnot(None))
        .order_by(DeviceCrash.received_at.desc(), DeviceCrash.id.desc())
    ).scalars().all()
    for crash in with_file[FILES_KEPT:]:
        path_of(crash).unlink(missing_ok=True)
        crash.file_received_at = None


def crashes(device):
    """The device's crashes, the newest first."""
    return db.session.execute(
        db.select(DeviceCrash).where(DeviceCrash.device_id == device.id)
        .order_by(DeviceCrash.received_at.desc(), DeviceCrash.id.desc())
    ).scalars().all()


def crash_of(device, crash_pk):
    crash = db.session.get(DeviceCrash, crash_pk)
    if crash is None or crash.device_id != device.id:
        raise NotFoundError("No such crash report.", code="crash_unknown")
    return crash


def has_file(crash):
    return crash.file_received_at is not None and path_of(crash).is_file()


def remove(actor, crash):
    """A crash report deleted by an admin, with its dump."""
    log_audit_event("devices", "device_crash_deleted", actor_user=actor,
                    metadata={"device_id": crash.device_id, "crash_id": crash.crash_id})
    path_of(crash).unlink(missing_ok=True)
    db.session.delete(crash)
    db.session.flush()


def forget_device(device):
    """A device removed: its crash reports and dumps go with it."""
    db.session.execute(db.delete(DeviceCrash).where(DeviceCrash.device_id == device.id))
    shutil.rmtree(crash_dir() / str(device.id), ignore_errors=True)


def forget_old(now=None):
    """Reports older than 90 days deleted, with their dumps (the nightly clean-up). Returns how many."""
    cutoff = (now or get_now_utc()) - KEPT
    old = db.session.execute(db.select(DeviceCrash).where(DeviceCrash.received_at < cutoff)).scalars().all()
    for crash in old:
        path_of(crash).unlink(missing_ok=True)
        db.session.delete(crash)
    return len(old)
