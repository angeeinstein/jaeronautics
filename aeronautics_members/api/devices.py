"""Devices (services/devices.py, docs/devices-plan.md).

Two sides:

- **The device API** (``/device``), for card readers and whatever comes
  later: signed in with the device's key as a bearer token, never a session,
  so no cookies and no CSRF token (exempted in app.py).
- **Their management** (``/admin/devices``), for admins: Admin › Settings ›
  Devices.
"""

from typing import Any, Literal

from flask import current_app, request
from flask_login import current_user
from pydantic import Field

from ..app import limiter, rate_limit_network
from ..config import RATELIMIT_DEVICE, RATELIMIT_DEVICE_PER_IP
from ..db_models import db
from ..permissions import Permission
from ..security_utils import build_public_url
from ..services import ServiceError, device_types
from ..services import devices as devices_service
from ._core import Model, UtcDateTime, endpoint


def device_rate_key():
    """A device's own budget, by the key id in its Authorization header."""
    _scheme, _, key = (request.headers.get("Authorization") or "").partition(" ")
    parts = key.strip().split("_", 2)
    if len(parts) == 3 and parts[0] == devices_service.KEY_PREFIX and parts[1]:
        return f"device:{parts[1][:16]}"
    return f"device-unknown:{rate_limit_network()}"


def _as_device(answer):
    """Sign the request's device in, answer it with ``answer(device)``, and
    record the request and its answer on the device -- a refusal too, when
    the key named a device -- for watching it from its page."""
    device = None
    try:
        device = devices_service.authenticate(request.headers.get("Authorization"))
        devices_service.seen(device, address=request.remote_addr,
                             firmware=request.headers.get("X-Device-Firmware"))
        result = answer(device)
        _record_request(device, 200)
        db.session.commit()
        return result
    except ServiceError as exc:
        db.session.rollback()
        device = device or getattr(exc, "device", None)
        if device is not None:
            if isinstance(exc, devices_service.DeviceRefused):
                devices_service.refused(device, exc)
            _record_request(device, exc.http_status, exc.code)
            db.session.commit()
        raise
    except Exception:
        db.session.rollback()
        if device is not None:
            _record_request(device, 500, "internal_error")
            db.session.commit()
        raise


def record_if_limited_device():
    """Called for every 429: Flask-Limiter refuses before the view runs, so a
    device held back by its limit would never reach ``_as_device``. The
    request is listed on the device its key names (a run of them once a
    minute); a key naming no device is not."""
    if request.endpoint not in DEVICE_ENDPOINTS:
        return
    try:
        try:
            device = devices_service.authenticate(request.headers.get("Authorization"))
        except devices_service.DeviceRefused as exc:
            device = exc.device
        if device is not None and devices_service.record_limited(
                device, method=request.method, path=request.path, address=request.remote_addr):
            db.session.commit()
    except Exception:  # noqa: BLE001 -- the 429 is answered whatever happens here
        db.session.rollback()
        current_app.logger.exception("Could not list a rate-limited device request")


def _record_request(device, status, code=None):
    devices_service.record_request(device, method=request.method, path=request.path, status=status, code=code,
                                   address=request.remote_addr)


# --- The device API ---------------------------------------------------------------------------


class DeviceHelloOut(Model):
    device_id: int
    name: str
    #: Its type, which its firmware is written for.
    type: str
    place: str | None
    #: The settings of its type, as set on its page.
    settings: dict[str, Any]
    #: What it may fetch (GET /device/resources/<name>) and report (POST /device/events).
    resources: list[str]
    reports: list[str]
    #: The portal's clock, UTC: "2026-10-12T07:38:11Z".
    server_time: str


class DeviceResourceOut(Model):
    resource: str
    data: dict[str, Any]


class DeviceReportIn(Model):
    #: The device's own id for this report: the same id again gets the first answer.
    event_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")
    #: What it reports, one of its type's reports ("ping").
    kind: str = Field(min_length=1, max_length=40)
    data: dict[str, Any] = Field(default_factory=dict)


class DeviceReportOut(Model):
    event_id: str
    answer: dict[str, Any]


DEVICE_LIMITS = (
    limiter.limit(RATELIMIT_DEVICE, key_func=device_rate_key),
    limiter.limit(RATELIMIT_DEVICE_PER_IP, key_func=rate_limit_network),
)


def _limited(view):
    for limit in DEVICE_LIMITS:
        view = limit(view)
    return view


@endpoint("GET", "/device", response=DeviceHelloOut, public=True, tag="Device")
@_limited
def device_hello():
    """A device's sign of life: who it is, its settings, the time. Bearer key."""
    return _as_device(lambda device: DeviceHelloOut(**devices_service.hello(device)))


@endpoint("GET", "/device/resources/<name>", response=DeviceResourceOut, public=True, tag="Device")
@_limited
def device_resource(name):
    """Something the device's type offers it, such as a dispenser's items. Bearer key."""
    return _as_device(lambda device: DeviceResourceOut(resource=name, data=devices_service.resource(device, name)))


