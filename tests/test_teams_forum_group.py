"""Teams: a team's forum group, kept in step with the team.

A team may name a forum group. Its active members are in it and everybody else
is not, sent with every forum sync in both directions like the other groups,
so nobody keeps a team's group after leaving. Joining, leaving, being removed
and leaving the association each queue a sync. While teams are switched off,
nothing about team groups is sent at all.
"""
import pytest

from conftest import db
from aeronautics_members.db_models import ExternalWorkItem
from aeronautics_members.forum_service import DiscourseConnectProvider
from aeronautics_members.services import teams
from test_teams_flow import _led, _person, switched_on  # noqa: F401
from test_teams_foundation import _in_team


def _groups(user):
    provider = DiscourseConnectProvider(settings={
        "forum_member_group": "members",
        "discourse_connect_secret": "s",
    })
    payload = provider.build_sso_payload(user, user.member, desired_state="active", nonce="n1")
    split = lambda key: set(filter(None, (payload.get(key) or "").split(",")))  # noqa: E731
    return split("add_groups"), split("remove_groups")


def _queued_for(user):
    return db.session.query(ExternalWorkItem).filter_by(
        kind=ExternalWorkItem.KIND_FORUM_SYNC, member_id=user.member.id
    ).count()


@pytest.mark.usefixtures("switched_on")
class TestTheGroup:
    def test_a_member_is_in_it_and_others_are_not(self, app):
        team, _lead = _led(forum_group="team-rocket")
        anna, ben = _person(), _person("ben@example.com", "Ben", "Berg")
        _in_team(anna, team)

        assert "team-rocket" in _groups(anna)[0]
        assert "team-rocket" in _groups(ben)[1]

    def test_leaving_takes_it_away(self, app):
        team, _lead = _led(forum_group="team-rocket")
        anna = _person()
        _in_team(anna, team)

        teams.leave(anna, team)

        add, remove = _groups(anna)
        assert "team-rocket" not in add and "team-rocket" in remove

    def test_an_archived_team_has_nobody_in_it(self, app):
        team, _lead = _led(forum_group="team-rocket")
        anna = _person()
        _in_team(anna, team)

        teams.set_team_archived(None, team, True)

        assert "team-rocket" in _groups(anna)[1]

    def test_two_teams_sharing_a_group(self, app):
        rocket, _a = _led("Rocket", forum_group="workshop")
        _led("Glider", forum_group="workshop")
        anna = _person()
        _in_team(anna, rocket)

        add, remove = _groups(anna)
        assert "workshop" in add and "workshop" not in remove

    def test_a_team_without_a_group_sends_nothing(self, app):
        team, _lead = _led()
        anna = _person()
        _in_team(anna, team)

        team_groups = {"team-rocket", "workshop"}
        add, remove = _groups(anna)
        assert not (add | remove) & team_groups


@pytest.mark.usefixtures("switched_on")
class TestASyncIsQueued:
    def test_when_somebody_becomes_a_member(self, app):
        team, lead = _led(forum_group="team-rocket")
        anna = _person()
        membership = teams.join_or_apply(anna, team)
        assert _queued_for(anna) == 0, "an application changes nothing on the forum"

        teams.approve(lead, team, membership.id)

        assert _queued_for(anna) == 1

    def test_when_somebody_leaves_or_is_removed(self, app):
        team, lead = _led(forum_group="team-rocket")
        anna, ben = _person(), _person("ben@example.com", "Ben", "Berg")
        _in_team(anna, team)
        _in_team(ben, team)

        teams.leave(anna, team)
        teams.remove(lead, team, teams.active_team_membership(ben, team).id, "Never came.")

        assert (_queued_for(anna), _queued_for(ben)) == (1, 1)

    def test_not_for_a_team_without_a_group(self, app):
        team, lead = _led()
        anna = _person()
        membership = teams.join_or_apply(anna, team)

        teams.approve(lead, team, membership.id)

        assert _queued_for(anna) == 0


def test_nothing_about_team_groups_while_teams_are_off(app):
    team, _lead = _led(forum_group="team-rocket")
    anna = _person()
    _in_team(anna, team)

    add, remove = _groups(anna)

    assert "team-rocket" not in add | remove
