"""A person's picture, as an address -- one answer for every place that shows one.

The picture uploaded here and approved by an admin; else, for somebody from
the old forum, the picture the old forum had (``ImportedForumProfile``), which
the forum shows too: most members came from there, and a returning student is
not asked to upload theirs again (``ForumService.get_reclaimed_avatar``).
Nothing for an erased account.

Taken for a whole list at once -- two queries, however many people -- so a
page of fifty accounts does not ask fifty times. Who may see the picture is
in the privacy policy, § 22: the person, their teams' members and leads, and
the admins.
"""

from flask import url_for

from ..db_models import ForumAvatarSubmission, ImportedForumProfile, Member, db
from ..forum_service import FORUM_AVATAR_STATUS_APPROVED


def picture_urls(users):
    """``{user_id: address}`` for those of ``users`` who have a picture."""
    ids = {user.id for user in users if user is not None and user.deleted_at is None}
    if not ids:
        return {}

    pictures = {}
    # The old forum's first, so an approved picture from here replaces it.
    for user_id, token in db.session.execute(
        db.select(ImportedForumProfile.user_id, ImportedForumProfile.avatar_public_token).where(
            ImportedForumProfile.user_id.in_(ids),
            ImportedForumProfile.avatar_path.is_not(None),
            ImportedForumProfile.avatar_public_token.is_not(None),
        )
    ):
        pictures[user_id] = url_for("forum.forum_imported_avatar_public_file", token=token)

    # By the membership, not the uploader: a returning student's membership
    # moves onto their old account (forum_import.claim_archived_account).
    # Oldest first, so the newest approved one is the one left.
    for user_id, token in db.session.execute(
        db.select(Member.user_id, ForumAvatarSubmission.public_token)
        .join(Member, Member.id == ForumAvatarSubmission.member_id)
        .where(
            Member.user_id.in_(ids),
            ForumAvatarSubmission.status == FORUM_AVATAR_STATUS_APPROVED,
            ForumAvatarSubmission.public_token.is_not(None),
        )
        .order_by(ForumAvatarSubmission.uploaded_at.asc())
    ):
        pictures[user_id] = url_for("forum.forum_avatar_public_file", token=token)
    return pictures


def picture_url(user):
    """One person's picture as an address, or None."""
    return picture_urls([user]).get(getattr(user, "id", None))
