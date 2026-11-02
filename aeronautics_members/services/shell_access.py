"""Shell access, on System health: the command that opens a shell on the
portal's server -- and on the forum's, where one is set up -- for whoever
looks after them. Their addresses and the login are settings, entered once;
the key stays with the person (a password manager), never here.

The portal's own address is found by itself when none is entered: the one it
reaches the internet from, as Cloudflare sees it. On a server with its own
public address (Azure) that is the one to connect to; behind a router at home
it is the router's, so an address entered by hand comes first. When the two
differ, the page says so: the server's address may have changed.
"""

import ipaddress
import re
import urllib.request
from datetime import timedelta

from flask import current_app

from . import ValidationError
from .audit import log_audit_event
from .clock import get_now_utc
from .settings import get_settings_map, set_setting_value

USER_KEY = "shell_access.user"
PORTAL_KEY = "shell_access.portal_address"
FORUM_KEY = "shell_access.forum_address"
KEYS = [USER_KEY, PORTAL_KEY, FORUM_KEY]

# Cloudflare's own answer to "who is asking", over IPv4 (its address, not a name).
TRACE_URL = "https://1.1.1.1/cdn-cgi/trace"
DETECT_EVERY = timedelta(hours=6)
_detected = {"at": None, "value": None}

_USER = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$")
_HOST = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def detected_address():
    """The address this server reaches the internet from; None when it cannot be found."""
    if current_app.config.get("SHELL_ADDRESS_CHECK", not current_app.testing) is False:
        return None
    now = get_now_utc()
    if _detected["at"] and now - _detected["at"] < DETECT_EVERY:
        return _detected["value"]
    value = None
    try:
        request = urllib.request.Request(TRACE_URL, headers={"User-Agent": "jaeronautics-health"})
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 (a fixed address)
            for line in response.read(4096).decode("utf-8", "replace").splitlines():
                if line.startswith("ip="):
                    value = str(ipaddress.ip_address(line[3:].strip()))
    except (OSError, ValueError) as exc:
        current_app.logger.info("Could not find this server's public address: %s", exc)
    _detected.update(at=now, value=value)
    return value


def describe(with_forum):
    """The login, and each server: the address entered, the one found (the portal's)."""
    saved = get_settings_map(KEYS)
    servers = [{"name": "Portal", "address": saved.get(PORTAL_KEY) or None, "detected": detected_address()}]
    if with_forum:
        servers.append({"name": "Forum", "address": saved.get(FORUM_KEY) or None, "detected": None})
    return {"user": saved.get(USER_KEY) or None, "servers": servers}


def _address(value, field):
    value = (value or "").strip()
    if not value:
        return ""
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        if _HOST.match(value):
            return value.lower()
    raise ValidationError("Not an address.", details={"fields": {field: "An IP address or a host name."}})


def save(actor, *, user, portal_address, forum_address):
    user = (user or "").strip()
    if user and not _USER.match(user):
        raise ValidationError("Not a login name.", details={
            "fields": {"user": "Lower-case letters, digits, dots, dashes and underscores."}})
    values = {USER_KEY: user, PORTAL_KEY: _address(portal_address, "portal_address"),
              FORUM_KEY: _address(forum_address, "forum_address")}
    before = get_settings_map(KEYS)
    changed = [key.split(".", 1)[1] for key, value in values.items() if (before.get(key) or "") != value]
    for key, value in values.items():
        set_setting_value(key, value)
    if changed:
        log_audit_event("system", "shell_access_changed", actor_user=actor, target_user=actor,
                        before={key: before.get(key) for key in KEYS}, after=values)
    return changed
