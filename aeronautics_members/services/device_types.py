"""The kinds of device the portal knows (docs/devices-plan.md).

A device's firmware is written for its type -- a dispenser with a touchscreen
is not a plain card reader -- and the type, here, says what such a device is
in the portal:

- ``settings``: what the admin sets for one device of this type, on its page
  (a dispenser's seller, a reader's fixed item). The device receives them
  with its sign of life.
- ``resources``: what the device may fetch (``GET /api/v1/device/resources/
  <name>``), such as a dispenser's items and prices.
- ``reports``: what the device may report (``POST /api/v1/device/events``),
  such as a tap or a sale, and what it is answered.

A new use is a new type registered here (with ``register``) and its firmware;
the device management, keys and API around it stay as they are.
"""

from dataclasses import dataclass, field
from typing import Any, Callable

from . import ValidationError


@dataclass(frozen=True)
class Setting:
    """One setting of a type: ``kind`` is "text", "number" or "switch"."""

    name: str
    label: str
    kind: str = "text"
    default: Any = None
    help: str = ""
    max_length: int = 200
    minimum: int | None = None
    maximum: int | None = None


@dataclass(frozen=True)
class Resource:
    """Something a device may fetch: ``fetch(device)`` returns it, a dict.
    ``description`` says what it is, on the device's page."""

    fetch: Callable[[Any], dict]
    description: str = ""


@dataclass(frozen=True)
class Report:
    """Something a device may report. ``answer(device, data)`` decides and
    returns what the device is told, a dict. ``tryable``: harmless enough for
    an admin to send it as the device from its page (never a sale).
    ``description`` says what it is, on the device's page.

    A report sent again is answered from the first one, but two copies
    arriving at the same moment can both reach ``answer`` before either is
    recorded: one that changes something (a sale) keys that change by the
    report's event id as well, so it happens once.
    """

    answer: Callable[[Any, dict], dict]
    tryable: bool = False
    description: str = ""


@dataclass(frozen=True)
class DeviceType:
    slug: str
    label: str
    description: str
    settings: tuple[Setting, ...] = ()
    resources: dict[str, Resource] = field(default_factory=dict)
    reports: dict[str, Report] = field(default_factory=dict)


_TYPES: dict[str, DeviceType] = {}


def register(device_type):
    _TYPES[device_type.slug] = device_type
    return device_type


def get(slug):
    return _TYPES.get(slug)


def all_types():
    return sorted(_TYPES.values(), key=lambda device_type: device_type.label)


def clean_settings(device_type, given, *, current=None):
    """The settings as kept: every setting of the type, from ``given``, else
    from ``current``, else its default. Anything else is refused."""
    given = dict(given or {})
    known = {setting.name: setting for setting in device_type.settings}
    unknown = sorted(set(given) - set(known))
    if unknown:
        raise ValidationError(f"Not a setting of this type: {', '.join(unknown)}.", code="device_setting_unknown")
    current = current or {}
    cleaned = {}
    for name, setting in known.items():
        value = given.get(name, current.get(name, setting.default))
        cleaned[name] = _checked(setting, value)
    return cleaned


def _refuse(setting, message):
    raise ValidationError(message, code="device_setting_invalid",
                          details={"fields": {f"settings.{setting.name}": message}})


def _checked(setting, value):
    if setting.kind == "switch":
        if not isinstance(value, bool):
            _refuse(setting, f"{setting.label}: on or off.")
        return value
    if setting.kind == "number":
        if value is None or isinstance(value, bool) or not isinstance(value, int):
            _refuse(setting, f"{setting.label}: a whole number.")
        if setting.minimum is not None and value < setting.minimum:
            _refuse(setting, f"{setting.label}: at least {setting.minimum}.")
        if setting.maximum is not None and value > setting.maximum:
            _refuse(setting, f"{setting.label}: at most {setting.maximum}.")
        return value
    value = "" if value is None else value
    if not isinstance(value, str):
        _refuse(setting, f"{setting.label}: text.")
    value = value.strip()
    if len(value) > setting.max_length:
        _refuse(setting, f"{setting.label}: at most {setting.max_length} characters.")
    return value


# --- Test ---------------------------------------------------------------------------
#
# For writing firmware before any real use exists: it answers what it is sent.


def _hello(device):
    return {"greeting": (device.settings or {}).get("greeting") or "Hello", "device": device.name}


def _ping(device, data):
    return {"result": "ok", "message": (device.settings or {}).get("greeting") or "Hello", "echo": data}


register(DeviceType(
    slug="test",
    label="Test",
    description="For trying a device out: answers what it is sent. Fetch \"hello\"; report \"ping\".",
    settings=(Setting("greeting", "Greeting", default="Hello", max_length=60,
                      help="Sent back with every answer, to see a change arrive."),),
    resources={"hello": Resource(_hello, "The greeting and the device's name.")},
    reports={"ping": Report(_ping, tryable=True,
                            description="Answered with the greeting and the data it was sent.")},
))
