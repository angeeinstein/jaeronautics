"""Teams, step two: joining, applying, the leads' decisions, and the pages.

Members join an open team or apply to one that approves its members; the leads
invite, approve, decide against, remove and keep notes; everybody in a team sees
who else is in it. Nothing of it exists while teams are switched off. And teams
follow the association: whoever leaves it leaves their teams, and an erased
account takes its teams with it. See docs/teams-plan.md.
"""
from datetime import timedelta

import pytest

from conftest import db
from aeronautics_members.db_models import AuditLog, NotificationEvent, TeamMembership, TeamNote, TeamRole
from aeronautics_members.services import ConflictError, ValidationError, privacy, teams
from aeronautics_members.services.clock import get_membership_today
from test_admin_reviews import _login as _set_session_user, _staff
from test_emails import FakeSMTP, _parts, _user_status_event, outbox  # noqa: F401
from test_teams_foundation import _association_member, _in_team, _team


def _login(client, user_id):
    """Sign in as somebody, also when somebody else was signed in before.

    The tests run inside one application context, which the requests share, and
    Flask-Login keeps the signed-in user on it: without forgetting that, the
    second person would be served as the first.
    """
    from flask import g

    g.pop("_login_user", None)
    _set_session_user(client, user_id)


@pytest.fixture
def switched_on(app):
    teams.save_team_settings(None, enabled=True, label_singular="", label_plural="")
    db.session.commit()


def _led(name="Rocket", **overrides):
    """A team with a lead who is in it."""
    team = _team(name, **overrides)
    lead = _association_member(f"lead-{team.slug}@example.com")
    lead.member.first_name, lead.member.last_name = "Lena", "Lead"
    _in_team(lead, team)
    teams.grant_team_role(None, team, lead, teams.ROLE_LEAD)
    db.session.commit()
    return team, lead


def _person(email="anna@example.com", first="Anna", last="Berger", **member_fields):
    user = _association_member(email)
    user.member.first_name, user.member.last_name = first, last
    for key, value in member_fields.items():
        setattr(user.member, key, value)
    db.session.commit()
    return user


def _events(event_type, recipient=None):
    query = db.session.query(NotificationEvent).filter_by(event_type=event_type)
    if recipient:
        query = query.filter_by(recipient_email=recipient)
    return query.all()


class TestSwitchedOff:
    @pytest.mark.parametrize("path", ["/teams", "/teams/rocket", "/teams/rocket/manage"])
    def test_nothing_is_there(self, app, client, path):
        _team("Rocket")
        _login(client, _staff("admin@example.com", "admin").id)

        assert client.get(path).status_code == 404

    def test_and_there_is_no_link(self, app, client):
        _login(client, _person().id)

        assert 'href="/teams"' not in client.get("/account", follow_redirects=True).get_data(as_text=True)


@pytest.mark.usefixtures("switched_on")
class TestJoining:
    def test_the_link_is_there_once_switched_on(self, app, client):
        _login(client, _person().id)

        assert 'href="/teams"' in client.get("/account", follow_redirects=True).get_data(as_text=True)

    def test_an_open_team_is_joined_at_once_and_the_leads_are_told(self, app, client):
        team, lead = _led("Glider", admission_mode="open")
        anna = _person()
        _login(client, anna.id)

        client.post("/teams/glider/join")

        assert teams.active_team_membership(anna, team) is not None
        [event] = _events("team_member_joined", lead.email)
        assert "Anna Berger joined Glider." in event.summary

    def test_applying_keeps_the_answer_and_tells_the_leads(self, app, client):
        team, lead = _led(application_prompt="Why do you want to join?")
        anna = _person()
        _login(client, anna.id)

        assert "Why do you want to join?" in client.get("/teams").get_data(as_text=True)
        client.post("/teams/rocket/join", data={"application_text": "I like rockets."})

        membership = teams.ongoing_membership(anna, team)
        assert (membership.status, membership.application_text) == (teams.APPLIED, "I like rockets.")
        assert len(_events("team_application_received", lead.email)) == 1

    def test_without_a_question_no_text_is_kept(self, app):
        team, _lead = _led()
        membership = teams.join_or_apply(_person(), team, "unasked")

        assert membership.application_text is None

    def test_one_attempt_at_a_time(self, app):
        team, _lead = _led()
        anna = _person()
        teams.join_or_apply(anna, team)

        with pytest.raises(ConflictError):
            teams.join_or_apply(anna, team)

    def test_only_members_of_the_association(self, app, client):
        _led()
        lapsed = _association_member("old@example.com", active=False)
        _login(client, lapsed.id)

        body = client.get("/teams").get_data(as_text=True)
        assert "Teams are for members of the association." in body

        client.post("/teams/rocket/join")
        assert db.session.query(TeamMembership).filter_by(user_id=lapsed.id).count() == 0

    def test_not_while_closed_or_full(self, app):
        team, _lead = _led(max_members="1")
        with pytest.raises(ConflictError, match="full"):
            teams.join_or_apply(_person(), team)

        team.max_members, team.applications_open = None, False
        with pytest.raises(ConflictError, match="not taking new members"):
            teams.join_or_apply(_person("b@example.com"), team)

    def test_an_application_can_be_withdrawn(self, app, client):
        team, _lead = _led()
        anna = _person()
        teams.join_or_apply(anna, team)
        db.session.commit()
        _login(client, anna.id)

        client.post("/teams/rocket/withdraw")

        assert teams.ongoing_membership(anna, team) is None
        assert db.session.query(TeamMembership).filter_by(user_id=anna.id).one().status == teams.WITHDRAWN

    def test_leaving_tells_the_leads(self, app, client):
        team, lead = _led()
        anna = _person()
        _in_team(anna, team)
        _login(client, anna.id)

        client.post("/teams/rocket/leave")

        membership = db.session.query(TeamMembership).filter_by(user_id=anna.id).one()
        assert (membership.status, membership.end_reason) == (teams.ENDED, teams.END_LEFT)
        assert len(_events("team_member_left", lead.email)) == 1

    def test_applying_again_after_a_rejection_is_allowed(self, app):
        team, lead = _led()
        anna = _person()
        first = teams.join_or_apply(anna, team)
        teams.reject(lead, team, first.id)

        assert teams.join_or_apply(anna, team).status == teams.APPLIED


