"""Updates installed by themselves, at night (docs/automatic-updates-plan.md, part 2).

A switch on Settings › Updates. When it is on, the job ``automatic-updates``
(a timer, 03:30 in the portal's time zone, one of the night's steps behind its
lock) asks for the newest version the same way the button does --
it writes the request the privileged runner acts on, and nothing more, so the
unprivileged side still cannot choose what an update is. It asks only when
there is a newer version, CI has passed for that very version, and no update
is running.

A version an automatic update could not install -- it failed and undid
itself, or somebody went back from it -- is not tried again night after
night: the attempt is remembered, and only a newer version is tried next.

Off where the portal runs already, until an administrator switches it on; on
for a new installation (install.sh switches it on there).
"""

import json

from ..db_models import Setting, db
from .audit import log_audit_event
from .clock import get_now_utc

ENABLED_KEY = "automatic_updates"
# What the last night's run did: {"at": ..., "outcome": ..., "revision": ...}.
LAST_RUN_KEY = "automatic_updates.last_run"
# The version the last automatic update asked for.
ATTEMPT_KEY = "automatic_updates.attempt"
AT = "03:30"

# What a run can come to; the page says each in words.
OFF = "off"
UP_TO_DATE = "up_to_date"
REQUESTED = "requested"
UNREACHABLE = "unreachable"
CI_NOT_PASSED = "ci_not_passed"
BUSY = "busy"
FAILED_BEFORE = "failed_before"
NO_RUNNER = "no_runner"


def _get(key):
    setting = db.session.get(Setting, key)
    return setting.value if setting is not None else None


def _set(key, value):
    setting = db.session.get(Setting, key)
    if setting is None:
        db.session.add(Setting(key=key, value=value))
    else:
        setting.value = value


def _get_json(key):
    try:
        return json.loads(_get(key) or "null")
    except ValueError:
        return None


def is_enabled():
    return _get(ENABLED_KEY) == "on"


def set_enabled(enabled, actor=None):
    before = is_enabled()
    _set(ENABLED_KEY, "on" if enabled else "off")
    if before != bool(enabled):
        log_audit_event("system", "automatic_updates_switched", actor_user=actor, target_user=actor,
                        before={"enabled": before}, after={"enabled": bool(enabled)})


def last_run():
    """What the job did the last time it ran, or None before its first run."""
    return _get_json(LAST_RUN_KEY)


def _record(outcome, revision=None):
    _set(LAST_RUN_KEY, json.dumps({"at": get_now_utc().isoformat(), "outcome": outcome,
                                   "revision": (revision or "")[:12] or None}))
    return outcome


def run():
    """The nightly check: ask for the newest version if it is time to. Returns what it came to."""
    from . import system_update

    if not is_enabled():
        return OFF
    if not system_update.runner_is_installed():
        return _record(NO_RUNNER)
    local = system_update.get_local_version().get("revision")
    remote = system_update.get_remote_version(force=True)
    if not remote or not local:
        return _record(UNREACHABLE)
    if remote == local:
        return _record(UP_TO_DATE, remote)
    if system_update.update_is_in_progress():
        return _record(BUSY, remote)
    # Strictly passed: "unknown" -- GitHub out of reach -- is offered to a
    # person on the page, but nothing is installed unattended on it.
    if system_update.ci_state(remote, force=True)["state"] != "success":
        return _record(CI_NOT_PASSED, remote)
    if (_get_json(ATTEMPT_KEY) or {}).get("revision") == remote:
        # Asked for before, and still not what runs: it failed and undid
        # itself, or somebody went back from it.
        return _record(FAILED_BEFORE, remote)

    system_update.request_update(requested_by_user_id=None)
    _set(ATTEMPT_KEY, json.dumps({"revision": remote, "at": get_now_utc().isoformat()}))
    log_audit_event("system", "automatic_update_requested", before={"revision": local}, after={"revision": remote})
    return _record(REQUESTED, remote)
