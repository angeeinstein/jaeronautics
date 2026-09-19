"""Changing which roles an account holds.

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
"""

from ..db_models import Role, User, db
from ..permissions import PROTECTED_PERMISSIONS, ROLE_PERMISSIONS, permissions_for, roles_with
from . import ConflictError, ValidationError


def assignable_roles():
    """Every role that may be granted, in a stable order for display."""
    return sorted(ROLE_PERMISSIONS)


def _active_holders_excluding(permission, user_id):
    """How many other accounts could still exercise ``permission``."""
    slugs = roles_with(permission)
    if not slugs:
        return 0
    return db.session.scalar(
        db.select(db.func.count())
        .select_from(User)
        .where(
            User.roles.any(Role.slug.in_(slugs)),
            User.deleted_at.is_(None),
            User.id != user_id,
        )
    ) or 0


def describe_role_change(user, requested_slugs):
    """What changing ``user``'s roles to ``requested_slugs`` would do.

    Returned rather than raised so a caller can show the consequence before
    asking for confirmation, and so the refusal reasons are testable directly.
    """
    if user is None:
        raise ValidationError("An account is required.")

    requested = set(requested_slugs or ())
    unknown = requested - set(ROLE_PERMISSIONS)
    if unknown:
        raise ValidationError(f"Unknown role(s): {', '.join(sorted(unknown))}")

    current = {role.slug for role in user.roles}
    losing = permissions_for(current) - permissions_for(requested)

    blockers = []
    if user.deleted_at is not None:
        blockers.append(("erased", "This account was erased and cannot be given access."))

    for permission, consequence in PROTECTED_PERMISSIONS.items():
        if permission not in losing:
            continue
        if _active_holders_excluding(permission, user.id) == 0:
            blockers.append((
                "last_holder",
                f"This is the only account that can do this -- {consequence}. "
                "Give another account the role first.",
            ))

    return {
        "current": sorted(current),
        "requested": sorted(requested),
        "granted": sorted(requested - current),
        "revoked": sorted(current - requested),
        "permissions_lost": sorted(losing),
        "permissions_gained": sorted(permissions_for(requested) - permissions_for(current)),
        "blockers": blockers,
        "changed": requested != current,
    }


def set_account_roles(user, requested_slugs, *, actor_user):
    """Make ``user``'s roles exactly ``requested_slugs``. Returns the change.

    Does not commit: the caller owns the transaction so the change and its audit
    entry land together.
    """
    if actor_user is not None and actor_user.id == user.id:
        raise ConflictError(
            "You cannot change your own roles here. Ask another administrator, "
            "or use grant-superadmin on the server.",
            code="self_role_change",
        )

    change = describe_role_change(user, requested_slugs)
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

    return change
