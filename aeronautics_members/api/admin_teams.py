"""Teams, the admins' part: switching teams on and naming them, creating
teams, their details, fee and leads, and archiving them.

What a team says about itself -- its texts, pictures, logo, the question for
applicants, its rules -- its leads keep on the team's own management page,
which site admins can open too (``manage_url``). The rules are in
services/teams.py and services/team_payments.py. Drawn by
frontend/src/pages/admin/teams/.
"""

from typing import Literal

from flask import url_for
from flask_login import current_user
from pydantic import Field

from ..db_models import User, db
from ..permissions import Permission
from ..services import NotFoundError
from ..services import teams as teams_service
from ._core import Model, UtcDateTime, endpoint

TAG = "Admin"
PERMISSIONS = [Permission.TEAMS_MANAGE]

AdmissionMode = Literal["approval", "open"]
PaymentMode = Literal["none", "subscription", "one_time"]
TeamRoleName = Literal["lead", "treasurer"]


class TeamSettings(Model):
    enabled: bool
    label_singular: str
    label_plural: str


class TeamRow(Model):
    slug: str
    name: str
    status: Literal["active", "archived"]
    logo_url: str | None
    admission_mode: AdmissionMode
    applications_open: bool
    members: int
    #: How the fee reads, e.g. "€10.00 every 6 months"; ``None`` when free.
    fee: str | None
    leads: list[str]
    #: Whether a lead counts: is a member of the association and the team.
    has_lead_in_force: bool


class TeamsOut(Model):
    settings: TeamSettings
    teams: list[TeamRow]


def _logo_url(team):
    return url_for("teams.team_logo", token=team.logo_token) if team.logo_token else None


def _person_name(user):
    member = user.member
    if member is not None:
        return f"{member.first_name} {member.last_name}".strip()
    return user.email


def _settings():
    singular, plural = teams_service.team_labels()
    return TeamSettings(enabled=teams_service.teams_enabled(), label_singular=singular, label_plural=plural)


def _row(team):
    return TeamRow(
        slug=team.slug, name=team.name, status=team.status, logo_url=_logo_url(team),
        admission_mode=team.admission_mode, applications_open=bool(team.applications_open),
        members=teams_service.active_member_count(team),
        fee=team.fee_display if team.payment_mode != teams_service.PAYMENT_NONE else None,
        leads=[_person_name(holder.user) for holder in teams_service.role_holders(team, teams_service.ROLE_LEAD)],
        has_lead_in_force=teams_service.has_lead_in_force(team),
    )


@endpoint("GET", "/admin/teams", response=TeamsOut, permissions=PERMISSIONS, tag=TAG)
def admin_teams():
    """Whether teams are on, what they are called, and every team."""
    return TeamsOut(settings=_settings(), teams=[_row(team) for team in teams_service.all_teams()])


class TeamSettingsIn(Model):
    enabled: bool
    label_singular: str = Field(max_length=50)
    label_plural: str = Field(max_length=50)


class TeamSettingsSavedOut(Model):
    changed: bool


@endpoint("PUT", "/admin/team-settings", response=TeamSettingsSavedOut, body=TeamSettingsIn, permissions=PERMISSIONS, tag=TAG)
def admin_teams_settings(body):
    """Switch teams on or off for members, and say what they are called."""
    changed = teams_service.save_team_settings(current_user, enabled=body.enabled,
                                               label_singular=body.label_singular, label_plural=body.label_plural)
    db.session.commit()
    return TeamSettingsSavedOut(changed=bool(changed))


# --- One team --------------------------------------------------------------------------


class RoleHolder(Model):
    user_id: int
    name: str
    email: str
    role: TeamRoleName
    role_label: str
    #: A role counts only while its holder is in the association and the team.
    in_force: bool


class Fee(Model):
    payment_mode: PaymentMode
    stripe_price_id: str | None
    period_starts: str | None
    #: How it reads for members, e.g. "€10.00 every 6 months".
    display: str | None


class TeamOut(Model):
    slug: str
    name: str
    status: Literal["active", "archived"]
    logo_url: str | None
    admission_mode: AdmissionMode
    max_members: int | None
    forum_group: str | None
    access_list_enabled: bool
    fee: Fee
    roles: list[RoleHolder]
    has_lead_in_force: bool
    members: int
    archived_at: UtcDateTime | None
    #: The team's own page and settings, which site admins can open.
    manage_url: str


def _team(slug):
    return teams_service.get_team(slug)


def _team_out(team):
    return TeamOut(
        slug=team.slug, name=team.name, status=team.status, logo_url=_logo_url(team),
        admission_mode=team.admission_mode, max_members=team.max_members, forum_group=team.forum_group,
        access_list_enabled=bool(team.access_list_enabled),
        fee=Fee(payment_mode=team.payment_mode, stripe_price_id=team.stripe_price_id,
                period_starts=team.period_starts, display=team.fee_display),
        roles=[RoleHolder(user_id=holder.user_id, name=_person_name(holder.user), email=holder.user.email,
                          role=holder.role, role_label=teams_service.TEAM_ROLE_LABELS.get(holder.role, holder.role),
                          in_force=bool(teams_service.role_counts(holder)))
               for holder in teams_service.role_holders(team)],
        has_lead_in_force=teams_service.has_lead_in_force(team),
        members=teams_service.active_member_count(team),
        archived_at=team.archived_at,
        manage_url=url_for("teams.team_manage", slug=team.slug),
    )


