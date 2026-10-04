"""Signups that were never paid for: a notice, then removal.

Somebody fills in the form and never pays. Nothing is owed and nothing has to
be kept, so after a long while the account goes -- deleted outright, not
erased into an anonymous row: an erased row exists to keep the books (seven
years for a paid membership), and an unpaid signup has none to keep. Years of
abandoned signups must not pile up as nameless leftovers.

* After ``KEEP_DAYS`` without payment, counted from the signup or the last
  attempt to pay, whichever is later.
* ``NOTICE_DAYS`` before, the person is emailed: pay by then, or the signup is
  removed. Removal waits until the notice has been out that long, so a night
  the job did not run never removes anybody unwarned. Trying to pay again
  starts the count afresh.
* Only a bare signup: no Stripe subscription, and no row anywhere in the
  database pointing at it except its own log, email and background-task
  rows. A role, a team, a forum account, a picture, a change request, a
  membership period, a payment -- anything more, and it is left alone for an
  admin. Checked against the schema, not a list, so it stays true as tables
  are added, and the delete can never fail on a foreign key.

Removed with the account: its profile and the log, email and background-task
rows about it. One log entry, without the person, records how many went.
"""

from datetime import timedelta, timezone

from ..db_models import Member, db
from .audit import log_audit_event
from .clock import get_now_utc
from .membership import format_date_display

KEEP_DAYS = 90
NOTICE_DAYS = 7
UNPAID_STATUSES = ("pending_checkout", "failed", "unpaid")


def _utc(moment):
    """Stored UTC; the database may hand it back without its zone."""
    return moment.replace(tzinfo=timezone.utc) if moment is not None and moment.tzinfo is None else moment


def _last_activity(member):
    return max(_utc(moment) for moment in (member.created_at, member.pending_checkout_started_at) if moment)


def removal_day(member):
    """The day an unfinished signup is removed, unless it is paid before."""
    return _last_activity(member) + timedelta(days=KEEP_DAYS)


# The signup's own traces: deleted with it. Anything else pointing at the
# account means it is more than a signup.
_OWN_TRACES = ("audit_logs", "notification_events", "email_delivery_jobs", "external_work_items")


def _referenced_elsewhere(user, member):
    """Whether any row, in any table, points at this account beyond its own traces.

    Read from the schema rather than listed by hand, so a table added later
    makes a signup it points at stay, instead of making the delete fail.
    """
    ids = {"users": user.id, "member": member.id}
    for table in db.metadata.sorted_tables:
        if table.name in _OWN_TRACES:
            continue
        for foreign_key in table.foreign_keys:
            target = foreign_key.column.table.name
            if target not in ids:
                continue
            if table.name == "member" and foreign_key.parent.name == "user_id":
                continue  # the signup itself
            found = db.session.execute(
                db.select(foreign_key.parent).where(foreign_key.parent == ids[target]).limit(1)
            ).first()
            if found is not None:
                return True
    return False


def _bare(member):
    """Only a signup: nothing anybody would miss, nothing the books need."""
    user = member.user
    if user is None or user.deleted_at is not None:
        return False
    return not _referenced_elsewhere(user, member)


def unfinished_signups(now=None):
    """Every bare signup never paid for, oldest first."""
    candidates = db.session.execute(
        db.select(Member)
        .where(
            Member.deleted_at.is_(None),
            Member.is_active.is_(False),
            Member.payment_status.in_(UNPAID_STATUSES),
            Member.stripe_subscription_id.is_(None),
        )
        .order_by(Member.created_at)
    ).scalars().all()
    return [member for member in candidates if _bare(member)]


def _noticed(member):
    """Told since the last activity -- a new attempt to pay needs a new notice."""
    noticed_at = _utc(member.unfinished_signup_notice_at)
    return noticed_at is not None and noticed_at >= _last_activity(member)


def _send_notice(member, removal, now):
    from ..security_utils import build_public_url
    from .notifications import queue_user_status_notification

    queue_user_status_notification(
        "unfinished_signup_removal",
        f"Unfinished signup of {member.email_private} is removed on {format_date_display(removal)}.",
        member.email_private,
        payload={"first_name": member.first_name, "removal_day": format_date_display(removal),
                 "account_url": build_public_url("account.account")},
        target_user=member.user,
        target_member=member,
        object_type="member",
        object_id=member.id,
    )
    member.unfinished_signup_notice_at = now


def _delete(member):
    """The signup, and every one of its own traces, by whatever column points at it."""
    user = member.user
    ids = {"users": user.id, "member": member.id}
    for table in db.metadata.sorted_tables:
        if table.name not in _OWN_TRACES:
            continue
        pointing = [foreign_key.parent == ids[foreign_key.column.table.name]
                    for foreign_key in table.foreign_keys if foreign_key.column.table.name in ids]
        if pointing:
            db.session.execute(table.delete().where(db.or_(*pointing)))
    db.session.delete(member)
    db.session.flush()
    db.session.delete(user)


def clean_up(now=None):
    """Notify what is due for a notice, remove what was noticed long enough ago.

    Returns ``{"noticed": n, "removed": n}``.
    """
    now = now or get_now_utc()
    noticed = removed = 0
    for member in unfinished_signups(now):
        removal = removal_day(member)
        if not _noticed(member):
            if now >= removal - timedelta(days=NOTICE_DAYS):
                _send_notice(member, max(removal, now + timedelta(days=NOTICE_DAYS)), now)
                noticed += 1
            continue
        if now >= removal and now >= _utc(member.unfinished_signup_notice_at) + timedelta(days=NOTICE_DAYS):
            _delete(member)
            removed += 1
    if removed:
        log_audit_event("members", "unfinished_signups_removed", metadata={
            "count": removed, "after_days": KEEP_DAYS,
        })
    db.session.flush()
    return {"noticed": noticed, "removed": removed}
