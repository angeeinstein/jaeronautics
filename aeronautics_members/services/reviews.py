"""What is waiting for an administrator's decision, in one place.

Name changes and profile pictures used to live on two pages -- Approvals and
Forum -- each with its own counters on the dashboard. For the person doing the
reviewing they are the same job: somebody asked for something, look at it, say
yes or no. So they are one queue here, oldest first, while each item keeps its
own decision: a member who changed their name and uploaded a picture on the same
evening can have the picture approved and the name change turned down.

Who sees what still follows the permission table: name changes need
APPROVALS_REVIEW, pictures and forum sync problems FORUM_MODERATE. A role with
only one of them gets a queue with only that kind in it.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload

from ..db_models import (
    ForumAccount,
    ForumAvatarSubmission,
    Member,
    MemberProfileChangeRequest,
    db,
)
from ..forum_service import (
    FORUM_AVATAR_STATUS_APPROVED,
    FORUM_AVATAR_STATUS_PENDING,
    FORUM_AVATAR_STATUS_REJECTED,
    FORUM_STATE_SYNC_ERROR,
)
from ..member_categories import category_label
from ..permissions import Permission

KIND_NAME_CHANGE = "name_change"
KIND_PICTURE = "picture"

# Shown at once; the rest follow as these are decided. An intake can bring a
# hundred pictures in a week, and a page of a hundred photographs and forms is
# slow to load and no faster to work through.
QUEUE_LIMIT = 50

# Past decisions per history page.
HISTORY_PAGE_SIZE = 25


@dataclass
class ReviewItem:
    kind: str
    record: object
    when: datetime


# What a change request can change, in the order it is shown.
_CHANGE_FIELDS = (
    ("salutation", "requested_salutation", "Salutation"),
    ("title", "requested_title", "Title"),
    ("first_name", "requested_first_name", "First name"),
    ("last_name", "requested_last_name", "Last name"),
    ("member_category", "requested_member_category", "Category"),
    ("year_group", "requested_year_group", "Year group"),
)
_NAME_FIELDS = {"title", "first_name", "last_name"}


def describe_changes(request_record):
    """Only what differs, as (field, label, current, requested).

    The old page showed every field twice side by side, which made a one-letter
    typo fix look like a full form to compare.
    """
    member = request_record.member
    changes = []
    for field, requested_field, label in _CHANGE_FIELDS:
        current = getattr(member, field, None) if member is not None else None
        requested = getattr(request_record, requested_field)
        if (current or "") != (requested or ""):
            if field == "member_category":
                current, requested = category_label(current), category_label(requested)
            changes.append((field, label, current, requested))
    return changes


def is_name_change(changes):
    return any(field in _NAME_FIELDS for field, *_rest in changes)


def can_review_name_changes(user):
    return user.can(Permission.APPROVALS_REVIEW)


def can_review_pictures(user):
    return user.can(Permission.FORUM_MODERATE)


def can_review_anything(user):
    return can_review_name_changes(user) or can_review_pictures(user)


def _pending_name_changes():
    return db.select(MemberProfileChangeRequest).where(MemberProfileChangeRequest.status == "pending")


def _pending_pictures():
    return db.select(ForumAvatarSubmission).where(ForumAvatarSubmission.status == FORUM_AVATAR_STATUS_PENDING)


def _sync_problems():
    # The state says "sync error" only while the account is stuck; last_error
    # also catches one that is otherwise fine but whose last update did not
    # reach the forum. Both are cleared by the next sync that works.
    return db.select(ForumAccount).where(
        or_(ForumAccount.state == FORUM_STATE_SYNC_ERROR, ForumAccount.last_error.is_not(None))
    )


def _count(select):
    return db.session.scalar(db.select(func.count()).select_from(select.subquery())) or 0


def waiting_counts(user):
    """How much is waiting, counting only what this user may act on."""
    counts = {KIND_NAME_CHANGE: 0, KIND_PICTURE: 0, "sync_problems": 0}
    if can_review_name_changes(user):
        counts[KIND_NAME_CHANGE] = _count(_pending_name_changes())
    if can_review_pictures(user):
        counts[KIND_PICTURE] = _count(_pending_pictures())
        counts["sync_problems"] = _count(_sync_problems())
    return counts


def waiting_for_review_count(user):
    """The number on the Reviews tab: decisions waiting, not sync problems.

    Sync problems are shown on the page and the dashboard, but they are not
    somebody waiting for an answer and often clear by themselves on the next
    sync, so they do not keep the tab lit.
    """
    if not can_review_anything(user):
        return 0
    counts = waiting_counts(user)
    return counts[KIND_NAME_CHANGE] + counts[KIND_PICTURE]


def oldest_waiting(user):
    """The first item of each kind, for the dashboard's one-line summaries."""
    oldest = {KIND_NAME_CHANGE: None, KIND_PICTURE: None, "sync_problems": None}
    if can_review_name_changes(user):
        oldest[KIND_NAME_CHANGE] = db.session.execute(
            _pending_name_changes()
            .options(selectinload(MemberProfileChangeRequest.member))
            .order_by(MemberProfileChangeRequest.created_at.asc())
            .limit(1)
        ).scalar_one_or_none()
        if oldest[KIND_NAME_CHANGE] is not None:
            changes = describe_changes(oldest[KIND_NAME_CHANGE])
            oldest[KIND_NAME_CHANGE].changes = changes
            oldest[KIND_NAME_CHANGE].is_name_change = is_name_change(changes)
    if can_review_pictures(user):
        oldest[KIND_PICTURE] = db.session.execute(
            _pending_pictures()
            .options(selectinload(ForumAvatarSubmission.member))
            .order_by(ForumAvatarSubmission.uploaded_at.asc())
            .limit(1)
        ).scalar_one_or_none()
        oldest["sync_problems"] = db.session.execute(
            _sync_problems()
            .options(selectinload(ForumAccount.user))
            .order_by(ForumAccount.updated_at.desc())
            .limit(1)
        ).scalar_one_or_none()
    return oldest


