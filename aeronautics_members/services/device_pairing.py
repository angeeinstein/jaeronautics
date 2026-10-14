"""Pairing: a device gets its key without anyone copying it (docs/devices-plan.md,
"Pairing"). The scheme TVs use -- "go to this address and enter the code".

1. The unpaired device asks to be paired (:func:`start`), saying what it is
   with the headers it sends with every request. It gets a short code for
   people, shown on its screen with a QR code to the approval page, and a
   long pairing id that only it and the portal know (kept here as a hash).
2. An admin enters the code, or scans the QR code, and says what the device
   is: a new device (owner, type, name) or the one it replaces (:func:`approve`).
   Or turns it away (:func:`refuse`).
3. The device asks with its pairing id every few seconds (:func:`poll`).
   Once approved, it is answered its key -- the one time the key exists in
   words, and never shown to anyone -- with what its sign of life would say;
   the pairing is gone then.

A code lasts ten minutes. A device that missed its answer (the network)
starts again, and the admin pairs it as replacing the device it became.
"""

import hashlib
import secrets
from datetime import timedelta, timezone

from ..db_models import DevicePairing, db
from . import ConflictError, NotFoundError, ServiceError, ValidationError
from . import device_types
from . import devices as devices_service
from .audit import log_audit_event
from .clock import get_now_utc
from .locking import locked

#: As a device's code: no 0/O, 1/I/L, so one read off a screen is not misheard.
CODE_ALPHABET = devices_service.CODE_ALPHABET
CODE_LENGTH = 6
#: How long a code works, and an approved pairing waits for its device.
LASTS = timedelta(minutes=10)
#: How often a device asks whether it was approved.
POLL_EVERY = timedelta(seconds=5)
#: At most this many pairings wait at once: more is not someone pairing devices.
MOST_WAITING = 50


class PairingRefused(ServiceError):
    """A pairing request turned away: too many waiting (429), asked too often (429)."""

    http_status = 429


def _aware(moment):
    return moment if moment is None or moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _hash(secret):
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def shown(code):
    """A code as people see it: "K7Q-4X9"."""
    return f"{code[:3]}-{code[3:]}"


def normalize(code):
    """A code as typed -- "k7q 4x9", "K7Q-4X9" -- as it is kept: "K7Q4X9"."""
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def _new_code():
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if db.session.execute(db.select(DevicePairing.id).filter_by(code=code)).first() is None:
            return code


def forget_expired(now=None):
    """Pairings past their ten minutes deleted. Returns how many."""
    now = now or get_now_utc()
    return db.session.query(DevicePairing).filter(DevicePairing.expires_at < now).delete(synchronize_session=False)


def _is_waiting(pairing, now):
    return pairing.approved_at is None and pairing.refused_at is None and _aware(pairing.expires_at) > now


def waiting(now=None):
    """The pairings waiting for an admin, the oldest first."""
    now = now or get_now_utc()
    found = db.session.execute(
        db.select(DevicePairing).where(DevicePairing.approved_at.is_(None), DevicePairing.refused_at.is_(None))
        .order_by(DevicePairing.created_at, DevicePairing.id)
    ).scalars().all()
    return [pairing for pairing in found if _is_waiting(pairing, now)]


def find(code, now=None):
    """The pairing waiting under ``code`` (as typed)."""
    now = now or get_now_utc()
    pairing = db.session.execute(db.select(DevicePairing).filter_by(code=normalize(code))).scalar_one_or_none()
    if pairing is None or not _is_waiting(pairing, now):
        raise NotFoundError("No device is waiting with this code. Codes last ten minutes: check the device's screen.",
                            code="pairing_unknown")
    return pairing


def start(*, hardware=None, firmware=None, board=None, types=None, address=None, now=None):
    """A device asks to be paired. Returns ``(pairing, pairing id)``; the id is
    the device's alone and is not kept."""
    now = now or get_now_utc()
    forget_expired(now)
    count = db.session.execute(db.select(db.func.count(DevicePairing.id))).scalar() or 0
    if count >= MOST_WAITING:
        raise PairingRefused("Too many devices are waiting to be paired. Try again in a few minutes.",
                             code="pairing_busy")
    secret = secrets.token_urlsafe(32)
    pairing = DevicePairing(
        code=_new_code(), secret_hash=_hash(secret), hardware_id=devices_service.normalize_mac(hardware),
        firmware=(firmware or "").strip()[:60] or None, board=(board or "").strip()[:60] or None,
        supported_types=types, address=(address or "")[:45] or None, created_at=now, expires_at=now + LASTS,
    )
    db.session.add(pairing)
    db.session.flush()
    return pairing, secret


