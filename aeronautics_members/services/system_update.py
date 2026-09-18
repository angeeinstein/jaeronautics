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
from . import ConflictError, ServiceError
from .clock import get_now_utc

# Where the web process and the privileged runner exchange files. Overridable so
# tests (and a developer checkout) do not need the real system directory.
UPDATE_STATE_DIR = Path(
    os.getenv("UPDATE_STATE_DIR", "/var/lib/jaeronautics/updates")
)

REQUEST_FILENAME = "request.json"
STATUS_FILENAME = "status.json"

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

    return {
        "local": local,
        "remote_revision": remote,
        "remote_short_revision": remote[:8] if remote else None,
        "remote_check_failed": remote_check_failed,
        "update_available": update_available,
        "runner_installed": runner_is_installed(),
        "in_progress": update_is_in_progress(),
        "last_run": {
            "state": status.get("state"),
            "requested_at": status.get("requested_at"),
            "started_at": status.get("started_at"),
            "finished_at": status.get("finished_at"),
            "exit_code": status.get("exit_code"),
            "revision_before": status.get("revision_before"),
            "revision_after": status.get("revision_after"),
            "log_tail": status.get("log_tail"),
        },
    }


def request_update(requested_by_user_id):
    """Ask the privileged runner to install an update.

    Writes the request and returns immediately: the update takes minutes and
    restarts the very process serving this request, so it cannot be awaited.
    """
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
