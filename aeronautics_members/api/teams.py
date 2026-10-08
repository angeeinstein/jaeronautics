"""Teams, for members: the overview, a team's page and what it is about,
joining or applying, paying the team fee, leaving -- and a team's rules.

What somebody sees of their membership of a team -- its state, what is owed
or coming, the buttons -- is worked out here, once, for the overview and the
team's own page alike (``_membership``). The rules are in services/teams.py
and services/team_payments.py. Drawn by frontend/src/pages/teams/.
"""

from datetime import date, timedelta
from typing import Literal

from flask import abort, current_app, url_for
from flask_login import current_user
from pydantic import Field

from ..db_models import db
from ..permissions import Permission
from ..services import NotFoundError, ServiceError, team_payments
from ..services import teams as teams_service
from ..services.clock import get_membership_today
from ..services.membership import format_membership_date_display as day_text
from ..services.notifications import flush_marked_notification_channels
from ._core import Model, endpoint
from .legal import LegalTextOut, TextQuery, legal_text

TAG = "Teams"
P = teams_service.TeamPermission

Status = Literal["applied", "invited", "approved", "active", "ended", "rejected", "withdrawn"]
Action = Literal["open", "pay_join", "pay_next", "pay_stay", "stay", "leave", "withdraw", "join", "manage", "money"]

PAYMENT_ARRIVING = "Payment received. It is confirmed in a moment; by SEPA debit it can take a few days."


class LabelsOut(Model):
    #: What teams are called here: "Team", or another word an admin chose.
    singular: str
    plural: str


class MembershipNoteOut(Model):
    tone: Literal["plain", "info", "warning"]
    text: str


class TeamMembershipOut(Model):
    """Somebody's membership of one team, as they see it."""

    #: The attempt under way, or else the last one; None if there never was one.
    status: Status | None
    status_label: str | None
    #: Under way: applied, invited, approved or a member.
    ongoing: bool
    #: The fee, while not a member yet.
    fee: str | None
    #: What is owed or coming, in order.
    notes: list[MembershipNoteOut]
    #: When invited: where and when the leads would like to meet.
    meeting: str | None
    #: The buttons, in order; the first of the pay ones is the main one.
    actions: list[Action]
    #: The words on the join button: Join, Apply or Rejoin.
    join_label: str | None
    #: Why somebody may not join now -- instead of the button.
    why_not: str | None


TeamPermissionName = Literal[
    "team.view_members", "team.review_applications", "team.remove_members", "team.write_notes", "team.export",
    "team.edit_settings", "team.send_access_list", "team.view_money", "team.edit_bank_details",
    "team.appoint_treasurer", "team.appoint_leads",
]

#: Applications still to be decided or completed: applied, invited, or approved and paying.
WAITING = {teams_service.APPLIED, teams_service.INVITED, teams_service.APPROVED}


class TeamCardOut(Model):
    slug: str
    name: str
    description: str | None
    logo_url: str | None
    #: The cover of its About page, for the tile.
    picture_url: str | None
    member_count: int
    #: The role this person holds in it: "Lead", "Treasurer"; None.
    role: str | None
    #: For whoever sees its people: applications waiting.
    applications_waiting: int | None
    #: How somebody gets in now; None while it takes nobody new.
    admission: Literal["open", "approval"] | None
    membership: TeamMembershipOut
    #: Its members see its own page; anybody else what it is about.
    opens: Literal["team", "about"]
    #: "About & join", "About & apply" or "About & rejoin".
    about_label: str


class TeamsHomeOut(Model):
    labels: LabelsOut
    #: Teams are for members of the association.
    is_member: bool
    #: In, applying, or helping to run.
    mine: list[TeamCardOut]
    others: list[TeamCardOut]


class RulesOut(Model):
    #: The day of the version in force.
    version: date
    #: The version this person accepted, if any.
    accepted: date | None
    #: They accepted an earlier version; this one applies now.
    changed_since: bool


class JoiningOut(Model):
    """The form for somebody not in the team, or why there is none."""

    mode: Literal["open", "approval"]
    #: Ended unpaid not long ago: back by paying again, without applying, until this day.
    rejoin_until: date | None
    #: The question the leads ask, when applying.
    prompt: str | None
    #: Instead of the form.
    why_not: str | None
    submit_label: str


class RosterPersonOut(Model):
    name: str
    picture_url: str | None
    is_lead: bool
    university_email: str | None


class PhotoOut(Model):
    id: int
    url: str
    caption: str | None
    width: int
    height: int


