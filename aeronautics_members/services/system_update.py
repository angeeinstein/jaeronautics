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
import re
import statistics
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
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
# The steps the last successful update went through, kept by this side (the
# runner rewrites its own files on every run), to list the ones still to come.
PLAN_FILENAME = "step-plan.json"

# Written by the installer before each update, on the privileged side.
ROLLBACK_FILE = Path(os.getenv("ROLLBACK_FILE", "/etc/jaeronautics/rollback.conf"))

# How much of the running update's output to show. An install log is long and
# the interesting part is always the end.
LIVE_LOG_TAIL_LINES = 40

# The whole output is read for the step list and the full log on the page. An
# update's output is a few hundred kilobytes; past this only its end is read.
LOG_READ_LIMIT = 2 * 1024 * 1024
# The installer's colours, when it ran in a terminal: not for a web page.
_COLOURS = re.compile(r"\x1b\[[0-9;]*m")
# Its marked lines: a step begins, or something within it is worth saying.
_MARKED = re.compile(r"^\[(STEP|WARN|ERR|ERROR)\]\s*(.*)$")
# Of a step's warnings, how many are said under it.
WARNINGS_SHOWN = 2

# How long a remote-revision lookup is reused. The check is a network call to
# the git remote, so it is not made on every page load.
REMOTE_CHECK_TTL_SECONDS = 600

# The runner stops a hung update after 45 minutes and systemd stops the runner
# after 55. A status still saying "running" past this is a run nothing will
# ever finish -- the runner was killed before it could say so.
RUNNING_TOO_LONG = timedelta(minutes=60)
# The runner's watcher fires immediately, its timer every five minutes. A
# request nobody has picked up by now will not be.
UNCLAIMED_TOO_LONG = timedelta(minutes=15)
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")

_remote_cache = {"checked_at": None, "value": None}

# Whether CI passed for the newest revision. The installer only installs a
# version whose CI passed -- it takes CI's build of the front end -- and waits
# for CI while it runs (wait_for_ci in install.sh). Offering a version before
# that would start an update that sits waiting for up to 25 minutes; so the page
# asks GitHub the same question first. Unauthenticated: the repository is
# public, and GitHub answers 60 such questions an hour, so the answers are kept:
# briefly while CI runs, longer once it has a result.
CI_WORKFLOW = ".github/workflows/ci.yml"
CI_CHECK_TTL_SECONDS = {"running": 90, "none": 90, "unknown": 120}
CI_RESULT_TTL_SECONDS = 1800
CI_TYPICAL_TTL_SECONDS = 6 * 3600
GITHUB_API = "https://api.github.com"
_ci_cache = {}
_typical_cache = {"checked_at": None, "value": None}


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
    if branch == "HEAD":
        # A rollback checks the old revision out detached, so there is no
        # current branch to compare against. Falling back to the branch the
        # installer recorded matters here more than anywhere else: having just
        # gone back a version, the administrator most needs to be told that a
        # newer one exists and they can go forward again.
        branch = (read_rollback_point() or {}).get("branch")
    if not branch:
        return None

    output = _run_git(["ls-remote", "origin", f"refs/heads/{branch}"], timeout=20)
    revision = output.split()[0] if output else None

    _remote_cache["checked_at"] = now
    _remote_cache["value"] = revision
    return revision


def github_slug():
    """``owner/repository`` of the deployment's remote on GitHub, or None."""
    url = (_run_git(["config", "--get", "remote.origin.url"]) or "").strip()
    match = re.match(r"^(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$", url)
    return match.group(1) if match else None


def _asks_github():
    # Never from the test suite unless a test means to: it would go to the network.
    return current_app.config.get("UPDATE_CI_CHECK", not current_app.testing)


def _github_json(path):
    """GitHub's answer to ``GET path``, or None when it cannot be had."""
    request = urllib.request.Request(f"{GITHUB_API}{path}", headers={
        "Accept": "application/vnd.github+json", "User-Agent": "jaeronautics-updates",
    })
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 (a fixed host)
            return json.loads(response.read(2_000_000))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        current_app.logger.info("GitHub could not be asked about CI: %s", exc)
        return None