def _check_type(pairing, type):
    if pairing.supported_types is not None and type not in pairing.supported_types:
        device_type = device_types.get(type)
        label = device_type.label if device_type is not None else type
        message = f"This device's firmware cannot be a {label.lower()}."
        raise ValidationError(message, code="device_type_unsupported", details={"fields": {"type": message}})


def _describe(device, pairing):
    """What the pairing says of the board, onto the device."""
    device.hardware_id = pairing.hardware_id or device.hardware_id
    device.firmware = pairing.firmware or device.firmware
    device.board = pairing.board or device.board
    if pairing.supported_types is not None:
        device.supported_types = pairing.supported_types


def approve(actor, pairing, *, type=None, owner=None, name=None, place=None, description=None, settings=None,
            replaces=None, now=None):
    """The device is paired: as a new device (``type``, ``owner`` -- a team or
    None for the association -- and the rest as when adding one), or as
    ``replaces``, which then gets the new board, and the old board's key stops
    working at once. Returns the device; the device fetches its key itself."""
    now = now or get_now_utc()
    pairing = db.session.execute(locked(db.select(DevicePairing).filter_by(id=pairing.id))).scalar_one_or_none()
    if pairing is None or not _is_waiting(pairing, now):
        raise ConflictError("This pairing is no longer waiting.", code="pairing_gone")
    board = pairing.hardware_id or "an unknown board"
    if replaces is not None:
        device = replaces
        _check_type(pairing, device.type)
        devices_service.new_key(actor, device)
        _describe(device, pairing)
        devices_service.record_event(device, "paired", detail=f"Paired with board {board}; the old board's key stopped")
    else:
        _check_type(pairing, type)
        device, _unused_key = devices_service.add(actor, type=type, owner=owner, name=name, place=place,
                                                  description=description, settings=settings)
        _describe(device, pairing)
        devices_service.record_event(device, "paired", detail=f"Paired with board {board}")
    pairing.device_id = device.id
    pairing.approved_at = now
    pairing.approved_by_user_id = actor.id if actor is not None else None
    pairing.expires_at = now + LASTS
    log_audit_event("devices", "device_paired", actor_user=actor,
                    metadata={"device_id": device.id, "code": device.code, "board": pairing.hardware_id,
                              "replaces": replaces is not None})
    return device


def refuse(actor, pairing, now=None):
    """Turned away: the device is told so when it asks next."""
    now = now or get_now_utc()
    if not _is_waiting(pairing, now):
        raise ConflictError("This pairing is no longer waiting.", code="pairing_gone")
    pairing.refused_at = now
    log_audit_event("devices", "device_pairing_refused", actor_user=actor,
                    metadata={"board": pairing.hardware_id, "address": pairing.address})


def poll(secret, *, now=None):
    """The device asks how its pairing stands. ``{"status": "waiting"}``,
    ``{"status": "refused"}``, or ``{"status": "paired", "key": ..., "device":
    <its sign of life>}`` -- the key this once; the pairing is gone after the
    last two."""
    now = now or get_now_utc()
    found = db.session.execute(
        locked(db.select(DevicePairing).filter_by(secret_hash=_hash(secret or "")))
    ).scalar_one_or_none()
    if found is None:
        raise NotFoundError("This pairing is not known: start again.", code="pairing_unknown")
    if _aware(found.expires_at) <= now:
        db.session.delete(found)
        raise NotFoundError("This pairing has expired: start again.", code="pairing_expired")
    last = _aware(found.last_poll_at)
    # A second's leeway: a device's clock and the network are not exact.
    if last is not None and now - last < POLL_EVERY - timedelta(seconds=1):
        raise PairingRefused(f"Ask at most every {int(POLL_EVERY.total_seconds())} seconds.",
                             code="pairing_too_fast")
    found.last_poll_at = now
    if found.refused_at is not None:
        db.session.delete(found)
        return {"status": "refused"}
    if found.approved_at is None or found.device is None:
        return {"status": "waiting", "poll_every": int(POLL_EVERY.total_seconds()),
                "expires_in": max(0, int((_aware(found.expires_at) - now).total_seconds()))}
    device = found.device
    key = devices_service.give_key(device, detail="Key given to the device at pairing")
    db.session.delete(found)
    return {"status": "paired", "key": key, "device": devices_service.hello(device, now=now)}
