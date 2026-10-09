"""What an administrator does to one account, each as one complete action.

Moved here from the admin page's route handlers with the page's move to the
new front end (api/admin_account.py). Each function is the whole action --
the checks, the change, the audit entry, the forum and Stripe follow-ups, and
the commits, in the order the old routes did them -- so an endpoint only
translates the outcome. Failures are ServiceErrors with a message for the
person who tried; what the action did comes back as a plain dict.

The rules themselves stay where they were: access.py (roles, switching an
account off), privacy.py (erasure), forum_import.py (reconnecting),
workflows.py (billing and forum state).
"""

import re

import stripe
from flask import current_app

from ..db_models import ImportedForumProfile, Member, User, db
from ..permissions import Permission, role_label
from . import ConflictError, ExternalServiceError, NotFoundError, PermissionError_, ValidationError
from .access import set_account_disabled, set_account_roles
from .audit import (
    log_audit_event,
    serialize_audit_value,
    snapshot_forum_account_for_audit,
    snapshot_member_for_audit,
    snapshot_user_for_audit,
)
from .clock import get_now_utc
from .forum import get_forum_service, log_out_forum_session_if_possible, sync_member_forum_state
from .locking import lock_administration, locked
from .membership import member_has_active_access
from .privacy import INITIATED_BY_ADMIN, erase_account

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def _member_of(user):
    if user.member is None:
        raise ValidationError("This account does not have a membership.", code="no_membership")
    return user.member


def locked_user(user_id):
    """The account, locked for a decision about it; NotFoundError when there is none."""
    user = db.session.execute(locked(db.select(User).filter_by(id=user_id))).scalar_one_or_none()
    if user is None:
        raise NotFoundError("There is no such account.")
    return user


# --- Billing and forum ---------------------------------------------------------------


def sync_billing(user, *, actor_user):
    """Ask Stripe for this member's subscription and repair what a missed webhook left behind.

    ``{"changed": bool, "forum_error": str | None}``.
    """
    from .notifications import queue_curated_admin_notification
    from .workflows import refresh_member_billing_state
    from ..notification_service import ADMIN_ERROR_CHANNEL

    member = _member_of(user)
    if not (member.stripe_customer_id or member.stripe_subscription_id):
        raise ValidationError("No Stripe billing reference is stored for this membership yet.",
                              code="no_billing_reference")

    before = snapshot_member_for_audit(member)
    try:
        changed, subscription, forum_result = refresh_member_billing_state(member, force_stripe_sync=True,
                                                                           sync_forum=True)
    except stripe.StripeError as exc:
        db.session.rollback()
        current_app.logger.error("Manual Stripe billing sync failed for member_id=%s: %s", member.id, exc)
        queue_curated_admin_notification(
            ADMIN_ERROR_CHANNEL,
            "manual_billing_sync_failed",
            f"A manual Stripe billing sync failed for {member.email_private}.",
            payload={"member_email": member.email_private, "user_id": user.id, "member_id": member.id,
                     "error": str(exc)},
            target_user=user,
            target_member=member,
            object_type="member",
            object_id=member.id,
            commit=True,
        )
        raise ExternalServiceError("Stripe could not be reached just now. Please try again later.") from exc

    forum_error = forum_result.error if forum_result else None
    log_audit_event(
        category="billing",
        event_type="manual_billing_sync",
        actor_user=actor_user,
        target_user=user,
        target_member=member,
        before=before,
        after=snapshot_member_for_audit(member),
        metadata={
            "changed": changed,
            "stripe_status": subscription.get("status") if subscription else None,
            "stripe_cancel_at_period_end": subscription.get("cancel_at_period_end") if subscription else None,
            "stripe_cancel_at": subscription.get("cancel_at") if subscription else None,
            "forum_sync_error": forum_error,
        },
    )
    db.session.commit()
    return {"changed": bool(changed), "forum_error": forum_error}


def resync_forum(user, *, actor_user):
    """Bring the forum in line with this account now. ``{"error": str | None}``.

    What a reconnect left behind on the forum goes first: until it is gone,
    the forum refuses this person's address.
    """
    from .workflows import cleanup_then_sync

    member = _member_of(user)
    before = snapshot_forum_account_for_audit(user.forum_account)
    result, waiting = cleanup_then_sync(user)
    if result is None:
        raise ConflictError(
            "The forum account left behind by the reconnection could not be removed yet, so the forum still "
            f"refuses this address: {(waiting.last_error if waiting else None) or '-'}",
            code="forum_cleanup_pending",
        )
    log_audit_event(
        category="forum",
        event_type="manual_forum_resync",
        actor_user=actor_user,
        target_user=user,
        target_member=member,
        before=before,
        after=snapshot_forum_account_for_audit(user.forum_account),
        metadata={"desired_state": result.desired_state, "error": result.error},
    )
    db.session.commit()
    return {"error": result.error}


