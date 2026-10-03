"""Teams: groups inside the association with their own members and leads.

A feature that can be switched off, and is off until somebody switches it on.
Off, nothing about teams is shown to members and nothing runs; the membership
works exactly as it does without them. So everything team-specific lives here
and in its own tables, and the rest of the portal only ever asks this module.

Teams are created in the portal, not in code, so that teams can work
differently from one another and the software can serve another association.
For the same reason the values that could one day take another state --
statuses, admission modes, roles -- are text, and what each one means is
written down here.

What has been discussed and why is in docs/teams-plan.md. None of it is fixed.

**Who may do what inside a team** is decided here, not in ``permissions.py``:
a lead leads one team, and the global roles hold for the whole portal. A team
role counts only while its holder is an active member of the association *and*
of the team -- a lead is a team member with a role on top. The role itself is
never taken away by a lapse; it simply stops counting until the membership is
back. Site admins, through ``Permission.TEAMS_MANAGE``, can do everything in
every team, which is what keeps a team with no active lead manageable.
"""

import re

from ..db_models import Setting, Team, TeamMembership, TeamRole, db
from ..permissions import Permission
from . import ConflictError, NotFoundError, ValidationError
from .audit import log_audit_event
from .clock import get_now_utc
from .locking import locked
from .membership import member_has_active_access

SETTING_ENABLED = "teams_enabled"
SETTING_LABEL_SINGULAR = "teams_label_singular"
SETTING_LABEL_PLURAL = "teams_label_plural"
SETTING_KEYS = (SETTING_ENABLED, SETTING_LABEL_SINGULAR, SETTING_LABEL_PLURAL)

DEFAULT_LABEL_SINGULAR = "Team"
DEFAULT_LABEL_PLURAL = "Teams"

STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"

#: Joining directly, or applying and being approved by the leads.
ADMISSION_OPEN = "open"
ADMISSION_APPROVAL = "approval"
ADMISSION_MODES = (ADMISSION_APPROVAL, ADMISSION_OPEN)

#: Until payment exists, every team is free.
PAYMENT_NONE = "none"

# The states of one attempt at being in a team (a TeamMembership row).
APPLIED = "applied"
INVITED = "invited"
APPROVED = "approved"
ACTIVE = "active"
ENDED = "ended"
REJECTED = "rejected"
WITHDRAWN = "withdrawn"
#: An attempt still under way. A person has at most one of these per team.
ONGOING = frozenset({APPLIED, INVITED, APPROVED, ACTIVE})


class TeamPermission:
    """What a role may do inside its own team."""

    VIEW_MEMBERS = "team.view_members"
    REVIEW_APPLICATIONS = "team.review_applications"
    REMOVE_MEMBERS = "team.remove_members"
    WRITE_NOTES = "team.write_notes"
    EXPORT = "team.export"
    EDIT_SETTINGS = "team.edit_settings"

    ALL = frozenset({
        VIEW_MEMBERS, REVIEW_APPLICATIONS, REMOVE_MEMBERS,
        WRITE_NOTES, EXPORT, EDIT_SETTINGS,
    })


ROLE_LEAD = "lead"

#: Every team role, and what it may do. A new role -- a treasurer who may only
#: export, say -- is a new entry here and needs no change to the database.
TEAM_ROLE_PERMISSIONS = {
    ROLE_LEAD: TeamPermission.ALL,
}

TEAM_ROLE_LABELS = {
    ROLE_LEAD: "Lead",
}

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SLUG_MAX_LENGTH = 60


# --- The switch and the label ----------------------------------------------


def _setting(key):
    row = db.session.get(Setting, key)
    return row.value if row is not None else None


def _write_setting(key, value):
    row = db.session.get(Setting, key)
    if value is None or value == "":
        if row is not None:
            db.session.delete(row)
        return
    if row is None:
        db.session.add(Setting(key=key, value=str(value)))
    else:
        row.value = str(value)


def teams_enabled():
    return _setting(SETTING_ENABLED) == "True"


def team_labels():
    """What teams are called here, as (singular, plural)."""
    return (
        _setting(SETTING_LABEL_SINGULAR) or DEFAULT_LABEL_SINGULAR,
        _setting(SETTING_LABEL_PLURAL) or DEFAULT_LABEL_PLURAL,
    )


