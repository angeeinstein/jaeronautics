"""How people join a team and how many it takes are its leads' to set (the
team's Applying page, api/team_manage.py); its name, fee, forum group and
archiving stay the association's. The maintainer: "most configuration
options can be open to the team leads."
"""
import pytest

from api_helpers import send
from conftest import db
from aeronautics_members.db_models import AuditLog
from test_teams_flow import _led, _login, switched_on  # noqa: F401

API = "/api/v1/teams/rocket/manage/applying"


@pytest.mark.usefixtures("switched_on")
class TestTheLeads:
    def test_set_how_people_join_and_how_many(self, app, client):
        team, lead = _led()
        _login(client, lead.id)

        body = send(client, "PUT", API, {"applications_open": True, "application_prompt": None,
                                         "admission_mode": "open", "max_members": 15}).get_json()

        assert (body["admission_mode"], body["max_members"]) == ("open", 15)
        assert (team.admission_mode, team.max_members) == ("open", 15)
        assert db.session.query(AuditLog).filter_by(event_type="team_updated").count() == 1

    def test_no_limit_is_an_empty_number(self, app, client):
        team, lead = _led(max_members=10)
        _login(client, lead.id)

        send(client, "PUT", API, {"applications_open": True, "max_members": None})

        assert team.max_members is None

    def test_left_out_stays_as_it_is(self, app, client):
        team, lead = _led(admission_mode="open", max_members=10)
        _login(client, lead.id)

        send(client, "PUT", API, {"applications_open": False})

        assert (team.admission_mode, team.max_members, team.applications_open) == ("open", 10, False)

    @pytest.mark.parametrize("change", [{"admission_mode": "lottery"}, {"max_members": 0}])
    def test_nonsense_is_refused(self, app, client, change):
        team, lead = _led()
        _login(client, lead.id)

        response = send(client, "PUT", API, {"applications_open": True, **change})

        assert response.status_code == 400 and team.admission_mode == "approval"
