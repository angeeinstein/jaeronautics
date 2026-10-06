"""What the admin dashboard counts: accounts, memberships, the forum, the archive.

Moved here from app.py with the dashboard's move to the new front end
(api/admin_dashboard.py); the numbers and what they include are unchanged.
"""

from sqlalchemy import func

from ..db_models import (
    ForumAccount,
    ForumAvatarSubmission,
    ImportedForumProfile,
    Member,
    MemberProfileChangeRequest,
    User,
    db,
)
from ..forum_service import (
    FORUM_AVATAR_STATUS_PENDING,
    FORUM_STATE_ACTIVE,
    FORUM_STATE_ONBOARDING,
    FORUM_STATE_SYNC_ERROR,
)


def metrics():
    """The membership in figures, as the dashboard shows them."""
    # Erased rows stay -- they are the payment record -- but they are not people
    # the association has any more, so they are excluded here exactly as they
    # are in the health report. Counting them made the dashboard disagree with
    # the Maintenance panel by the number of erasures, and any future import of
    # non-member accounts would widen that gap until neither number meant
    # anything.
    present_user = User.deleted_at.is_(None)
    present_member = Member.deleted_at.is_(None)
    return {
        # Portal accounts only. Counting the archive here would say the
        # association has 760 accounts when it has twenty, and this number is
        # read as "how many people use this".
        #
        # Somebody who has reconnected counts, though: they signed up, they
        # pay, they are here. Excluding them on the grounds that they were once
        # imported would leave this figure hundreds short after an intake, and
        # permanently.
        "total_accounts": db.session.scalar(
            db.select(func.count()).select_from(User).where(
                present_user,
                ~User.imported_forum_profile.has(
                    ImportedForumProfile.claimed_at.is_(None)
                ),
            )
        ) or 0,
        "archived_forum_accounts": db.session.scalar(
            db.select(func.count()).select_from(ImportedForumProfile)
        ) or 0,
        "archived_forum_claimed": db.session.scalar(
            db.select(func.count()).select_from(ImportedForumProfile).where(
                ImportedForumProfile.claimed_at.is_not(None)
            )
        ) or 0,
        "linked_members": db.session.scalar(
            db.select(func.count()).select_from(Member).where(present_member, Member.user_id.is_not(None))
        ) or 0,
        "active_memberships": db.session.scalar(db.select(func.count()).select_from(Member).where(present_member, Member.is_active.is_(True))) or 0,
        "pending_checkouts": db.session.scalar(db.select(func.count()).select_from(Member).where(present_member, Member.payment_status == "pending_checkout")) or 0,
        "pending_identity_requests": db.session.scalar(db.select(func.count()).select_from(MemberProfileChangeRequest).where(MemberProfileChangeRequest.status == "pending")) or 0,
        "cancel_scheduled_memberships": db.session.scalar(db.select(func.count()).select_from(Member).where(present_member, Member.cancel_at_period_end.is_(True))) or 0,
        "forum_onboarding_accounts": db.session.scalar(db.select(func.count()).select_from(ForumAccount).where(ForumAccount.state == FORUM_STATE_ONBOARDING)) or 0,
        "forum_active_accounts": db.session.scalar(db.select(func.count()).select_from(ForumAccount).where(ForumAccount.state == FORUM_STATE_ACTIVE)) or 0,
        "forum_sync_errors": db.session.scalar(db.select(func.count()).select_from(ForumAccount).where(ForumAccount.state == FORUM_STATE_SYNC_ERROR)) or 0,
        "pending_forum_avatars": db.session.scalar(db.select(func.count()).select_from(ForumAvatarSubmission).where(ForumAvatarSubmission.status == FORUM_AVATAR_STATUS_PENDING)) or 0,
    }


def shared_cancellation_end():
    """The day every cancelled membership ends on, when they all end on the same one; else None.

    Cancelled memberships mostly end on the same day -- the end of the paid
    period everybody shares -- and "ending 31.12." says more than "cancelled".
    Only claimed when it is true for all of them.
    """
    days = db.session.execute(
        db.select(Member.membership_ends_on)
        .where(Member.deleted_at.is_(None), Member.cancel_at_period_end.is_(True))
        .distinct()
    ).scalars().all()
    return days[0] if len(days) == 1 else None