def set_picture_replacement(user_id, *, allow, actor_user):
    """Allow a member one new profile picture, or withdraw that.

    Members cannot change an approved picture themselves, on purpose; somebody
    who wants to asks an admin. Their current picture, and their forum access
    with it, stay until the new one is approved -- which uses the permission up.
    Nobody is emailed: the admin has usually just spoken to them.
    """
    member = db.session.execute(locked(db.select(Member).filter_by(user_id=user_id))).scalar_one_or_none()
    if member is None:
        raise ValidationError("This account does not have a membership.", code="no_membership")
    before = member.avatar_replacement_allowed_at
    member.avatar_replacement_allowed_at = get_now_utc() if allow else None
    log_audit_event(
        category="forum",
        event_type="avatar_replacement_allowed" if allow else "avatar_replacement_withdrawn",
        actor_user=actor_user,
        target_user=member.user,
        target_member=member,
        before={"avatar_replacement_allowed_at": serialize_audit_value(before)},
        after={"avatar_replacement_allowed_at": serialize_audit_value(member.avatar_replacement_allowed_at)},
    )
    db.session.commit()
    return member.avatar_replacement_allowed_at


# --- The private address ---------------------------------------------------------------


def may_correct_email(user, actor_user):
    """Whether ``actor_user`` may correct this account's private address (see correct_private_email)."""
    return (
        user.member is not None and user.deleted_at is None and user.id != actor_user.id
        and actor_user.can(Permission.APPROVALS_REVIEW)
        and (not user.roles or actor_user.can(Permission.ROLES_MANAGE))
    )


def correct_private_email(user, new_email, *, actor_user):
    """Correct a member's private address, for somebody locked out by a wrong one.

    The private address is the login and where password resets go: mistyped
    at signup or changed to a wrong one, and the password forgotten too, the
    person cannot get back in. Changing it hands the account to whoever reads
    the new address, so it is for an admin who knows who is asking. The new
    address must then be confirmed like any other, the address replaced is
    told when it was ever confirmed, and the change is logged with both.

    Returns the new address.
    """
    from ..security_utils import build_public_url
    from .identity import send_email_verification_email
    from .notifications import flush_marked_notification_channels, queue_user_status_notification
    from .workflows import sync_member_primary_email

    if user.deleted_at is not None or user.member is None:
        raise NotFoundError("There is no such account.")
    member = user.member
    new_email = (new_email or "").strip().lower()
    if user.id == actor_user.id:
        raise PermissionError_("Change your own address in your profile.", code="own_account")
    if user.roles and not actor_user.can(Permission.ROLES_MANAGE):
        # Their address is a way into somebody else's admin rights.
        raise PermissionError_("Only somebody who manages access can change the address of an account with a role.",
                               code="account_has_role")
    if not _EMAIL.fullmatch(new_email):
        raise ValidationError("Enter a valid email address.", details={"fields": {"email": "Enter a valid email address."}})
    old_email = user.email
    if new_email == (old_email or "").strip().lower():
        raise ConflictError("That is the address the account has already.", code="unchanged")

    has_forum_account = user.forum_account is not None or member_has_active_access(member)
    if has_forum_account and get_forum_service().address_taken_by_another_forum_account(user, new_email):
        raise ConflictError("That address belongs to another account on the forum.", code="taken_on_forum")
    old_was_confirmed = user.email_verified_at is not None
    before = snapshot_user_for_audit(user)
    try:
        sync_member_primary_email(member, new_email)
    except ValueError as exc:
        db.session.rollback()
        raise ConflictError(str(exc), code="address_taken") from exc
    log_audit_event(
        category="profile",
        event_type="private_email_corrected_by_admin",
        actor_user=actor_user,
        target_user=user,
        target_member=member,
        before=before,
        after=snapshot_user_for_audit(user),
    )
    if old_was_confirmed and old_email:
        new_local, _at, new_domain = new_email.partition("@")
        queue_user_status_notification(
            "account_email_changed_by_admin",
            f"The email address of account {user.id} was changed by an admin.",
            old_email,
            payload={"first_name": member.first_name, "new_email_masked": f"{new_local[:2]}…@{new_domain}",
                     "contact_url": build_public_url("public.contact")},
            target_user=user, target_member=member,
        )
    if has_forum_account:
        sync_member_forum_state(member)
    db.session.commit()
    flush_marked_notification_channels()
    try:
        send_email_verification_email(current_app._get_current_object(), user)
    except Exception as exc:  # noqa: BLE001 -- the change stands; they can ask for the link again
        current_app.logger.warning("Could not send the verification email to the corrected address of user %s: %s",
                                   user.id, exc)
    return new_email


