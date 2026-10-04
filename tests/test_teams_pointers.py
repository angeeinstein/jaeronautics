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
    @pytest.mark.usefixtures("switched_on")
    def test_points_to_the_teams(self, app, client):
        body = client.get("/thank-you?method=checkout&phase=prorated").get_data(as_text=True)

        assert 'href="/teams"' in body and "once the debit has cleared" in body

    def test_not_while_switched_off(self, app, client):
        assert 'href="/teams"' not in client.get("/thank-you").get_data(as_text=True)


@pytest.mark.usefixtures("switched_on")
class TestTheAccountPage:
    def test_a_member_in_no_team_is_shown_the_way(self, app, client):
        _led()
        _login(client, _person().id)

        assert "See the Teams" in client.get("/account", follow_redirects=True).get_data(as_text=True)

    def test_not_once_in_a_team(self, app, client):
        team, _lead = _led()
        anna = _person()
        _in_team(anna, team)
        _login(client, anna.id)

        assert "See the Teams" not in client.get("/account", follow_redirects=True).get_data(as_text=True)

    def test_not_before_the_membership_is_active(self, app, client):
        _led()
        waiting = _association_member("waiting@example.com", active=False)
        _login(client, waiting.id)

        assert teams.invite_to_teams(waiting) is False
        assert "See the Teams" not in client.get("/account", follow_redirects=True).get_data(as_text=True)
