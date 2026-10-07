"""Settings -> Backup and restore: making an encrypted backup of everything,
the backups kept on the server, how to restore one -- and, on a portal just
restored, its paused background jobs and the checklist after resuming them.

For whoever may make backups. The backups themselves are services/backup.py,
the pause services/background_jobs.py, the checklist
services/resume_checks.py, the actions services/maintenance.py. A backup is
downloaded from Flask's own /admin/backup/files/<name>. Drawn by
frontend/src/pages/admin/settings/Backup.tsx.
"""

from typing import Literal

from flask_login import current_user
from pydantic import Field

from ..app import limiter
from ..config import RATELIMIT_ADMIN_EMAIL
from ..db_models import db
from ..permissions import Permission
from ..services import background_jobs, maintenance, resume_checks
from ..services import backup as backups
from ..services.system_update import parse_time
from ._core import Model, UtcDateTime, endpoint

TAG = "Admin"
BACKUP = [Permission.SYSTEM_BACKUP]

CheckState = Literal["ok", "failed", "running", "waiting", "scheduled", "skipped"]


class CheckOut(Model):
    key: str
    label: str
    #: running: being checked, or about to be; waiting: for a timer to fire; scheduled: runs later on its own.
    state: CheckState
    detail: str


class ChecklistOut(Model):
    items: list[CheckOut]
    #: Every line has its answer; then the page asks only now and then, for the timers.
    done: bool
    paused: bool


class PausedOut(Model):
    since: UtcDateTime | None
    #: When the backup this portal was restored from was made.
    backup_created_at: UtcDateTime | None


class StepOut(Model):
    key: str
    label: str
    state: Literal["pending", "running", "done", "failed"]


class BackupResultOut(Model):
    file: str
    rows: int
    files: int


class BackupRunOut(Model):
    """The last backup made, or the one being made."""

    state: Literal["running", "completed", "failed"]
    steps: list[StepOut]
    log: list[str]
    error: str | None
    result: BackupResultOut | None
    started_at: UtcDateTime | None
    finished_at: UtcDateTime | None


class BackupFileOut(Model):
    name: str
    size: int
    #: "12.4 MB".
    size_display: str
    made_at: UtcDateTime


class BackupOut(Model):
    #: Restored and not declared the real portal yet: nothing runs on its own.
    paused: PausedOut | None
    #: While paused: whether each background timer fires.
    jobs: list[CheckOut]
    #: After resuming, until put away: does everything work?
    checklist: list[CheckOut] | None
    run: BackupRunOut | None
    #: Newest first.
    backups: list[BackupFileOut]
    min_passphrase_length: int
    #: How many are kept on the server; older ones go when a new one is made.
    kept: int


def _check(item):
    return CheckOut(key=item["key"], label=item["label"], state=item["state"], detail=item.get("detail") or "")


def _run(status):
    if not status or status.get("state") not in {"running", "completed", "failed"}:
        return None
    result = status.get("result")
    return BackupRunOut(
        state=status["state"],
        steps=[StepOut(key=step["key"], label=step["label"], state=step["state"]) for step in status.get("steps", [])],
        log=status.get("log", []), error=status.get("error"),
        result=BackupResultOut(file=result["file"], rows=result["rows"], files=result["files"]) if result else None,
        started_at=parse_time(status.get("started_at")), finished_at=parse_time(status.get("finished_at")),
    )


def _page():
    page = backups.describe_backup_page()
    paused = page["paused"]
    return BackupOut(
        paused=PausedOut(since=parse_time(paused.get("since")),
                         backup_created_at=parse_time(paused.get("backup_created_at"))) if paused else None,
        # Before anyone resumes, a frequent job waiting for its timer runs later on its own.
        jobs=[CheckOut(key=entry["job"].name, label=entry["job"].label,
                       state="scheduled" if entry["state"] == "waiting" else entry["state"], detail=entry["detail"])
              for entry in page["jobs"]],
        checklist=[_check(item) for item in page["checklist"]] if page["checklist"] is not None else None,
        run=_run(page["status"]),
        backups=[BackupFileOut(name=entry["name"], size=entry["size"], size_display=entry["size_display"],
                               made_at=entry["modified"]) for entry in page["backups"]],
        min_passphrase_length=page["min_passphrase_length"], kept=page["kept"],
    )


@endpoint("GET", "/admin/settings/backup", response=BackupOut, permissions=BACKUP, tag=TAG)
def admin_backup():
    """Backups, the one being made, and -- on a restored portal -- its background jobs."""
    return _page()


class BackupIn(Model):
    #: Encrypts the file and is kept nowhere: without it the backup cannot be restored.
    passphrase: str = Field(max_length=1024)
    passphrase_again: str = Field(max_length=1024)


@endpoint("POST", "/admin/settings/backup", response=BackupOut, body=BackupIn, permissions=BACKUP,
          status=202, tag=TAG)
@limiter.limit(RATELIMIT_ADMIN_EMAIL)
def admin_backup_start(body):
    """Start a backup. It is made in the background; the page follows along."""
    maintenance.start_backup(current_user, body.passphrase, body.passphrase_again)
    return _page()


class DeletedOut(Model):
    name: str


@endpoint("DELETE", "/admin/settings/backup/files/<name>", response=DeletedOut, permissions=BACKUP, tag=TAG)
def admin_backup_delete(name):
    """Delete a backup from the server."""
    maintenance.delete_backup(current_user, name)
    db.session.commit()
    return DeletedOut(name=name)


class ResumeIn(Model):
    #: That this is the server members use -- not a copy on a test machine.
    confirm: Literal[True]


@endpoint("POST", "/admin/settings/backup/resume", response=BackupOut, body=ResumeIn, permissions=BACKUP, tag=TAG)
def admin_background_jobs_resume(body):
    """Let every background job run again: emails, forum sync, payment checks."""
    maintenance.resume_background_jobs(current_user)
    db.session.commit()
    return _page()


def _checklist(run_next):
    items = resume_checks.checklist(run_next=run_next)
    db.session.commit()
    return ChecklistOut(items=[_check(item) for item in items], done=resume_checks.all_done(items),
                        paused=background_jobs.is_paused())


@endpoint("GET", "/admin/settings/backup/checklist", response=ChecklistOut, permissions=BACKUP, tag=TAG)
def admin_background_jobs_checklist():
    """The checklist after resuming -- running the next service check not done yet, one per call,
    so each line turns from waiting to its answer as it really finishes."""
    return _checklist(run_next=True)


@endpoint("POST", "/admin/settings/backup/checklist/again", response=ChecklistOut, permissions=BACKUP, tag=TAG)
def admin_background_jobs_check_again():
    """Forget the answers, so every check runs again."""
    resume_checks.reset()
    return _checklist(run_next=False)


class DismissedOut(Model):
    ok: bool


@endpoint("POST", "/admin/settings/backup/checklist/dismiss", response=DismissedOut, permissions=BACKUP, tag=TAG)
def admin_background_jobs_dismiss_checklist():
    """Put the checklist away."""
    background_jobs.clear_resumed()
    resume_checks.reset()
    db.session.commit()
    return DismissedOut(ok=True)
