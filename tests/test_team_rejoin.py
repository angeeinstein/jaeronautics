# ruff: noqa: F811 -- the fixture imported below is taken as an argument
"""Former members rejoin without approval (a team setting, Team.rejoin_months;
docs/team-passive-members.md): within the months after their membership
ended -- left, not paid, association membership ended, not removed -- they
come back without applying.
"""
from datetime import date, datetime, timedelta

import pytest

from conftest import db
from aeronautics_members.services import teams
from test_teams_flow import _events, _led, _login, _person, switched_on  # noqa: F401 -- a fixture
from test_teams_foundation import _in_team

pytestmark = pytest.mark.usefixtures("switched_on")


def _former(months=None, reason="left"):
    """A team that approves its members, and Anna, who was in it until today."""
    team, lead = _led()
    team.rejoin_months = months
    anna = _person()
    _in_team(anna, team)
    membership = teams.ongoing_membership(anna, team)
    if reason == "left":
        teams.leave(anna, team)
    else:
        membership.status, membership.ended_at, membership.end_reason = teams.ENDED, datetime.now(), reason
    db.session.commit()
    return team, lead, anna, membership


def test_off_by_default_somebody_who_left_applies_again(app):
    team, _lead, anna, _membership = _former()
    assert team.rejoin_months is None
    assert teams.join_or_apply(anna, team).status == teams.APPLIED


def test_with_it_on_somebody_who_left_is_back_at_once(app):
    team, lead, anna, _membership = _former(months=12)

    back = teams.join_or_apply(anna, team)

    assert back.status == teams.ACTIVE  # a free team: no payment step
    assert not _events("team_application_received")
    assert _events("team_member_joined", lead.email)


@pytest.mark.parametrize("reason", [teams.END_PAYMENT_FAILED, teams.END_NOT_RENEWED, teams.END_MEMBERSHIP_ENDED])
def test_also_after_not_paying_or_the_association_membership_ending(app, reason):
    team, _lead, anna, _membership = _former(months=12, reason=reason)
    assert teams.join_or_apply(anna, team).status == teams.ACTIVE


def test_not_after_being_removed(app):
    team, _lead, anna, _membership = _former(months=12, reason=teams.END_REMOVED)
    assert teams.rejoin_until(anna, team) is None
    assert teams.join_or_apply(anna, team).status == teams.APPLIED


def test_only_within_the_months(app):
    team, _lead, anna, membership = _former(months=12)
    ended = membership.ended_at.date()

    assert teams.rejoin_until(anna, team) == ended.replace(year=ended.year + 1)
    assert teams.rejoin_until(anna, team, today=ended.replace(year=ended.year + 1) + timedelta(days=1)) is None


def test_a_later_application_turned_down_closes_the_way(app):
    team, lead, anna, _membership = _former()
    application = teams.join_or_apply(anna, team)
    teams.reject(lead, team, application.id)
    team.rejoin_months = 12
    db.session.commit()

    assert teams.rejoin_until(anna, team) is None


def test_an_open_team_needs_no_setting(app):
    team, _lead, anna, _membership = _former(months=12)
    team.admission_mode = teams.ADMISSION_OPEN
    db.session.commit()
    assert teams.rejoin_until(anna, team) is None  # nothing to skip: anybody joins
    assert teams.join_or_apply(anna, team).status == teams.ACTIVE


@pytest.mark.parametrize("day, months, expected", [
    (date(2026, 1, 31), 1, date(2026, 2, 28)),
    (date(2026, 10, 11), 12, date(2027, 10, 11)),
    (date(2026, 11, 30), 3, date(2027, 2, 28)),
    (date(2027, 8, 31), 6, date(2028, 2, 29)),
])
def test_months_count_by_the_calendar(day, months, expected):
    assert teams._months_after(day, months) == expected


class TestThePages:
    def test_the_leads_switch_it_on(self, client):
        team, lead = _led()
        _login(client, lead.id)
        url = f"/api/v1/teams/{team.slug}/manage/applying"

        saved = client.put(url, json={"applications_open": True, "rejoin_months": 12}).get_json()
        assert saved["rejoin_months"] == 12 and team.rejoin_months == 12

        assert client.put(url, json={"applications_open": True, "rejoin_months": 0}).status_code == 400
        client.put(url, json={"applications_open": True, "rejoin_months": None})
        assert team.rejoin_months is None

    def test_left_out_it_stays(self, client):
        team, lead = _led()
        team.rejoin_months = 6
        db.session.commit()
        _login(client, lead.id)
        client.put(f"/api/v1/teams/{team.slug}/manage/applying", json={"applications_open": False})
        assert team.rejoin_months == 6

    def test_the_former_members_say_until_when(self, client):
        team, lead, anna, membership = _former(months=12)
        _login(client, lead.id)

        former = client.get(f"/api/v1/teams/{team.slug}/manage/former").get_json()["former"]

        ended = membership.ended_at.date()
        assert [(row["name"], row["rejoin_until"]) for row in former] == [
            ("Anna Berger", ended.replace(year=ended.year + 1).isoformat())]

    def test_the_person_sees_rejoin_not_apply(self, client):
        team, _lead, anna, _membership = _former(months=12)
        team.application_prompt = "Why us?"
        db.session.commit()
        _login(client, anna.id)

        joining = client.get(f"/api/v1/teams/{team.slug}").get_json()["joining"]

        assert joining["submit_label"] == "Rejoin" and joining["prompt"] is None and joining["rejoin_until"]