@endpoint("POST", "/device/events", response=DeviceReportOut, body=DeviceReportIn, public=True, tag="Device")
@_limited
def device_report(body):
    """Something the device reports, answered by its type. Bearer key."""
    return _as_device(lambda device: DeviceReportOut(
        event_id=body.event_id,
        answer=devices_service.report(device, event_id=body.event_id, name=body.kind, data=body.data),
    ))


#: The device API's views, exempt from CSRF in app.py: devices have no session.
DEVICE_VIEWS = (device_hello, device_resource, device_report)
DEVICE_ENDPOINTS = frozenset(f"api.{view.__name__}" for view in DEVICE_VIEWS)


# --- Managing them ------------------------------------------------------------------------------


class DeviceChoiceOut(Model):
    value: Any
    label: str
    #: The heading it is listed under: the seller of an item.
    group: str


class DeviceSettingOut(Model):
    name: str
    label: str
    kind: Literal["text", "number", "switch", "choice"]
    default: Any
    help: str
    max_length: int
    minimum: int | None
    maximum: int | None
    #: For a choice: what may be chosen now.
    choices: list[DeviceChoiceOut] | None = None


class DeviceResourceInfoOut(Model):
    name: str
    description: str


class DeviceReportInfoOut(Model):
    name: str
    description: str
    #: Harmless enough to be sent as the device from its page.
    tryable: bool


class DeviceTypeOut(Model):
    slug: str
    label: str
    description: str
    settings: list[DeviceSettingOut]
    #: What a device of this type may fetch (GET /device/resources/<name>).
    resources: list[DeviceResourceInfoOut]
    #: What it may report (POST /device/events).
    reports: list[DeviceReportInfoOut]


class DeviceOut(Model):
    id: int
    name: str
    type: str
    type_label: str
    place: str | None
    settings: dict[str, Any]
    enabled: bool
    #: Seen in the last five minutes.
    online: bool
    last_seen_at: UtcDateTime | None
    last_address: str | None
    firmware: str | None
    #: Its key as it may be shown: "jadev_3f9a1c2b7d10_…AbCd"; none when revoked.
    key_hint: str | None
    key_created_at: UtcDateTime
    #: When the present key was last used (its last sign of life since the key was made).
    key_last_used_at: UtcDateTime | None
    key_revoked_at: UtcDateTime | None
    created_at: UtcDateTime


class DeviceEventOut(Model):
    id: int
    at: UtcDateTime
    kind: str
    name: str | None
    event_id: str | None
    detail: str | None
    data: dict[str, Any] | None
    answer: dict[str, Any] | None


class DeviceRequestOut(Model):
    id: int
    at: UtcDateTime
    method: str
    path: str
    status: int
    #: The error's code, for an answer that refused.
    code: str | None
    address: str | None


class DevicesOut(Model):
    items: list[DeviceOut]
    types: list[DeviceTypeOut]
    #: Where devices talk to: the device API's address.
    server: str


class DeviceDetailOut(Model):
    device: DeviceOut
    events: list[DeviceEventOut]
    #: Its last 100 requests and their answers, the newest first.
    requests: list[DeviceRequestOut]
    server: str


class DeviceIn(Model):
    name: str = Field(max_length=200)
    type: str = Field(max_length=40)
    place: str | None = Field(default=None, max_length=300)
    settings: dict[str, Any] = Field(default_factory=dict)


class DeviceChangeIn(Model):
    name: str | None = Field(default=None, max_length=200)
    place: str | None = Field(default=None, max_length=300)
    enabled: bool | None = None
    settings: dict[str, Any] | None = None


class DeviceKeyOut(Model):
    device: DeviceOut
    #: The key, shown this once.
    key: str
    #: The file *Download configuration* gives: server, device_id, name, type, key.
    configuration: dict[str, Any]


#: The event id a report tried from the device's page is shown with.
TRY_EVENT_ID = "try"


class DeviceTryIn(Model):
    what: Literal["hello", "resource", "report"]
    name: str | None = Field(default=None, max_length=40)
    data: dict[str, Any] = Field(default_factory=dict)


class DeviceTryOut(Model):
    answer: dict[str, Any]


def _server():
    return build_public_url("api.device_hello")


def _type_out(device_type):
    return DeviceTypeOut(
        slug=device_type.slug, label=device_type.label, description=device_type.description,
        settings=[DeviceSettingOut(name=s.name, label=s.label, kind=s.kind, default=s.default, help=s.help,
                                   max_length=s.max_length, minimum=s.minimum, maximum=s.maximum,
                                   choices=[DeviceChoiceOut(value=c.value, label=c.label, group=c.group)
                                            for c in s.choices()] if s.choices else None)
                  for s in device_type.settings],
        resources=[DeviceResourceInfoOut(name=name, description=offered.description)
                   for name, offered in sorted(device_type.resources.items())],
        reports=[DeviceReportInfoOut(name=name, description=report.description, tryable=report.tryable)
                 for name, report in sorted(device_type.reports.items())],
    )


