"""Changing which roles and permissions an account holds (docs/permissions-plan.md).

One function decides this, for one reason: the interesting part is not granting
a role, it is refusing to grant or remove one, and those refusals have to hold
however the change arrives. Four separate grant/revoke endpoints each carried
their own copy of the guards, which is three opportunities to forget one.

So the caller says what the account's roles *should be*, and this works out the
difference and whether it is allowed. Adding a role to ``ROLE_PERMISSIONS`` makes
it assignable here with no further change.

The guards, and why each exists:

* **Not your own account.** Somebody removing their own last privileged role
  locks themselves out, and the confirmation dialog is not the place to discover
  that. Recovery needs a shell on the server.
* **Not the last holder of a protected capability.** ``PROTECTED_PERMISSIONS``
  names what must never reach zero holders -- administering the site, installing
  an update, granting access. This counts what would be left *after* the change
  rather than checking for a particular role, so a future role carrying one of
  them counts as cover.
* **Not an erased account.** Its rows survive as the record; it cannot sign in,
  and handing it a role would be an odd thing to find later.
* **Only what one holds.** Whoever grants (``roles.manage``, in the admin role)
  gives or takes away only permissions they hold themselves; a super admin --
  every permission, no exceptions -- is made, or unmade, only by a super admin.
  And an account holding a permission its actor does not -- its address, its
  switch, its erasure -- is out of their reach (``covers``): otherwise an admin
  could change a super admin's address and reset its password.

A person holds roles, plus exceptions: permissions added to what the roles
give, or taken away (``UserPermission``). The page sends the roles and every
permission ticked; the exceptions are what differs from the roles.
"""

from ..db_models import User, UserPermission, db
from ..permissions import (
    ALL_PERMISSIONS,
    PROTECTED_PERMISSIONS,
    ROLE_PERMISSIONS,
    SUPERADMIN,
    effective_permissions,
    minimal_roles,
    permissions_for,
    with_needs,
)
from . import ConflictError, ValidationError
from .clock import get_now_utc


def assignable_roles():
    """Every role that may be granted, in a stable order for display."""
    return sorted(ROLE_PERMISSIONS)


def is_superadmin(user):
    return user is not None and user.deleted_at is None and any(role.slug == SUPERADMIN for role in user.roles)


def holders(permission, *, include_disabled=True):
    """The accounts that hold ``permission`` now -- by a role or an exception --, not erased.

    Asked person by person: with exceptions, which roles carry a permission no longer says who
    holds it (somebody may have it added, or taken away)."""
    query = db.select(User).where(
        User.deleted_at.is_(None),
        db.or_(User.roles.any(), User.permission_exceptions.any(UserPermission.granted.is_(True))))
    if not include_disabled:
        query = query.where(User.disabled_at.is_(None))
    return [user for user in db.session.execute(query).scalars() if user.can(permission)]


def holder_emails(*permissions, include_disabled=True):
    """The verified addresses of whoever holds any of ``permissions``: whom an email for them goes to."""
    found = {}
    for permission in permissions:
        for user in holders(permission, include_disabled=include_disabled):
            if user.email_verified_at is not None:
                found[user.id] = user.email
    return sorted(found.values())


def _active_holders_excluding(permission, user_id):
    """How many other accounts could still exercise ``permission``.

    A switched-off admin cannot administer anything, so counting them as cover would let the
    last working one be disabled or stripped."""
    return sum(1 for user in holders(permission, include_disabled=False) if user.id != user_id)


def covers(actor, user):
    """Whether ``actor`` holds every permission ``user`` holds -- and so may act on the things
    that are a way into them: their access, their address, switching them off, erasing them."""
    return actor is not None and user.permissions <= actor.permissions


def _exceptions(user):
    added = {row.permission for row in user.permission_exceptions if row.granted}
    taken = {row.permission for row in user.permission_exceptions if not row.granted}
    return added, taken


def describe_role_change(user, requested_slugs, requested_permissions=None, *, actor_user=None):
    """What changing ``user``'s roles to ``requested_slugs`` -- and, when given, their
    permissions to exactly ``requested_permissions`` -- would do.

    ``requested_permissions`` None keeps the exceptions as they are. Each permission ticked
    brings what it needs. Returned rather than raised so a caller can show the consequence
    before asking for confirmation, and so the refusal reasons are testable directly.
    """
    if user is None:
        raise ValidationError("An account is required.")

    asked_for = set(requested_slugs or ())
    unknown = asked_for - set(ROLE_PERMISSIONS)
    if unknown:
        raise ValidationError(f"Unknown role(s): {', '.join(sorted(unknown))}")
    unknown = set(requested_permissions or ()) - ALL_PERMISSIONS
    if unknown:
        raise ValidationError(f"Unknown permission(s): {', '.join(sorted(unknown))}")

    # Ticking Admin as well as Super Admin describes the same account as Super
    # Admin alone, so only one of those may be stored -- otherwise the same
    # access has two spellings and the badges disagree with the checkboxes.
    # This removes nothing the account can do: the covering role grants it all.
    requested = minimal_roles(asked_for)
    current = {role.slug for role in user.roles}
    before = user.permissions
    from_roles = with_needs(permissions_for(requested))
    if SUPERADMIN in requested:
        added, taken = set(), set()  # holds everything; takes no exceptions
    elif requested_permissions is None:
        added, taken = _exceptions(user)
    else:
        wanted = with_needs(requested_permissions)
        added, taken = wanted - from_roles, from_roles - wanted
    after = effective_permissions(requested, added, taken)
    losing = before - after
    gaining = after - before

    blockers = []
    if user.deleted_at is not None:
        blockers.append(("erased", "This account was erased and cannot be given access."))
    if actor_user is not None and not is_superadmin(actor_user) and (
            SUPERADMIN in requested) != (SUPERADMIN in current):
        blockers.append(("superadmin_only", "Only a super admin can make somebody a super admin, or unmake one."))
    if actor_user is not None and ((losing | gaining) - actor_user.permissions or not covers(actor_user, user)):
        # Within what one holds, and for somebody who holds no more than oneself -- as their
        # address, their switch and their erasure (covers).
        blockers.append((
            "beyond_own",
            "You can only give or take away permissions you hold yourself, for somebody who holds "
            "no more than you.",
        ))

    for permission, consequence in PROTECTED_PERMISSIONS.items():
        if permission not in losing:
            continue
        if _active_holders_excluding(permission, user.id) == 0:
            blockers.append((
                "last_holder",
                f"This is the only account that can do this -- {consequence}. "
                "Give another account the role first.",
            ))

    before_added, before_taken = _exceptions(user)
    return {
        "current": sorted(current),
        "requested": sorted(requested),
        "redundant": sorted(asked_for - requested),
        "granted": sorted(requested - current),
        "revoked": sorted(current - requested),
        "permissions_lost": sorted(losing),
        "permissions_gained": sorted(gaining),
        "added": sorted(added),
        "taken": sorted(taken),
        "blockers": blockers,
        "changed": requested != current or (added, taken) != (before_added, before_taken),
    }


