"""What Settings -> System health, Updates and Backup and restore do, beyond
showing what is: send background tasks that gave up once more, retry or
dismiss an email that could not be delivered, ask for an update or a
rollback, make or delete a backup, and resume the background jobs of a
restored portal. Each is logged.

The report itself is services/diagnostics.py, the update machinery
services/system_update.py, backups services/backup.py.
"""

from ..db_models import EmailDeliveryJob, db
from . import ConflictError, NotFoundError, ValidationError
from . import background_jobs, backup, resume_checks
from .audit import log_audit_event
from .notifications import dismiss_email_delivery_job, requeue_email_delivery_job
from .outbox import retry_failed
from .system_update import describe_update_state, request_update


def retry_forum_tasks(actor):
    """Put background tasks that gave up back in the queue: how many.

    They give up after about seven hours of failing -- in practice a forum that
    was down that long. Once it is back, this sends them again rather than
    leaving each member's forum out of date until something else changes.
    """
    count = retry_failed()
    if count:
        log_audit_event("system", "forum_tasks_retried", actor_user=actor, metadata={"count": count})
    return count


def resolve_undelivered_email(actor, job_id, action):
    """Retry (to the address on the profile now) or dismiss an email that gave up.

    Nothing prunes these rows, so without this one mistyped address would leave
    the health report red for good. Answers the recipient.
    """
    job = db.session.get(EmailDeliveryJob, job_id)
    if job is None:
        raise NotFoundError("That email no longer exists.")
    recipient = job.recipient_email
    changed = requeue_email_delivery_job(job) if action == "retry" else dismiss_email_delivery_job(job)
    if not changed:
        raise ConflictError("That email is no longer waiting to be resolved.", code="email_not_waiting")
    log_audit_event("notification", f"undelivered_email_{action}", actor_user=actor,
                    target_user=job.target_user, target_member=job.target_member,
                    metadata={"job_id": job.id, "email_type": job.email_type, "recipient": recipient})
    return recipient


def request_version_change(actor, action):
    """Ask the privileged runner for the update (or the rollback) and log it.

    Nothing is awaited: the runner picks the request up and restarts the site.
    """
    before = describe_update_state()
    request_update(requested_by_user_id=actor.id, action=action)
    log_audit_event(
        "system", "rollback_requested" if action == "rollback" else "update_requested",
        actor_user=actor, target_user=actor,
        before={"revision": (before.get("local") or {}).get("revision")},
        after={"revision": before.get("remote_revision")},
        metadata={"branch": (before.get("local") or {}).get("branch")},
    )


# --- Backup and restore -----------------------------------------------------------------


def _start_backup_process(passphrase, requested_by):
    """Run ``flask create-backup`` in the background and hand it the passphrase.

    A separate process, because a backup takes longer than a request should
    and must not die with a gunicorn worker. The passphrase goes through a
    pipe: never on a command line, where other users of the machine could
    read it, and never into a file.
    """
    import os
    import subprocess
    import sys

    from flask import current_app

    from ..config import REPO_ROOT

    if current_app.config.get("BACKUP_RUN_INLINE"):
        # Tests: the same command, in this process.
        current_app.test_cli_runner().invoke(
            args=["create-backup", "--passphrase-stdin", "--created-by", requested_by],
            input=passphrase + "\n",
        )
        return

    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPO_ROOT)
    process = subprocess.Popen(
        [sys.executable, "-m", "flask", "--app", "aeronautics_members.app:create_app",
         "create-backup", "--passphrase-stdin", "--created-by", requested_by],
        cwd=str(REPO_ROOT),
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    backup.record_pid(process.pid)
    process.stdin.write((passphrase + "\n").encode("utf-8"))
    process.stdin.close()


def start_backup(actor, passphrase, passphrase_again):
    """Make a backup in the background, encrypted with ``passphrase`` -- which is kept nowhere.

    Commits before the process starts, so the log entry is there whatever
    the process then does.
    """
    if len(passphrase or "") < backup.MIN_PASSPHRASE_LENGTH:
        message = f"At least {backup.MIN_PASSPHRASE_LENGTH} characters."
        raise ValidationError(message, code="passphrase_too_short", details={"fields": {"passphrase": message}})
    if passphrase != passphrase_again:
        message = "The two passphrases are not the same."
        raise ValidationError(message, code="passphrases_differ", details={"fields": {"passphrase_again": message}})
    status = backup.read_status()
    if status and status.get("state") == "running":
        raise ConflictError("A backup is already being made.", code="backup_running")
    backup.start_status(actor.email)
    log_audit_event("system", "backup_requested", actor_user=actor, target_user=actor)
    db.session.commit()
    _start_backup_process(passphrase, actor.email)


def delete_backup(actor, name):
    path = backup.backup_path(name)
    if path is None:
        raise NotFoundError("That backup is not on the server.")
    path.unlink()
    log_audit_event("system", "backup_deleted", actor_user=actor, target_user=actor, metadata={"file": name})


def resume_background_jobs(actor):
    """Say "this is the server members use": every background job runs again, and the
    checklist starts showing that each part really works."""
    paused = background_jobs.pause_state()
    background_jobs.resume(actor.email)
    resume_checks.reset()
    log_audit_event("system", "background_jobs_resumed", actor_user=actor, target_user=actor,
                    metadata={"paused": paused})
