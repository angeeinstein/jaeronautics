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
