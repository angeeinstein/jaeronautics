"""A team's management, for its leads (and the association's admins): who
applies, who is in it and who was, a person with the leads' notes, the
team's page and how to apply, the access list, and its roles.

Each team decides for itself who may do what (services/teams.py,
TeamPermission), so the checks are made here per team rather than by the
endpoint's site-wide permissions. Drawn by frontend/src/pages/teams/manage/.
The members' export stays a download from Flask (/teams/<slug>/manage/export.csv).
"""

from datetime import date, datetime
from typing import Literal

from flask import abort, url_for
from flask_login import current_user
from pydantic import Field

from ..db_models import TeamMembership, User, db
from ..permissions import Permission
from ..services import NotFoundError, PermissionError_, ServiceError, team_payments
from ..services import teams as teams_service
from ..services.pictures import picture_url
from ..services.notifications import flush_marked_notification_channels
from ._core import Model, UtcDateTime, endpoint
from .teams import (
    WAITING, LabelsOut, PhotoOut, TeamPermissionName, _labels, _logo_url, _team, photos_out,
)

TAG = "Teams"
P = teams_service.TeamPermission



def _managed(slug, permission=P.VIEW_MEMBERS):
    team = _team(slug)
    _may(team, permission)
    return team


def _may(team, permission):
    if not teams_service.can_in_team(current_user, team, permission):
        raise PermissionError_("You may not do that in this team.", code="team_permission")


def _commit():
    db.session.commit()
    flush_marked_notification_channels()


def _name(user):
    return teams_service.person_details(user)["name"]


# --- The frame ----------------------------------------------------------------------------


class ManageOut(Model):
    """What the management's sidebar and heading need."""

    slug: str
    name: str
    logo_url: str | None
    labels: LabelsOut
    #: What this person may do in this team.
    permissions: list[TeamPermissionName]
    #: Applications waiting: applied, invited, or approved and paying.
    applications: int
    has_lead_in_force: bool
    access_list_enabled: bool


def _frame_team(slug):
    """The team for its frame: whoever manages its people, or sees its money -- the
    association's treasurer for every team, also archived or with teams off."""
    if current_user.can(Permission.TEAMS_MONEY):
        try:
            team = teams_service.get_team(slug)
        except ServiceError:
            abort(404)
    else:
        team = _team(slug)
    held = teams_service.team_permissions(current_user, team)
    if P.VIEW_MEMBERS not in held and P.VIEW_MONEY not in held:
        raise PermissionError_("You may not do that in this team.", code="team_permission")
    return team


@endpoint("GET", "/teams/<slug>/manage", response=ManageOut, tag=TAG)
def team_manage(slug):
    """The frame of the team's management and money: what this person may do in it,
    and how many applications wait."""
    team = _frame_team(slug)
    return ManageOut(
        slug=team.slug, name=team.name, logo_url=_logo_url(team), labels=_labels(),
        permissions=sorted(teams_service.team_permissions(current_user, team)),
        applications=len(teams_service.team_memberships(team, WAITING))
        if teams_service.can_in_team(current_user, team, P.VIEW_MEMBERS) else 0,
        has_lead_in_force=teams_service.has_lead_in_force(team),
        access_list_enabled=bool(team.access_list_enabled),
    )


# --- People -------------------------------------------------------------------------------

PAYMENT_NOTES = {"processing": "Payment on its way", "failed": "Payment failed", "paid": "Paid"}


class ApplicationOut(Model):
    user_id: int
    name: str
    status: Literal["applied", "invited", "approved"]
    status_label: str
    #: Approved: where the payment is.
    payment: str | None
    applied_on: date | None


class ApplicationsOut(Model):
    applications: list[ApplicationOut]