def _iso(value):
    return parse_time(value).isoformat() if parse_time(value) else None


def ci_state(revision, force=False):
    """Whether CI passed for ``revision``, as the installer asks it (ci_state in install.sh).

    ``state``: success, running, none (no run yet -- a push only just made),
    failure, or unknown (GitHub could not be asked, or the code is not on
    GitHub). With the run's address and when it started.
    """
    slug = github_slug() if _asks_github() else None
    if not slug or not revision:
        return {"state": "unknown", "url": None, "started_at": None}
    now = get_now_utc()
    cached = _ci_cache.get(revision)
    if cached and not force:
        age = (now - cached[0]).total_seconds()
        if age < CI_CHECK_TTL_SECONDS.get(cached[1]["state"], CI_RESULT_TTL_SECONDS):
            return cached[1]

    answer = _github_json(f"/repos/{slug}/actions/runs?head_sha={revision}&event=push&per_page=50")
    actions = f"https://github.com/{slug}/actions"
    if answer is None:
        result = {"state": "unknown", "url": actions, "started_at": None}
    else:
        runs = [run for run in answer.get("workflow_runs") or []
                if (run.get("path") or "").split("@")[0] == CI_WORKFLOW]
        if not runs:
            result = {"state": "none", "url": actions, "started_at": None}
        else:
            run = max(runs, key=lambda run: (run.get("run_number") or 0, run.get("run_attempt") or 0))
            if run.get("status") != "completed":
                state = "running"
            elif run.get("conclusion") == "success":
                state = "success"
            else:
                state = "failure"
            result = {"state": state, "url": run.get("html_url") or actions,
                      "started_at": _iso(run.get("run_started_at") or run.get("created_at"))}
    _ci_cache.clear()  # only the newest revision is ever asked about
    _ci_cache[revision] = (now, result)
    return result


def typical_ci_minutes():
    """How long CI usually takes, from its last successful runs; None when unknown."""
    now = get_now_utc()
    if _typical_cache["checked_at"] and (now - _typical_cache["checked_at"]).total_seconds() < CI_TYPICAL_TTL_SECONDS:
        return _typical_cache["value"]
    slug = github_slug() if _asks_github() else None
    answer = _github_json(f"/repos/{slug}/actions/workflows/ci.yml/runs?status=success&per_page=10") if slug else None
    minutes = []
    for run in (answer or {}).get("workflow_runs") or []:
        started, finished = parse_time(run.get("run_started_at")), parse_time(run.get("updated_at"))
        if started and finished and finished > started:
            minutes.append((finished - started).total_seconds() / 60)
    value = max(1, round(statistics.median(minutes))) if minutes else None
    if answer is not None:
        _typical_cache.update(checked_at=now, value=value)
    return value


def runner_is_installed():
    """Whether the privileged side is present to act on a request.

    Without it the button would appear to work and silently do nothing, so the
    page says so instead.
    """
    return UPDATE_STATE_DIR.is_dir()


