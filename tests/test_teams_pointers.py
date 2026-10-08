"""Pointing new members to the teams, instead of asking at signup.

Somebody can only join a team once their association membership is active --
at once by card or in October, after the debit by SEPA. So nothing is asked
during signup; the welcome email, the page after paying and the account page
point to the Teams page instead, while teams are switched on.
"""
from datetime import datetime, timezone

import pytest
from flask import render_template

from conftest import db
from aeronautics_members.db_models import Setting
from aeronautics_members.services import teams, workflows
from test_teams_flow import _led, _login, _person, switched_on  # noqa: F401
from test_teams_foundation import _association_member, _in_team


def _welcome_email(app, monkeypatch, member):
    for key, value in {"automatic_emails_enabled": "True", "welcome_email_sender": "office",
                       "automatic_email_template": "emails/welcome_email.html"}.items():
        db.session.add(Setting(key=key, value=value))
    db.session.commit()
    sent = {}

    def fake_send_mail(**kwargs):
        sent.update(kwargs)
        return True, None

    monkeypatch.setattr(workflows, "send_mail", fake_send_mail)
    with app.test_request_context():
        workflows.send_member_welcome_email(app, member)
        template_vars = {k: v for k, v in sent.items()
                         if k not in {"from_account", "to_email", "subject", "template_name", "return_error"}}
        template_vars.setdefault("now", datetime.now(timezone.utc))
        return render_template("emails/welcome_email.html", **template_vars)


class TestTheWelcomeEmail:
    @pytest.mark.usefixtures("switched_on")
    def test_points_to_the_teams(self, app, monkeypatch):
        _led()
        anna = _person()

        body = _welcome_email(app, monkeypatch, anna.member)

        assert "Interested in one of our teams?" in body and "/teams" in body

    def test_says_nothing_of_them_while_switched_off(self, app, monkeypatch):
        _led()
        anna = _person()

        assert "Interested in one of our teams?" not in _welcome_email(app, monkeypatch, anna.member)


class TestThePageAfterPaying:
    """It points to the teams by what they are called here (frontend/src/pages/public/Payment.tsx)."""

    @pytest.mark.usefixtures("switched_on")
    def test_points_to_the_teams(self, app, client):
        assert client.get("/api/v1/site").get_json()["teams_label"] == "Teams"

    def test_not_while_switched_off(self, app, client):
        assert client.get("/api/v1/site").get_json()["teams_label"] is None


def _teams_card(client):
    """My Account's Teams card (frontend/src/pages/account/Account.tsx)."""
    return client.get("/api/v1/account").get_json()["member"]["teams"]


@pytest.mark.usefixtures("switched_on")
class TestTheAccountPage:
    def test_a_member_in_no_team_is_shown_the_way(self, app, client):
        _led()
        _login(client, _person().id)

        assert _teams_card(client)["invite"] is True

    def test_not_once_in_a_team(self, app, client):
        team, _lead = _led()
        anna = _person()
        _in_team(anna, team)
        _login(client, anna.id)

        card = _teams_card(client)
        assert card["invite"] is False
        assert [team["status_label"] for team in card["mine"]] == ["Member"]

    def test_not_before_the_membership_is_active(self, app, client):
        _led()
        waiting = _association_member("waiting@example.com", active=False)
        _login(client, waiting.id)

        assert teams.invite_to_teams(waiting) is False
        assert _teams_card(client)["invite"] is False
