"""Teams: the list of current members for access to the team's rooms.

Whoever gives access to a team's rooms needs to know who is in it. The list --
name and university email of every current member -- is emailed on a button,
or by itself on days of the year the team chose, if it switched that on. The
leads are copied in, visibly, as they would be if they wrote it themselves.
"""
from datetime import date

import pytest

from conftest import db
from aeronautics_members.db_models import AuditLog, NotificationEvent, TeamMembership
from aeronautics_members.services import ValidationError, privacy, teams
from test_emails import FakeSMTP, _parts, outbox  # noqa: F401
from test_teams_flow import _led, _login, _person, switched_on  # noqa: F401
from test_teams_foundation import _in_team


def _configured(team, recipients="office@uni.example", dates="15.10, 15.03", auto_send=True):
    teams.update_access_list(None, team, recipients=recipients, dates=dates, auto_send=auto_send)
    db.session.commit()
    return team


def _with_members(team):
    _in_team(_person("anna@example.com", "Anna", "Berger", email_work="anna@edu.example"), team)
    _in_team(_person("ben@example.com", "Ben", "Adler", email_work=None), team)
    return team


class TestTheSettings:
    def test_addresses_by_comma_semicolon_or_line(self, app):
        assert teams.parse_recipients("a@x.at, B@x.at;\nc@x.at a@x.at") == ["a@x.at", "b@x.at", "c@x.at"]

    def test_not_an_address(self, app):
        with pytest.raises(ValidationError, match="Not an email address"):
            teams.parse_recipients("office at uni")

    def test_days_in_calendar_order(self, app):
        assert teams.parse_dates("15.10, 1.3\n29.02.") == [(29, 2), (1, 3), (15, 10)]

    @pytest.mark.parametrize("text", ["31.02", "15/10", "15.13", "October"])
    def test_not_a_day(self, app, text):
        with pytest.raises(ValidationError):
            teams.parse_dates(text)

    def test_sending_by_itself_needs_somebody_and_a_day(self, app):
        team, _lead = _led()
        with pytest.raises(ValidationError):
            teams.update_access_list(None, team, recipients="", dates="15.10", auto_send=True)
        with pytest.raises(ValidationError):
            teams.update_access_list(None, team, recipients="office@uni.example", dates="", auto_send=True)

    def test_stored_tidily_and_off_unless_switched_on(self, app):
        team, _lead = _led()

        _configured(team, recipients="Office@Uni.example, porter@uni.example", dates="15.3 15.10", auto_send=False)

        assert team.access_list_recipients == "office@uni.example\nporter@uni.example"
        assert team.access_list_dates == "15.03, 15.10"
        assert team.access_list_auto_send is False

    def test_the_next_day(self, app):
        team, _lead = _configured(_led()[0]), None

        assert teams.next_access_list_date(team, date(2026, 10, 3)) == date(2026, 10, 15)
        assert teams.next_access_list_date(team, date(2026, 10, 16)) == date(2027, 3, 15)
        team.access_list_auto_send = False
        assert teams.next_access_list_date(team, date(2026, 10, 3)) is None


@pytest.mark.usefixtures("outbox", "switched_on")
class TestSending:
    def test_the_email(self, app):
        team, lead = _led()
        _with_members(_configured(team, recipients="office@uni.example, porter@uni.example"))

        assert teams.send_access_list(lead, team, today=date(2026, 10, 15)) == 3

        message = FakeSMTP.sent[-1]
        assert message["To"] == "office@uni.example"
        assert message["Cc"] == f"porter@uni.example, {lead.email}"
        assert message["Subject"] == "Rocket: current members (15.10.2026)"
        text = _parts(message, "text/plain")[0].get_payload(decode=True).decode()
        assert "Anna Berger" in text and "anna@edu.example" in text and "Ben Adler" in text
        assert text.index("Ben Adler") < text.index("Anna Berger") < text.index("Lena Lead"), "by surname"
        assert "anna@example.com" not in text, "the university address, not the private one"
        assert team.access_list_last_sent_on == date(2026, 10, 15)
        assert db.session.query(AuditLog).filter_by(event_type="team_access_list_sent").count() == 1

    def test_with_the_logo_below_the_association_header(self, app, tmp_path):
        from test_teams_logo_and_payment_step import _png

        app.config["TEAM_LOGO_DIR"] = str(tmp_path)
        team, lead = _led()
        _configured(team)
        teams.set_team_logo(None, team, _png())

        teams.send_access_list(lead, team)

        content_ids = {part.get("Content-ID") for part in FakeSMTP.sent[-1].walk() if part.get("Content-ID")}
        assert content_ids == {"<logo>", "<teamlogo>"}

    def test_nobody_to_send_to(self, app):
        team, lead = _led()

        with pytest.raises(ValidationError):
            teams.send_access_list(lead, team)

    def test_by_itself_on_its_day_and_only_once(self, app):
        team, _lead = _led()
        _configured(team)

        assert teams.send_due_access_lists(date(2026, 10, 14)) == 0
        assert teams.send_due_access_lists(date(2026, 10, 15)) == 1
        assert teams.send_due_access_lists(date(2026, 10, 15)) == 0, "not twice on one day"
        assert len(FakeSMTP.sent) == 1

    def test_not_by_itself_when_switched_off(self, app):
        team, _lead = _led()
        _configured(team, auto_send=False)

        assert teams.send_due_access_lists(date(2026, 10, 15)) == 0

    def test_not_while_teams_are_off(self, app):
        team, _lead = _led()
        _configured(team)
        teams.save_team_settings(None, enabled=False, label_singular="", label_plural="")

        assert teams.send_due_access_lists(date(2026, 10, 15)) == 0

    def test_a_failure_is_reported_to_the_admins(self, app, monkeypatch):
        from aeronautics_members import mail_utils

        team, _lead = _led()
        _configured(team)
        monkeypatch.setattr(mail_utils, "send_mail", lambda *a, **k: (False, "SMTP down"))

        assert teams.send_due_access_lists(date(2026, 10, 15)) == 0

        [event] = db.session.query(NotificationEvent).filter_by(event_type="team_access_list_failed").all()
        assert "SMTP down" in event.summary
        assert team.access_list_last_sent_on is None


