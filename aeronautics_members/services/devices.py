"""Devices that talk to the portal: card readers, and whatever comes later
(docs/devices-plan.md). Their types are in services/device_types.py.

A device signs in with a key sent as a bearer token over HTTPS:

    jadev_<key id>_<secret>

The key id finds the device; only the SHA-256 of the secret is kept, so the
key is shown once -- when the device is added or given a new one -- and never
again. The secret is 32 random bytes, so a slow hash would add nothing. Its
last four characters are kept to tell keys apart (``jadev_<key id>_…AbCd``),
which tells nothing useful about the other 39.
"""

import hashlib
import hmac
import json
import secrets
from datetime import timedelta, timezone

from sqlalchemy.exc import IntegrityError

from ..db_models import Device, DeviceEvent, DeviceRequest, db
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
#: Each device's requests kept, the newest.
REQUESTS_KEPT = 100
TAIL = 4
NAME_MAX = 80
PLACE_MAX = 120


class DeviceRefused(ServiceError):
    """A request a device is not let in with: an unknown key (401), a device
    switched off (403). ``device`` is the device, when the key named one."""

    http_status = 401

    def __init__(self, message, *, device=None, **kwargs):
        super().__init__(message, **kwargs)
        self.device = device


def _aware(moment):
    return moment if moment is None or moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _hash(secret):
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _new_key():
    """``(key, key_id, hash, tail)``: a new key, the part that finds the device,
    the hash kept, and its last characters."""
    key_id = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    return f"{KEY_PREFIX}_{key_id}_{secret}", key_id, _hash(secret), secret[-TAIL:]


