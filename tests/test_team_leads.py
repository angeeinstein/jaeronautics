"""Leads appointed from the team's own Roles page (api/team_manage.py).

The maintainer: making someone a team lead "should be done a bit more similar
to the normal way I give someone roles … directly from the team page … a team
lead should also be possible and allowed to appoint another team lead … selection
based … not … type in their exact email address." A lead chooses from the
team's members; a site admin also from all of the association's members, since
a new team's first lead is not in it yet.
"""
import pytest

from api_helpers import send
from conftest import db
from aeronautics_members.db_models import AuditLog
from aeronautics_members.services import teams
from test_team_page import _admin
from test_teams_flow import _led, _login, _person, switched_on  # noqa: F401
from test_teams_foundation import _in_team

API = "/api/v1/teams/rocket/manage"


def _leads(team):
    return sorted(holder.user.member.first_name for holder in teams.role_holders(team, teams.ROLE_LEAD))


@pytest.mark.usefixtures("switched_on")
class TestALead:
    def test_appoints_another_member_from_the_list(self, app, client):
        team, lead = _led()
        anna = _person()
        _in_team(anna, team)
        _login(client, lead.id)

        roles = client.get(f"{API}/roles").get_json()
        assert roles["may_appoint_leads"] is True and roles["searches_everyone"] is False
        assert [candidate["name"] for candidate in roles["lead_candidates"]] == ["Anna Berger"]

        response = send(client, "POST", f"{API}/leads", {"user_id": anna.id})

        assert response.status_code == 200
        assert _leads(team) == ["Anna", "Lena"]
        assert teams.can_in_team(anna, team, teams.TeamPermission.APPOINT_LEADS)
        assert db.session.query(AuditLog).filter_by(event_type="team_role_granted", target_user_id=anna.id).count() == 1

    def test_only_from_the_team(self, app, client):
        team, lead = _led()
        outsider = _person("out@example.com", "Otto", "Outside")
        _login(client, lead.id)

        response = send(client, "POST", f"{API}/leads", {"user_id": outsider.id})

        assert response.status_code == 400 and _leads(team) == ["Lena"]
        assert client.get(f"{API}/lead-candidates?q=Otto").get_json()["items"] == []

    def test_removes_another_lead(self, app, client):
        team, lead = _led()
        anna = _person()
        _in_team(anna, team)
        teams.grant_team_role(None, team, anna, teams.ROLE_LEAD)
        db.session.commit()
        _login(client, lead.id)

        assert send(client, "DELETE", f"{API}/leads/{anna.id}").status_code == 200
        assert _leads(team) == ["Lena"]

    def test_the_last_lead_only_when_confirmed(self, app, client):
        team, lead = _led()
        _login(client, lead.id)

        refused = send(client, "DELETE", f"{API}/leads/{lead.id}")
        assert refused.status_code == 409 and refused.get_json()["error"]["code"] == "team_last_lead"

        assert send(client, "DELETE", f"{API}/leads/{lead.id}?confirmed=true").status_code == 200
        assert _leads(team) == []

    def test_not_a_treasurer_or_an_ordinary_member(self, app, client):
        team, _lead = _led()
        anna = _person()
        _in_team(anna, team)
        tim = _person("tim@example.com", "Tim", "Treasurer")
        _in_team(tim, team)
        teams.grant_team_role(None, team, tim, teams.ROLE_TREASURER)
        db.session.commit()

        for who in (anna, tim):
            _login(client, who.id)
            assert send(client, "POST", f"{API}/leads", {"user_id": anna.id}).status_code == 403


@pytest.mark.usefixtures("switched_on")
class TestASiteAdmin:
    def test_finds_any_member_of_the_association_by_name(self, app, client):
        team, _lead = _led()
        _person("tom@example.com", "Tom", "Neu", year_group="LAV24")
        _login(client, _admin().id)

        assert client.get(f"{API}/roles").get_json()["searches_everyone"] is True
        found = client.get(f"{API}/lead-candidates?q=neu").get_json()["items"]

        assert found == [{"user_id": found[0]["user_id"], "name": "Tom Neu", "detail": "LAV24", "in_team": False}]

    def test_makes_the_first_lead_of_a_new_team_before_they_join(self, app, client):
        team, lead = _led()
        teams.revoke_team_role(None, team, lead, teams.ROLE_LEAD, confirmed=True)
        tom = _person("tom@example.com", "Tom", "Neu")
        db.session.commit()
        _login(client, _admin().id)

        assert send(client, "POST", f"{API}/leads", {"user_id": tom.id}).status_code == 200

        assert _leads(team) == ["Tom"]
        # It counts once they are in the team.
        assert not teams.can_in_team(tom, team, teams.TeamPermission.VIEW_MEMBERS)
        _in_team(tom, team)
        assert teams.can_in_team(tom, team, teams.TeamPermission.VIEW_MEMBERS)

    def test_not_somebody_who_is_no_member_of_the_association(self, app, client):
        team, _lead = _led()
        lapsed = _person("old@example.com", "Olga", "Old")
        lapsed.member.is_active = False
        lapsed.member.payment_status = "inactive"
        db.session.commit()
        _login(client, _admin().id)

        assert send(client, "POST", f"{API}/leads", {"user_id": lapsed.id}).status_code == 400

    def test_sees_the_teams_admin_settings_in_its_menu(self, app, client):
        _led()
        _login(client, _admin().id)

        assert client.get("/api/v1/teams/rocket").get_json()["administers"] is True
