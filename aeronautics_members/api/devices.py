"""Devices (services/devices.py, docs/devices-plan.md).

Two sides:

- **The device API** (``/device``), for card readers and whatever comes
  later: signed in with the device's key as a bearer token, never a session,
  so no cookies and no CSRF token (exempted in app.py).
- **Their management** (``/admin/devices``), for admins: Admin › Settings ›
  Devices.
"""

from typing import Any, Literal

from flask import Response, current_app, jsonify, request, send_file
from flask_login import current_user
from pydantic import Field

from ..app import limiter, rate_limit_network
from ..config import RATELIMIT_DEVICE, RATELIMIT_DEVICE_PAIR, RATELIMIT_DEVICE_PER_IP
from ..db_models import Team, db
from ..permissions import Permission
from ..security_utils import build_public_url
from ..services import NotFoundError, ServiceError, ValidationError, credit_sales, device_firmware, device_pairing
from ..services import card_links, device_starter, device_types
from ..services import devices as devices_service
from ._core import Model, UtcDateTime, endpoint

#: The owner value for the association (a team's is its slug).
ASSOCIATION = "association"


def device_rate_key():
    """A device's own budget, by the key id in its Authorization header."""
    _scheme, _, key = (request.headers.get("Authorization") or "").partition(" ")
    parts = key.strip().split("_", 2)
    if len(parts) == 3 and parts[0] == devices_service.KEY_PREFIX and parts[1]:
        return f"device:{parts[1][:16]}"
    return f"device-unknown:{rate_limit_network()}"


def _as_device(answer, *, listed=True):
    """Sign the request's device in, answer it with ``answer(device)``, and
    record the request and its answer on the device -- a refusal too, when
    the key named a device -- for watching it from its page. ``listed=False``:
    a question it asks every few seconds is listed only when refused."""
    device = None
    try:
        device = devices_service.authenticate(request.headers.get("Authorization"))
        devices_service.seen(device, address=request.remote_addr,
                             firmware=request.headers.get("X-Device-Firmware"),
                             hardware=request.headers.get("X-Device-Hardware"),
                             board=request.headers.get("X-Device-Board"),
                             types=devices_service.parse_types(request.headers.get("X-Device-Types")))
        result = answer(device)
        status = result.status_code if isinstance(result, Response) else 200
        # "Nothing new" to a device asking every few seconds is not listed: its
        # last sign of life says it is there, and the list keeps what happened.
        if status != 304 and listed:
            _record_request(device, status)
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


class DeviceFirmwareOfferOut(Model):
    #: The version to install, as its app description names it.
    version: str
    size: int
    #: Of the whole file: check it before installing.
    sha256: str


class DeviceHelloOut(Model):
    device_id: int
    #: Its random ID: "K7MQ-2X9A".
    code: str
    #: Its name, else its code.
    name: str
    #: Its type, which its firmware is written for.
    type: str
    #: Whose it is: a team's name, or the association's.
    owner: str
    place: str | None
    #: The settings of its type, as set on its page.
    settings: dict[str, Any]
    #: What it may fetch (GET /device/resources/<name>) and report (POST /device/events).
    resources: list[str]
    reports: list[str]
    #: SHA-256 of "<code>:<backup code>": the backup code opens the settings on
    #: the device when the portal cannot be reached.
    backup_code_hash: str | None
    #: Firmware to install (GET /device/firmware/file), or none.
    firmware: DeviceFirmwareOfferOut | None
    #: Ask for the sign of life this often, in seconds, with If-None-Match:
    #: the answer is 304 when nothing changed.
    poll_every: int
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
    """A device's sign of life: who it is, its settings, firmware to install, the
    time. Bearer key. With ``If-None-Match`` (the ETag of the last answer):
    304 when nothing but the time changed."""

    def answer(device):
        body = devices_service.hello(device)
        tag = devices_service.hello_tag(body)
        if tag in [part.strip() for part in (request.headers.get("If-None-Match") or "").split(",")]:
            return Response(status=304, headers={"ETag": tag})
        response = jsonify(DeviceHelloOut(**body).model_dump(mode="json"))
        response.headers["ETag"] = tag
        return response

    return _as_device(answer)