@endpoint("GET", "/teams/<slug>/manage/applications", response=ApplicationsOut, tag=TAG)
def team_applications(slug):
    """Who has applied, been invited, or been approved and is paying -- newest first."""
    team = _managed(slug)
    return ApplicationsOut(applications=[
        ApplicationOut(
            user_id=membership.user_id, name=_name(membership.user), status=membership.status,
            status_label=teams_service.STATUS_LABELS[membership.status],
            payment=PAYMENT_NOTES.get(membership.payment_state)
            if membership.status == teams_service.APPROVED else None,
            applied_on=membership.applied_at.date() if membership.applied_at else None,
        )
        for membership in teams_service.team_memberships(team, WAITING)
    ])


class MemberOut(Model):
    user_id: int
    name: str
    picture_url: str | None
    is_lead: bool
    university_email: str | None
    cohort: str | None
    since: date | None
    paid_until: date | None
    #: What a lead should know about the payment or an ending, in a few words.
    notes: list[str]


class MembersOut(Model):
    max_members: int | None
    #: The team has a fee: show what is paid.
    charges: bool
    #: May download the list (/teams/<slug>/manage/export.csv).
    may_export: bool
    members: list[MemberOut]


def _member_notes(membership):
    notes = []
    if team_payments.needs_to_pay(membership):
        notes.append("Free until then, not paid yet")
    elif team_payments.renewal_open(membership):
        notes.append("Next period not paid yet")
    if membership.ends_on:
        notes.append(f"Leaves {membership.ends_on.strftime('%d.%m.%Y')}")
    if membership.payment_state == "failed":
        notes.append("Payment failed")
    return notes


@endpoint("GET", "/teams/<slug>/manage/members", response=MembersOut, tag=TAG)
def team_members(slug):
    """Who is in the team, by surname."""
    team = _managed(slug)
    return MembersOut(
        max_members=team.max_members, charges=team_payments.charges(team),
        may_export=teams_service.can_in_team(current_user, team, P.EXPORT),
        members=[
            MemberOut(
                user_id=row["user"].id, name=row["name"], picture_url=row["picture_url"],
                is_lead=row["is_lead"], university_email=row["university_email"], cohort=row["cohort"],
                since=row["membership"].started_at.date() if row["membership"].started_at else None,
                paid_until=row["membership"].paid_until, notes=_member_notes(row["membership"]),
            )
            for row in teams_service.roster(team)
        ],
    )


class FormerOut(Model):
    user_id: int
    name: str
    #: The years they were in it: "2023–2024, 2026".
    periods: str
    last_active: UtcDateTime | None
    why: str | None


class FormerMembersOut(Model):
    former: list[FormerOut]


@endpoint("GET", "/teams/<slug>/manage/former", response=FormerMembersOut, tag=TAG)
def team_former_members(slug):
    """Everybody who was in the team and is not now, most recently active first. Kept for good."""
    team = _managed(slug)
    return FormerMembersOut(former=[
        FormerOut(user_id=row["user_id"], name=row["name"], periods=row["periods"], last_active=row["last_active"],
                  why=teams_service.END_REASON_LABELS.get(row["end_reason"]))
        for row in teams_service.former_members(team)
    ])


# --- A person -----------------------------------------------------------------------------


class DetailsOut(Model):
    university_email: str | None
    private_email: str | None
    phone: str | None
    cohort: str | None


class NowOut(Model):
    """Their attempt under way, and what may be decided about it."""

    membership_id: int
    status: Literal["applied", "invited", "approved", "active"]
    status_label: str
    #: The leads' question, and the answer.
    question: str | None
    answer: str | None
    meeting: str | None


class TeamHistoryOut(Model):
    status: str
    status_label: str
    applied_on: date | None
    ended_on: date | None
    why: str | None
    #: Their message when leaving, or the reason they were removed.
    note: str | None


class TeamNoteOut(Model):
    at: UtcDateTime
    author: str | None
    body: str


class TeamPersonOut(Model):
    user_id: int
    name: str
    #: Their picture (services/pictures.py), or None.
    picture_url: str | None = None
    details: DetailsOut
    now: NowOut | None
    history: list[TeamHistoryOut]
    notes: list[TeamNoteOut]
    may_review: bool
    may_remove: bool
    may_write_notes: bool


def _day(moment):
    return moment.date() if isinstance(moment, datetime) else moment