@pytest.mark.usefixtures("switched_on")
class TestTheTeamPage:
    def test_members_see_each_other(self, app, client):
        team, _lead = _led()
        anna = _person(email_work="anna.berger@edu.example")
        _in_team(anna, team)
        _login(client, anna.id)

        body = client.get("/teams/rocket").get_data(as_text=True)

        assert "Anna Berger" in body and "anna.berger@edu.example" in body
        assert "Lena Lead" in body
        assert "anna@example.com" not in body, "the private address is for the leads only"

    def test_others_do_not(self, app, client):
        _led()
        _login(client, _person().id)

        assert client.get("/teams/rocket").status_code == 404


@pytest.mark.usefixtures("switched_on")
class TestTheLeadsPage:
    def test_a_lead_reaches_it_and_an_ordinary_member_does_not(self, app, client):
        team, lead = _led()
        anna = _person()
        _in_team(anna, team)

        _login(client, anna.id)
        assert client.get("/teams/rocket/manage").status_code == 403
        _login(client, lead.id)
        assert client.get("/teams/rocket/manage").status_code == 200

    def test_leading_one_team_does_not_open_another(self, app, client):
        _team_a, lead = _led("Rocket")
        _led("Glider")
        _login(client, lead.id)

        assert client.get("/teams/glider/manage").status_code == 403

    def test_a_site_admin_reaches_every_team(self, app, client):
        _led()
        _login(client, _staff("admin@example.com", "admin").id)

        assert client.get("/teams/rocket/manage").status_code == 200

    def test_invite_then_approve(self, app, client):
        team, lead = _led()
        anna = _person()
        membership = teams.join_or_apply(anna, team)
        db.session.commit()
        _login(client, lead.id)

        assert "Anna Berger" in client.get("/teams/rocket/manage").get_data(as_text=True)
        client.post(f"/teams/rocket/manage/memberships/{membership.id}/invite",
                    data={"meeting_details": "Tuesday 18:00, room 2.04"})
        [invited] = _events("team_invited", anna.email)
        assert invited.payload["meeting_details"] == "Tuesday 18:00, room 2.04"

        client.post(f"/teams/rocket/manage/memberships/{membership.id}/approve")

        assert teams.active_team_membership(anna, team) is not None
        assert len(_events("team_approved", anna.email)) == 1

    def test_an_invitation_needs_a_time_and_place(self, app):
        team, lead = _led()
        membership = teams.join_or_apply(_person(), team)

        with pytest.raises(ValidationError):
            teams.invite(lead, team, membership.id, "  ")

    def test_not_accepting(self, app):
        team, lead = _led()
        anna = _person()
        membership = teams.join_or_apply(anna, team)

        teams.reject(lead, team, membership.id)

        assert membership.status == teams.REJECTED
        assert len(_events("team_rejected", anna.email)) == 1

    def test_a_decision_on_a_withdrawn_application_is_refused(self, app):
        team, lead = _led()
        anna = _person()
        membership = teams.join_or_apply(anna, team)
        teams.withdraw(anna, team)

        with pytest.raises(ConflictError):
            teams.approve(lead, team, membership.id)

    def test_approving_into_a_full_team_is_refused(self, app):
        team, lead = _led(max_members="2")
        membership = teams.join_or_apply(_person(), team)
        _in_team(_person("b@example.com", "Ben", "Berg"), team)

        with pytest.raises(ConflictError, match="full"):
            teams.approve(lead, team, membership.id)

    def test_removing_needs_a_reason_which_the_person_is_not_told(self, app, client):
        team, lead = _led()
        anna = _person()
        _in_team(anna, team)
        membership = teams.active_team_membership(anna, team)
        _login(client, lead.id)

        client.post(f"/teams/rocket/manage/memberships/{membership.id}/remove", data={"reason": ""})
        assert membership.status == teams.ACTIVE

        client.post(f"/teams/rocket/manage/memberships/{membership.id}/remove", data={"reason": "Never came."})
        db.session.refresh(membership)
        assert (membership.status, membership.end_reason, membership.end_note) == (teams.ENDED, teams.END_REMOVED, "Never came.")
        [told] = _events("team_removed", anna.email)
        assert "Never came." not in str(told.payload)

    def test_former_members_stay_listed(self, app, client):
        team, lead = _led()
        anna = _person()
        _in_team(anna, team)
        teams.leave(anna, team)
        db.session.commit()
        _login(client, lead.id)

        body = client.get("/teams/rocket/manage").get_data(as_text=True)

        assert "Former members" in body and "Anna Berger" in body and "Left" in body

    def test_notes_are_for_the_leads(self, app, client):
        team, lead = _led()
        anna = _person()
        _in_team(anna, team)
        _login(client, lead.id)

        client.post(f"/teams/rocket/manage/people/{anna.id}/notes", data={"body": "Knows CATIA."})
        assert "Knows CATIA." in client.get(f"/teams/rocket/manage/people/{anna.id}").get_data(as_text=True)

        _login(client, anna.id)
        assert "Knows CATIA." not in client.get("/teams/rocket").get_data(as_text=True)
        assert client.get(f"/teams/rocket/manage/people/{anna.id}").status_code == 403

    def test_the_person_page_shows_contact_details_but_no_address(self, app, client):
        team, lead = _led()
        anna = _person(phone_private="+43 660 1234567", year_group="LAV24")
        _in_team(anna, team)
        _login(client, lead.id)

        body = client.get(f"/teams/rocket/manage/people/{anna.id}").get_data(as_text=True)

        assert "+43 660 1234567" in body and "LAV24" in body and "anna@example.com" in body
        assert anna.member.street not in body

    def test_the_export_lists_the_current_members(self, app, client):
        team, lead = _led()
        _in_team(_person(first="Jörg", email_work="joerg@edu.example"), team)
        _login(client, lead.id)

        response = client.get("/teams/rocket/manage/export.csv")
        text = response.get_data(as_text=True)

        assert response.mimetype == "text/csv"
        assert text.startswith("﻿Name,University email")
        assert "Jörg Berger,joerg@edu.example" in text
        assert db.session.query(AuditLog).filter_by(event_type="team_members_exported").count() == 1

    def test_the_leads_change_their_own_settings(self, app, client):
        team, lead = _led()
        _login(client, lead.id)

        client.post("/teams/rocket/manage/settings", data={"description": "We fly.", "application_prompt": ""})

        db.session.refresh(team)
        assert (team.description, team.applications_open) == ("We fly.", False)

    def test_without_a_lead_in_force_the_admins_are_told(self, app):
        team = _team("Rocket")
        teams.join_or_apply(_person(), team)

        [event] = _events("team_application_received")
        assert event.channel == "admin_general" and "no active lead" in event.summary


