"""Teams, filling the gaps before they go live.

Somebody downloading their data gets their teams too -- memberships, roles,
what they wrote when applying, and the leads' notes about them (without
who wrote them). A site admin sees a person's teams on their account page. And an
approval for a team that charges lapses when it is not paid within 14 days.
"""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import db
from aeronautics_members.db_models import NotificationEvent
from aeronautics_members.services import privacy, teams
from test_admin_reviews import _staff
from test_emails import FakeSMTP, _user_status_event, outbox  # noqa: F401
from test_teams_flow import _led, _login, _person, switched_on  # noqa: F401


@pytest.mark.usefixtures("switched_on")
class TestTheDataExport:
    def test_includes_the_teams_and_the_leads_notes(self, app):
        team, lead = _led(application_prompt="Why?")
        anna = _person()
        teams.join_or_apply(anna, team, "I like rockets.")
        teams.add_note(lead, team, anna, "Owes us a soldering iron.")
        teams.grant_team_role(None, team, anna, teams.ROLE_LEAD)
        db.session.commit()

        exported = privacy.export_account_data(anna)

        [membership] = exported["teams"]["memberships"]
        assert (membership["team"], membership["status"], membership["application_text"]) == (
            "Rocket", teams.APPLIED, "I like rockets.",
        )
        assert exported["teams"]["roles"][0]["role"] == "lead"
        [note] = exported["teams"]["notes_by_leads"]
        assert (note["team"], note["text"]) == ("Rocket", "Owes us a soldering iron.")
        assert lead.email not in str(exported["teams"])


@pytest.mark.usefixtures("switched_on")
class TestTheAdminAccountPage:
    def test_shows_the_persons_teams_and_roles(self, app, client):
        team, lead = _led()
        _login(client, _staff("admin@example.com", "admin").id)

        teams = client.get(f"/api/v1/admin/accounts/{lead.id}").get_json()["teams"]

        assert teams["roles"] == [{"role_label": "Lead", "team": "Rocket"}]
        assert [m["link_url"] for m in teams["memberships"]] == [f"/teams/rocket/manage/people/{lead.id}"]

    def test_says_nothing_for_somebody_in_no_team(self, app, client):
        anna = _person()
        _login(client, _staff("admin@example.com", "admin").id)

        teams = client.get(f"/api/v1/admin/accounts/{anna.id}").get_json()["teams"]

        assert teams == {"roles": [], "memberships": []}


@pytest.mark.usefixtures("switched_on")
class TestAnUnpaidApprovalLapses:
    def _approved(self, days_ago):
        team, lead = _led()
        team.payment_mode = "subscription"
        anna = _person()
        membership = teams.join_or_apply(anna, team)
        teams.approve(lead, team, membership.id)
        membership.approved_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
        db.session.commit()
        return membership, anna

    def test_after_fourteen_days(self, app):
        membership, anna = self._approved(days_ago=15)

        assert teams.lapse_unpaid_approvals() == 1

        assert (membership.status, membership.end_reason) == (teams.WITHDRAWN, teams.END_NOT_PAID)
        assert db.session.query(NotificationEvent).filter_by(
            event_type="team_approval_lapsed", recipient_email=anna.email
        ).count() == 1

    def test_not_before(self, app):
        membership, _anna = self._approved(days_ago=13)

        assert teams.lapse_unpaid_approvals() == 0
        assert membership.status == teams.APPROVED

    def test_a_free_team_never_waits(self, app):
        team, lead = _led()
        membership = teams.join_or_apply(_person(), team)
        teams.approve(lead, team, membership.id)

        assert teams.lapse_unpaid_approvals() == 0
        assert membership.status == teams.ACTIVE

    def test_applying_again_afterwards(self, app):
        membership, anna = self._approved(days_ago=15)
        teams.lapse_unpaid_approvals()

        assert teams.join_or_apply(anna, membership.team).status == teams.APPLIED


@pytest.mark.usefixtures("outbox")
def test_the_lapse_email_goes_out(app):
    from aeronautics_members.services.notifications import get_notification_service

    service = get_notification_service()
    event = _user_status_event("team_approval_lapsed", team_name="Rocket", team_slug="rocket")
    with app.test_request_context():
        subject, template_vars = service._build_user_status_message(event)
        ok, _error = service._send_user_status_mail(event, subject, template_vars)

    assert ok and subject == "Your approval for Rocket has lapsed"
    assert FakeSMTP.sent
