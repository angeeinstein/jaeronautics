"""Pairing a device by the code on its screen (services/device_pairing.py,
docs/devices-plan.md, "Pairing"): Admin › Settings › Devices › Pair. The
device's own side -- asking to be paired, fetching its key -- is in
api/devices.py with the rest of the device API.
"""

from typing import Any

from flask_login import current_user
from pydantic import Field

from ..db_models import db
from ..services import device_pairing
from ..services import devices as devices_service
from ._core import Model, endpoint
from .devices import ASSOCIATION, MANAGE, DeviceOut, DevicePairingOut, device_out, owner_of, pairing_out


class DevicePairIn(Model):
    #: The device it replaces: it gets the new board, and the old board's key
    #: stops. Otherwise a new device, from the fields below.
    replaces: int | None = None
    type: str | None = Field(default=None, max_length=40)
    #: "association" or a team's slug.
    owner: str = Field(default=ASSOCIATION, max_length=60)
    name: str | None = Field(default=None, max_length=200)
    place: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=600)
    settings: dict[str, Any] = Field(default_factory=dict)


@endpoint("GET", "/admin/devices/pairings/<code>", response=DevicePairingOut, permissions=MANAGE, tag="Admin")
def admin_device_pairing(code):
    """The device waiting with this code: what it says it is."""
    return pairing_out(device_pairing.find(code))


@endpoint("POST", "/admin/devices/pairings/<code>", response=DeviceOut, body=DevicePairIn, permissions=MANAGE,
          status=201, tag="Admin")
def admin_device_pair(code, body):
    """Pair the device waiting with this code: as a new device, or as the one it
    replaces. It fetches its key itself; nobody sees it."""
    pairing = device_pairing.find(code)
    if body.replaces is not None:
        device = device_pairing.approve(current_user, pairing, replaces=devices_service.device_of(body.replaces))
    else:
        device = device_pairing.approve(current_user, pairing, type=body.type or "", owner=owner_of(body.owner),
                                        name=body.name, place=body.place, description=body.description,
                                        settings=body.settings)
    db.session.commit()
    return device_out(device)


@endpoint("DELETE", "/admin/devices/pairings/<code>", permissions=MANAGE, tag="Admin")
def admin_device_pairing_refuse(code):
    """Turn the device waiting with this code away; it is told so."""
    device_pairing.refuse(current_user, device_pairing.find(code))
    db.session.commit()
