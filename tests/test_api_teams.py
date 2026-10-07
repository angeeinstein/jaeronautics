"""Teams, for members (api/teams.py): what somebody is told about their
membership of a team, worked out once for the overview and the team's pages.
The flows themselves are tested in test_teams_flow.py and the payment tests.
"""
import pytest

from api_helpers import send
from conftest import db
from aeronautics_members.services import teams
from test_team_payments import _approved, _charging, fake_stripe  # noqa: F401 -- fixture
from test_teams_flow import _led, _login, _person, switched_on  # noqa: F401 -- fixture
from test_teams_foundation import _in_team

API = "/api/v1/teams"


def test_nothing_while_teams_are_off(app, client):
    _led()
    _login(client, _person().id)

    assert client.get(API).status_code == 404
    assert client.get(f"{API}/rocket").status_code == 404


@pytest.mark.usefixtures("switched_on")
class TestTheMembership:
    def test_approved_in_a_team_with_a_fee_one_step_left(self, app, client):
        team, lead = _led()
        _charging(team)
        anna, _membership = _approved(team, lead)
        _login(client, anna.id)

        membership = client.get(f"{API}/rocket").get_json()["membership"]

        assert membership["status"] == "approved" and membership["actions"][:2] == ["pay_join", "withdraw"]
        assert membership["notes"][-1]["text"].startswith("One step left. Paying now covers until ")

    def test_back_from_paying_nothing_asks_to_pay_again(self, app, client):
        team, lead = _led()
        _charging(team)
        anna, _membership = _approved(team, lead)
        _login(client, anna.id)

        membership = client.get(f"{API}/rocket?paid=rocket").get_json()["membership"]

        assert "pay_join" not in membership["actions"]
        assert membership["notes"][0]["tone"] == "info" and "Payment received" in membership["notes"][0]["text"]

    def test_invited_with_where_to_meet(self, app, client):
        team, lead = _led()
        anna = _person()
        applied = teams.join_or_apply(anna, team)
        teams.invite(lead, team, applied.id, "Tuesday 18:00, room 2.04")
        db.session.commit()
        _login(client, anna.id)

        membership = client.get(f"{API}/rocket").get_json()["membership"]

        assert (membership["status_label"], membership["meeting"]) == ("Invited", "Tuesday 18:00, room 2.04")

    def test_a_lead_manages_from_the_overview(self, app, client):
        _team, lead = _led()
        _login(client, lead.id)

        [card] = client.get(API).get_json()["mine"]

        assert card["opens"] == "team" and card["membership"]["actions"][0] == "open"
        assert "manage" in card["membership"]["actions"]


@pytest.mark.usefixtures("switched_on")
class TestLeaving:
    def test_only_a_member(self, app, client):
        _led()
        _login(client, _person().id)

        assert client.get(f"{API}/rocket/leave").status_code == 404
        assert send(client, "POST", f"{API}/rocket/leave", {"confirm": True}).status_code == 404

    def test_a_lead_is_told_the_role_ends(self, app, client):
        team, lead = _led()
        _login(client, lead.id)

        leaving = client.get(f"{API}/rocket/leave").get_json()

        assert leaving["is_lead"] is True and leaving["labels"]["plural"] == "Teams"

    def test_somebody_else_is_not(self, app, client):
        team, _lead = _led()
        anna = _person()
        _in_team(anna, team)
        _login(client, anna.id)

        assert client.get(f"{API}/rocket/leave").get_json()["is_lead"] is False
