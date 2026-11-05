"""Shell access, on System health: the command that opens a shell on the
portal's server -- and on the forum's, where one is set up -- for whoever
looks after them. Their addresses and the login are settings, entered once;
the key stays with the person (a password manager), never here.

The command is copied into a terminal, so nothing but a login name and a
plain address may get into it: checked when saved, and again when shown --
an IPv6 address with a zone ("fe80::1%eth0", and whatever follows the "%")
is never one.

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
                    value = _plain_address(line[3:])
    except (OSError, ValueError) as exc:
        current_app.logger.info("Could not find this server's public address: %s", exc)
    _detected.update(at=now, value=value)
    return value


def describe(with_forum):
    """The login, and each server: the address entered, the one found (the portal's). Whatever is
    saved is checked again: a value that would not make a safe command is not shown."""
    saved = get_settings_map(KEYS)
    servers = [{"name": "Portal", "address": _plain_address(saved.get(PORTAL_KEY)), "detected": detected_address()}]
    if with_forum:
        servers.append({"name": "Forum", "address": _plain_address(saved.get(FORUM_KEY)), "detected": None})
    user = (saved.get(USER_KEY) or "").strip()
    return {"user": user if _USER.match(user) else None, "servers": servers}


def _plain_address(value):
    """An IP address -- without an IPv6 zone -- or a host name, as written in the command; None
    when it is neither."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return value.lower() if _HOST.match(value) else None
    if getattr(address, "scope_id", None) is not None or "%" in value:
        return None
    return str(address)


def _address(value, field):
    if not (value or "").strip():
        return ""
    plain = _plain_address(value)
    if plain is None:
        raise ValidationError("Not an address.", details={"fields": {field: "An IP address or a host name."}})
    return plain


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