@endpoint("GET", "/device/resources/<name>", response=DeviceResourceOut, public=True, tag="Device")
@_limited
def device_resource(name):
    """Something the device's type offers it, such as a dispenser's items. Bearer key."""
    return _as_device(lambda device: DeviceResourceOut(resource=name, data=devices_service.resource(device, name)))


class DeviceCardLinkOut(Model):
    #: "waiting" (keep the QR code), "opened" (a phone has it), "linked", "expired".
    state: Literal["waiting", "opened", "linked", "expired"]
    #: Once linked: whom to greet. May be null.
    first_name: str | None = None


@endpoint("GET", "/device/card-links/<int:link_id>", response=DeviceCardLinkOut, public=True, tag="Device")
@_limited
def device_card_link(link_id):
    """Where the link of a QR code the device shows stands: asked every few
    seconds while it shows it (services/card_links.py). Bearer key; not
    listed on the device's page unless refused."""
    return _as_device(lambda device: DeviceCardLinkOut(**card_links.state_for_device(device, link_id)),
                      listed=False)


@endpoint("POST", "/device/events", response=DeviceReportOut, body=DeviceReportIn, public=True, tag="Device")
@_limited
def device_report(body):
    """Something the device reports, answered by its type. Bearer key."""
    return _as_device(lambda device: DeviceReportOut(
        event_id=body.event_id,
        answer=devices_service.report(device, event_id=body.event_id, name=body.kind, data=body.data),
    ))


class DeviceFirmwareFileOut(Model):
    version: str
    size: int
    sha256: str
    #: When it was built, as the file says.
    built: str | None
    #: Where to download it, signed in with the device's key.
    url: str


class DeviceFirmwareOut(Model):
    #: The version the device said it runs (``X-Device-Firmware``).
    running: str | None
    #: The firmware it should run; none when none is chosen.
    wanted: DeviceFirmwareFileOut | None
    #: Install ``wanted`` now: it runs another version, and has not reported
    #: that it could not install this one.
    install: bool


class DeviceFirmwareReportIn(Model):
    #: The version it is about.
    version: str = Field(min_length=1, max_length=32)
    #: "started" (installing) or "failed" -- then that version is not offered
    #: again until an admin asks. That it worked shows with its next request.
    result: Literal["started", "failed"]
    #: Why it failed, in words.
    detail: str | None = Field(default=None, max_length=500)


class DeviceManagerIn(Model):
    #: The card's ID as read: hex.
    card: str = Field(min_length=1, max_length=40)


class DeviceManagerOut(Model):
    #: Whether this card's holder may open the device's settings.
    allowed: bool
    #: Their first name, to greet them; none when not allowed.
    name: str | None


def _firmware_out(device):
    release = device_firmware.wanted(device)
    wanted = None
    if release is not None:
        wanted = DeviceFirmwareFileOut(version=release.version, size=release.size, sha256=release.sha256,
                                       built=release.built, url=build_public_url("api.device_firmware_file"))
    return DeviceFirmwareOut(running=device.firmware, wanted=wanted,
                             install=device_firmware.offer(device) is not None)


@endpoint("GET", "/device/firmware", response=DeviceFirmwareOut, public=True, tag="Device")
@_limited
def device_firmware_info():
    """The firmware the device should run, and whether to install it now. Bearer key."""
    return _as_device(_firmware_out)


