"""A team's permissions per person (docs/permissions-plan.md, decision 3).

Lead and treasurer are the team's presets; a member's permissions in the team
are their roles' plus what is added, less what is taken away. The leads adjust
them -- "they are actually managing the team and should basically be able to
manage almost everything regarding the team" -- within what they hold there
themselves.
"""
import pytest

from api_helpers import send
from conftest import db
from aeronautics_members.db_models import AuditLog, TeamMemberPermission
from aeronautics_members.services import teams
from test_teams_flow import _led, _login, _person, switched_on  # noqa: F401
from test_teams_foundation import _in_team

API = "/api/v1/teams/rocket/manage"
P = teams.TeamPermission


def _member(team, email="anna@example.com", first="Anna"):
    person = _person(email, first, "Berger")
    _in_team(person, team)
    db.session.commit()
    return person


def _set(client, person, permissions):
    return send(client, "PUT", f"{API}/people/{person.id}/permissions", {"permissions": sorted(permissions)})


class TestTheCatalogue:
    def test_every_team_permission_has_a_label_and_an_area_once(self):
        listed = [permission for _area, permissions in teams.TEAM_PERMISSION_AREAS for permission in permissions]

        assert sorted(listed) == sorted(P.ALL) and len(listed) == len(set(listed))
        assert set(teams.TEAM_PERMISSION_LABELS) == P.ALL

    def test_changing_needs_seeing(self):
        assert teams.with_team_needs({P.EDIT_BANK_DETAILS}) == {P.EDIT_BANK_DETAILS, P.VIEW_MONEY}
        assert teams.without_unmet_team_needs({P.REMOVE_MEMBERS}) == set()

    def test_the_presets_need_nothing_more(self):
        for preset in teams.TEAM_ROLE_PERMISSIONS.values():
            assert teams.with_team_needs(preset) == set(preset)


@pytest.mark.usefixtures("switched_on")
class TestALead:
    def test_gives_a_member_one_permission(self, app, client):
        team, lead = _led()
        anna = _member(team)
        _login(client, lead.id)

        page = client.get(f"{API}/people/{anna.id}").get_json()
        assert {option["name"] for option in page["permissions"] if option["may_change"]} == set(P.ALL)
        saved = _set(client, anna, {P.WRITE_NOTES})

        assert saved.status_code == 200
        assert teams.team_permissions(anna, team) == {P.WRITE_NOTES, P.VIEW_MEMBERS}
        assert team in teams.teams_led_by(anna)
        entry = db.session.query(AuditLog).filter_by(event_type="team_permissions_changed").one()
        assert entry.event_metadata["added"] == [P.VIEW_MEMBERS, P.WRITE_NOTES]

    def test_takes_one_from_the_treasurer_and_the_role_stays_live(self, app, client):
        team, lead = _led()
        anna = _member(team)
        teams.grant_team_role(lead, team, anna, teams.ROLE_TREASURER)
        db.session.commit()
        _login(client, lead.id)

        _set(client, anna, P.MONEY - {P.DEVICES})

        assert teams.team_permissions(anna, team) == P.MONEY - {P.DEVICES}
        assert [(row.permission, row.granted) for row in team.permission_exceptions] == [(P.DEVICES, False)]

    def test_unticking_the_money_takes_the_bank_details_with_it(self, app, client):
        team, lead = _led()
        anna = _member(team)
        teams.grant_team_role(lead, team, anna, teams.ROLE_TREASURER)
        db.session.commit()
        _login(client, lead.id)

        _set(client, anna, {P.EDIT_PRICES, P.DEVICES})

        assert teams.team_permissions(anna, team) == {P.EDIT_PRICES, P.DEVICES}

    def test_not_their_own(self, app, client):
        team, lead = _led()
        _login(client, lead.id)

        assert client.get(f"{API}/people/{lead.id}").get_json()["permissions"] is None
        assert _set(client, lead, set()).status_code == 409


@pytest.mark.usefixtures("switched_on")
class TestWithinWhatOneHolds:
    def test_a_member_given_the_permission_grants_only_what_they_hold(self, app, client):
        team, lead = _led()
        anna = _member(team)
        ben = _member(team, "ben@example.com", "Ben")
        teams.set_team_member_permissions(lead, team, anna, {P.PERMISSIONS, P.WRITE_NOTES})
        db.session.commit()
        _login(client, anna.id)

        page = client.get(f"{API}/people/{ben.id}").get_json()
        refused = _set(client, ben, {P.REMOVE_MEMBERS})
        given = _set(client, ben, {P.WRITE_NOTES})

        assert {option["name"] for option in page["permissions"] if option["may_change"]} == {
            P.VIEW_MEMBERS, P.WRITE_NOTES, P.PERMISSIONS}
        assert refused.status_code == 409 and given.status_code == 200
        assert teams.team_permissions(ben, team) == {P.VIEW_MEMBERS, P.WRITE_NOTES}

    def test_a_lead_is_out_of_their_reach(self, app, client):
        team, lead = _led()
        anna = _member(team)
        teams.set_team_member_permissions(lead, team, anna, {P.PERMISSIONS})
        db.session.commit()
        _login(client, anna.id)

        page = client.get(f"{API}/people/{lead.id}").get_json()

        assert not any(option["may_change"] for option in page["permissions"])
        assert _set(client, lead, P.ALL - {P.MESSAGES}).status_code == 409
        assert teams.team_permissions(lead, team) == P.ALL

    def test_an_ordinary_member_may_not(self, app, client):
        team, _lead = _led()
        anna = _member(team)
        ben = _member(team, "ben@example.com", "Ben")
        _login(client, anna.id)

        assert _set(client, ben, {P.WRITE_NOTES}).status_code == 403


@pytest.mark.usefixtures("switched_on")
class TestWhileInTheTeam:
    def test_counts_only_while_a_member(self, app, client):
        team, lead = _led()
        anna = _member(team)
        teams.set_team_member_permissions(lead, team, anna, {P.WRITE_NOTES})
        db.session.commit()

        membership = teams.active_team_membership(anna, team)
        membership.status = teams.ENDED
        db.session.commit()

        assert teams.team_permissions(anna, team) == frozenset()
        assert teams.teams_led_by(anna) == []

    def test_a_role_change_drops_exceptions_it_made_pointless(self, app, client):
        team, lead = _led()
        anna = _member(team)
        teams.set_team_member_permissions(lead, team, anna, {P.VIEW_MONEY})
        db.session.commit()

        teams.grant_team_role(lead, team, anna, teams.ROLE_TREASURER)
        db.session.commit()

        assert db.session.query(TeamMemberPermission).count() == 0
        assert teams.team_permissions(anna, team) == P.MONEY