class TeamPageOut(Model):
    slug: str
    name: str
    description: str | None
    logo_url: str | None
    #: The longer text, for everybody.
    about: str | None
    #: The same, formatted (Markdown, without HTML): safe to show as it is.
    about_html: str | None
    #: The cover of the About page.
    picture_url: str | None
    #: The gallery, in its order.
    photos: list[PhotoOut]
    #: How many are in the team now.
    member_count: int
    #: What being in it costs, when it charges ("€10.00 every 6 months").
    fee: str | None
    labels: LabelsOut
    is_member: bool
    membership: TeamMembershipOut
    #: A member (or somebody who manages it) sees the team's own page and who is in it.
    sees_team_page: bool
    can_manage: bool
    #: May change this page: its text, cover and photos.
    can_edit_page: bool
    can_see_money: bool
    #: What this person may do in this team, for its side menu.
    permissions: list[TeamPermissionName]
    #: The role this person holds in it: "Lead", "Treasurer"; None.
    role: str | None
    #: For whoever sees its people: applications waiting.
    applications_waiting: int | None
    access_list_enabled: bool
    #: Said to whoever runs it: no lead in force.
    lead_missing: bool
    #: A site admin: the team's admin settings (details, fee, archiving) are
    #: in its side menu for them (api/admin_teams.py).
    administers: bool = False
    #: active or archived; an archived team is seen only by site admins.
    status: str = "active"
    #: None when the team has no rules.
    rules: RulesOut | None
    #: None when there is a membership under way.
    joining: JoiningOut | None
    #: Who is in the team, for those who see its page.
    members: list[RosterPersonOut] | None


class HomeQuery(Model):
    #: Back from paying: that team's payment is on its way, so nothing asks to pay again.
    paid: str | None = None


# --- Lookups ----------------------------------------------------------------------------


def _team(slug):
    """A team somebody may see: running, with teams switched on -- or, for whoever
    sets teams up, any team, before they are switched on for everybody."""
    may_set_up = current_user.can(Permission.TEAMS_MANAGE)
    if not may_set_up and not teams_service.teams_enabled():
        abort(404)
    try:
        team = teams_service.get_team(slug)
    except ServiceError:
        abort(404)
    if team.status != teams_service.STATUS_ACTIVE and not may_set_up:
        abort(404)
    return team


def _labels():
    singular, plural = teams_service.team_labels()
    return LabelsOut(singular=singular, plural=plural)


def _logo_url(team):
    return url_for("teams.team_logo", token=team.logo_token) if team.logo_token else None


def _latest():
    """This person's latest attempt per team: somebody who left and applied again is an applicant."""
    latest = {}
    for membership in teams_service.memberships_of(current_user):  # newest first
        latest.setdefault(membership.team_id, membership)
    return latest


def _manages_people(team):
    """Money alone -- a treasurer's -- is not a say over the team's people."""
    return teams_service.can_in_team(current_user, team, teams_service.TeamPermission.VIEW_MEMBERS)


def _sees_money(team):
    return teams_service.can_in_team(current_user, team, teams_service.TeamPermission.VIEW_MONEY)


def _join_label(team):
    if teams_service.rejoin_by_paying_until(current_user, team):
        return "Rejoin"
    return "Apply" if team.admission_mode == teams_service.ADMISSION_APPROVAL else "Join"


# --- What somebody sees of their membership ---------------------------------------------


def _notes(team, current, just_paid):
    notes = []

    def say(text, tone="plain"):
        notes.append(MembershipNoteOut(tone=tone, text=text))

    processing = current is not None and current.payment_state == "processing"
    if current is not None and current.status == teams_service.APPROVED and team_payments.charges(team):
        if processing or just_paid == team.slug:
            say(PAYMENT_ARRIVING, "info")
        else:
            if current.payment_state == "failed":
                say("The payment did not go through. Please try again.", "warning")
            until = day_text(team_payments.joining_period(team)["paid_until"])
            if team.payment_mode == team_payments.PAYMENT_ONE_TIME:
                say(f"One step left. Paying now covers until {until}. Nothing renews by itself: before it "
                    "ends, you are reminded to pay for the next period.")
            else:
                say(f"One step left. Paying now covers until {until}; after that the fee is charged at the "
                    "start of each period until you leave.")
    if current is None or current.status != teams_service.ACTIVE:
        return notes

    if team_payments.needs_to_pay(current):
        say(f"{team.name} charges a fee from {day_text(current.paid_until + timedelta(days=1))}: "
            f"{team.fee_display or ''}. Pay before then to stay in the team; nothing is charged earlier.", "warning")
    elif current.ends_with_association:
        say(f"Ends with your association membership on {day_text(current.ends_on)}.")
    elif current.payment_mode == team_payments.PAYMENT_ONE_TIME and current.paid_until:
        say(f"You leave on {day_text(current.ends_on)}." if current.ends_on
            else f"Paid until {day_text(current.paid_until)}.")
        if processing:
            say(PAYMENT_ARRIVING, "info")
        elif team_payments.renewal_open(current):
            text = (f"Pay for the next period, until {day_text(team_payments.next_period_until(current))}, "
                    "to stay without a gap.")
            if current.payment_state == "failed":
                text += " The last payment did not go through."
            say(text, "warning")
    elif current.stripe_subscription_id:
        if current.ends_on:
            say(f"You leave on {day_text(current.ends_on)}.")
        elif current.paid_until:
            say(f"Paid until {day_text(current.paid_until)}.")
        if current.payment_state == "failed":
            say("The last payment did not go through. Update your payment method under Account, "
                "Manage billing.", "warning")
    return notes