@endpoint("GET", "/device/firmware/file", public=True, tag="Device", produces="application/octet-stream")
@_limited
def device_firmware_file():
    """The firmware file the device should run, to install over the air. Bearer
    key; ``Range`` requests resume a download."""

    def answer(device):
        release = device_firmware.wanted(device)
        path = device_firmware.path_of(release) if release is not None else None
        if path is None or not path.is_file():
            raise NotFoundError("No firmware is chosen for this device.", code="firmware_none")
        if (request.headers.get("Range") or "bytes=0-").replace(" ", "").startswith("bytes=0-"):
            device_firmware.downloading(device, release)
        response = send_file(path, mimetype="application/octet-stream", conditional=True, etag=release.sha256,
                             download_name=f"firmware-{release.version}.bin", max_age=0)
        response.headers["X-Firmware-Version"] = release.version
        response.headers["X-Firmware-SHA256"] = release.sha256
        return response

    return _as_device(answer)


@endpoint("POST", "/device/firmware", response=DeviceFirmwareOut, body=DeviceFirmwareReportIn, public=True,
          tag="Device")
@_limited
def device_firmware_report(body):
    """How an update goes: started, or failed. Bearer key."""

    def answer(device):
        device_firmware.reported(device, result=body.result, version=body.version, detail=body.detail)
        return _firmware_out(device)

    return _as_device(answer)


@endpoint("POST", "/device/manager", response=DeviceManagerOut, body=DeviceManagerIn, public=True, tag="Device")
@_limited
def device_manager(body):
    """Whether a card tapped on the device opens its settings there: its holder
    manages devices in the portal (admins). Asked each time. Bearer key."""
    return _as_device(lambda device: DeviceManagerOut(**devices_service.manager(device, body.card)))


class DevicePairingStartOut(Model):
    #: For people: show it on the screen, "K7Q-4X9".
    code: str
    #: The device's alone: never shown. Ask with it (POST /device/pair/poll).
    pairing_id: str
    #: The approval page with the code filled in: show it as a QR code.
    url: str
    #: Seconds the code works.
    expires_in: int
    #: Ask this often, in seconds.
    poll_every: int


class DevicePairingPollIn(Model):
    pairing_id: str = Field(min_length=10, max_length=100)


class DevicePairingPollOut(Model):
    #: "waiting", "refused" (start again only if asked to), or "paired".
    status: Literal["waiting", "refused", "paired"]
    poll_every: int | None = None
    expires_in: int | None = None
    #: Once paired: the device's key, this once. Keep it; nobody else has it.
    key: str | None = None
    #: Once paired: what its sign of life (GET /device) answers.
    device: DeviceHelloOut | None = None


@endpoint("POST", "/device/pair/start", response=DevicePairingStartOut, public=True, status=201, tag="Device")
@limiter.limit(RATELIMIT_DEVICE_PAIR, key_func=rate_limit_network)
def device_pair_start():
    """An unpaired device asks to be paired. No key: it says what it is with the
    usual headers (X-Device-Hardware, -Firmware, -Board, -Types)."""
    pairing, secret = device_pairing.start(
        hardware=request.headers.get("X-Device-Hardware"), firmware=request.headers.get("X-Device-Firmware"),
        board=request.headers.get("X-Device-Board"),
        types=devices_service.parse_types(request.headers.get("X-Device-Types")), address=request.remote_addr,
    )
    db.session.commit()
    code = device_pairing.shown(pairing.code)
    return DevicePairingStartOut(
        code=code, pairing_id=secret, url=build_public_url("admin.admin_settings_device_pair", code=code),
        expires_in=int(device_pairing.LASTS.total_seconds()),
        poll_every=int(device_pairing.POLL_EVERY.total_seconds()))


@endpoint("POST", "/device/pair/poll", response=DevicePairingPollOut, body=DevicePairingPollIn, public=True,
          tag="Device")
@_limited
def device_pair_poll(body):
    """How the pairing stands; once approved, the device's key, this once."""
    try:
        answer = device_pairing.poll(body.pairing_id)
    except ServiceError:
        db.session.commit()  # an expired pairing is deleted either way
        raise
    db.session.commit()
    if answer.get("device") is not None:
        answer = {**answer, "device": DeviceHelloOut(**answer["device"])}
    return DevicePairingPollOut(**answer)


