"""Reporting the deployed version, and asking for an update to be installed.

The point of this module is to let an administrator update the site from the
admin page instead of needing shell access, so running the association does not
require someone comfortable with SSH.

The awkward part is that updating needs root -- systemd, nginx, package
installs -- while the web application deliberately runs as an unprivileged user.
The obvious fix, a sudoers rule letting the app run the update script, would
turn any compromise of the web process into root in one step.

So the web process cannot run the update at all. It only writes a *request*,
into a directory it owns. A root-owned watcher, driven by a systemd timer,
notices the request and runs the update itself. The request says nothing about
what to install: the branch and remote come from the installer's own state file
on the privileged side. The worst an attacker who reaches this endpoint can do
is force a redeploy of the code that was already configured -- not choose code.

Everything here returns plain data, so the same call backs the HTML panel and
the JSON status endpoint the page polls.
"""

import json
import os
import subprocess
from pathlib import Path

from flask import current_app

from ..config import REPO_ROOT
from . import ConflictError, ServiceError, ValidationError
from .clock import get_now_utc

# Where the web process and the privileged runner exchange files. Overridable so
# tests (and a developer checkout) do not need the real system directory.
UPDATE_STATE_DIR = Path(
    os.getenv("UPDATE_STATE_DIR", "/var/lib/jaeronautics/updates")
)

REQUEST_FILENAME = "request.json"
STATUS_FILENAME = "status.json"
LOG_FILENAME = "last-run.log"

# Written by the installer before each update, on the privileged side.
ROLLBACK_FILE = Path(os.getenv("ROLLBACK_FILE", "/etc/jaeronautics/rollback.conf"))

# How much of the running update's output to show. An install log is long and
# the interesting part is always the end.
LIVE_LOG_TAIL_LINES = 40

# How long a remote-revision lookup is reused. The check is a network call to
# the git remote, so it is not made on every page load.
REMOTE_CHECK_TTL_SECONDS = 600

_remote_cache = {"checked_at": None, "value": None}


