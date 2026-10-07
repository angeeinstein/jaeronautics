"""What Settings -> System health and Updates do, beyond showing the report:
send background tasks that gave up once more, retry or dismiss an email that
could not be delivered, and ask for an update or a rollback. Each is logged.

The report itself is services/diagnostics.py, the update machinery
services/system_update.py.
"""

from ..db_models import EmailDeliveryJob, db
from . import ConflictError, NotFoundError
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