# --- The old forum -----------------------------------------------------------------------

RECONNECT_RESULT_LIMIT = 15


def may_reconnect(user, actor_user):
    """Searching the old forum for an account: only for one that is not an old-forum
    account already, and only for admins who decide identity changes."""
    return (
        user.imported_forum_profile is None
        and user.deleted_at is None
        and actor_user.can(Permission.APPROVALS_REVIEW)
    )


def old_forum_candidates(user, query=""):
    """Unclaimed old-forum accounts for reconnecting by hand: ``[(profile, reason)]``.

    With a search (two characters or more), what matches it, reason None.
    Without one, what is probably theirs, with why: "address" (differs only
    in dots or spelling) or "name".
    """
    from sqlalchemy import func, or_

    from .forum_import import likely_old_accounts

    query = (query or "").strip()
    if not query:
        return likely_old_accounts(user)
    if len(query) < 2:
        return []
    like = f"%{query.lower()}%"
    profiles = db.session.execute(
        db.select(ImportedForumProfile)
        .join(User, User.id == ImportedForumProfile.user_id)
        .where(
            ImportedForumProfile.claimed_at.is_(None),
            User.deleted_at.is_(None),
            or_(
                func.lower(ImportedForumProfile.source_username).like(like),
                func.lower(ImportedForumProfile.display_name).like(like),
                func.lower(ImportedForumProfile.source_email).like(like),
            ),
        )
        .order_by(ImportedForumProfile.source_username)
        .limit(RECONNECT_RESULT_LIMIT)
    ).scalars().all()
    return [(profile, None) for profile in profiles]


def reconnect(user_id, profile_id, *, actor_user):
    """Reconnect a member to their old forum account by hand.

    The ordinary way is confirming the university address the old forum had.
    That fails when the address changed with a married name, no longer works,
    or the old forum never had one -- and the student ends up with an empty
    new forum account beside their old one. An admin who recognises them
    picks the old account here instead; the reconnection itself is the same.

    The account then lives on the old forum's row and the one it came from is
    gone: ``{"account_id", "old_username", "forum": "synced" | "background",
    "forum_error"}``.
    """
    from .forum_import import claim_archived_account
    from .workflows import cleanup_then_sync

    user = locked_user(user_id)
    profile = db.session.execute(
        locked(db.select(ImportedForumProfile).filter_by(id=profile_id))
    ).scalar_one_or_none()
    if user.imported_forum_profile is not None:
        raise ConflictError("This account is an old forum account already.", code="already_old_forum")
    if profile is None or profile.claimed_at is not None:
        raise ConflictError("That old forum account is taken already.", code="old_account_taken")

    old_username = profile.source_username
    claimed = claim_archived_account(user, profile=profile)
    if claimed is None:
        db.session.rollback()
        raise ConflictError("Could not reconnect this account. See the log for why.", code="reconnect_failed")

    archived = claimed.user
    log_audit_event(
        category="forum",
        event_type="forum_account_reconnected_by_admin",
        actor_user=actor_user,
        target_user=archived,
        target_member=archived.member,
        metadata={"old_forum_username": old_username, "source_user_id": claimed.source_user_id,
                  "retired_user_id": user_id},
    )
    db.session.commit()
    # The account they leave behind holds their address; it goes first, then
    # the forum is told -- now, so the admin sees how it went.
    try:
        result, _waiting = cleanup_then_sync(archived)
        db.session.commit()
    except Exception:  # noqa: BLE001 -- both stay queued and run in the background
        db.session.rollback()
        current_app.logger.exception("Forum cleanup and sync after reconnecting user %s", archived.id)
        result = None
    return {
        "account_id": archived.id,
        "old_username": old_username,
        "forum": "background" if result is None else "synced",
        "forum_error": result.error if result is not None else None,
    }