@endpoint("GET", "/admin/teams/<slug>", response=TeamOut, permissions=PERMISSIONS, tag=TAG)
def admin_team(slug):
    """One team: its details, fee and roles."""
    return _team_out(_team(slug))


class TeamDetailsIn(Model):
    name: str = Field(max_length=120)
    admission_mode: AdmissionMode
    #: Empty for no limit.
    max_members: int | None = Field(None, ge=1)
    #: Its active members are put in this forum group, which has to exist there.
    forum_group: str | None = Field(None, max_length=100)
    #: Whether the team has rooms that need an access list.
    access_list_enabled: bool = False


def _fields(body):
    return {"name": body.name, "admission_mode": body.admission_mode, "max_members": body.max_members,
            "forum_group": body.forum_group}


class FeeIn(Model):
    payment_mode: PaymentMode
    #: The Stripe price, on a product of the team's own (price_...).
    stripe_price_id: str | None = Field(None, max_length=100)
    #: Day and month each period starts, e.g. "01.10, 01.04".
    period_starts: str | None = Field(None, max_length=100)


class FeeChangeOut(Model):
    """What a change of fee did to the members already in."""

    #: Running subscriptions moving to the new price from their next renewal.
    moving: int
    #: Running subscriptions stopping at the end of what is paid; those stay in for free.
    stopping: int
    #: Members who keep what they paid for and pay the new way from then on.
    switched: int
    #: Members staying free until the next period, asked to pay by then.
    asked_to_pay: int


def _fee_change(outcome):
    outcome = outcome or {}
    return FeeChangeOut(moving=outcome.get("moving", 0), stopping=outcome.get("stopping", 0),
                        switched=outcome.get("switched", 0), asked_to_pay=outcome.get("asked_to_pay", 0))


def _set_fee(team, fee):
    from ..services.team_payments import update_payment_settings

    return update_payment_settings(current_user, team, payment_mode=fee.payment_mode,
                                   stripe_price_id=fee.stripe_price_id, period_starts=fee.period_starts)


class NewTeamIn(TeamDetailsIn):
    #: Used in links, made from the name if left out; cannot be changed later.
    slug: str | None = Field(None, max_length=60)
    #: Free when left out.
    fee: FeeIn | None = None


@endpoint("POST", "/admin/teams", response=TeamOut, body=NewTeamIn, permissions=PERMISSIONS, status=201, tag=TAG)
def admin_team_create(body):
    """Create a team. Its leads are given once it exists."""
    team = teams_service.create_team(current_user, slug=body.slug, **_fields(body))
    teams_service.set_access_list_enabled(current_user, team, body.access_list_enabled)
    if body.fee is not None and body.fee.payment_mode != "none":
        _set_fee(team, body.fee)
    db.session.commit()
    return _team_out(team)


@endpoint("PUT", "/admin/teams/<slug>", response=TeamOut, body=TeamDetailsIn, permissions=PERMISSIONS, tag=TAG)
def admin_team_update(slug, body):
    """Change a team's name, how people join, its size, forum group and access list."""
    team = _team(slug)
    teams_service.update_team(current_user, team, **_fields(body))
    teams_service.set_access_list_enabled(current_user, team, body.access_list_enabled)
    db.session.commit()
    return _team_out(team)


@endpoint("PUT", "/admin/teams/<slug>/fee", response=FeeChangeOut, body=FeeIn, permissions=PERMISSIONS, tag=TAG)
def admin_team_fee(slug, body):
    """Change how a team charges. Checked with Stripe first; members already in are moved or asked, and emailed."""
    outcome = _set_fee(_team(slug), body)
    db.session.commit()
    return _fee_change(outcome)


class ArchivedIn(Model):
    archived: bool
    #: The team's name, typed as the confirmation for archiving.
    confirm_name: str | None = Field(None, max_length=120)


@endpoint("PUT", "/admin/teams/<slug>/archived", response=TeamOut, body=ArchivedIn, permissions=PERMISSIONS,
          tag=TAG)
def admin_team_archived(slug, body):
    """Archive a team -- its members lose it at once, nothing is deleted -- or restore it."""
    team = _team(slug)
    teams_service.set_team_archived(current_user, team, body.archived, confirmed_name=body.confirm_name)
    db.session.commit()
    return _team_out(team)


class GrantRoleIn(Model):
    #: The address the person signs in with.
    email: str = Field(max_length=255)
    role: TeamRoleName = "lead"


@endpoint("POST", "/admin/teams/<slug>/roles", response=TeamOut, body=GrantRoleIn, permissions=PERMISSIONS,
          tag=TAG)
def admin_team_grant_role(slug, body):
    """Give somebody a role in a team -- allowed before they are in it."""
    team = _team(slug)
    teams_service.grant_team_role(current_user, team, teams_service.find_account(body.email), body.role)
    db.session.commit()
    return _team_out(team)


class RevokeRoleIn(Model):
    user_id: int
    role: TeamRoleName
    #: Needed to take away the team's last lead.
    confirmed: bool = False


@endpoint("POST", "/admin/teams/<slug>/roles/revoke", response=TeamOut, body=RevokeRoleIn,
          permissions=PERMISSIONS, tag=TAG)
def admin_team_revoke_role(slug, body):
    """Take a role away. The team's last lead only when confirmed (409 ``team_last_lead`` otherwise)."""
    team = _team(slug)
    user = db.session.get(User, body.user_id)
    if user is None:
        raise NotFoundError("That account does not exist.")
    teams_service.revoke_team_role(current_user, team, user, body.role, confirmed=body.confirmed)
    db.session.commit()
    return _team_out(team)
