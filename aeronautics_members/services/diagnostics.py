"""A single, readable answer to "is this installation healthy?".

Diagnosing the site used to mean an SSH session and several ad-hoc queries,
which puts it out of reach of the people who actually run the association. This
collects the same facts in one place: which schema revision is applied, whether
queued work is moving, whether anything has given up retrying.

Returned as plain data, so the same call backs the CLI command and the
Maintenance tab -- an administrator sees it in the browser, and someone with
shell access gets the identical report.
"""

from datetime import timedelta

from ..db_models import (
    EmailDeliveryJob,
    ExternalWorkItem,
    Member,
    MembershipPeriod,
    ProcessedStripeEvent,
    db,
)
from .clock import get_membership_today, get_now_utc
from .membership import ACTIVE_MEMBER_STATUSES

# How far past its scheduled attempt a queued email has to be before the delay
# stops being ordinary backoff and starts meaning nobody is delivering it. The
# timer runs every 15 minutes, so an hour is four missed runs -- generous enough
# not to fire on a slow SMTP server, tight enough to catch a stopped timer the
# same morning rather than never.
QUEUE_STALL_GRACE = timedelta(hours=1)


def _count(model, *filters):
    query = db.select(db.func.count(model.id))
    for condition in filters:
        query = query.where(condition)
    return db.session.execute(query).scalar_one()


def get_schema_revision():
    """The Alembic revision the database is actually at, and the expected head."""
    try:
        applied = db.session.execute(db.text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:
        applied = None

    expected = None
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        from ..config import REPO_ROOT

        config = Config(str(REPO_ROOT / "migrations" / "alembic.ini"))
        config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
        expected = ScriptDirectory.from_config(config).get_current_head()
    except Exception:
        expected = None

    return {
        "applied": applied,
        "expected": expected,
        "up_to_date": bool(applied and expected and applied == expected),
    }


def get_membership_summary():
    today = get_membership_today()
    # Erased members keep their row and their coverage dates, because the
    # payment record has to survive. They are not members any more, so every
    # count below excludes them -- otherwise erasing someone would leave the
    # association's own membership numbers overstated.
    present = Member.deleted_at.is_(None)

    members = _count(Member, present)
    erased = _count(Member, Member.deleted_at.isnot(None))
    active = _count(Member, present, Member.is_active.is_(True))
    covered = _count(
        Member,
        present,
        Member.membership_ends_on.isnot(None),
        Member.membership_ends_on >= today,
        Member.payment_status.in_(sorted(ACTIVE_MEMBER_STATUSES)),
    )
    periods = _count(MembershipPeriod)
    revoked = _count(MembershipPeriod, MembershipPeriod.revoked_at.isnot(None))

    # A covered member with no period means the ledger disagrees with the cached
    # fields -- worth surfacing, since access is meant to have evidence behind it.
    without_evidence = db.session.execute(
        db.select(db.func.count(Member.id))
        .where(
            present,
            Member.membership_ends_on.isnot(None),
            Member.membership_ends_on >= today,
            Member.payment_status.in_(sorted(ACTIVE_MEMBER_STATUSES)),
            ~Member.membership_periods.any(MembershipPeriod.revoked_at.is_(None)),
        )
    ).scalar_one()

    return {
        "members": members,
        "erased_members": erased,
        "active_flag": active,
        "currently_covered": covered,
        "coverage_periods": periods,
        "revoked_periods": revoked,
        "covered_without_evidence": without_evidence,
    }


def get_queue_summary():
    """Whether background work is moving or piling up."""
    return {
        "external_work_pending": _count(
            ExternalWorkItem,
            ExternalWorkItem.status.in_(
                [ExternalWorkItem.STATUS_PENDING, ExternalWorkItem.STATUS_PROCESSING]
            ),
        ),
        "external_work_failed": _count(
            ExternalWorkItem, ExternalWorkItem.status == ExternalWorkItem.STATUS_FAILED
        ),
        "webhook_events_failed": _count(
            ProcessedStripeEvent,
            ProcessedStripeEvent.status == ProcessedStripeEvent.STATUS_FAILED,
        ),
        "webhook_events_completed": _count(
            ProcessedStripeEvent,
            ProcessedStripeEvent.status == ProcessedStripeEvent.STATUS_COMPLETED,
        ),
        "emails_pending": _count(EmailDeliveryJob, EmailDeliveryJob.status == "pending"),
        "emails_exhausted": _count(EmailDeliveryJob, EmailDeliveryJob.status == "exhausted"),
        # Queued emails whose retry was due well over an hour ago. A count on its
        # own says nothing -- three emails backing off for a day look exactly
        # like three emails nobody is delivering -- and the difference is whether
        # the deliver-notifications timer is alive.
        "emails_overdue": _count(
            EmailDeliveryJob,
            EmailDeliveryJob.status == "pending",
            EmailDeliveryJob.next_attempt_at.isnot(None),
            EmailDeliveryJob.next_attempt_at < get_now_utc() - QUEUE_STALL_GRACE,
        ),
    }


def collect_system_health():
    """Everything the health report shows, as plain serializable data."""
    schema = get_schema_revision()
    membership = get_membership_summary()
    queues = get_queue_summary()

    problems = []
    if not schema["up_to_date"]:
        problems.append(
            "The database schema is not at the expected revision "
            f"(applied {schema['applied']}, expected {schema['expected']}). "
            "Install the pending update."
        )
    if queues["external_work_failed"]:
        problems.append(
            f"{queues['external_work_failed']} forum synchronisation task(s) gave up retrying."
        )
    if queues["webhook_events_failed"]:
        problems.append(
            f"{queues['webhook_events_failed']} Stripe event(s) could not be processed."
        )
    if queues["emails_exhausted"]:
        problems.append(f"{queues['emails_exhausted']} email(s) could not be delivered.")
    if queues["emails_overdue"]:
        problems.append(
            f"{queues['emails_overdue']} queued email(s) are long past their retry time. "
            "Check that the notification timer is running: "
            "systemctl status jaeronautics-notifications.timer"
        )
    if membership["covered_without_evidence"]:
        problems.append(
            f"{membership['covered_without_evidence']} member(s) have access with no coverage "
            "record explaining why."
        )

    warnings = []
    if queues["external_work_pending"] > 20:
        warnings.append(
            f"{queues['external_work_pending']} forum tasks are queued; the worker may not be running."
        )
    if queues["emails_pending"] > 20:
        warnings.append(f"{queues['emails_pending']} emails are queued for delivery.")

    return {
        "healthy": not problems,
        "problems": problems,
        "warnings": warnings,
        "schema": schema,
        "membership": membership,
        "queues": queues,
    }