def read_status():
    """The privileged runner's report on the most recent update, if any.

    A run the file still calls "running" but that cannot be -- the server has
    restarted since, or it has gone on far longer than the runner allows -- is
    reported as the failure it is. Otherwise a runner killed mid-update left
    the page showing a frozen update and refusing to start another, and no
    reboot or shell update ever changed that file.
    """
    status_path = UPDATE_STATE_DIR / STATUS_FILENAME
    try:
        status = json.loads(status_path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        current_app.logger.warning("Could not read update status file: %s", exc)
        return {}
    reason = _why_it_cannot_still_be_running(status)
    if reason:
        status = {**status, "state": "failed", "interrupted": reason}
    return status


def parse_time(value):
    """An ISO time written by the runner or the installer, as an aware datetime; None if it is none."""
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _current_boot_id():
    try:
        return BOOT_ID_PATH.read_text().strip()
    except OSError:
        return None


def _why_it_cannot_still_be_running(status):
    if status.get("state") not in {"requested", "running"}:
        return None
    recorded_boot, current_boot = status.get("boot_id"), _current_boot_id()
    if recorded_boot and current_boot and recorded_boot != current_boot:
        return ("The server restarted while this update was running, so it did not finish. "
                "Start it again, or run \"update\" from a shell.")
    started_at = parse_time(status.get("started_at"))
    if started_at and get_now_utc() - started_at > RUNNING_TOO_LONG:
        return ("This update stopped without finishing: it was still marked as running after more "
                "than an hour. Start it again, or run \"update\" from a shell to see where it stops.")
    return None


def read_pending_request():
    request_path = UPDATE_STATE_DIR / REQUEST_FILENAME
    try:
        return json.loads(request_path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        # A malformed request file still means one is outstanding.
        return {}


def request_is_waiting():
    """A request the runner has not picked up yet -- and still might."""
    pending = read_pending_request()
    if pending is None:
        return False
    requested_at = parse_time(pending.get("requested_at"))
    return not (requested_at and get_now_utc() - requested_at > UNCLAIMED_TOO_LONG)


def request_was_never_picked_up():
    return read_pending_request() is not None and not request_is_waiting()


def update_is_in_progress():
    if request_is_waiting():
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
        # The branch this installation tracks. After a rollback HEAD is
        # detached, and this is the only record of where "forward" is.
        "branch": values.get("ROLLBACK_BRANCH") or None,
    }


def read_full_log():
    """The latest run's whole output, without colour codes: ``(text, cut)``.

    ``cut`` says only the end was read (past LOG_READ_LIMIT). None for the
    text when there is no log, or it cannot be read.
    """
    log_path = UPDATE_STATE_DIR / LOG_FILENAME
    try:
        with log_path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - LOG_READ_LIMIT))
            raw = handle.read()
    except FileNotFoundError:
        return None, False
    except OSError as exc:
        current_app.logger.warning("Could not read the update log: %s", exc)
        return None, False
    text = raw.decode("utf-8", errors="replace")
    cut = size > LOG_READ_LIMIT
    if cut:
        text = text.partition("\n")[2]  # the first line read is a part of one
    return _COLOURS.sub("", text), cut


def read_steps(log_text):
    """The installer's [STEP] lines, each with the warnings and errors said within it.

    What is said before the first step (the runner's own errors) goes with a
    step of its own, so a run that failed before it began still says why.
    """
    steps, before = [], {"label": None, "warnings": [], "errors": []}
    for line in (log_text or "").splitlines():
        marked = _MARKED.match(line.strip())
        if not marked:
            continue
        kind, text = marked.groups()
        if kind == "STEP":
            steps.append({"label": text, "warnings": [], "errors": []})
            continue
        into = steps[-1] if steps else before
        (into["warnings"] if kind == "WARN" else into["errors"]).append(text)
    if before["errors"] or before["warnings"]:
        steps.insert(0, {**before, "label": "Starting the update", "before": True})
    return steps


def _said_under(step, state):
    if step["errors"] and state in {"failed", "done"}:
        return step["errors"][-1]
    warnings = step["warnings"]
    if not warnings:
        return None
    more = len(warnings) - WARNINGS_SHOWN
    return " · ".join(warnings[:WARNINGS_SHOWN]) + (f" (and {more} more)" if more > 0 else "")


def _read_plan():
    try:
        plan = json.loads((UPDATE_STATE_DIR / PLAN_FILENAME).read_text())
    except (OSError, ValueError):
        return {}
    return plan if isinstance(plan, dict) else {}


def _is_rollback(status, steps):
    # Runners from before the status said which, by what a rollback does first.
    return status.get("action") == "rollback" or any(step["label"].startswith("Rolling back") for step in steps)


