"""Reviews: what waits for an administrator's yes or no, and the forum sync problems.

One queue, oldest first, for change requests and profile pictures -- each
kind only for whoever may decide it -- and the decisions, each its own: a
member who asked for a name change and uploaded a picture the same evening
can have one approved and the other turned down. The rules and the work are
in services/reviews.py. Drawn by frontend/src/pages/admin/Reviews.tsx.
"""

from typing import Literal

from flask import url_for
from flask_login import current_user
from pydantic import Field

from ..permissions import Permission
from ..services import PermissionError_, reviews
from ._core import Model, UtcDateTime, endpoint

TAG = "Admin"


def _may_review():
    if not reviews.can_review_anything(current_user):
        raise PermissionError_("You do not review anything.")


class Person(Model):
    """The member the item is about."""

    user_id: int | None
    name: str
    email: str | None


class Change(Model):
    field: str
    label: str
    current: str | None
    requested: str | None


class ForumUsername(Model):
    """Approving would make this the suggested forum username; the admin chooses."""

    current: str
    suggested: str


class ChangeRequest(Model):
    is_name_change: bool
    requested_full_name: str
    member_note: str | None
    #: Only what differs.
    changes: list[Change]
    forum_username: ForumUsername | None


class Picture(Model):
    image_url: str | None
    forum_username: str | None


class QueueItem(Model):
    kind: Literal["name_change", "picture"]
    id: int
    #: When it was asked for or uploaded.
    at: UtcDateTime
    person: Person
    change: ChangeRequest | None
    picture: Picture | None


class Waiting(Model):
    """How much waits of each kind; ``None`` for a kind this person does not decide."""

    name_changes: int | None
    pictures: int | None
    sync_problems: int | None


class SyncProblem(Model):
    user_id: int | None
    email: str | None
    error: str | None
    last_synced_at: UtcDateTime | None


class ReviewsOut(Model):
    waiting: Waiting
    #: The oldest of what waits; the rest follow as these are decided.
    queue: list[QueueItem]
    #: ``None`` for somebody who does not moderate the forum.
    sync_problems: list[SyncProblem] | None


def _person(member, user):
    if member is not None:
        return Person(user_id=member.user_id, name=f"{member.first_name} {member.last_name}".strip(),
                      email=member.email_private)
    return Person(user_id=user.id if user else None, name=user.email if user else "Unknown",
                  email=user.email if user else None)


def _queue_item(item):
    record = item.record
    if item.kind == reviews.KIND_NAME_CHANGE:
        changes = reviews.describe_changes(record)
        username = reviews.forum_username_change(record)
        return QueueItem(
            kind="name_change", id=record.id, at=record.created_at, person=_person(record.member, None),
            change=ChangeRequest(
                is_name_change=reviews.is_name_change(changes),
                requested_full_name=record.requested_full_name,
                member_note=record.member_note,
                changes=[Change(field=field, label=label, current=current, requested=requested)
                         for field, label, current, requested in changes],
                forum_username=ForumUsername(current=username[0], suggested=username[1]) if username else None,
            ),
            picture=None,
        )
    return QueueItem(
        kind="picture", id=record.id, at=record.uploaded_at, person=_person(record.member, record.user),
        change=None,
        picture=Picture(
            image_url=(url_for("forum.forum_avatar_public_file", token=record.public_token)
                       if record.public_token else None),
            forum_username=record.user.forum_username if record.user else None,
        ),
    )


@endpoint("GET", "/admin/reviews", response=ReviewsOut, permissions=[Permission.ADMIN_ACCESS], tag=TAG)
def admin_reviews():
    """What waits for this person's decision, oldest first, and the forum sync problems."""
    _may_review()
    counts = reviews.waiting_counts(current_user)
    names = reviews.can_review_name_changes(current_user)
    pictures = reviews.can_review_pictures(current_user)
    return ReviewsOut(
        waiting=Waiting(
            name_changes=counts[reviews.KIND_NAME_CHANGE] if names else None,
            pictures=counts[reviews.KIND_PICTURE] if pictures else None,
            sync_problems=counts["sync_problems"] if pictures else None,
        ),
        queue=[_queue_item(item) for item in reviews.review_queue(current_user)],
        sync_problems=[
            SyncProblem(user_id=account.user.id if account.user else None,
                        email=account.user.email if account.user else None,
                        error=account.last_error, last_synced_at=account.last_synced_at)
            for account in reviews.sync_problems(current_user)
        ] if pictures else None,
    )