def set_account_roles(user, requested_slugs, *, actor_user, requested_permissions=None):
    """Make ``user``'s roles exactly ``requested_slugs`` -- and, when given, their permissions
    exactly ``requested_permissions`` (the exceptions are what differs from the roles). Returns
    the change.

    Does not commit: the caller owns the transaction so the change and its audit
    entry land together.
    """
    if actor_user is not None and actor_user.id == user.id:
        raise ConflictError(
            "You cannot change your own roles here. Ask another administrator, "
            "or use grant-superadmin on the server.",
            code="self_role_change",
        )

    change = describe_role_change(user, requested_slugs, requested_permissions, actor_user=actor_user)
    if change["blockers"]:
        code, message = change["blockers"][0]
        raise ConflictError(message, code=code)

    if not change["changed"]:
        return change

    from ..app import get_role

    for slug in change["revoked"]:
        user.revoke_role(slug)
    for slug in change["granted"]:
        user.grant_role(get_role(slug))
    wanted = {permission: True for permission in change["added"]}
    wanted.update({permission: False for permission in change["taken"]})
    # A row whose sign flips is changed in place: removed and added again, the insert would
    # reach the database before the delete and break the one-row-per-permission rule.
    for row in list(user.permission_exceptions):
        if wanted.get(row.permission) is None:
            user.permission_exceptions.remove(row)
        elif wanted[row.permission] != row.granted:
            row.granted = wanted[row.permission]
            row.granted_by_user_id = actor_user.id if actor_user is not None else None
    kept = {row.permission for row in user.permission_exceptions}
    for permission, granted in wanted.items():
        if permission not in kept:
            user.permission_exceptions.append(UserPermission(
                permission=permission, granted=granted,
                granted_by_user_id=actor_user.id if actor_user is not None else None))
    db.session.flush()

    return change


def describe_account_disable(user, *, actor_user, disable=True):
    """What switching this account off (or back on) would do, and what forbids it.

    Deliberately says nothing about the membership. The two states answer
    different questions and are allowed to disagree: somebody paid up for the
    year can be barred from signing in, and an account in good standing can sit
    here with no membership at all. Suspending a person by cancelling what they
    paid for would be a different act with a different meaning, and a refund
    attached to it.

    The same guards as a role change, for the same reasons -- switching an
    account off removes its access just as surely as taking the role away.
    """
    blockers = []

    if user.deleted_at is not None:
        blockers.append(("erased", "This account was erased. There is nothing to switch off."))
        return {"blockers": blockers, "changed": False}

    if disable and user.is_disabled:
        return {"blockers": [], "changed": False}
    if not disable and not user.is_disabled:
        return {"blockers": [], "changed": False}

    if actor_user is not None and not covers(actor_user, user):
        blockers.append((
            "beyond_own",
            "This account holds permissions you do not. Only somebody who holds all of them can switch it off or on.",
        ))
    if disable:
        if actor_user is not None and actor_user.id == user.id:
            blockers.append((
                "self",
                "You cannot switch off your own account. Somebody else has to do it.",
            ))

        for permission, consequence in PROTECTED_PERMISSIONS.items():
            if permission not in user.permissions:
                continue
            if _active_holders_excluding(permission, user.id) == 0:
                blockers.append((
                    "last_holder",
                    f"This is the only account that can do this -- {consequence}. "
                    "Give another account the role first.",
                ))

    return {"blockers": blockers, "changed": True}


def set_account_disabled(user, *, disable, actor_user, reason=None):
    """Switch an account off or back on. Returns what happened.

    Leaves the membership exactly as it is, including an active subscription
    that keeps renewing: barring somebody is not a refund.
    """
    outcome = describe_account_disable(user, actor_user=actor_user, disable=disable)
    if outcome["blockers"]:
        raise ConflictError(outcome["blockers"][0][1])
    if not outcome["changed"]:
        return outcome

    if disable:
        user.disabled_at = get_now_utc()
        user.disabled_reason = (reason or "").strip() or None
        user.disabled_by_user_id = actor_user.id if actor_user is not None else None
    else:
        user.disabled_at = None
        user.disabled_reason = None
        user.disabled_by_user_id = None

    return outcome