#: The device API's views, exempt from CSRF in app.py: devices have no session.
DEVICE_VIEWS = (device_hello, device_resource, device_report, device_firmware_info, device_firmware_file,
                device_firmware_report, device_manager, device_pair_start, device_pair_poll, device_card_link)
DEVICE_ENDPOINTS = frozenset(f"api.{view.__name__}" for view in DEVICE_VIEWS)


# --- Managing them ------------------------------------------------------------------------------


class DeviceChoiceOut(Model):
    value: Any
    label: str
    #: The heading it is listed under: the seller of an item.
    group: str
    #: Whose devices may choose it: "association" or a team's slug.
    owner: str


class DeviceSettingOut(Model):
    name: str
    label: str
    kind: Literal["text", "number", "switch", "choice", "choices"]
    default: Any
    help: str
    max_length: int
    minimum: int | None
    maximum: int | None
    #: For a choice: what may be chosen now, for each owner.
    choices: list[DeviceChoiceOut] | None = None
    #: Its owner changes it on their own page; otherwise only admins.
    owner_may_change: bool = False
    #: For "choices": at most this many.
    most: int | None = None
    #: Set with the owner's price list, on their Sales page, not on the device's pages.
    with_prices: bool = False


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
    #: Its random ID: "K7MQ-2X9A".
    code: str
    name: str | None
    #: Its name, else its code.
    label: str
    type: str
    type_label: str
    #: "association" or the owning team's slug.
    owner: str
    owner_label: str
    place: str | None
    description: str | None
    #: The MAC address of the board it last spoke from.
    hardware_id: str | None
    settings: dict[str, Any]
    enabled: bool
    #: Seen in the last five minutes.
    online: bool
    last_seen_at: UtcDateTime | None
    last_address: str | None
    firmware: str | None
    #: The board, as its firmware names it.
    board: str | None
    #: The types its firmware can be used as; none when it has not said (then any).
    supported_types: list[str] | None
    #: Opens its settings on the device when the portal cannot be reached.
    backup_code: str | None
    #: The firmware it should run, and how that stands: "none" chosen,
    #: "current", "waiting" (not installed yet), "failed" (it could not).
    firmware_wanted: str | None
    firmware_state: Literal["none", "current", "waiting", "failed"]
    #: The firmware chosen for this device alone; none: what every device runs.
    firmware_release_id: int | None
    #: Why it could not install ``firmware_wanted``, as it said.
    firmware_failed_detail: str | None
    #: Its key as it may be shown: "jadev_3f9a1c2b7d10_…AbCd"; none when revoked.
    key_hint: str | None
    #: Its key came from pairing: nobody saw it, so there is none to rotate or revoke.
    paired: bool = False
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


class DeviceOwnerOut(Model):
    #: "association" or a team's slug.
    value: str
    label: str


class DevicePairingOut(Model):
    #: As the device shows it: "K7Q-4X9".
    code: str
    #: The board's MAC address, its firmware and board, what it can be used as.
    hardware_id: str | None
    firmware: str | None
    board: str | None
    supported_types: list[str] | None
    address: str | None
    created_at: UtcDateTime
    expires_at: UtcDateTime


class DevicesOut(Model):
    items: list[DeviceOut]
    #: Devices waiting to be paired, the oldest first.
    pairings: list[DevicePairingOut]
    types: list[DeviceTypeOut]
    #: Who a device can belong to: the association, and each active team.
    owners: list[DeviceOwnerOut]
    #: Where devices talk to: the device API's address.
    server: str


class DeviceDetailOut(Model):
    device: DeviceOut
    events: list[DeviceEventOut]
    #: Its last 100 requests and their answers, the newest first.
    requests: list[DeviceRequestOut]
    server: str