def _person(team, user_id):
    person = db.session.get(User, user_id)
    history = teams_service.history_of(team, person) if person is not None else []
    if not history:
        raise NotFoundError("That person has never been in this team.", code="not_in_team")
    return person, history


def _person_out(team, user_id):
    person, history = _person(team, user_id)
    current = next((membership for membership in history if membership.status in teams_service.ONGOING), None)
    details = teams_service.person_details(person)
    return TeamPersonOut(
        user_id=person.id, name=details["name"], picture_url=picture_url(person),
        details=DetailsOut(university_email=details["university_email"], private_email=details["private_email"],
                           phone=details["phone"], cohort=details["cohort"]),
        now=NowOut(membership_id=current.id, status=current.status,
                   status_label=teams_service.STATUS_LABELS[current.status],
                   question=team.application_prompt if current.application_text else None,
                   answer=current.application_text, meeting=current.meeting_details) if current else None,
        history=[TeamHistoryOut(status=membership.status,
                            status_label=teams_service.STATUS_LABELS.get(membership.status, membership.status),
                            applied_on=_day(membership.applied_at), ended_on=_day(membership.ended_at),
                            why=teams_service.END_REASON_LABELS.get(membership.end_reason),
                            note=membership.end_note) for membership in history],
        notes=[TeamNoteOut(at=note.created_at, author=note.author.email if note.author else None, body=note.body)
               for note in teams_service.notes_about(team, person)],
        may_review=teams_service.can_in_team(current_user, team, P.REVIEW_APPLICATIONS),
        may_remove=teams_service.can_in_team(current_user, team, P.REMOVE_MEMBERS),
        may_write_notes=teams_service.can_in_team(current_user, team, P.WRITE_NOTES),
    )


@endpoint("GET", "/teams/<slug>/manage/people/<int:user_id>", response=TeamPersonOut, tag=TAG)
def team_person(slug, user_id):
    """A person as the leads see them: how to reach them, their application, their history, the notes."""
    return _person_out(_managed(slug), user_id)


def _decide(slug, membership_id, permission, action):
    team = _managed(slug, permission)
    membership = db.session.get(TeamMembership, membership_id)
    if membership is None or membership.team_id != team.id:
        raise NotFoundError("That application or membership is not in this team.", code="not_in_team")
    try:
        action(team)
    except ServiceError:
        db.session.rollback()
        raise
    _commit()
    return _person_out(team, membership.user_id)


class InviteIn(Model):
    #: When and where, or a link.
    meeting_details: str = Field(max_length=2000)


@endpoint("POST", "/teams/<slug>/manage/memberships/<int:membership_id>/invite", response=TeamPersonOut, body=InviteIn,
          tag=TAG)
def team_invite(slug, membership_id, body):
    """Invite an applicant to meet the leads. They are emailed."""
    return _decide(slug, membership_id, P.REVIEW_APPLICATIONS,
                   lambda team: teams_service.invite(current_user, team, membership_id, body.meeting_details))


@endpoint("POST", "/teams/<slug>/manage/memberships/<int:membership_id>/approve", response=TeamPersonOut, tag=TAG)
def team_approve(slug, membership_id):
    """Approve: in the team, or -- where there is a fee -- paying is all that is left. They are emailed."""
    return _decide(slug, membership_id, P.REVIEW_APPLICATIONS,
                   lambda team: teams_service.approve(current_user, team, membership_id))


@endpoint("POST", "/teams/<slug>/manage/memberships/<int:membership_id>/reject", response=TeamPersonOut, tag=TAG)
def team_reject(slug, membership_id):
    """Not accept an application. They are emailed."""
    return _decide(slug, membership_id, P.REVIEW_APPLICATIONS,
                   lambda team: teams_service.reject(current_user, team, membership_id))


class RemoveIn(Model):
    #: For the record; the person is only told that their membership ended.
    reason: str = Field(max_length=2000)


@endpoint("POST", "/teams/<slug>/manage/memberships/<int:membership_id>/remove", response=TeamPersonOut, body=RemoveIn,
          tag=TAG)