def review_queue(user, limit=QUEUE_LIMIT):
    """Everything waiting that this user may decide, oldest first."""
    items = []
    if can_review_name_changes(user):
        requests_ = db.session.execute(
            _pending_name_changes()
            .options(
                selectinload(MemberProfileChangeRequest.member).selectinload(Member.user),
                selectinload(MemberProfileChangeRequest.requested_by),
            )
            .order_by(MemberProfileChangeRequest.created_at.asc())
            .limit(limit)
        ).scalars().all()
        items += [ReviewItem(KIND_NAME_CHANGE, r, r.created_at) for r in requests_]
    if can_review_pictures(user):
        submissions = db.session.execute(
            _pending_pictures()
            .options(
                selectinload(ForumAvatarSubmission.user),
                selectinload(ForumAvatarSubmission.member),
            )
            .order_by(ForumAvatarSubmission.uploaded_at.asc())
            .limit(limit)
        ).scalars().all()
        items += [ReviewItem(KIND_PICTURE, s, s.uploaded_at) for s in submissions]
    items.sort(key=lambda item: item.when)
    return items[:limit]


def sync_problems(user, limit=25):
    if not can_review_pictures(user):
        return []
    return db.session.execute(
        _sync_problems()
        .options(selectinload(ForumAccount.user), selectinload(ForumAccount.member))
        .order_by(ForumAccount.updated_at.desc())
        .limit(limit)
    ).scalars().all()


@dataclass
class HistoryPage:
    items: list
    page: int
    pages: int
    total: int