@pytest.mark.usefixtures("switched_on")
class TestFollowingTheAssociation:
    def _lapse(self, user):
        user.member.membership_ends_on = get_membership_today() - timedelta(days=30)
        user.member.payment_status = "unpaid"
        db.session.commit()

    def test_whoever_leaves_the_association_leaves_their_teams(self, app):
        team, lead = _led()
        anna, ben = _person(), _person("ben@example.com", "Ben", "Berg")
        _in_team(anna, team)
        teams.join_or_apply(ben, team)
        db.session.commit()
        self._lapse(anna)
        self._lapse(ben)

        assert teams.end_lapsed_team_memberships() == 1

        memberships = {m.user_id: m for m in db.session.query(TeamMembership).filter(TeamMembership.user_id.in_([anna.id, ben.id]))}
        assert (memberships[anna.id].status, memberships[anna.id].end_reason) == (teams.ENDED, teams.END_MEMBERSHIP_ENDED)
        assert memberships[ben.id].status == teams.WITHDRAWN
        [summary] = _events("team_members_lapsed", lead.email)
        assert "Anna Berger" in summary.summary and "Ben" not in summary.summary

    def test_a_lead_who_lapses_keeps_the_role(self, app):
        team, lead = _led()
        self._lapse(lead)

        teams.end_lapsed_team_memberships()

        assert db.session.query(TeamRole).filter_by(user_id=lead.id).count() == 1
        assert teams.has_lead_in_force(team) is False

    def test_a_disabled_account_is_left_alone(self, app):
        team, _lead = _led()
        anna = _person()
        _in_team(anna, team)
        from aeronautics_members.services.clock import get_now_utc

        anna.disabled_at = get_now_utc()
        db.session.commit()

        assert teams.end_lapsed_team_memberships() == 0
        assert teams.active_team_membership(anna, team) is not None

    def test_nothing_happens_while_switched_off(self, app):
        team, _lead = _led()
        anna = _person()
        _in_team(anna, team)
        self._lapse(anna)
        teams.save_team_settings(None, enabled=False, label_singular="", label_plural="")

        assert teams.end_lapsed_team_memberships() == 0

    def test_erasing_an_account_ends_its_teams_and_keeps_the_notes(self, app):
        team, lead = _led(application_prompt="Why?")
        anna = _person()
        membership = teams.join_or_apply(anna, team, "Because.")
        teams.add_note(lead, team, anna, "Seemed keen.")
        teams.grant_team_role(None, team, anna, teams.ROLE_LEAD)
        db.session.commit()

        privacy.erase_account(anna, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()

        db.session.refresh(membership)
        assert (membership.status, membership.application_text) == (teams.WITHDRAWN, None)
        assert db.session.query(TeamRole).filter_by(user_id=anna.id).count() == 0
        assert db.session.query(TeamNote).filter_by(user_id=anna.id).count() == 1


TEAM_EMAILS = [
    ("team_invited", "Invitation from Rocket"),
    ("team_approved", "Welcome to Rocket"),
    ("team_rejected", "Your application to Rocket"),
    ("team_removed", "Your membership in Rocket has ended"),
    ("team_application_received", "New application for Rocket"),
    ("team_member_joined", "New member in Rocket"),
    ("team_member_left", "A member left Rocket"),
    ("team_members_lapsed", "Members left Rocket"),
]


@pytest.mark.usefixtures("outbox")
@pytest.mark.parametrize("event_type, subject", TEAM_EMAILS)
def test_every_team_email_goes_out(app, event_type, subject):
    from aeronautics_members.services.notifications import get_notification_service

    service = get_notification_service()
    event = _user_status_event(event_type, team_name="Rocket", team_slug="rocket",
                               meeting_details="Tuesday 18:00")
    with app.test_request_context():
        built_subject, template_vars = service._build_user_status_message(event)
        ok, error = service._send_user_status_mail(event, built_subject, template_vars)

    assert (ok, error) == (True, None)
    assert built_subject == subject
    assert "Hello Anna," in _parts(FakeSMTP.sent[-1], "text/plain")[0].get_payload(decode=True).decode()


@pytest.mark.usefixtures("switched_on")
class TestFormerMembers:
    """Kept for good: who was in the team in which year is worth knowing later."""

    def _stint(self, user, team, start, end):
        from datetime import datetime

        db.session.add(TeamMembership(team=team, user=user, status=teams.ENDED, end_reason=teams.END_LEFT,
                                      started_at=datetime(start, 10, 1), ended_at=datetime(end, 6, 30)))
        db.session.commit()

    def test_once_per_person_with_every_period(self, app):
        team, _lead = _led()
        anna = _person()
        self._stint(anna, team, 2022, 2023)
        self._stint(anna, team, 2025, 2026)

        [row] = teams.former_members(team)

        assert row["name"] == "Anna Berger"
        assert row["periods"] == "2022–2023, 2025–2026"
        assert row["last_active_shown"] == "30.06.2026"

    def test_most_recently_active_first_and_not_whoever_is_back(self, app):
        team, _lead = _led()
        anna, ben, cara = _person(), _person("ben@example.com", "Ben", "Berg"), _person("cara@example.com", "Cara", "Cole")
        self._stint(anna, team, 2020, 2021)
        self._stint(ben, team, 2024, 2025)
        self._stint(cara, team, 2022, 2023)
        _in_team(cara, team)

        assert [row["name"] for row in teams.former_members(team)] == ["Ben Berg", "Anna Berger"]