def team_remove(slug, membership_id, body):
    """Remove somebody from the team now."""
    return _decide(slug, membership_id, P.REMOVE_MEMBERS,
                   lambda team: teams_service.remove(current_user, team, membership_id, body.reason))


class TeamNoteIn(Model):
    body: str = Field(max_length=5000)


@endpoint("POST", "/teams/<slug>/manage/people/<int:user_id>/notes", response=TeamPersonOut, body=TeamNoteIn, status=201,
          tag=TAG)
def team_add_note(slug, user_id, body):
    """A note about somebody, for the leads and the admins. Not shown to them, but part of their data."""
    team = _managed(slug, P.WRITE_NOTES)
    person, _history = _person(team, user_id)
    teams_service.add_note(current_user, team, person, body.body)
    _commit()
    return _person_out(team, user_id)


# --- The team's page and applying --------------------------------------------------------


class PageOut(Model):
    #: One or two sentences, on the overview.
    description: str | None
    #: The longer text on the About page.
    about: str | None
    #: The cover of the About page.
    picture_url: str | None
    logo_url: str | None
    #: The gallery on the About page, in its order.
    photos: list[PhotoOut]
    #: How many photos the gallery holds.
    photos_max: int


class PageIn(Model):
    description: str | None = Field(None, max_length=500)
    about: str | None = Field(None, max_length=teams_service.ABOUT_MAX_LENGTH)


def _page_out(team):
    return PageOut(description=team.description, about=team.about, logo_url=_logo_url(team),
                   picture_url=url_for("teams.team_picture", token=team.picture_token) if team.picture_token else None,
                   photos=photos_out(team), photos_max=teams_service.PHOTOS_MAX)


@endpoint("GET", "/teams/<slug>/manage/page", response=PageOut, tag=TAG)
def team_page_settings(slug):
    """What everybody sees about the team: its description, the longer text, the picture and the logo."""
    return _page_out(_managed(slug, P.EDIT_SETTINGS))


@endpoint("PUT", "/teams/<slug>/manage/page", response=PageOut, body=PageIn, tag=TAG)
def team_page_save(slug, body):
    """Save the description and the longer text."""
    team = _managed(slug, P.EDIT_SETTINGS)
    teams_service.update_team_by_lead(current_user, team, description=body.description)
    teams_service.update_team_page(current_user, team, about=body.about)
    _commit()
    return _page_out(team)


def _image(slug, kind, files=None):
    team = _managed(slug, P.EDIT_SETTINGS)
    if files is None:
        (teams_service.remove_team_logo if kind == "logo" else teams_service.remove_team_picture)(current_user, team)
    else:
        (teams_service.set_team_logo if kind == "logo" else teams_service.set_team_picture)(
            current_user, team, files["image"].read())
    _commit()
    return _page_out(team)


@endpoint("POST", "/teams/<slug>/manage/page/logo", response=PageOut, uploads={"image": True}, tag=TAG)
def team_logo_upload(slug, files):
    """A new logo: PNG, JPG or WebP. Seen on dark pages and on the white pages of the rules' PDF."""
    return _image(slug, "logo", files)


@endpoint("DELETE", "/teams/<slug>/manage/page/logo", response=PageOut, tag=TAG)
def team_logo_remove(slug):
    """No logo."""
    return _image(slug, "logo")


@endpoint("POST", "/teams/<slug>/manage/page/picture", response=PageOut, uploads={"image": True}, tag=TAG)
def team_picture_upload(slug, files):
    """A new picture for the About page: PNG, JPG or WebP."""
    return _image(slug, "picture", files)


class PreviewIn(Model):
    about: str | None = Field(None, max_length=teams_service.ABOUT_MAX_LENGTH)


class PreviewOut(Model):
    html: str | None


@endpoint("POST", "/teams/<slug>/manage/page/preview", response=PreviewOut, body=PreviewIn, tag=TAG)
def team_page_preview(slug, body):
    """The longer text as the About page will show it, before it is saved."""
    _managed(slug, P.EDIT_SETTINGS)
    return PreviewOut(html=teams_service.render_about(body.about))