def _actions(team, current, just_paid, is_member):
    actions, why_not = [], None
    processing = current is not None and current.payment_state == "processing"
    if current is not None and current.status == teams_service.ACTIVE:
        actions.append("open")
        if team_payments.renewal_open(current) and not processing:
            actions.append("pay_next")
        if team_payments.needs_to_pay(current):
            actions.append("pay_stay")
    elif current is not None:
        if (current.status == teams_service.APPROVED and team_payments.charges(team) and not processing
                and just_paid != team.slug):
            actions.append("pay_join")
        if not processing:
            actions.append("withdraw")
    elif is_member:
        why_not = teams_service.why_not_joinable(current_user, team)
        if why_not is None:
            actions.append("join")
    if _manages_people(team):
        actions.append("manage")
    if _sees_money(team):
        actions.append("money")
    if current is not None and current.status == teams_service.ACTIVE and not current.ends_with_association:
        actions.append("stay" if current.ends_on else "leave")
    return actions, why_not


def _membership(team, latest, just_paid, is_member):
    last = latest.get(team.id)
    current = last if last is not None and last.status in teams_service.ONGOING else None
    shown = current or last
    actions, why_not = _actions(team, current, just_paid, is_member)
    active = current is not None and current.status == teams_service.ACTIVE
    return TeamMembershipOut(
        status=shown.status if shown else None,
        status_label=teams_service.STATUS_LABELS.get(shown.status, shown.status) if shown else None,
        ongoing=current is not None,
        fee=team.fee_display if team_payments.charges(team) and team.fee_display and not active else None,
        notes=_notes(team, current, just_paid),
        meeting=current.meeting_details if current is not None and current.status == teams_service.INVITED else None,
        actions=actions,
        join_label=_join_label(team) if "join" in actions else None,
        why_not=why_not,
    )


# --- The overview -----------------------------------------------------------------------


def _cover_url(team):
    return url_for("teams.team_picture", token=team.picture_token) if team.picture_token else None


def _waiting(team):
    if not _manages_people(team):
        return None
    return len(teams_service.team_memberships(team, WAITING))


def _admission(team):
    if not team.applications_open:
        return None
    return "approval" if team.admission_mode == teams_service.ADMISSION_APPROVAL else "open"


def _card(team, latest, just_paid, is_member):
    membership = _membership(team, latest, just_paid, is_member)
    rejoin = teams_service.rejoin_by_paying_until(current_user, team)
    member = membership.status == "active" and membership.ongoing
    return TeamCardOut(
        slug=team.slug, name=team.name, description=team.description, logo_url=_logo_url(team),
        picture_url=_cover_url(team), member_count=teams_service.member_count(team),
        role=teams_service.role_label(current_user, team), applications_waiting=_waiting(team),
        admission=_admission(team), membership=membership,
        opens="team" if member or _manages_people(team) else "about",
        about_label="About & rejoin" if rejoin else (
            "About & apply" if team.admission_mode == teams_service.ADMISSION_APPROVAL else "About & join"),
    )


@endpoint("GET", "/teams", response=TeamsHomeOut, query=HomeQuery, tag=TAG)
def teams_home(query):
    """Every running team: first those somebody is in, applying to or helps run -- with what to do
    next -- then the others."""
    if not teams_service.teams_enabled():
        abort(404)
    latest = _latest()
    is_member = teams_service.is_active_association_member(current_user)
    led = teams_service.teams_led_by(current_user)
    runs = {team.id for team in led if _manages_people(team) or _sees_money(team)}
    mine, others = [], []
    for team in teams_service.all_teams(include_archived=False):
        card = _card(team, latest, query.paid, is_member)
        (mine if card.membership.ongoing or team.id in runs else others).append(card)
    return TeamsHomeOut(labels=_labels(), is_member=is_member, mine=mine, others=others)


