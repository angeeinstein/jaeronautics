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
* Only a bare signup. Anything more -- a role, a team, a forum account, a
  picture, a change request, a membership period, a payment, a Stripe
  subscription -- and it is left alone for an admin to look at.

Removed with the account: its profile and the log, email and background-task
rows about it. One log entry, without the person, records how many went.
"""

from datetime import timedelta, timezone

from ..db_models import (
    AuditLog,
    EmailDeliveryJob,
    ExternalWorkItem,
    ForumAccount,
    ForumAvatarSubmission,
    ImportedForumProfile,
    Member,
    MemberProfileChangeRequest,
    MembershipPeriod,
    NotificationEvent,
    Payment,
    TeamMembership,
    TeamNote,
    TeamRole,
    db,
)
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


def _exists(model, *conditions):
    return db.session.execute(db.select(model.id).where(*conditions).limit(1)).first() is not None


def _bare(member):
    """Only a signup: nothing anybody would miss, nothing the books need."""
    user = member.user
    if user is None or user.deleted_at is not None or user.roles:
        return False
    return not any((
        _exists(MembershipPeriod, MembershipPeriod.member_id == member.id),
        _exists(Payment, Payment.user_id == user.id),
        _exists(ForumAccount, db.or_(ForumAccount.user_id == user.id, ForumAccount.member_id == member.id)),
        _exists(ImportedForumProfile, ImportedForumProfile.user_id == user.id),
        _exists(ForumAvatarSubmission, ForumAvatarSubmission.member_id == member.id),
        _exists(MemberProfileChangeRequest, MemberProfileChangeRequest.member_id == member.id),
        _exists(TeamMembership, TeamMembership.user_id == user.id),
        _exists(TeamRole, TeamRole.user_id == user.id),
        _exists(TeamNote, TeamNote.user_id == user.id),
    ))


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
    user = member.user
    for model in (NotificationEvent, EmailDeliveryJob):
        db.session.execute(db.delete(model).where(
            db.or_(model.target_user_id == user.id, model.target_member_id == member.id)))
    db.session.execute(db.delete(ExternalWorkItem).where(
        db.or_(ExternalWorkItem.user_id == user.id, ExternalWorkItem.member_id == member.id)))
    db.session.execute(db.delete(AuditLog).where(db.or_(
        AuditLog.target_user_id == user.id, AuditLog.target_member_id == member.id,
        AuditLog.actor_user_id == user.id)))
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