def _remember_plan(status, steps):
    """After a successful update, its steps: the ones to list ahead next time."""
    if status.get("state") != "completed" or not steps or _is_rollback(status, steps):
        return
    finished_at = status.get("finished_at")
    if _read_plan().get("finished_at") == finished_at:
        return
    plan_path = UPDATE_STATE_DIR / PLAN_FILENAME
    temporary_path = plan_path.with_suffix(".json.tmp")
    try:
        temporary_path.write_text(json.dumps(
            {"finished_at": finished_at, "steps": [step["label"] for step in steps if not step.get("before")]}))
        os.replace(temporary_path, plan_path)
    except OSError as exc:
        current_app.logger.warning("Could not keep the update's steps: %s", exc)


def _still_to_come(plan, seen):
    """The planned steps after the last one reached, that have not come yet."""
    seen = set(seen)
    after = 0
    for index, label in enumerate(plan):
        if label in seen:
            after = index + 1
    return [label for label in plan[after:] if label not in seen]


def describe_steps(steps, state, in_progress, plan=()):
    """The run as a list to tick off: done, the one under way, failed, and those still to come."""
    lines = []
    for index, step in enumerate(steps):
        last = index == len(steps) - 1
        if last and in_progress:
            line_state = "running"
        elif last and state == "failed":
            line_state = "failed"
        else:
            line_state = "done"
        lines.append({"label": step["label"], "state": line_state, "detail": _said_under(step, line_state)})
    if in_progress:
        if not steps:
            lines.append({"label": "Starting the update", "state": "running", "detail": None})
        lines += [{"label": label, "state": "pending", "detail": None}
                  for label in _still_to_come(plan, [step["label"] for step in steps])]
    elif state == "failed" and not steps:
        lines.append({"label": "Starting the update", "state": "failed", "detail": None})
    return lines


def describe_progress(steps, status, in_progress=False):
    """How far the run is, for the bar and the list.

    The number of steps is not fixed -- a local database or a TLS certificate
    each add their own -- so the expected total comes from how many the previous
    successful update actually took, and the first ever run simply has no
    percentage to show.
    """
    labels = [step["label"] for step in steps if not step.get("before")]
    expected = status.get("steps_expected") or 0
    percent = None
    if expected and labels:
        # Hold just short of complete until the runner says it finished, so the
        # bar never sits at 100% while work is still going on.
        percent = min(int(len(labels) * 100 / expected), 95)
    rollback = _is_rollback(status, steps)
    plan = [] if rollback else (_read_plan().get("steps") or [])
    return {
        "steps_done": len(labels),
        "steps_expected": expected or None,
        "percent": percent,
        "current_step": labels[-1] if labels else None,
        "steps": describe_steps(steps, status.get("state"), in_progress, plan),
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

    newer = bool(remote and local.get("revision") and remote != local["revision"])
    # A newer version is offered once CI has passed for it -- or when GitHub
    # cannot say, as before: the installer then asks again itself.
    ci = ci_state(remote, force=force_remote_check) if newer else None
    if ci and ci["state"] in ("running", "none"):
        ci["typical_minutes"] = typical_ci_minutes()
    update_available = newer and ci["state"] in ("success", "unknown")
    in_progress = update_is_in_progress()

    # While the update runs, prefer what the log says right now over the summary
    # the runner will only write when it finishes.
    log_tail = status.get("log_tail")
    if in_progress:
        log_tail = read_live_log_tail() or log_tail
    # The whole log for the steps: the tail alone misses the early ones. Until
    # the runner has taken a request up, the log is still the previous run's.
    started = status.get("state") in {"running", "completed", "failed"} and not request_is_waiting()
    full_log = read_full_log()[0] if started else None
    steps = read_steps(full_log if full_log is not None else (log_tail if started else None))
    if not in_progress:
        _remember_plan(status, steps)
    progress = describe_progress(steps, status, in_progress=in_progress)

    return {
        "local": local,
        "remote_revision": remote,
        "remote_short_revision": remote[:8] if remote else None,
        "remote_check_failed": remote_check_failed,
        "newer_version": newer,
        "remote_ci": ci,
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
            "interrupted": status.get("interrupted"),
        },
        "request_never_picked_up": request_was_never_picked_up(),
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