class DeviceIn(Model):
    #: Optional: its code stands in for a name.
    name: str | None = Field(default=None, max_length=200)
    type: str = Field(max_length=40)
    #: "association" or a team's slug.
    owner: str = Field(default=ASSOCIATION, max_length=60)
    place: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=600)
    settings: dict[str, Any] = Field(default_factory=dict)


class DeviceChangeIn(Model):
    #: Left out: as it is; "": none.
    name: str | None = Field(default=None, max_length=200)
    place: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=600)
    owner: str | None = Field(default=None, max_length=60)
    #: Another type: its settings start afresh. Only one its firmware can be.
    type: str | None = Field(default=None, max_length=40)
    enabled: bool | None = None
    settings: dict[str, Any] | None = None


class DeviceKeyOut(Model):
    device: DeviceOut
    #: The key, shown this once.
    key: str
    #: The file *Download configuration* gives: server, device_id, name, type, key.
    configuration: dict[str, Any]
    #: *Download starter*: everything firmware for it needs, with the key, as
    #: Markdown (services/device_starter.py).
    starter: str
    starter_name: str


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


def owner_value(team):
    return team.slug if team is not None else ASSOCIATION


def _owners():
    """Who a device can belong to: the association, then each active team."""
    teams = db.session.execute(
        db.select(Team).filter_by(status="active").order_by(db.func.lower(Team.name))
    ).scalars().all()
    return [None, *teams]


def owner_of(value):
    """The team an owner value names (None for the association), or a refusal."""
    if value in (None, "", ASSOCIATION):
        return None
    team = db.session.execute(db.select(Team).filter_by(slug=value, status="active")).scalar_one_or_none()
    if team is None:
        raise ValidationError("Choose who it belongs to.", code="device_owner_unknown",
                              details={"fields": {"owner": "Choose who it belongs to."}})
    return team


def setting_out(setting, owners):
    """A setting as a form shows it, its choices for each of ``owners``."""
    choices = None
    if setting.choices:
        choices = [DeviceChoiceOut(value=c.value, label=c.label, group=c.group, owner=owner_value(owner))
                   for owner in owners for c in setting.choices(owner)]
    return DeviceSettingOut(name=setting.name, label=setting.label, kind=setting.kind, default=setting.default,
                            help=setting.help, max_length=setting.max_length, minimum=setting.minimum,
                            maximum=setting.maximum, choices=choices, owner_may_change=setting.owner_may_change,
                            most=setting.most, with_prices=setting.with_prices)


def _type_out(device_type, owners=None):
    owners = _owners() if owners is None else owners
    return DeviceTypeOut(
        slug=device_type.slug, label=device_type.label, description=device_type.description,
        settings=[setting_out(s, owners) for s in device_type.settings],
        resources=[DeviceResourceInfoOut(name=name, description=offered.description)
                   for name, offered in sorted(device_type.resources.items())],
        reports=[DeviceReportInfoOut(name=name, description=report.description, tryable=report.tryable)
                 for name, report in sorted(device_type.reports.items())],
    )


def pairing_out(pairing):
    return DevicePairingOut(code=device_pairing.shown(pairing.code), hardware_id=pairing.hardware_id,
                            firmware=pairing.firmware, board=pairing.board, supported_types=pairing.supported_types,
                            address=pairing.address, created_at=pairing.created_at, expires_at=pairing.expires_at)


def device_out(device):
    device_type = device_types.get(device.type)
    wanted = device_firmware.wanted(device)
    return DeviceOut(
        id=device.id, code=device.code, name=device.name, label=devices_service.label(device), type=device.type,
        type_label=device_type.label if device_type is not None else device.type,
        owner=owner_value(device.team), owner_label=devices_service.owner_name(device),
        place=device.place, description=device.description, hardware_id=device.hardware_id,
        settings=device.settings or {}, enabled=device.enabled,
        online=devices_service.is_online(device), last_seen_at=device.last_seen_at,
        last_address=device.last_address, firmware=device.firmware, board=device.board,
        supported_types=device.supported_types, backup_code=device.backup_code,
        firmware_wanted=wanted.version if wanted is not None else None,
        firmware_state=device_firmware.state(device), firmware_release_id=device.firmware_release_id,
        firmware_failed_detail=device.firmware_failed_detail if device_firmware.state(device) == "failed" else None,
        key_hint=devices_service.key_hint(device), paired=device.paired_at is not None,
        key_created_at=device.key_created_at,
        key_last_used_at=devices_service.key_last_used(device), key_revoked_at=device.key_revoked_at,
        created_at=device.created_at,
    )


