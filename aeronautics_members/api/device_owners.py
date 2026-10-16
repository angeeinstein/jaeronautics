"""A device's owner runs it (docs/devices-plan.md, "Owners"): a team's leads
and team treasurer on the team's management page, admins and the treasurer
for the association's on Admin › Credit. They name and place their devices,
choose what each does within its type (a reader's item), switch them on or
off, and see what they reported lately -- "so that they can actually see
that this is working". What is technical -- the key, the type, the owner,
removing -- stays with admins (api/devices.py).
"""

from typing import Any

from flask_login import current_user
from pydantic import Field

from ..db_models import db
from ..permissions import Permission
from ..services import NotFoundError, device_types
from ..services import devices as devices_service
from ..services import teams as teams_service
from ._core import Model, UtcDateTime, endpoint
from .devices import DeviceSettingOut, setting_out

TAG = "Devices"


class OwnedReportOut(Model):
    at: UtcDateTime
    #: In words: "…E5F6 bought Beer, €1.50; €3.50 left".
    detail: str


class OwnedDeviceOut(Model):
    id: int
    #: Its random ID: "K7MQ-2X9A".
    code: str
    name: str | None
    #: Its name, else its code.
    label: str
    type_label: str
    place: str | None
    description: str | None
    enabled: bool
    #: Seen in the last five minutes.
    online: bool
    last_seen_at: UtcDateTime | None
    settings: dict[str, Any]
    #: How many outputs it can switch (a sale can switch them); none when it has not said.
    outputs: int | None = None
    #: What the owner may choose on it, with the choices that are theirs.
    fields: list[DeviceSettingOut]
    #: What it reported lately, newest first.
    recent: list[OwnedReportOut]


class OwnedDevicesOut(Model):
    items: list[OwnedDeviceOut]


class OwnedDeviceChangeIn(Model):
    #: Left out: as it is; "": none.
    name: str | None = Field(default=None, max_length=200)
    place: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=600)
    enabled: bool | None = None
    settings: dict[str, Any] | None = None


def _owned_out(device):
    device_type = device_types.get(device.type)
    fields = [setting_out(setting, [device.team]) for setting in (device_type.settings if device_type else ())
              if setting.owner_may_change]
    return OwnedDeviceOut(
        id=device.id, code=device.code, name=device.name, label=devices_service.label(device),
        type_label=device_type.label if device_type else device.type, place=device.place,
        description=device.description, enabled=device.enabled, online=devices_service.is_online(device),
        last_seen_at=device.last_seen_at, settings=device.settings or {}, outputs=device.outputs, fields=fields,
        recent=[OwnedReportOut(at=event.at, detail=event.detail or event.name or "Reported")
                for event in devices_service.recent_reports(device)],
    )


def _owned(owner):
    return OwnedDevicesOut(items=[_owned_out(device) for device in devices_service.devices(owner)])


def _their_device(owner, device_id):
    device = devices_service.device_of(device_id)
    if device.team_id != (owner.id if owner is not None else None):
        raise NotFoundError("No such device.")
    return device


def _change(owner, device_id, body):
    keep = devices_service.KEEP
    devices_service.change(
        current_user, _their_device(owner, device_id), by_owner=True,
        name=keep if body.name is None else body.name, place=keep if body.place is None else body.place,
        description=keep if body.description is None else body.description,
        enabled=body.enabled, settings=body.settings)
    db.session.commit()
    return _owned(owner)


# --- A team's ------------------------------------------------------------------------


def _devices_team(slug):
    from .team_manage import _may
    from .teams import _team

    team = _team(slug)
    _may(team, teams_service.TeamPermission.DEVICES)
    return team


@endpoint("GET", "/teams/<slug>/manage/devices", response=OwnedDevicesOut, tag=TAG)
def team_devices(slug):
    """The team's devices, what each does, and what they reported lately."""
    return _owned(_devices_team(slug))


@endpoint("PATCH", "/teams/<slug>/manage/devices/<int:device_id>", response=OwnedDevicesOut,
          body=OwnedDeviceChangeIn, tag=TAG)
def team_device_change(slug, device_id, body):
    """Name, place or describe one of the team's devices, choose what it does, switch it on or off."""
    return _change(_devices_team(slug), device_id, body)


# --- The association's -----------------------------------------------------------------

MANAGE = [Permission.CREDIT_MANAGE]


@endpoint("GET", "/admin/credit/devices", response=OwnedDevicesOut, permissions=MANAGE, tag=TAG)
def association_devices():
    """The association's own devices, what each does, and what they reported lately."""
    return _owned(None)


@endpoint("PATCH", "/admin/credit/devices/<int:device_id>", response=OwnedDevicesOut, body=OwnedDeviceChangeIn,
          permissions=MANAGE, tag=TAG)
def association_device_change(device_id, body):
    """Name, place or describe one of the association's devices, choose what it does, switch it on or off."""
    return _change(None, device_id, body)