class PhotoQuery(Model):
    caption: str | None = Field(None, max_length=teams_service.CAPTION_MAX_LENGTH)


@endpoint("POST", "/teams/<slug>/manage/page/photos", response=PageOut, query=PhotoQuery, uploads={"image": True},
          status=201, tag=TAG)
def team_photo_add(slug, query, files):
    """A photo for the gallery: PNG, JPG or WebP, after the ones there."""
    team = _managed(slug, P.EDIT_SETTINGS)
    teams_service.add_team_photo(current_user, team, files["image"].read(), query.caption)
    _commit()
    return _page_out(team)


class PhotoPlaceIn(Model):
    id: int
    caption: str | None = Field(None, max_length=teams_service.CAPTION_MAX_LENGTH)


class PhotosIn(Model):
    #: Every photo of the gallery once, in the order to show them.
    photos: list[PhotoPlaceIn] = Field(max_length=teams_service.PHOTOS_MAX)


@endpoint("PUT", "/teams/<slug>/manage/page/photos", response=PageOut, body=PhotosIn, tag=TAG)
def team_photos_arrange(slug, body):
    """The gallery's order and captions."""
    team = _managed(slug, P.EDIT_SETTINGS)
    teams_service.arrange_team_photos(current_user, team, [(photo.id, photo.caption) for photo in body.photos])
    _commit()
    return _page_out(team)


@endpoint("DELETE", "/teams/<slug>/manage/page/photos/<int:photo_id>", response=PageOut, tag=TAG)
def team_photo_remove(slug, photo_id):
    """Take a photo out of the gallery."""
    team = _managed(slug, P.EDIT_SETTINGS)
    teams_service.remove_team_photo(current_user, team, photo_id)
    _commit()
    return _page_out(team)


@endpoint("DELETE", "/teams/<slug>/manage/page/picture", response=PageOut, tag=TAG)
def team_picture_remove(slug):
    """No picture."""
    return _image(slug, "picture")


class RulesInfoOut(Model):
    version: date
    page_url: str
    pdf_url: str


class ApplyingOut(Model):
    applications_open: bool
    #: Asked of applicants; none: they are not asked to write anything.
    application_prompt: str | None
    #: Kept by the association with its legal texts; read-only here.
    rules: RulesInfoOut | None


class ApplyingIn(Model):
    applications_open: bool
    application_prompt: str | None = Field(None, max_length=255)


def _applying_out(team):
    rules = teams_service.team_rules(team)
    return ApplyingOut(
        applications_open=bool(team.applications_open), application_prompt=team.application_prompt,
        rules=RulesInfoOut(version=rules.day, page_url=url_for("teams.team_rules_text", slug=team.slug),
                           pdf_url=url_for("teams.team_rules_pdf", slug=team.slug)) if rules else None,
    )


@endpoint("GET", "/teams/<slug>/manage/applying", response=ApplyingOut, tag=TAG)
def team_applying(slug):
    """Whether the team takes new members, the question for applicants, and the rules they accept."""
    return _applying_out(_managed(slug, P.EDIT_SETTINGS))


@endpoint("PUT", "/teams/<slug>/manage/applying", response=ApplyingOut, body=ApplyingIn, tag=TAG)
def team_applying_save(slug, body):
    """Save whether the team takes new members and the question for applicants."""
    team = _managed(slug, P.EDIT_SETTINGS)
    teams_service.update_team_by_lead(current_user, team, applications_open=body.applications_open,
                                      application_prompt=body.application_prompt)
    _commit()
    return _applying_out(team)


# --- The access list ----------------------------------------------------------------------


class AccessRowOut(Model):
    name: str
    email: str | None
    #: Not on the list sent last time.
    new: bool


class GoneOut(Model):
    name: str | None
    email: str | None


