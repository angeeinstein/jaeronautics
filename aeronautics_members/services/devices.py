"""Devices that talk to the portal: card readers, and whatever comes later
(docs/devices-plan.md). Their types are in services/device_types.py.

A device signs in with a key sent as a bearer token over HTTPS:

    jadev_<key id>_<secret>

The key id finds the device; only the SHA-256 of the secret is kept, so the
key is shown once -- when the device is added or given a new one -- and never
again. The secret is 32 random bytes, so a slow hash would add nothing.
"""

import hashlib
import hmac
import secrets
from datetime import timedelta, timezone

from sqlalchemy.exc import IntegrityError

from ..db_models import Device, DeviceEvent, db
from . import ConflictError, NotFoundError, ServiceError, ValidationError
from . import device_types
from .audit import log_audit_event
from .clock import get_now_utc

KEY_PREFIX = "jadev"
#: Events older than this are deleted (the nightly clean-up).
EVENTS_KEPT = timedelta(days=90)
#: Seen this lately: shown as online.
ONLINE = timedelta(minutes=5)
#: "last seen" is written at most this often, so a device asking every few
#: seconds does not write a row each time.
SEEN_EVERY = timedelta(seconds=30)
NAME_MAX = 80
PLACE_MAX = 120


class DeviceRefused(ServiceError):
    """A request a device is not let in with: an unknown key (401), a device
    switched off (403)."""

    http_status = 401


def _aware(moment):
    return moment if moment is None or moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _hash(secret):
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _new_key():
    """``(key, key_id, hash)``: a new key, the part that finds the device, the hash kept."""
    key_id = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    return f"{KEY_PREFIX}_{key_id}_{secret}", key_id, _hash(secret)


def _type_of(slug):
    device_type = device_types.get(slug)
    if device_type is None:
        raise ValidationError("Choose a type.", code="device_type_unknown",
                              details={"fields": {"type": "Choose a type."}})
    return device_type


def _clean_name(name, *, besides=None):
    name = (name or "").strip()
    if not name:
        raise ValidationError("Give it a name.", details={"fields": {"name": "Give it a name."}})
    if len(name) > NAME_MAX:
        raise ValidationError(f"At most {NAME_MAX} characters.",
                              details={"fields": {"name": f"At most {NAME_MAX} characters."}})
    query = db.select(Device.id).where(db.func.lower(Device.name) == name.lower())
    if besides is not None:
        query = query.where(Device.id != besides.id)
    if db.session.execute(query).first() is not None:
        raise ConflictError("Another device has this name.", code="device_name_taken",
                            details={"fields": {"name": "Another device has this name."}})
    return name


def _clean_place(place):
    place = (place or "").strip()
    if len(place) > PLACE_MAX:
        raise ValidationError(f"At most {PLACE_MAX} characters.",
                              details={"fields": {"place": f"At most {PLACE_MAX} characters."}})
    return place or None


def _record(device, kind, *, name=None, event_id=None, detail=None, data=None, answer=None):
    event = DeviceEvent(device=device, at=get_now_utc(), kind=kind, name=name, event_id=event_id,
                        detail=(detail or "")[:255] or None, data=data, answer=answer)
    db.session.add(event)
    return event


# --- Managing them (Admin › Settings › Devices) ---------------------------------------------


def devices():
    return db.session.execute(db.select(Device).order_by(db.func.lower(Device.name))).scalars().all()


def device_of(device_id):
    device = db.session.get(Device, device_id)
    if device is None:
        raise NotFoundError("No such device.")
    return device


def add(actor, *, name, type, place=None, settings=None):
    """A new device, and its key -- the only time the key can be read. ``(device, key)``."""
    device_type = _type_of(type)
    key, key_id, key_hash = _new_key()
    now = get_now_utc()
    device = Device(
        name=_clean_name(name), type=device_type.slug, place=_clean_place(place),
        settings=device_types.clean_settings(device_type, settings), enabled=True, auth_method="key",
        key_id=key_id, key_hash=key_hash, key_created_at=now, created_at=now,
        created_by_user_id=actor.id if actor is not None else None,
    )
    db.session.add(device)
    db.session.flush()
    _record(device, "added", detail=f"Added as {device_type.label}")
    log_audit_event("devices", "device_added", actor_user=actor,
                    metadata={"device_id": device.id, "name": device.name, "type": device.type})
    return device, key


def change(actor, device, *, name=None, place=None, enabled=None, settings=None):
    """Its name, place, switch or settings; what is not given stays."""
    before = {"name": device.name, "place": device.place, "enabled": device.enabled, "settings": device.settings}
    if name is not None:
        device.name = _clean_name(name, besides=device)
    if place is not None:
        device.place = _clean_place(place)
    if settings is not None:
        device.settings = device_types.clean_settings(_type_of(device.type), settings, current=device.settings)
    if enabled is not None and enabled != device.enabled:
        device.enabled = enabled
        _record(device, "switched", detail="Switched on" if enabled else "Switched off")
    after = {"name": device.name, "place": device.place, "enabled": device.enabled, "settings": device.settings}
    if after != before:
        log_audit_event("devices", "device_changed", actor_user=actor, before=before, after=after,
                        metadata={"device_id": device.id})
    return device


def new_key(actor, device):
    """A new key for the device; the old one stops working at once. Returns the key."""
    key, key_id, key_hash = _new_key()
    device.key_id, device.key_hash, device.key_created_at = key_id, key_hash, get_now_utc()
    _record(device, "key", detail="New key; the old one no longer works")
    log_audit_event("devices", "device_key_replaced", actor_user=actor, metadata={"device_id": device.id})
    return key