# --- History ---------------------------------------------------------------------------


class HistoryQuery(Model):
    page: int = Field(1, ge=1)


class HistoryItem(Model):
    kind: Literal["name_change", "picture"]
    id: int
    at: UtcDateTime
    is_name_change: bool
    requested_full_name: str | None
    member_email: str | None
    decision: Literal["approved", "rejected", "withdrawn"]
    by: str | None


class HistoryOut(Model):
    items: list[HistoryItem]
    page: int
    pages: int
    total: int


@endpoint("GET", "/admin/reviews/history", response=HistoryOut, query=HistoryQuery,
          permissions=[Permission.ADMIN_ACCESS], tag=TAG)
def admin_reviews_history(query):
    """Past decisions this person could have made, newest first."""
    _may_review()
    history = reviews.review_history(current_user, page=query.page)
    items = []
    for item in history.items:
        record = item.record
        is_change = item.kind == reviews.KIND_NAME_CHANGE
        name_change = is_change and reviews.is_name_change(reviews.describe_changes(record))
        items.append(HistoryItem(
            kind=item.kind, id=record.id, at=item.when,
            is_name_change=name_change,
            requested_full_name=record.requested_full_name if name_change else None,
            member_email=record.member.email_private if record.member else None,
            decision=record.status if record.status in ("approved", "rejected") else "withdrawn",
            by=record.reviewed_by.email if record.reviewed_by else None,
        ))
    return HistoryOut(items=items, page=history.page, pages=history.pages, total=history.total)


# --- Deciding ------------------------------------------------------------------------------


class NoteIn(Model):
    #: Sent to the member with the decision.
    note: str | None = Field(None, max_length=2000)


class PictureApprovedOut(Model):
    #: The picture is approved even so; the next sync carries it to the forum.
    forum_error: str | None


@endpoint("POST", "/admin/reviews/pictures/<int:submission_id>/approve", response=PictureApprovedOut,
          body=NoteIn, permissions=[Permission.FORUM_MODERATE], tag=TAG)
def admin_review_picture_approve(submission_id, body):
    """Approve a profile picture and send it to the forum."""
    return PictureApprovedOut(**reviews.approve_picture(submission_id, review_note=body.note,
                                                        actor_user=current_user))


@endpoint("POST", "/admin/reviews/pictures/<int:submission_id>/reject", body=NoteIn,
          permissions=[Permission.FORUM_MODERATE], tag=TAG)
def admin_review_picture_reject(submission_id, body):
    """Reject a profile picture; the member is asked for another."""
    reviews.reject_picture(submission_id, review_note=body.note, actor_user=current_user)


class ChangeApproveIn(Model):
    note: str | None = Field(None, max_length=2000)
    #: Rename on the forum too: the name wanted ("" for the suggested one).
    #: Left out, the forum username stays.
    forum_username: str | None = Field(None, max_length=60)


class ChangeApprovedOut(Model):
    #: The forum could not be renamed just now; it is tried again by itself.
    rename_pending: bool
    forum_error: str | None


@endpoint("POST", "/admin/reviews/name-changes/<int:request_id>/approve", response=ChangeApprovedOut,
          body=ChangeApproveIn, permissions=[Permission.APPROVALS_REVIEW], tag=TAG)
def admin_review_change_approve(request_id, body):
    """Approve a change request: the member's record takes the requested details."""
    return ChangeApprovedOut(**reviews.approve_change(request_id, admin_note=body.note,
                                                      forum_username=body.forum_username,
                                                      actor_user=current_user))


@endpoint("POST", "/admin/reviews/name-changes/<int:request_id>/reject", body=NoteIn,
          permissions=[Permission.APPROVALS_REVIEW], tag=TAG)
def admin_review_change_reject(request_id, body):
    """Turn a change request down; the member is told, with the note."""
    reviews.reject_change(request_id, admin_note=body.note, actor_user=current_user)