# --- A team -----------------------------------------------------------------------------


def _rules(team, current):
    rules = teams_service.team_rules(team)
    if rules is None:
        return None
    accepted = current.terms_version.date() if current is not None and current.terms_version else None
    return RulesOut(version=rules.day, accepted=accepted,
                    changed_since=current is not None and not teams_service.accepted_rules_in_force(current, rules))


def _joining(team):
    back_until = teams_service.rejoin_by_paying_until(current_user, team)
    approval = team.admission_mode == teams_service.ADMISSION_APPROVAL
    return JoiningOut(
        mode=team.admission_mode,
        rejoin_until=back_until,
        prompt=team.application_prompt if approval and team.application_prompt and not back_until else None,
        why_not=teams_service.why_not_joinable(current_user, team),
        submit_label="Rejoin and pay" if back_until else ("Apply" if approval else "Join"),
    )


def photos_out(team):
    """A team's gallery, for its About page and its settings."""
    return [PhotoOut(id=photo.id, url=url_for("teams.team_photo", token=photo.token), caption=photo.caption,
                     width=photo.width, height=photo.height) for photo in team.photos]


def _team_out(team, just_paid=None):
    latest = _latest()
    is_member = teams_service.is_active_association_member(current_user)
    membership = _membership(team, latest, just_paid, is_member)
    current = teams_service.ongoing_membership(current_user, team)
    active = teams_service.active_team_membership(current_user, team)
    sees = _manages_people(team) or (is_member and active is not None)
    held = teams_service.team_permissions(current_user, team)
    return TeamPageOut(
        slug=team.slug, name=team.name, description=team.description, logo_url=_logo_url(team),
        about=team.about, about_html=teams_service.render_about(team.about),
        picture_url=_cover_url(team), photos=photos_out(team), member_count=teams_service.member_count(team),
        fee=team.fee_display if team_payments.charges(team) and team.fee_display else None,
        labels=_labels(), is_member=is_member, membership=membership, sees_team_page=sees,
        can_manage=_manages_people(team), can_edit_page=P.EDIT_SETTINGS in held, can_see_money=_sees_money(team),
        permissions=sorted(held), role=teams_service.role_label(current_user, team),
        applications_waiting=_waiting(team), access_list_enabled=bool(team.access_list_enabled),
        lead_missing=_manages_people(team) and not teams_service.has_lead_in_force(team),
        administers=current_user.can(Permission.TEAMS_MANAGE), status=team.status,
        rules=_rules(team, current), joining=_joining(team) if current is None else None,
        members=[RosterPersonOut(name=row["name"], picture_url=row["picture_url"], is_lead=row["is_lead"],
                           university_email=row["university_email"])
                 for row in teams_service.roster(team)] if sees else None,
    )


@endpoint("GET", "/teams/<slug>", response=TeamPageOut, query=HomeQuery, tag=TAG)
def team(slug, query):
    """A team: what it is about, somebody's membership of it -- and, for its members, who is in it."""
    return _team_out(_team(slug), query.paid)


def _act(team, action):
    try:
        action()
    except ServiceError:
        db.session.rollback()
        raise
    db.session.commit()
    flush_marked_notification_channels()


class JoinIn(Model):
    #: The answer to the leads' question, when applying.
    application_text: str | None = Field(None, max_length=5000)
    #: The rules in force, accepted -- needed when the team has rules.
    accept_rules: bool = False


class DoneOut(Model):
    #: What happened, in a sentence.
    message: str
    team: TeamPageOut


@endpoint("POST", "/teams/<slug>/join", response=DoneOut, body=JoinIn, tag=TAG)
def team_join(slug, body):
    """Join, apply -- or come back by paying, after a membership that ended unpaid."""
    found = _team(slug)
    _act(found, lambda: teams_service.join_or_apply(current_user, found, body.application_text,
                                                    accepted_terms=body.accept_rules))
    current = teams_service.ongoing_membership(current_user, found)
    if current is not None and current.status == teams_service.APPROVED:
        message = "One step left: pay the team fee."
    elif found.admission_mode == teams_service.ADMISSION_OPEN:
        message = f"Welcome to {found.name}."
    else:
        message = "Application sent. The leads will be in touch."
    return DoneOut(message=message, team=_team_out(found))