def review_history(user, page=1, per_page=HISTORY_PAGE_SIZE):
    """Past decisions of both kinds, newest first.

    The two tables are paged together by taking the newest page*per_page rows
    of each, merging them and cutting out the requested page: the rows of any
    page can only come from those. Cheap at the numbers this association has,
    and it needs no union across two differently shaped tables.
    """
    page = max(page, 1)
    wanted = page * per_page
    items = []
    total = 0
    if can_review_name_changes(user):
        decided = db.select(MemberProfileChangeRequest).where(MemberProfileChangeRequest.status != "pending")
        total += _count(decided)
        when = func.coalesce(MemberProfileChangeRequest.reviewed_at, MemberProfileChangeRequest.created_at)
        rows = db.session.execute(
            decided.options(
                selectinload(MemberProfileChangeRequest.member),
                selectinload(MemberProfileChangeRequest.reviewed_by),
            )
            .order_by(when.desc(), MemberProfileChangeRequest.id.desc())
            .limit(wanted)
        ).scalars().all()
        items += [ReviewItem(KIND_NAME_CHANGE, r, r.reviewed_at or r.created_at) for r in rows]
    if can_review_pictures(user):
        # Superseded pictures were replaced by the member before anybody
        # looked at them: nobody decided anything.
        decided = db.select(ForumAvatarSubmission).where(
            ForumAvatarSubmission.status.in_([FORUM_AVATAR_STATUS_APPROVED, FORUM_AVATAR_STATUS_REJECTED])
        )
        total += _count(decided)
        when = func.coalesce(ForumAvatarSubmission.reviewed_at, ForumAvatarSubmission.uploaded_at)
        rows = db.session.execute(
            decided.options(
                selectinload(ForumAvatarSubmission.member),
                selectinload(ForumAvatarSubmission.reviewed_by),
            )
            .order_by(when.desc(), ForumAvatarSubmission.id.desc())
            .limit(wanted)
        ).scalars().all()
        items += [ReviewItem(KIND_PICTURE, s, s.reviewed_at or s.uploaded_at) for s in rows]
    items.sort(key=lambda item: item.when, reverse=True)
    pages = max((total + per_page - 1) // per_page, 1)
    return HistoryPage(
        items=items[(page - 1) * per_page:wanted],
        page=page,
        pages=pages,
        total=total,
    )


# --- Deciding ----------------------------------------------------------------------
#
# Each decision is one whole action: the change, the audit entry, the email to
# the member, the forum follow-up, the commit. Each first locks its record, so
# a second admin deciding the same thing at the same moment waits, then finds
# it decided (services/locking.py) and is told so -- a conflict, not an error.


def forum_username_change(request_record):
    """What approving this request would do to the forum username: ``(current, suggested)``,
    or None when it would stay the same -- the admin decides whether to change it."""
    from .forum import generate_unique_forum_username

    user = request_record.member.user if request_record.member is not None else None
    current = user.forum_username if user is not None else None
    suggested = generate_unique_forum_username(
        request_record.requested_first_name,
        request_record.requested_last_name,
        request_record.requested_year_group,
        exclude_user_id=user.id if user is not None else None,
    )
    return (current, suggested) if current and current != suggested else None


def _no_longer_waiting(what):
    from . import ConflictError

    return ConflictError(f"That {what} is no longer waiting for review.", code="already_decided")


def _locked_submission(submission_id):
    from .locking import locked

    submission = db.session.execute(
        locked(
            db.select(ForumAvatarSubmission)
            .options(
                selectinload(ForumAvatarSubmission.user),
                selectinload(ForumAvatarSubmission.member).selectinload(Member.user),
            )
            .where(ForumAvatarSubmission.id == submission_id)
        )
    ).scalar_one_or_none()
    if submission is None or submission.status != FORUM_AVATAR_STATUS_PENDING:
        raise _no_longer_waiting("profile picture")
    return submission


def _forum_snapshots(submission):
    from .audit import snapshot_forum_account_for_audit, snapshot_forum_avatar_submission_for_audit

    return {
        "submission": snapshot_forum_avatar_submission_for_audit(submission),
        "forum_account": snapshot_forum_account_for_audit(submission.user.forum_account if submission.user else None),
    }


def approve_picture(submission_id, *, review_note, actor_user):
    """Approve a profile picture and send it to the forum.

    ``{"forum_error": str | None}``: the approval stands even when the forum
    could not be told; the next sync carries it there.
    """
    from . import ExternalServiceError
    from ..forum_service import FORUM_STATE_ACTIVE, ForumProviderError
    from .audit import log_audit_event
    from .forum import get_forum_service
    from .notifications import queue_user_status_notification

    submission = _locked_submission(submission_id)
    forum_service = get_forum_service()
    before = _forum_snapshots(submission)
    review_note = (review_note or "").strip() or None
    account = submission.user.forum_account if submission.user else None
    was_active = account is not None and account.state == FORUM_STATE_ACTIVE
    # A replacement an admin allowed: the member already has a picture.
    replacing = submission.member is not None and (
        forum_service.get_current_approved_submission(submission.member) is not None
        or forum_service.get_reclaimed_avatar(submission.member) is not None
    )
    try:
        result = forum_service.approve_avatar_submission(submission, reviewer=actor_user, review_note=review_note)
    except ForumProviderError as exc:
        db.session.rollback()
        raise ExternalServiceError(str(exc)) from exc

    log_audit_event(
        category="forum",
        event_type="avatar_approved" if not result.error else "avatar_approval_failed",
        actor_user=actor_user,
        target_user=submission.user,
        target_member=submission.member,
        before=before,
        after=_forum_snapshots(submission),
        metadata={"review_note": review_note, "error": result.error, "desired_state": result.desired_state},
    )
    # The moment their forum access becomes complete, which they were waiting
    # for. Not for somebody replacing a picture: their access was complete.
    if not result.error and replacing:
        # The one replacement allowed is used; the next one needs asking again.
        submission.member.avatar_replacement_allowed_at = None
        queue_user_status_notification(
            "forum_avatar_replaced",
            "Your new profile picture was approved.",
            recipient_email=submission.user.email if submission.user is not None else None,
            payload={"first_name": submission.member.first_name},
            target_user=submission.user,
            target_member=submission.member,
            object_type="forum_avatar_submission",
            object_id=submission.id,
        )
    elif not result.error and result.desired_state == FORUM_STATE_ACTIVE and not was_active:
        queue_user_status_notification(
            "forum_avatar_approved",
            "Your profile picture was approved.",
            recipient_email=submission.user.email if submission.user is not None else None,
            payload={"first_name": submission.member.first_name if submission.member is not None else None},
            target_user=submission.user,
            target_member=submission.member,
            object_type="forum_avatar_submission",
            object_id=submission.id,
        )
    db.session.commit()
    return {"forum_error": result.error}


def reject_picture(submission_id, *, review_note, actor_user):
    """Reject a profile picture. The member is emailed, with the note, and uploads another."""
    from . import ExternalServiceError
    from ..forum_service import ForumProviderError
    from .audit import log_audit_event
    from .forum import get_forum_service
    from .notifications import queue_user_status_notification

    submission = _locked_submission(submission_id)
    before = _forum_snapshots(submission)
    review_note = (review_note or "").strip() or None
    try:
        result = get_forum_service().reject_avatar_submission(submission, reviewer=actor_user, review_note=review_note)
    except ForumProviderError as exc:
        db.session.rollback()
        raise ExternalServiceError(str(exc)) from exc

    log_audit_event(
        category="forum",
        event_type="avatar_rejected",
        actor_user=actor_user,
        target_user=submission.user,
        target_member=submission.member,
        before=before,
        after=_forum_snapshots(submission),
        metadata={"review_note": review_note, "error": result.error if result else None},
    )
    member = submission.member
    queue_user_status_notification(
        "forum_avatar_rejected",
        "Your forum profile picture was rejected.",
        recipient_email=(submission.user.email if submission.user is not None
                         else (member.email_private if member is not None else None)),
        payload={"first_name": member.first_name if member is not None else None, "review_note": review_note},
        target_user=submission.user,
        target_member=member,
        object_type="forum_avatar_submission",
        object_id=submission.id,
    )
    db.session.commit()


def _locked_change_request(request_id):
    from .locking import locked

    record = db.session.execute(
        locked(db.select(MemberProfileChangeRequest).where(MemberProfileChangeRequest.id == request_id))
    ).scalar_one_or_none()
    if record is None or record.status != "pending":
        raise _no_longer_waiting("change request")
    return record


def approve_change(request_id, *, admin_note, forum_username, actor_user):
    """Approve a change request: the member's record takes the requested details.

    ``forum_username``: None keeps the forum username; a name (or "" for the
    suggested one) renames them on the forum too -- a number is added when it
    is taken. ``{"rename_pending": bool, "forum_error": str | None}``.
    """
    from . import outbox
    from ..db_models import ExternalWorkItem
    from .audit import log_audit_event, snapshot_member_for_audit, snapshot_user_for_audit
    from .clock import get_now_utc
    from .forum import build_forum_username_base, generate_unique_forum_username, sync_member_forum_state
    from .members import IDENTITY_MEMBER_FIELDS
    from .membership import member_has_active_access
    from .notifications import queue_user_status_notification

    record = _locked_change_request(request_id)
    member = record.member
    user = member.user
    before_member = snapshot_member_for_audit(member, fields=IDENTITY_MEMBER_FIELDS)
    before_user = snapshot_user_for_audit(user)
    previous_forum_username = user.forum_username if user is not None else None

    member.salutation = record.requested_salutation
    member.title = record.requested_title
    member.first_name = record.requested_first_name
    member.last_name = record.requested_last_name
    member.member_category = record.requested_member_category
    member.year_group = record.requested_year_group

    if user is not None and forum_username is not None:
        preferred = forum_username.strip() or build_forum_username_base(
            member.first_name, member.last_name, member.year_group)
        user.forum_username = generate_unique_forum_username(
            member.first_name, member.last_name, member.year_group, exclude_user_id=user.id, preferred=preferred)
        if user.forum_username != previous_forum_username:
            # A rename has to be asked for as one: the sync below leaves the
            # forum's username alone on purpose. Queued first, so that sync
            # does not take the old name back from the forum meanwhile.
            outbox.enqueue_forum_rename(user, reason="Forum username changed on approval.")

    record.status = "approved"
    record.admin_note = (admin_note or "").strip() or None
    record.reviewed_by = actor_user
    record.reviewed_at = get_now_utc()
    forum_result = None
    if user is not None and (user.forum_account is not None or member_has_active_access(member)):
        forum_result, _service = sync_member_forum_state(member)
    log_audit_event(
        category="profile_change_request",
        event_type="identity_request_approved",
        actor_user=actor_user,
        target_user=user,
        target_member=member,
        before={"request_status": "pending", "user": before_user, "member": before_member},
        after={"request_status": record.status, "user": snapshot_user_for_audit(user),
               "member": snapshot_member_for_audit(member, fields=IDENTITY_MEMBER_FIELDS)},
        metadata={
            "request_id": record.id,
            "member_note": record.member_note,
            "admin_note": record.admin_note,
            "previous_forum_username": previous_forum_username,
            "new_forum_username": user.forum_username if user is not None else None,
            "forum_sync_error": forum_result.error if forum_result else None,
        },
    )
    queue_user_status_notification(
        "identity_request_approved",
        "Your identity change request was approved.",
        recipient_email=user.email if user is not None else member.email_private,
        payload={"first_name": member.first_name, "admin_note": record.admin_note},
        target_user=user,
        target_member=member,
        object_type="member_profile_change_request",
        object_id=record.id,
    )
    db.session.commit()
    rename_pending = False
    if user is not None:
        # Done now rather than on the next worker pass, so the member sees the
        # new name when they next look. Left queued if the forum is down.
        outbox.process_pending(limit=5, kinds=[ExternalWorkItem.KIND_FORUM_RENAME], user_id=user.id)
        rename_pending = outbox.pending_count(kinds=[ExternalWorkItem.KIND_FORUM_RENAME], user_id=user.id) > 0
    return {"rename_pending": rename_pending, "forum_error": forum_result.error if forum_result else None}


def reject_change(request_id, *, admin_note, actor_user):
    """Turn a change request down. The member is emailed, with the note."""
    from .audit import log_audit_event
    from .clock import get_now_utc
    from .notifications import queue_user_status_notification

    record = _locked_change_request(request_id)
    record.status = "rejected"
    record.admin_note = (admin_note or "").strip() or None
    record.reviewed_by = actor_user
    record.reviewed_at = get_now_utc()
    member = record.member
    user = member.user if member is not None else None
    log_audit_event(
        category="profile_change_request",
        event_type="identity_request_rejected",
        actor_user=actor_user,
        target_user=user,
        target_member=member,
        before={"request_id": record.id, "status": "pending"},
        after={"request_id": record.id, "status": record.status},
        metadata={"member_note": record.member_note, "admin_note": record.admin_note},
    )
    queue_user_status_notification(
        "identity_request_rejected",
        "Your identity change request was rejected.",
        recipient_email=(user.email if user is not None else member.email_private) if member is not None else None,
        payload={"first_name": member.first_name if member is not None else None, "admin_note": record.admin_note},
        target_user=user,
        target_member=member,
        object_type="member_profile_change_request",
        object_id=record.id,
    )
    db.session.commit()