def _run_git(args, timeout=10):
    """Run a read-only git command in the deployment, or return None."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        current_app.logger.warning("git %s failed: %s", " ".join(args), exc)
        return None
    if result.returncode != 0:
        current_app.logger.warning(
            "git %s exited %s: %s", " ".join(args), result.returncode, result.stderr.strip()
        )
        return None
    return result.stdout.strip()


def get_local_version():
    """The revision this deployment is actually running."""
    revision = _run_git(["rev-parse", "HEAD"])
    return {
        "revision": revision,
        "short_revision": revision[:8] if revision else None,
        "branch": _run_git(["rev-parse", "--abbrev-ref", "HEAD"]),
        "committed_at": _run_git(["log", "-1", "--format=%cI"]),
        "subject": _run_git(["log", "-1", "--format=%s"]),
    }


def get_remote_version(force=False):
    """The newest revision on the deployed branch, or None if unreachable.

    Cached, because this reaches out to the git remote over the network and the
    admin page should not depend on that being fast.
    """
    now = get_now_utc()
    cached_at = _remote_cache["checked_at"]
    if not force and cached_at and (now - cached_at).total_seconds() < REMOTE_CHECK_TTL_SECONDS:
        return _remote_cache["value"]

    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    if not branch or branch == "HEAD":
        return None

    output = _run_git(["ls-remote", "origin", f"refs/heads/{branch}"], timeout=20)
    revision = output.split()[0] if output else None

    _remote_cache["checked_at"] = now
    _remote_cache["value"] = revision
    return revision


def runner_is_installed():
    """Whether the privileged side is present to act on a request.

    Without it the button would appear to work and silently do nothing, so the
    page says so instead.
    """
    return UPDATE_STATE_DIR.is_dir()


def read_status():
    """The privileged runner's report on the most recent update, if any."""
    status_path = UPDATE_STATE_DIR / STATUS_FILENAME
    try:
        return json.loads(status_path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        current_app.logger.warning("Could not read update status file: %s", exc)
        return {}


def read_pending_request():
    request_path = UPDATE_STATE_DIR / REQUEST_FILENAME
    try:
        return json.loads(request_path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        # A malformed request file still means one is outstanding.
        return {}


def update_is_in_progress():
    if read_pending_request() is not None:
        return True
    return read_status().get("state") in {"requested", "running"}


def read_live_log_tail(lines=LIVE_LOG_TAIL_LINES):
    """The end of the running update's output, or None.

    The runner writes this file as it goes, so an administrator can watch
    progress instead of waiting for a summary once everything is finished.
    """
    log_path = UPDATE_STATE_DIR / LOG_FILENAME
    try:
        with log_path.open("r", errors="replace") as handle:
            tail = handle.readlines()[-lines:]
    except FileNotFoundError:
        return None
    except OSError as exc:
        current_app.logger.warning("Could not read the update log: %s", exc)
        return None
    return "".join(tail).strip() or None


def read_rollback_point():
    """The revision the installer recorded before the last update, if any.

    Read for display only. The rollback itself never takes a revision from this
    side: the runner reads the same file as root, so the web process cannot
    choose what to roll back to.
    """
    try:
        text = ROLLBACK_FILE.read_text()
    except FileNotFoundError:
        # Normal before the first update: nothing has been replaced yet.
        return None
    except PermissionError:
        # Not normal, and indistinguishable from the above on the page unless
        # it is said out loud -- which is exactly how a rollback panel that
        # could never appear went unnoticed.
        current_app.logger.warning(
            "The rollback point at %s exists but is not readable by this process, "
            "so the admin page cannot offer a rollback. It should be group-readable "
            "by the application user.", ROLLBACK_FILE,
        )
        return None
    except OSError as exc:
        current_app.logger.warning("Could not read the rollback point: %s", exc)
        return None

    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"')

    revision = values.get("ROLLBACK_REVISION")
    if not revision:
        return None
    return {
        "revision": revision,
        "short_revision": revision[:8],
        "recorded_at": values.get("ROLLBACK_RECORDED_AT") or None,
        "schema_revision": values.get("ROLLBACK_SCHEMA_REVISION") or None,
        "database_backup": values.get("ROLLBACK_DB_BACKUP") or None,
    }


def describe_progress(log_text, status):
    """Turn the installer's [STEP] markers into something a bar can show.

    The number of steps is not fixed -- a local database or a TLS certificate
    each add their own -- so the expected total comes from how many the previous
    successful update actually took, and the first ever run simply has no
    percentage to show.
    """
    steps = [
        line[len("[STEP]"):].strip()
        for line in (log_text or "").splitlines()
        if line.startswith("[STEP]")
    ]
    expected = status.get("steps_expected") or 0
    percent = None
    if expected and steps:
        # Hold just short of complete until the runner says it finished, so the
        # bar never sits at 100% while work is still going on.
        percent = min(int(len(steps) * 100 / expected), 95)
    return {
        "steps_done": len(steps),
        "steps_expected": expected or None,
        "percent": percent,
        "current_step": steps[-1] if steps else None,
    }


def describe_update_state(force_remote_check=False):
    """Everything the admin page needs, as plain serializable data."""
    local = get_local_version()
    status = read_status()
    remote = None
    remote_check_failed = False
    if runner_is_installed() or local.get("revision"):
        remote = get_remote_version(force=force_remote_check)
        remote_check_failed = remote is None

    update_available = bool(remote and local.get("revision") and remote != local["revision"])
    in_progress = update_is_in_progress()

    # While the update runs, prefer what the log says right now over the summary
    # the runner will only write when it finishes.
    log_tail = status.get("log_tail")
    if in_progress:
        log_tail = read_live_log_tail() or log_tail
    progress = describe_progress(log_tail, status)

    return {
        "local": local,
        "remote_revision": remote,
        "remote_short_revision": remote[:8] if remote else None,
        "remote_check_failed": remote_check_failed,
        "update_available": update_available,
        "runner_installed": runner_is_installed(),
        "in_progress": in_progress,
        "progress": progress,
        "rollback_point": read_rollback_point(),
        "last_run": {
            "state": status.get("state"),
            "requested_at": status.get("requested_at"),
            "started_at": status.get("started_at"),
            "finished_at": status.get("finished_at"),
            "exit_code": status.get("exit_code"),
            "revision_before": status.get("revision_before"),
            "revision_after": status.get("revision_after"),
            "log_tail": log_tail,
        },
    }


def request_update(requested_by_user_id, action="update"):
    """Ask the privileged runner to install an update, or to roll one back.

    Writes the request and returns immediately: the work takes minutes and
    restarts the very process serving this request, so it cannot be awaited.

    ``action`` is the only influence the request has, and it chooses between two
    fixed operations. Neither carries a target: what an update installs and what
    a rollback returns to both come from the installer's own state, on the
    privileged side. An attacker reaching this endpoint can move the deployment
    between two revisions someone already chose, not to one of their own.
    """
    if action not in {"update", "rollback"}:
        raise ValidationError(f"Unknown update action: {action!r}")
    if action == "rollback" and read_rollback_point() is None:
        raise ConflictError(
            "There is no recorded version to roll back to yet. A rollback point is "
            "written the first time you install an update from here.",
            code="no_rollback_point",
        )
    if not runner_is_installed():
        raise ServiceError(
            "The update runner is not installed on this server, so updates cannot "
            "be started from here. Run the installer once from a shell to set it up.",
            code="update_runner_missing",
            http_status=503,
        )
    if update_is_in_progress():
        raise ConflictError(
            "An update is already in progress. Wait for it to finish before starting another.",
            code="update_already_running",
        )

    payload = {
        "requested_at": get_now_utc().isoformat(),
        "requested_by_user_id": requested_by_user_id,
        "action": action,
        "revision_before": get_local_version().get("revision"),
    }
    request_path = UPDATE_STATE_DIR / REQUEST_FILENAME
    temporary_path = request_path.with_suffix(".json.tmp")
    try:
        # Write then rename, so the watcher never sees a half-written request.
        temporary_path.write_text(json.dumps(payload, indent=2))
        os.replace(temporary_path, request_path)
    except OSError as exc:
        current_app.logger.error("Could not write update request: %s", exc)
        raise ServiceError(
            "The update request could not be written. Check the server's update directory.",
            code="update_request_failed",
            http_status=500,
        ) from exc

    current_app.logger.info(
        "System update requested by user_id=%s (revision %s).",
        requested_by_user_id,
        payload["revision_before"],
    )
    return payload