class CheckoutOut(Model):
    #: Stripe's payment page.
    url: str


@endpoint("POST", "/teams/<slug>/pay", response=CheckoutOut, tag=TAG)
def team_pay(slug):
    """Where to pay the team fee: Stripe's payment page, which comes back to the overview."""
    found = _team(slug)
    try:
        url = team_payments.start_checkout(current_user, found)
    except ServiceError:
        db.session.rollback()
        raise
    except Exception:  # noqa: BLE001 -- Stripe unreachable or refusing; logged
        db.session.rollback()
        current_app.logger.exception("Could not open a team Checkout for %s.", slug)
        from ..services import ExternalServiceError

        raise ExternalServiceError("The payment page could not be opened. Please try again in a few minutes.",
                                   code="checkout_unavailable") from None
    db.session.commit()
    return CheckoutOut(url=url)


@endpoint("POST", "/teams/<slug>/stay", response=DoneOut, tag=TAG)
def team_stay(slug):
    """Take back leaving, before the day it takes effect."""
    found = _team(slug)
    _act(found, lambda: teams_service.stay(current_user, found))
    return DoneOut(message="You stay. Nothing changes.", team=_team_out(found))


@endpoint("POST", "/teams/<slug>/withdraw", response=DoneOut, tag=TAG)
def team_withdraw(slug):
    """Withdraw an application."""
    found = _team(slug)
    _act(found, lambda: teams_service.withdraw(current_user, found))
    return DoneOut(message="Application withdrawn.", team=_team_out(found))


# --- Leaving ----------------------------------------------------------------------------


class LeavingOut(Model):
    """What leaving means for this person, before they decide."""

    labels: LabelsOut
    team: TeamCardOut
    #: The subscription runs to the end of what is paid: they stay until then.
    stays_until: date | None
    #: Paid by subscription: it stops then and nothing more is charged.
    subscription: bool
    #: Taken off the list for access to the team's rooms too.
    access_list: bool
    is_lead: bool


def _ongoing_active(team):
    current = teams_service.ongoing_membership(current_user, team)
    if current is None or current.status != teams_service.ACTIVE:
        raise NotFoundError("You are not a member of this team.", code="not_a_member")
    return current


@endpoint("GET", "/teams/<slug>/leave", response=LeavingOut, tag=TAG)
def team_leaving(slug):
    """What leaving would mean."""
    found = _team(slug)
    current = _ongoing_active(found)
    runs_to_end = bool(current.stripe_subscription_id and current.payment_mode == "subscription")
    paid_once_until = (current.paid_until if current.payment_mode == "one_time" and current.paid_until
                       and current.paid_until >= get_membership_today() else None)
    return LeavingOut(
        labels=_labels(),
        team=_card(found, _latest(), None, teams_service.is_active_association_member(current_user)),
        stays_until=current.paid_until if runs_to_end else paid_once_until,
        subscription=runs_to_end,
        access_list=bool(found.access_list_enabled),
        is_lead=any(role.role == teams_service.ROLE_LEAD
                    for role in teams_service.roles_of(current_user) if role.team_id == found.id),
    )


class LeaveIn(Model):
    #: A message to the leads.
    message: str | None = Field(None, max_length=1000)
    #: That they mean it.
    confirm: Literal[True]


class LeftOut(Model):
    message: str


@endpoint("POST", "/teams/<slug>/leave", response=LeftOut, body=LeaveIn, tag=TAG)
def team_leave(slug, body):
    """Leave -- at once, or at the end of what is paid."""
    found = _team(slug)
    _ongoing_active(found)
    _act(found, lambda: teams_service.leave(current_user, found, body.message))
    current = teams_service.ongoing_membership(current_user, found)
    if current is not None and current.ends_on is not None:
        return LeftOut(message=f"You leave {found.name} on {day_text(current.ends_on)}. Until then nothing changes.")
    return LeftOut(message=f"You have left {found.name}.")


# --- The rules --------------------------------------------------------------------------


@endpoint("GET", "/teams/<slug>/rules", response=LegalTextOut, query=TextQuery, tag=TAG)
def team_rules(slug, query):
    """A team's rules: the version in force or an earlier one, in German or the English translation."""
    from ..services import legal_texts as legal

    found = _team(slug)
    return legal_text(legal.TEAM_RULES, query.language, query.version, team=found.slug,
                      page=lambda language, version: url_for("teams.team_rules_text", slug=found.slug,
                                                             language=language, version=version),
                      pdf=lambda version: url_for("teams.team_rules_pdf", slug=found.slug, version=version))