class AccessListOut(Model):
    #: As typed: one address per line.
    recipients: str
    #: "15.10, 15.03".
    dates: str
    auto_send: bool
    next_on: date | None
    last_sent_on: date | None
    #: May change who receives it and when (else only send it).
    may_edit: bool
    # The email exactly as it would go out now.
    subject: str
    to: list[str]
    cc: list[str]
    intro: str
    rows: list[AccessRowOut]
    compared_note: str | None
    #: On the list sent last time, no longer in the team.
    gone: list[GoneOut]


class AccessListIn(Model):
    recipients: str = Field("", max_length=2000)
    dates: str = Field("", max_length=255)
    auto_send: bool = False


def _access_team(slug, permission=P.SEND_ACCESS_LIST):
    team = _managed(slug, permission)
    if not team.access_list_enabled:
        raise NotFoundError("This team has no access list.", code="team_access_list_off")
    return team


def _access_out(team):
    subject, message = teams_service.access_list_message(team)
    try:
        to = teams_service.parse_recipients(team.access_list_recipients)
    except ServiceError:
        to = []
    return AccessListOut(
        recipients=team.access_list_recipients or "", dates=team.access_list_dates or "",
        auto_send=bool(team.access_list_auto_send), next_on=teams_service.next_access_list_date(team),
        last_sent_on=team.access_list_last_sent_on,
        may_edit=teams_service.can_in_team(current_user, team, P.EDIT_SETTINGS),
        subject=subject, to=to, cc=teams_service.access_list_cc(team), intro=message["intro"],
        rows=[AccessRowOut(name=row["name"], email=row["email"] or None, new=row["new"]) for row in message["rows"]],
        compared_note=message["compared_note"],
        gone=[GoneOut(name=entry.get("name"), email=entry.get("email") or None) for entry in message["gone"]],
    )


@endpoint("GET", "/teams/<slug>/manage/access-list", response=AccessListOut, tag=TAG)
def team_access_list(slug):
    """Who receives the list of current members and when -- and the email exactly as it would go out."""
    return _access_out(_access_team(slug))


@endpoint("PUT", "/teams/<slug>/manage/access-list", response=AccessListOut, body=AccessListIn, tag=TAG)
def team_access_list_save(slug, body):
    """Save who receives the list, on which days, and whether it goes out by itself."""
    team = _access_team(slug, P.EDIT_SETTINGS)
    teams_service.update_access_list(current_user, team, recipients=body.recipients, dates=body.dates,
                                     auto_send=body.auto_send)
    _commit()
    return _access_out(team)


@endpoint("POST", "/teams/<slug>/manage/access-list/send", response=AccessListOut, tag=TAG)
def team_access_list_send(slug):
    """Send the list now, to whoever receives it, with the leads in copy."""
    team = _access_team(slug)
    try:
        teams_service.send_access_list(current_user, team)
    except ServiceError:
        db.session.rollback()
        raise
    _commit()
    return _access_out(team)


# --- Roles --------------------------------------------------------------------------------


class HolderOut(Model):
    user_id: int
    name: str
    #: A role counts only while its holder is a member of the team and of the association.
    in_force: bool


class CandidateOut(Model):
    user_id: int
    name: str
    #: For somebody not in the team yet (a site admin's search): their year
    #: group or address, to tell two of the same name apart.
    detail: str | None = None
    in_team: bool = True


class TeamRolesOut(Model):
    leads: list[HolderOut]
    treasurers: list[HolderOut]
    #: May give and take the treasurer role.
    may_appoint: bool
    #: Members who could be appointed treasurer.
    candidates: list[CandidateOut]
    #: May give and take the lead role: the team's leads and site admins.
    may_appoint_leads: bool
    #: Members who could be made a lead.
    lead_candidates: list[CandidateOut]
    #: A site admin: may also search all of the association's members for a lead
    #: (GET .../manage/lead-candidates?q=).
    searches_everyone: bool


def _candidate(team, user, name):
    in_team = teams_service.active_team_membership(user, team) is not None
    member = user.member
    detail = None if in_team else ((member.year_group if member else None) or user.email)
    return CandidateOut(user_id=user.id, name=name, detail=detail, in_team=in_team)