def device_out(device):
    device_type = device_types.get(device.type)
    return DeviceOut(
        id=device.id, name=device.name, type=device.type,
        type_label=device_type.label if device_type is not None else device.type,
        place=device.place, settings=device.settings or {}, enabled=device.enabled,
        online=devices_service.is_online(device), last_seen_at=device.last_seen_at,
        last_address=device.last_address, firmware=device.firmware,
        key_hint=devices_service.key_hint(device), key_created_at=device.key_created_at,
        key_last_used_at=devices_service.key_last_used(device), key_revoked_at=device.key_revoked_at,
        created_at=device.created_at,
    )


def _event_out(event):
    return DeviceEventOut(id=event.id, at=event.at, kind=event.kind, name=event.name, event_id=event.event_id,
                          detail=event.detail, data=event.data, answer=event.answer)


def _key_out(device, key):
    return DeviceKeyOut(device=device_out(device), key=key,
                        configuration=devices_service.configuration(device, key, _server()))


MANAGE = [Permission.DEVICES_MANAGE]


@endpoint("GET", "/admin/devices", response=DevicesOut, permissions=MANAGE, tag="Admin")
def admin_devices():
    """Every device, the types there are, and where devices talk to."""
    return DevicesOut(items=[device_out(device) for device in devices_service.devices()],
                      types=[_type_out(t) for t in device_types.all_types()], server=_server())


@endpoint("POST", "/admin/devices", response=DeviceKeyOut, body=DeviceIn,
          permissions=MANAGE, status=201, tag="Admin")
def admin_device_add(body):
    """Add a device. Its key is in the answer, the only time it can be read."""
    device, key = devices_service.add(current_user, name=body.name, type=body.type, place=body.place,
                                      settings=body.settings)
    db.session.commit()
    return _key_out(device, key)


@endpoint("GET", "/admin/devices/<int:device_id>", response=DeviceDetailOut, permissions=MANAGE, tag="Admin")
def admin_device(device_id):
    """One device and its recent events."""
    device = devices_service.device_of(device_id)
    return DeviceDetailOut(
        device=device_out(device),
        events=[_event_out(e) for e in devices_service.recent_events(device)],
        requests=[DeviceRequestOut(id=r.id, at=r.at, method=r.method, path=r.path, status=r.status, code=r.code,
                                   address=r.address) for r in devices_service.recent_requests(device)],
        server=_server(),
    )


@endpoint("PATCH", "/admin/devices/<int:device_id>", response=DeviceOut, body=DeviceChangeIn,
          permissions=MANAGE, tag="Admin")
def admin_device_change(device_id, body):
    """Change a device's name, place, settings, or switch it on or off."""
    device = devices_service.change(current_user, devices_service.device_of(device_id), name=body.name,
                                    place=body.place, enabled=body.enabled, settings=body.settings)
    db.session.commit()
    return device_out(device)


@endpoint("POST", "/admin/devices/<int:device_id>/key", response=DeviceKeyOut, permissions=MANAGE, tag="Admin")
def admin_device_key(device_id):
    """A new key; the old one stops working at once. Shown this once."""
    device = devices_service.device_of(device_id)
    key = devices_service.new_key(current_user, device)
    db.session.commit()
    return _key_out(device, key)


@endpoint("DELETE", "/admin/devices/<int:device_id>/key", response=DeviceOut, permissions=MANAGE, tag="Admin")
def admin_device_key_revoke(device_id):
    """Revoke the key without a new one: the device stays, and cannot sign in until it gets one."""
    device = devices_service.revoke_key(current_user, devices_service.device_of(device_id))
    db.session.commit()
    return device_out(device)


@endpoint("DELETE", "/admin/devices/<int:device_id>", permissions=MANAGE, tag="Admin")
def admin_device_remove(device_id):
    """Remove a device, its key and its events."""
    devices_service.remove(current_user, devices_service.device_of(device_id))
    db.session.commit()


@endpoint("POST", "/admin/devices/<int:device_id>/try", response=DeviceTryOut, body=DeviceTryIn,
          permissions=MANAGE, tag="Admin")
def admin_device_try(device_id, body):
    """What the device would be answered -- the body exactly as the device API
    sends it -- without it being marked as seen."""
    device = devices_service.device_of(device_id)
    answer = devices_service.try_out(current_user, device, what=body.what, name=body.name, data=body.data)
    db.session.commit()
    if body.what == "hello":
        sent = DeviceHelloOut(**answer)
    elif body.what == "resource":
        sent = DeviceResourceOut(resource=body.name, data=answer)
    else:
        sent = DeviceReportOut(event_id=TRY_EVENT_ID, answer=answer)
    return DeviceTryOut(answer=sent.model_dump(mode="json"))