def _event_out(event):
    return DeviceEventOut(id=event.id, at=event.at, kind=event.kind, name=event.name, event_id=event.event_id,
                          detail=event.detail, data=event.data, answer=event.answer)


def _key_out(device, key):
    server = _server()
    return DeviceKeyOut(device=device_out(device), key=key,
                        configuration=devices_service.configuration(device, key, server),
                        starter=device_starter.starter(device, key, server),
                        starter_name=device_starter.file_name(device))


MANAGE = [Permission.DEVICES_MANAGE]


@endpoint("GET", "/admin/devices", response=DevicesOut, permissions=MANAGE, tag="Admin")
def admin_devices():
    """Every device, the types there are, and where devices talk to."""
    owners = _owners()
    return DevicesOut(items=[device_out(device) for device in devices_service.devices()],
                      pairings=[pairing_out(pairing) for pairing in device_pairing.waiting()],
                      types=[_type_out(t, owners) for t in device_types.all_types()],
                      owners=[DeviceOwnerOut(value=owner_value(o), label=credit_sales.seller_name(o)) for o in owners],
                      server=_server())


@endpoint("POST", "/admin/devices", response=DeviceKeyOut, body=DeviceIn,
          permissions=MANAGE, status=201, tag="Admin")
def admin_device_add(body):
    """Add a device. Its key is in the answer, the only time it can be read."""
    device, key = devices_service.add(current_user, name=body.name, type=body.type, place=body.place,
                                      description=body.description, settings=body.settings,
                                      owner=owner_of(body.owner))
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
    """Change a device's owner, name, place, description, settings, or switch it on or off."""
    keep = devices_service.KEEP
    device = devices_service.change(
        current_user, devices_service.device_of(device_id),
        name=keep if body.name is None else body.name, place=keep if body.place is None else body.place,
        description=keep if body.description is None else body.description,
        owner=keep if body.owner is None else owner_of(body.owner), enabled=body.enabled, settings=body.settings,
        type=body.type)
    db.session.commit()
    return device_out(device)


@endpoint("POST", "/admin/devices/<int:device_id>/backup-code", response=DeviceOut, permissions=MANAGE, tag="Admin")
def admin_device_backup_code(device_id):
    """Another backup code: the device gets it with its next sign of life."""
    device = devices_service.device_of(device_id)
    devices_service.new_backup_code(current_user, device)
    db.session.commit()
    return device_out(device)


class DeviceFirmwareChoiceIn(Model):
    #: The firmware this device runs; none: what every device runs.
    release_id: int | None = None


@endpoint("PUT", "/admin/devices/<int:device_id>/firmware", response=DeviceOut, body=DeviceFirmwareChoiceIn,
          permissions=MANAGE, tag="Admin")
def admin_device_firmware(device_id, body):
    """Which firmware this device runs: one of its own -- to try a version on one device first -- or every device's."""
    device = devices_service.device_of(device_id)
    release = device_firmware.release_of(body.release_id) if body.release_id is not None else None
    device_firmware.choose_for_device(current_user, device, release)
    db.session.commit()
    return device_out(device)


@endpoint("POST", "/admin/devices/<int:device_id>/firmware/retry", response=DeviceOut, permissions=MANAGE,
          tag="Admin")
def admin_device_firmware_retry(device_id):
    """Offer the firmware the device could not install once more."""
    device = devices_service.device_of(device_id)
    device_firmware.try_again(current_user, device)
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