def remove(actor, device):
    log_audit_event("devices", "device_removed", actor_user=actor,
                    metadata={"device_id": device.id, "name": device.name, "type": device.type})
    # Its events first, by hand: SQLite, which the tests use, does not cascade.
    db.session.execute(db.delete(DeviceEvent).where(DeviceEvent.device_id == device.id))
    db.session.delete(device)


def recent_events(device, limit=50):
    return device.events.limit(limit).all()


def is_online(device, now=None):
    seen = _aware(device.last_seen_at)
    return seen is not None and (now or get_now_utc()) - seen <= ONLINE


def configuration(device, key, server):
    """What a device needs to talk to the portal: the file *Download configuration* gives."""
    return {"server": server, "device_id": device.id, "name": device.name, "type": device.type, "key": key}


# --- Being one (the device API) ---------------------------------------------------------------


def authenticate(authorization):
    """The device a request's ``Authorization: Bearer <key>`` belongs to.

    Raises DeviceRefused: 401 for no key or a wrong one, 403 for a device
    switched off. A wrong secret for a known key id is recorded on that device.
    """
    scheme, _, key = (authorization or "").partition(" ")
    parts = key.strip().split("_", 2) if scheme.lower() == "bearer" else []
    if len(parts) != 3 or parts[0] != KEY_PREFIX or not parts[1] or not parts[2]:
        raise DeviceRefused("Sign in with the device's key.", code="device_key_missing")
    device = db.session.execute(db.select(Device).filter_by(key_id=parts[1])).scalar_one_or_none()
    if device is None:
        raise DeviceRefused("This key is not known.", code="device_key_unknown")
    if not hmac.compare_digest(device.key_hash, _hash(parts[2])):
        _record(device, "refused", detail="A wrong key")
        db.session.commit()
        raise DeviceRefused("This key is not known.", code="device_key_unknown")
    if not device.enabled:
        raise DeviceRefused("This device is switched off.", code="device_off", http_status=403)
    return device


def seen(device, *, address=None, firmware=None, now=None):
    """The device spoke: when, from where, with which firmware. Written at most
    every half minute, unless something changed."""
    now = now or get_now_utc()
    firmware = (firmware or "").strip()[:60] or None
    address = (address or "")[:45] or None
    last = _aware(device.last_seen_at)
    if firmware and firmware != device.firmware:
        _record(device, "firmware", detail=f"Firmware {firmware}" + (f" (was {device.firmware})" if device.firmware else ""))
        device.firmware = firmware
    if address != device.last_address or last is None or now - last >= SEEN_EVERY:
        if last is None:
            _record(device, "first", detail="Spoke for the first time")
        device.last_seen_at = now
        device.last_address = address


def hello(device, now=None):
    """The answer to a sign of life: who it is, its settings, the time."""
    device_type = _type_of(device.type)
    return {
        "device_id": device.id, "name": device.name, "type": device.type, "place": device.place,
        "settings": device_types.clean_settings(device_type, {}, current=device.settings),
        "resources": sorted(device_type.resources), "reports": sorted(device_type.reports),
        "server_time": (now or get_now_utc()).astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def resource(device, name):
    fetch = _type_of(device.type).resources.get(name)
    if fetch is None:
        raise NotFoundError(f"A {device.type} device has no \"{name}\".", code="device_resource_unknown")
    return fetch(device)


def report(device, *, event_id, name, data):
    """Something the device reports, answered by its type. The same ``event_id``
    again -- sent after a network error -- gets the first answer, and nothing is
    done twice."""
    earlier = _earlier(device, event_id)
    if earlier is not None:
        return earlier.answer
    handler = _type_of(device.type).reports.get(name)
    if handler is None:
        raise NotFoundError(f"A {device.type} device does not report \"{name}\".", code="device_report_unknown")
    answer = handler.answer(device, data or {})
    try:
        with db.session.begin_nested():
            _record(device, "reported", name=name, event_id=event_id, data=data, answer=answer)
    except IntegrityError:
        # The same report arriving twice at once: the other one was first.
        earlier = _earlier(device, event_id)
        if earlier is not None:
            return earlier.answer
        raise
    return answer


def _earlier(device, event_id):
    return db.session.execute(
        db.select(DeviceEvent).filter_by(device_id=device.id, event_id=event_id, kind="reported")
    ).scalar_one_or_none()


def try_out(actor, device, *, what, name=None, data=None):
    """What the device would be answered, asked from its admin page: the sign of
    life, a resource, or a report its type allows to be tried. Not counted as
    the device being seen; recorded as tried."""
    device_type = _type_of(device.type)
    if what == "hello":
        answer = hello(device)
    elif what == "resource":
        answer = resource(device, name or "")
    elif what == "report":
        handler = device_type.reports.get(name or "")
        if handler is None:
            raise NotFoundError(f"A {device.type} device does not report \"{name}\".", code="device_report_unknown")
        if not handler.tryable:
            raise ConflictError(f"\"{name}\" cannot be tried from here: it would really happen.",
                                code="device_report_not_tryable")
        answer = handler.answer(device, data or {})
    else:
        raise ValidationError("Try the sign of life, a resource or a report.")
    _record(device, "tried", name=name if what != "hello" else None,
            detail=f"Tried by {actor.email}" if actor is not None else "Tried", data=data, answer=answer)
    return answer


def forget_old(now=None):
    """Events older than 90 days deleted (the nightly clean-up). Returns how many."""
    cutoff = (now or get_now_utc()) - EVENTS_KEPT
    return db.session.query(DeviceEvent).filter(DeviceEvent.at < cutoff).delete(synchronize_session=False)