def key_hint(device):
    """The key as it may be shown: its start, and its end where that is kept. None when revoked."""
    if device.key_revoked_at is not None:
        return None
    return f"{KEY_PREFIX}_{device.key_id}_…{device.key_tail or ''}"


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
    key, key_id, key_hash, key_tail = _new_key()
    now = get_now_utc()
    device = Device(
        name=_clean_name(name), type=device_type.slug, place=_clean_place(place),
        settings=device_types.clean_settings(device_type, settings), enabled=True, auth_method="key",
        key_id=key_id, key_hash=key_hash, key_tail=key_tail, key_created_at=now, created_at=now,
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
    replaced = device.key_revoked_at is None
    key, key_id, key_hash, key_tail = _new_key()
    device.key_id, device.key_hash, device.key_tail = key_id, key_hash, key_tail
    device.key_created_at, device.key_revoked_at = get_now_utc(), None
    _record(device, "key", detail="New key; the old one no longer works" if replaced else "New key")
    log_audit_event("devices", "device_key_replaced", actor_user=actor, metadata={"device_id": device.id})
    return key


def revoke_key(actor, device):
    """The key stops working, and none replaces it: the device stays, with its
    settings and history, until it is given a new key."""
    if device.key_revoked_at is not None:
        return device
    # Its hash stays, so the old key is answered "revoked" rather than "unknown";
    # authenticate() refuses it either way.
    device.key_revoked_at = get_now_utc()
    _record(device, "key", detail="Key revoked; no key works until a new one is made")
    log_audit_event("devices", "device_key_revoked", actor_user=actor, metadata={"device_id": device.id})
    return device


def key_last_used(device):
    """When the present key was last used: the device's last sign of life since the key was made."""
    seen, made = _aware(device.last_seen_at), _aware(device.key_created_at)
    return seen if seen is not None and made is not None and seen >= made else None


def remove(actor, device):
    log_audit_event("devices", "device_removed", actor_user=actor,
                    metadata={"device_id": device.id, "name": device.name, "type": device.type})
    # By hand, as the database would: SQLite, which the tests use, does not
    # cascade. Its events, requests and waits go; sales made on it and cards
    # last used on it stay, without it.
    from ..db_models import Card, CardLinkWait, CreditEntry

    db.session.execute(db.delete(DeviceEvent).where(DeviceEvent.device_id == device.id))
    db.session.execute(db.delete(DeviceRequest).where(DeviceRequest.device_id == device.id))
    db.session.execute(db.delete(CardLinkWait).where(CardLinkWait.device_id == device.id))
    db.session.execute(db.update(CreditEntry).where(CreditEntry.device_id == device.id).values(device_id=None))
    db.session.execute(db.update(Card).where(Card.last_used_device_id == device.id).values(last_used_device_id=None))
    db.session.delete(device)


def recent_events(device, limit=50):
    return device.events.limit(limit).all()


def recent_requests(device, limit=REQUESTS_KEPT):
    return db.session.execute(
        db.select(DeviceRequest).filter_by(device_id=device.id).order_by(DeviceRequest.id.desc()).limit(limit)
    ).scalars().all()


def record_request(device, *, method, path, status, code=None, address=None, now=None):
    """A request the device made and its answer; only the last 100 are kept."""
    db.session.add(DeviceRequest(device_id=device.id, at=now or get_now_utc(), method=method[:8],
                                 path=(path or "")[:200], status=status, code=(code or None) and code[:60],
                                 address=(address or "")[:45] or None))
    db.session.flush()
    oldest_kept = db.session.execute(
        db.select(DeviceRequest.id).filter_by(device_id=device.id)
        .order_by(DeviceRequest.id.desc()).offset(REQUESTS_KEPT - 1).limit(1)
    ).scalar_one_or_none()
    if oldest_kept is not None:
        db.session.execute(db.delete(DeviceRequest).where(DeviceRequest.device_id == device.id,
                                                          DeviceRequest.id < oldest_kept))


#: A run of requests held back by the rate limit is listed once in this time.
LIMITED_LISTED_EVERY = timedelta(minutes=1)


def record_limited(device, *, method, path, address=None, now=None):
    """A request held back by the rate limit (429): listed with the others, but
    a run of them only once a minute, so a device asking far too often cannot
    fill the database. Returns whether it was listed."""
    now = now or get_now_utc()
    last = db.session.execute(
        db.select(DeviceRequest).filter_by(device_id=device.id).order_by(DeviceRequest.id.desc()).limit(1)
    ).scalar_one_or_none()
    if last is not None and last.status == 429 and now - _aware(last.at) < LIMITED_LISTED_EVERY:
        return False
    record_request(device, method=method, path=path, status=429, code="rate_limited", address=address, now=now)
    return True


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
    switched off -- with the device, when the key named one, for the caller
    to record (``refused``).
    """
    scheme, _, key = (authorization or "").partition(" ")
    parts = key.strip().split("_", 2) if scheme.lower() == "bearer" else []
    if len(parts) != 3 or parts[0] != KEY_PREFIX or not parts[1] or not parts[2]:
        raise DeviceRefused("Sign in with the device's key.", code="device_key_missing")
    device = db.session.execute(db.select(Device).filter_by(key_id=parts[1])).scalar_one_or_none()
    if device is None:
        raise DeviceRefused("This key is not known.", code="device_key_unknown")
    if not hmac.compare_digest(device.key_hash, _hash(parts[2])):
        raise DeviceRefused("This key is not known.", code="device_key_unknown", device=device)
    if device.key_revoked_at is not None:
        raise DeviceRefused("This device's key was revoked.", code="device_key_revoked", device=device)
    if not device.enabled:
        raise DeviceRefused("This device is switched off.", code="device_off", http_status=403, device=device)
    return device


def refused(device, exc):
    """A request turned away for a wrong key: an event on the device it named."""
    if exc.code == "device_key_unknown":
        _record(device, "refused", detail="A wrong key")


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
    offered = _type_of(device.type).resources.get(name)
    if offered is None:
        raise NotFoundError(f"A {device.type} device has no \"{name}\".", code="device_resource_unknown")
    return offered.fetch(device)


#: What one report may carry, as JSON: it is kept with the event.
REPORT_DATA_MAX = 4096


def report(device, *, event_id, name, data):
    """Something the device reports, answered by its type. The same ``event_id``
    again -- sent after a network error -- gets the first answer, and nothing is
    done twice."""
    # In UTF-8 bytes as sent, not as escaped ASCII: "é" is two bytes, not six.
    if len(json.dumps(data or {}, separators=(",", ":"), ensure_ascii=False).encode()) > REPORT_DATA_MAX:
        raise ValidationError(f"A report carries at most {REPORT_DATA_MAX} bytes of data.",
                              code="device_report_too_large")
    earlier = _earlier(device, event_id)
    if earlier is not None:
        return earlier.answer
    handler = _type_of(device.type).reports.get(name)
    if handler is None:
        raise NotFoundError(f"A {device.type} device does not report \"{name}\".", code="device_report_unknown")
    answer = handler.answer(device, data or {}, event_id)
    kept = handler.keep(data or {}) if handler.keep else data
    try:
        with db.session.begin_nested():
            _record(device, "reported", name=name, event_id=event_id, data=kept, answer=answer)
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


def earlier_answer(device, event_id):
    """The answer a report with this event id was given already, read afresh
    and locked: for a type whose report changes something, called after it
    has locked the device, so a copy that arrived at the same moment waits
    and then finds the first one's answer (device_types.Report)."""
    from .locking import locked

    earlier = db.session.execute(
        locked(db.select(DeviceEvent).filter_by(device_id=device.id, event_id=event_id, kind="reported"))
    ).scalar_one_or_none()
    return earlier.answer if earlier is not None else None


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
        answer = handler.answer(device, data or {}, None)
        data = handler.keep(data or {}) if handler.keep else data
    else:
        raise ValidationError("Try the sign of life, a resource or a report.")
    _record(device, "tried", name=name if what != "hello" else None,
            detail=f"Tried by {actor.email}" if actor is not None else "Tried", data=data, answer=answer)
    return answer


def forget_old(now=None):
    """Events older than 90 days deleted (the nightly clean-up). Returns how many."""
    cutoff = (now or get_now_utc()) - EVENTS_KEPT
    return db.session.query(DeviceEvent).filter(DeviceEvent.at < cutoff).delete(synchronize_session=False)