def save_team_settings(actor, *, enabled, label_singular, label_plural):
    """Switch teams on or off and name them. Returns whether anything changed."""
    label_singular = (label_singular or "").strip()[:50]
    label_plural = (label_plural or "").strip()[:50]
    before = {
        "enabled": teams_enabled(),
        "label_singular": _setting(SETTING_LABEL_SINGULAR),
        "label_plural": _setting(SETTING_LABEL_PLURAL),
    }
    # Stored only when it differs from the default, so a later change of the
    # default reaches installations nobody has customised.
    after = {
        "enabled": bool(enabled),
        "label_singular": label_singular if label_singular not in ("", DEFAULT_LABEL_SINGULAR) else None,
        "label_plural": label_plural if label_plural not in ("", DEFAULT_LABEL_PLURAL) else None,
    }
    if before == after:
        return False
    _write_setting(SETTING_ENABLED, "True" if after["enabled"] else None)
    _write_setting(SETTING_LABEL_SINGULAR, after["label_singular"])
    _write_setting(SETTING_LABEL_PLURAL, after["label_plural"])
    log_audit_event("teams", "team_settings_changed", actor_user=actor, before=before, after=after)
    return True


# --- Teams -----------------------------------------------------------------


def suggest_slug(name):
    """A slug made from a team's name: "Rocket Team" -> "rocket-team"."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug[:SLUG_MAX_LENGTH].strip("-")


def all_teams(include_archived=True):
    query = db.select(Team).order_by(Team.status, Team.name)
    if not include_archived:
        query = query.where(Team.status == STATUS_ACTIVE)
    return db.session.execute(query).scalars().all()


def get_team(slug):
    team = db.session.execute(db.select(Team).filter_by(slug=(slug or "").strip().lower())).scalar_one_or_none()
    if team is None:
        raise NotFoundError("That team does not exist.")
    return team


def _clean_team_fields(*, name, description, admission_mode, applications_open,
                       application_prompt, max_members, forum_group):
    name = (name or "").strip()
    if not name:
        raise ValidationError("A team needs a name.", code="team_name_missing")
    if len(name) > 120:
        raise ValidationError("That name is too long.", code="team_name_too_long")
    if admission_mode not in ADMISSION_MODES:
        raise ValidationError("Choose how people join.", code="team_admission_mode_invalid")

    if max_members in (None, ""):
        max_members = None
    else:
        try:
            max_members = int(max_members)
        except (TypeError, ValueError):
            raise ValidationError("The maximum size must be a number.", code="team_max_members_invalid") from None
        if max_members <= 0:
            raise ValidationError("The maximum size must be above zero.", code="team_max_members_invalid")

    return {
        "name": name,
        "description": (description or "").strip() or None,
        "admission_mode": admission_mode,
        "applications_open": bool(applications_open),
        "application_prompt": (application_prompt or "").strip()[:255] or None,
        "max_members": max_members,
        "forum_group": (forum_group or "").strip()[:100] or None,
    }


def _snapshot(team):
    return {
        "slug": team.slug,
        "name": team.name,
        "description": team.description,
        "status": team.status,
        "admission_mode": team.admission_mode,
        "applications_open": team.applications_open,
        "application_prompt": team.application_prompt,
        "max_members": team.max_members,
        "forum_group": team.forum_group,
        "payment_mode": team.payment_mode,
    }


def create_team(actor, *, slug, **fields):
    cleaned = _clean_team_fields(**fields)
    slug = (slug or "").strip().lower() or suggest_slug(cleaned["name"])
    if not slug or len(slug) > SLUG_MAX_LENGTH or not SLUG_PATTERN.match(slug):
        raise ValidationError(
            "The short name may only contain lowercase letters, digits and hyphens.",
            code="team_slug_invalid",
        )
    if db.session.execute(db.select(Team.id).filter_by(slug=slug)).first() is not None:
        raise ConflictError("Another team already has that short name.", code="team_slug_taken")

    team = Team(slug=slug, status=STATUS_ACTIVE, payment_mode=PAYMENT_NONE, **cleaned)
    db.session.add(team)
    db.session.flush()
    log_audit_event("teams", "team_created", actor_user=actor, after=_snapshot(team))
    return team


def update_team(actor, team, **fields):
    """Change what a team is called and how people join it.

    The short name stays as it was created: it is in links people have
    bookmarked and, later, in what Stripe and the forum know the team by.
    """
    cleaned = _clean_team_fields(**fields)
    before = _snapshot(team)
    for key, value in cleaned.items():
        setattr(team, key, value)
    after = _snapshot(team)
    if before != after:
        log_audit_event("teams", "team_updated", actor_user=actor, before=before, after=after)
    return team


def set_team_archived(actor, team, archived):
    """Archive a team, or bring it back. Nothing is deleted either way."""
    target = STATUS_ARCHIVED if archived else STATUS_ACTIVE
    if team.status == target:
        return team
    before = _snapshot(team)
    team.status = target
    team.archived_at = get_now_utc() if archived else None
    log_audit_event(
        "teams", "team_archived" if archived else "team_restored",
        actor_user=actor, before=before, after=_snapshot(team),
    )
    return team


# --- Who is in a team, and what they may do there --------------------------


def is_active_association_member(user):
    """Whether this account currently belongs to the association."""
    if user is None or user.deleted_at is not None or user.disabled_at is not None:
        return False
    return member_has_active_access(user.member)


def active_team_membership(user, team):
    if user is None or team is None:
        return None
    return db.session.execute(
        db.select(TeamMembership).filter_by(team_id=team.id, user_id=user.id, status=ACTIVE)
    ).scalars().first()


def role_counts(team_role):
    """Whether a role held is in force: its holder is in the association and the team."""
    return (
        is_active_association_member(team_role.user)
        and active_team_membership(team_role.user, team_role.team) is not None
    )


def team_permissions(user, team):
    """Everything ``user`` may do in ``team``.

    Site admins may do everything in every team. Anybody else only what their
    roles in this team carry, only while those roles are in force, and only
    while the team is not archived.
    """
    if user is None or team is None or not getattr(user, "is_authenticated", True):
        return frozenset()
    if user.can(Permission.TEAMS_MANAGE):
        return TeamPermission.ALL
    if team.status != STATUS_ACTIVE:
        return frozenset()

    held = [team_role for team_role in team.roles if team_role.user_id == user.id]
    if not held or not role_counts(held[0]):
        return frozenset()
    granted = set()
    for team_role in held:
        granted |= TEAM_ROLE_PERMISSIONS.get(team_role.role, frozenset())
    return frozenset(granted)


def can_in_team(user, team, permission):
    return permission in team_permissions(user, team)


def teams_led_by(user):
    """The teams whose page this person may open as somebody with a role in it."""
    if user is None:
        return []
    teams = {team_role.team for team_role in db.session.execute(
        db.select(TeamRole).filter_by(user_id=user.id)
    ).scalars()}
    return sorted(
        (team for team in teams if team_permissions(user, team)),
        key=lambda team: team.name,
    )


def role_holders(team, role=None):
    roles = [team_role for team_role in team.roles if role is None or team_role.role == role]
    return sorted(roles, key=lambda team_role: (team_role.role, (team_role.user.email or "")))


def has_lead_in_force(team):
    return any(role_counts(team_role) for team_role in role_holders(team, ROLE_LEAD))


def _lock_team(team):
    """Make role changes to one team happen one after another.

    "Is this the last lead?" is a question about every lead of the team, so two
    admins each removing a different lead would otherwise both see one left.
    """
    db.session.execute(locked(db.select(Team.id).where(Team.id == team.id))).all()


def grant_team_role(actor, team, user, role):
    """Give somebody a role in a team.

    Allowed before they are a team member -- the first lead of a new team
    joins like anybody else and is approved by a site admin -- but the role
    only counts from the moment they are.
    """
    if role not in TEAM_ROLE_PERMISSIONS:
        raise ValidationError("That role does not exist.", code="team_role_unknown")
    if user is None or user.deleted_at is not None:
        raise ValidationError("That account cannot hold a role.", code="team_role_holder_invalid")

    _lock_team(team)
    existing = db.session.execute(
        db.select(TeamRole).filter_by(team_id=team.id, user_id=user.id, role=role)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    team_role = TeamRole(team=team, user=user, role=role, granted_by_user_id=getattr(actor, "id", None))
    db.session.add(team_role)
    db.session.flush()
    log_audit_event(
        "teams", "team_role_granted", actor_user=actor, target_user=user,
        metadata={"team": team.slug, "role": role},
    )
    return team_role


def revoke_team_role(actor, team, user, role, *, confirmed=False):
    """Take a role away.

    Taking away the last lead is allowed -- site admins can always run the
    team -- but only once the person doing it has confirmed they mean it.
    """
    _lock_team(team)
    team_role = db.session.execute(
        locked(db.select(TeamRole).filter_by(team_id=team.id, user_id=user.id, role=role))
    ).scalar_one_or_none()
    if team_role is None:
        return False

    if role == ROLE_LEAD and not confirmed:
        leads = db.session.scalar(
            db.select(db.func.count()).select_from(TeamRole).filter_by(team_id=team.id, role=ROLE_LEAD)
        )
        if leads <= 1:
            raise ConflictError(
                "This is the team's last lead. Confirm to remove them anyway.",
                code="team_last_lead",
            )

    db.session.delete(team_role)
    db.session.flush()
    log_audit_event(
        "teams", "team_role_revoked", actor_user=actor, target_user=user,
        metadata={"team": team.slug, "role": role},
    )
    return True


def find_account(address):
    """The account an address belongs to, for giving somebody a role."""
    from .identity import user_for_login_address

    user = user_for_login_address(address)
    if user is None or user.deleted_at is not None:
        raise NotFoundError("No account uses that address.", code="team_account_not_found")
    return user