@pytest.mark.usefixtures("outbox", "switched_on")
class TestThePages:
    def test_the_lead_previews_and_sends(self, app, client):
        team, lead = _led()
        _with_members(_configured(team))
        _login(client, lead.id)

        manage = client.get("/teams/rocket/manage").get_data(as_text=True)
        assert "office@uni.example" in manage and "Preview and send" in manage

        preview = client.get("/teams/rocket/manage/access-list").get_data(as_text=True)
        assert "Anna Berger" in preview and "anna@edu.example" in preview
        assert "2 member(s) have no university email." in preview  # Ben, and the lead

        client.post("/teams/rocket/manage/access-list/send")
        assert FakeSMTP.sent and team.access_list_last_sent_on is not None

    def test_an_ordinary_member_cannot(self, app, client):
        team, _lead = _led()
        _configured(team)
        anna = _person()
        _in_team(anna, team)
        _login(client, anna.id)

        assert client.get("/teams/rocket/manage/access-list").status_code == 403
        assert client.post("/teams/rocket/manage/access-list/send").status_code == 403

    def test_the_lead_sets_it_up(self, app, client):
        team, lead = _led()
        _login(client, lead.id)

        client.post("/teams/rocket/manage/settings", data={
            "access_list_recipients": "office@uni.example",
            "access_list_dates": "15.10",
            "access_list_auto_send": "on",
        })

        db.session.refresh(team)
        assert (team.access_list_recipients, team.access_list_dates, team.access_list_auto_send) == (
            "office@uni.example", "15.10", True,
        )

    def test_a_mistake_is_said_and_nothing_is_saved(self, app, client):
        team, lead = _led()
        _login(client, lead.id)

        response = client.post("/teams/rocket/manage/settings", data={
            "description": "Changed", "access_list_dates": "31.02",
        }, follow_redirects=True)

        assert "Not a day of the year: 31.02" in response.get_data(as_text=True)
        db.session.refresh(team)
        assert team.description is None


@pytest.mark.usefixtures("outbox", "switched_on")
class TestComparedWithTheLastList:
    """A help for whoever gives access, not the truth about who has it."""

    def _text(self):
        return _parts(FakeSMTP.sent[-1], "text/plain")[0].get_payload(decode=True).decode()

    def test_the_first_list_marks_nobody(self, app):
        team, lead = _led()
        _with_members(_configured(team))

        teams.send_access_list(lead, team, today=date(2026, 10, 15))

        text = self._text()
        assert "NEW" not in text and "Compared" not in text and "No longer" not in text

    def test_the_next_marks_who_is_new_and_who_left(self, app):
        from aeronautics_members.db_models import TeamAccessListSend

        team, lead = _led()
        _with_members(_configured(team))
        teams.send_access_list(lead, team, today=date(2026, 10, 15))
        anna = db.session.query(TeamMembership).join(TeamMembership.user).filter_by(email="anna@example.com").one().user
        teams.leave(anna, team)
        _in_team(_person("cara@example.com", "Cara", "Cole", email_work="cara@edu.example"), team)

        teams.send_access_list(lead, team, today=date(2027, 3, 15))

        text = self._text()
        new_line = next(line for line in text.splitlines() if "Cara Cole" in line)
        assert "NEW" in new_line
        assert "NEW" not in next(line for line in text.splitlines() if "Ben Adler" in line)
        assert "New: not on our list of 15.10.2026." in text
        gone = text[text.index("No longer in the team"):]
        assert "Anna Berger" in gone and "anna@edu.example" in gone
        assert db.session.query(TeamAccessListSend).count() == 1, "only the newest is kept"

    def test_somebody_who_erased_their_account_shows_once(self, app):
        team, lead = _led()
        _configured(team)
        anna = _person("anna@example.com", "Anna", "Berger", email_work="anna@edu.example")
        _in_team(anna, team)
        teams.send_access_list(lead, team, today=date(2026, 10, 15))

        privacy.erase_account(anna, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()

        teams.send_access_list(lead, team, today=date(2027, 3, 15))
        assert "anna@edu.example" in self._text()[self._text().index("No longer in the team"):]

        teams.send_access_list(lead, team, today=date(2027, 10, 15))
        assert "anna@edu.example" not in self._text()

    def test_the_preview_shows_the_same(self, app, client):
        team, lead = _led()
        _with_members(_configured(team))
        teams.send_access_list(lead, team, today=date(2026, 10, 15))
        _in_team(_person("cara@example.com", "Cara", "Cole"), team)
        db.session.commit()
        _login(client, lead.id)

        body = client.get("/teams/rocket/manage/access-list").get_data(as_text=True)

        assert "New: not on our list of 15.10.2026." in body
        assert body.count('status-label status-info">New<') == 1
