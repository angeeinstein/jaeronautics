"""The portal's own background work, and the switch that holds it after a restore.

A restored copy carries the real Stripe keys, the real mail accounts and the
real forum's API key. Left to itself it would start sending digests, pushing
accounts to the forum and reconciling payments the moment its timers fired --
from what may be a test machine. So a restore leaves every background job
paused, and they stay paused until an administrator says "this is the real
one" on the Backup & Restore page.

Things a person does on the copy still happen (a password reset, a manual
resync); the pause covers what the machine does on its own.

Each job also checks in every time its timer fires, paused or not. That is how
the resume checklist knows a timer is installed and running without having to
ask systemd, which the web process cannot.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..db_models import Setting, db

PAUSE_KEY = "background_jobs_paused"
RESUMED_KEY = "background_jobs_resumed"
HEARTBEAT_PREFIX = "job_heartbeat."


@dataclass(frozen=True)
class Job:
    name: str
    label: str
    schedule: str
    # A check-in older than this means the timer has stopped firing.
    stale_after: timedelta
    # Frequent jobs are waited for after a resume; the daily ones would keep
    # the checklist spinning until the night, so their last check-in counts.
    frequent: bool


JOBS = (
    Job("external-work", "Forum and account queue", "every 2 minutes", timedelta(minutes=10), True),
    Job("notifications", "Email delivery and admin notifications", "every 15 minutes", timedelta(minutes=45), True),
    Job("billing-reconcile", "Nightly billing check with Stripe", "daily at 03:15", timedelta(hours=26), False),
    Job("forum-drift", "Nightly forum check", "daily at 03:45", timedelta(hours=26), False),
    Job("cleanup-logs", "Log cleanup", "monthly", timedelta(days=32), False),
)
JOBS_BY_NAME = {job.name: job for job in JOBS}


def _now():
    return datetime.now(timezone.utc)


def _read_json(key):
    setting = db.session.get(Setting, key)
    if setting is None:
        return None
    try:
        return json.loads(setting.value)
    except (TypeError, ValueError):
        return None


def _write_json(key, value):
    encoded = json.dumps(value, separators=(",", ":"))
    setting = db.session.get(Setting, key)
    if setting is None:
        db.session.add(Setting(key=key, value=encoded))
    else:
        setting.value = encoded


def _delete(key):
    setting = db.session.get(Setting, key)
    if setting is not None:
        db.session.delete(setting)


def _parse_time(value):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def pause_state():
    """Why and since when the jobs are paused, or None when they run."""
    return _read_json(PAUSE_KEY)


def is_paused():
    return pause_state() is not None


def pause(reason, **details):
    """Hold every background job. The caller commits."""
    _write_json(PAUSE_KEY, {"since": _now().isoformat(timespec="seconds"), "reason": reason, **details})
    _delete(RESUMED_KEY)


def resume(by_email):
    """Let the jobs run again, and start the resume checklist. The caller commits."""
    _delete(PAUSE_KEY)
    _write_json(RESUMED_KEY, {"at": _now().isoformat(timespec="seconds"), "by": by_email})


def resumed_state():
    return _read_json(RESUMED_KEY)


def clear_resumed():
    """Put the resume checklist away."""
    _delete(RESUMED_KEY)


def clear_heartbeats():
    """Forget every check-in -- a restored database carries the old machine's."""
    db.session.execute(db.delete(Setting).where(Setting.key.like(f"{HEARTBEAT_PREFIX}%")))


def check_in(job_name):
    """Record that a job's timer fired. Returns True when the job should stand down.

    Commits on its own: a job that then stands down has nothing else to commit,
    and one that runs must not lose its check-in to a later rollback.
    """
    paused = is_paused()
    _write_json(f"{HEARTBEAT_PREFIX}{job_name}", {"at": _now().isoformat(timespec="seconds"), "paused": paused})
    db.session.commit()
    return paused


def last_check_in(job_name):
    """When the job's timer last fired, and whether it was paused then."""
    record = _read_json(f"{HEARTBEAT_PREFIX}{job_name}")
    if not record:
        return None, None
    return _parse_time(record.get("at")), bool(record.get("paused"))


def job_status(job, *, resumed_at=None, now=None):
    """One job's line on the checklist: ("ok" | "waiting" | "failed", detail)."""
    now = now or _now()
    seen_at, was_paused = last_check_in(job.name)
    if seen_at is None:
        if job.frequent:
            return "waiting", f"Waiting for its first run ({job.schedule})."
        return "scheduled", f"Has not run on this server yet; runs {job.schedule}."
    if now - seen_at > job.stale_after:
        return "failed", f"Last ran {_ago(now - seen_at)}, but it runs {job.schedule}. Is its timer enabled?"
    if job.frequent and resumed_at and (seen_at < resumed_at or was_paused):
        return "waiting", f"Timer is running; waiting for its first run since resuming ({job.schedule})."
    return "ok", f"Ran {_ago(now - seen_at)} ({job.schedule})."


def _ago(delta):
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 15:
        return "just now"
    if seconds < 90:
        return f"{seconds} seconds ago"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes} minutes ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hours ago"
    return f"{hours // 24} days ago"