def _roles_out(team):
    def holders(role):
        return [HolderOut(user_id=held.user_id, name=_name(held.user), in_force=teams_service.role_counts(held))
                for held in teams_service.role_holders(team, role)]

    treasurers = holders(teams_service.ROLE_TREASURER)
    taken = {holder.user_id for holder in treasurers}
    may_appoint_leads = teams_service.can_in_team(current_user, team, P.APPOINT_LEADS)
    return TeamRolesOut(
        leads=holders(teams_service.ROLE_LEAD), treasurers=treasurers,
        may_appoint=teams_service.can_in_team(current_user, team, P.APPOINT_TREASURER),
        candidates=[CandidateOut(user_id=row["user"].id, name=row["name"])
                    for row in teams_service.roster(team) if row["user"].id not in taken],
        may_appoint_leads=may_appoint_leads,
        lead_candidates=[_candidate(team, user, name)
                         for user, name in teams_service.lead_candidates(current_user, team)]
        if may_appoint_leads else [],
        searches_everyone=may_appoint_leads and teams_service.may_appoint_from_everyone(current_user),
    )


@endpoint("GET", "/teams/<slug>/manage/roles", response=TeamRolesOut, tag=TAG)
def team_roles(slug):
    """The team's leads and its treasurer, and whom this person may appoint."""
    return _roles_out(_managed(slug))


class LeadCandidateQuery(Model):
    q: str = Field("", max_length=200, description="Part of a name or address.")


class LeadCandidatesOut(Model):
    items: list[CandidateOut]


@endpoint("GET", "/teams/<slug>/manage/lead-candidates", response=LeadCandidatesOut, query=LeadCandidateQuery,
          tag=TAG)
def team_lead_candidates(slug, query):
    """Whom this person may make a lead: the team's members -- and, for a site
    admin who searches, the association's members whose name or address matches."""
    team = _managed(slug, P.APPOINT_LEADS)
    return LeadCandidatesOut(items=[_candidate(team, user, name)
                                for user, name in teams_service.lead_candidates(current_user, team, query.q)])


class LeadIn(Model):
    user_id: int


@endpoint("POST", "/teams/<slug>/manage/leads", response=TeamRolesOut, body=LeadIn, tag=TAG)
def team_lead_appoint(slug, body):
    """Make somebody a lead of the team. It counts once they are a member of it."""
    team = _managed(slug, P.APPOINT_LEADS)
    teams_service.appoint_lead(current_user, team, body.user_id)
    _commit()
    return _roles_out(team)


class DismissLeadQuery(Model):
    #: Needed to take away the team's last lead (409 ``team_last_lead`` otherwise).
    confirmed: bool = False


@endpoint("DELETE", "/teams/<slug>/manage/leads/<int:user_id>", response=TeamRolesOut, query=DismissLeadQuery,
          tag=TAG)
def team_lead_dismiss(slug, user_id, query):
    """No longer a lead of the team -- the last one only when confirmed."""
    team = _managed(slug, P.APPOINT_LEADS)
    teams_service.dismiss_lead(current_user, team, user_id, confirmed=query.confirmed)
    _commit()
    return _roles_out(team)


class TreasurerIn(Model):
    user_id: int


@endpoint("POST", "/teams/<slug>/manage/treasurer", response=TeamRolesOut, body=TreasurerIn, tag=TAG)
def team_treasurer_appoint(slug, body):
    """Make a member the team's treasurer: they see its money and keep its bank details, nothing about people."""
    team = _managed(slug, P.APPOINT_TREASURER)
    teams_service.appoint_treasurer(current_user, team, body.user_id)
    _commit()
    return _roles_out(team)


@endpoint("DELETE", "/teams/<slug>/manage/treasurer/<int:user_id>", response=TeamRolesOut, tag=TAG)
def team_treasurer_dismiss(slug, user_id):
    """No longer the team's treasurer."""
    team = _managed(slug, P.APPOINT_TREASURER)
    teams_service.dismiss_treasurer(current_user, team, user_id)
    _commit()
    return _roles_out(team)