# --- Access ------------------------------------------------------------------------------


def set_disabled(user_id, *, disable, reason, actor_user):
    """Switch an account off, or back on. Never touches the membership.

    ``{"changed": bool}``. Switching off also ends their forum session: the
    sync takes their groups away, but a session already open would stay open.
    """
    lock_administration()
    user = locked_user(user_id)
    before = snapshot_user_for_audit(user)
    change = set_account_disabled(user, disable=disable, actor_user=actor_user, reason=reason)
    if not change["changed"]:
        db.session.rollback()
        return {"changed": False}

    # The forum is a separate system holding its own group memberships, so
    # barring somebody here means nothing there until this runs.
    if user.member is not None:
        try:
            sync_member_forum_state(user.member)
        except Exception as exc:  # noqa: BLE001 -- the decision stands either way
            current_app.logger.warning("Could not sync forum state after disabling user_id=%s: %s", user.id, exc)
    if disable:
        try:
            log_out_forum_session_if_possible(user)
        except Exception as exc:  # noqa: BLE001
            current_app.logger.warning("Could not end the forum session of user_id=%s: %s", user.id, exc)

    log_audit_event(
        category="access",
        event_type="account_disabled" if disable else "account_enabled",
        actor_user=actor_user,
        target_user=user,
        target_member=user.member,
        before=before,
        after=snapshot_user_for_audit(user),
        metadata={"reason": user.disabled_reason},
    )
    db.session.commit()
    return {"changed": True}


def set_roles(user_id, slugs, *, actor_user):
    """Set an account's roles to exactly ``slugs``.

    One action rather than a grant and a revoke per role: the guards that
    matter are about the resulting state -- is anybody left who can install an
    update -- and a per-role action has to re-derive that each time.

    ``{"changed": bool, "redundant": [labels]}`` -- the roles not stored because
    another chosen one already covers them.
    """
    lock_administration()
    user = locked_user(user_id)
    before = snapshot_user_for_audit(user)
    change = set_account_roles(user, list(slugs), actor_user=actor_user)
    redundant = [role_label(slug) for slug in change["redundant"]]
    if not change["changed"]:
        db.session.rollback()
        return {"changed": False, "redundant": redundant}

    # Roles decide forum groups and, when the portal manages them, the forum's
    # own staff flags; the nightly check compares membership state, not roles,
    # so a role taken away here would otherwise stay on the forum.
    if user.member is not None:
        try:
            sync_member_forum_state(user.member)
        except Exception as exc:  # noqa: BLE001 -- the roles are saved either way
            current_app.logger.warning("Could not sync forum state after a role change for user_id=%s: %s",
                                       user.id, exc)
    log_audit_event(
        category="access",
        event_type="account_roles_changed",
        actor_user=actor_user,
        target_user=user,
        target_member=user.member,
        before=before,
        after=snapshot_user_for_audit(user),
        metadata={
            "granted_roles": change["granted"],
            "revoked_roles": change["revoked"],
            "permissions_gained": change["permissions_gained"],
            "permissions_lost": change["permissions_lost"],
        },
    )
    db.session.commit()
    return {"changed": True, "redundant": redundant}


# --- Erasure -----------------------------------------------------------------------------


def erase(user_id, *, confirm_email, reason, actor_user):
    """Erase a member's personal data, keeping the records that must survive.

    Deliberately not blocked when the member still has a paid subscription: an
    expelled member is exactly the case this exists for. The page states the
    consequences first, and the subscription is cancelled here so the
    association stops charging someone it no longer has a record of.

    Typing the address is the confirmation; a stray double-click on a
    destructive button must not be enough.
    ``{"subscription_cancelled": bool, "forum_deferred": bool}``.
    """
    lock_administration()
    user = locked_user(user_id)
    if (confirm_email or "").strip().lower() != (user.email or "").strip().lower():
        raise ValidationError("The typed address does not match, so nothing was erased.",
                              code="confirmation_mismatch",
                              details={"fields": {"confirm_email": "This is not the account's address."}})
    summary = erase_account(user, actor_user=actor_user, initiated_by=INITIATED_BY_ADMIN,
                            note=(reason or "").strip() or None)
    db.session.commit()
    return {"subscription_cancelled": bool(summary["subscription_cancelled"]),
            "forum_deferred": bool(summary["forum_deferred"])}
