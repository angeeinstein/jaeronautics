# ruff: noqa: F811 -- the fixture imported below is taken as an argument
"""Emails to many members (services/mailings.py, api/mailings.py,
docs/messages-plan.md): only active, paying members; news can be switched
off, notices and team mailings cannot; the general assembly's two weeks; a
paced queue; unsubscribing and subscribing again.
"""
from datetime import timedelta

import pytest

from conftest import db
from aeronautics_members.db_models import Mailing, MailingRecipient, TeamMembership
from aeronautics_members.services import mailings
from aeronautics_members.services.clock import get_now_utc
from aeronautics_members.services.teams import APPLIED
from test_admin_reviews import _staff
from test_teams_flow import _led, _login, switched_on  # noqa: F401 -- a fixture
from test_teams_foundation import _association_member, _in_team

API = "/api/v1/admin/announcements"
ALL = {"scope": "all", "kinds": [], "teams": []}


@pytest.fixture
def outbox(monkeypatch):
    """Every mailing email: the keyword arguments send_mail was called with."""
    sent = []

    def send_mail(**kwargs):
        sent.append(kwargs)
        return True, None

    monkeypatch.setattr(mailings, "send_mail", send_mail)
    monkeypatch.setattr(mailings, "sender_account", lambda: "office")
    return sent


@pytest.fixture
def admin(app):
    return _staff("admin@example.org", "admin")


@pytest.fixture
def people(app):
    """Two active members, one whose membership ended, one unpaid signup."""
    anna = _association_member("anna@example.com")
    ben = _association_member("ben@example.com")
    ben.member.member_category = "alumni"
    _association_member("ended@example.com", active=False)
    from conftest import make_member

    make_member(email="unpaid@example.com")
    db.session.commit()
    return anna, ben


def _addresses(mailing):
    return sorted(recipient.email for recipient in mailing.recipients)


class TestWho:
    def test_only_active_paying_members(self, admin, people, outbox):
        mailing = mailings.announce(admin, kind="news", audience=ALL, subject="Hello", body="News")
        assert _addresses(mailing) == ["anna@example.com", "ben@example.com"]

    def test_some_kinds_of_member(self, admin, people, outbox):
        mailing = mailings.announce(admin, kind="news", audience={"scope": "kinds", "kinds": ["alumni"]},
                                    subject="Alumni", body="Hi")
        assert _addresses(mailing) == ["ben@example.com"]

    def test_teams_mean_their_active_members_not_applicants(self, switched_on, admin, people, outbox):
        team, lead = _led()
        anna, ben = people
        _in_team(anna, team)
        db.session.add(TeamMembership(team=team, user=ben, status=APPLIED))
        db.session.commit()
        mailing = mailings.announce(admin, kind="news", audience={"scope": "teams", "teams": [team.id]},
                                    subject="Team", body="Hi")
        assert _addresses(mailing) == sorted(["anna@example.com", lead.email])

    def test_all_team_leads(self, switched_on, admin, people, outbox):
        _team, lead = _led()
        mailing = mailings.announce(admin, kind="notice", audience={"scope": "leads"}, subject="Leads", body="Hi")
        assert _addresses(mailing) == [lead.email]

    def test_news_leaves_out_who_switched_it_off_a_notice_does_not(self, admin, people, outbox):
        anna, _ben = people
        mailings.set_news(anna, False, how="test")
        news = mailings.announce(admin, kind="news", audience=ALL, subject="News", body="x")
        notice = mailings.announce(admin, kind="notice", audience=ALL, subject="Notice", body="x")
        assert _addresses(news) == ["ben@example.com"] and news.unsubscribed_count == 1
        assert _addresses(notice) == ["anna@example.com", "ben@example.com"]

    def test_the_count_before_sending(self, client, admin, people):
        mailings.set_news(people[0], False, how="test")
        _login(client, admin.id)
        counted = client.post(f"{API}/count", json={"kind": "news", "audience": ALL}).get_json()
        assert counted == {"recipients": 1, "unsubscribed": 1}


class TestTheGeneralAssembly:
    def test_two_weeks_ahead(self, admin, people, outbox):
        soon = get_now_utc() + timedelta(days=10)
        with pytest.raises(mailings.ValidationError) as refused:
            mailings.announce(admin, kind="notice", audience=ALL, subject="GA", body="Agenda", assembly_at=soon)
        assert refused.value.code == "too_late"

        mailing = mailings.announce(admin, kind="notice", audience=ALL, subject="GA", body="Agenda",
                                    assembly_at=soon, late_ok=True)
        assert mailing.assembly_at is not None

    def test_it_is_a_notice_to_everybody(self, admin, people, outbox):
        later = get_now_utc() + timedelta(days=20)
        for kind, audience in (("news", ALL), ("notice", {"scope": "kinds", "kinds": ["alumni"]})):
            with pytest.raises(mailings.ValidationError):
                mailings.announce(admin, kind=kind, audience=audience, subject="GA", body="x", assembly_at=later)


class TestTheQueue:
    def test_sent_by_the_timer_within_the_limits(self, admin, people, outbox, monkeypatch):
        monkeypatch.setattr(mailings, "settings", lambda: {"sender": "", "per_hour": 1, "per_day": 300})
        mailing = mailings.announce(admin, kind="news", audience=ALL, subject="Hello", body="**Bold**")

        assert mailings.process() == 1, "one an hour"
        assert mailings.process() == 0
        assert mailing.status == "sending"
        monkeypatch.setattr(mailings, "settings", lambda: {"sender": "", "per_hour": 100, "per_day": 300})
        assert mailings.process() == 1
        assert mailing.status == "sent" and mailings.progress(mailing)["sent"] == 2
        assert "<strong>Bold</strong>" in str(outbox[0]["body_html"])

    def test_news_carries_the_way_out_a_notice_does_not(self, admin, people, outbox):
        mailings.announce(admin, kind="news", audience=ALL, subject="News", body="x")
        mailings.process()
        news = outbox[-1]
        assert news["unsubscribe_url"] and "/unsubscribe/" in news["unsubscribe_url"]
        assert news["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

        outbox.clear()
        mailings.announce(admin, kind="notice", audience=ALL, subject="Notice", body="x")
        mailings.process()
        assert outbox[-1]["unsubscribe_url"] is None and outbox[-1]["headers"] == {}

    def test_a_failure_is_tried_again_then_given_up(self, admin, people, monkeypatch):
        monkeypatch.setattr(mailings, "sender_account", lambda: "office")
        monkeypatch.setattr(mailings, "send_mail", lambda **kwargs: (False, "mailbox full"))
        mailing = mailings.announce(admin, kind="news", audience={"scope": "kinds", "kinds": ["alumni"]},
                                    subject="x", body="x")
        for _ in range(3):
            mailings.process()
        assert mailings.progress(mailing)["failed"] == 1 and mailing.status == "sent"
        assert mailings.failed_addresses(mailing) == [("ben@example.com", "mailbox full")]

    def test_stopped(self, admin, people, outbox):
        mailing = mailings.announce(admin, kind="news", audience=ALL, subject="Oops", body="x")
        mailings.stop(admin, mailing)
        assert mailings.process() == 0 and mailing.status == "stopped"
        assert mailings.progress(mailing)["stopped"] == 2

    def test_recipients_are_forgotten_after_a_year_the_counts_stay(self, admin, people, outbox):
        mailing = mailings.announce(admin, kind="news", audience=ALL, subject="Old", body="x")
        mailings.process()
        mailing.created_at = get_now_utc() - timedelta(days=400)
        db.session.commit()

        assert mailings.forget_old() == 2
        db.session.commit()
        assert db.session.query(MailingRecipient).count() == 0
        assert mailings.progress(mailing)["sent"] == 2


class TestUnsubscribing:
    def test_from_the_emails_link_and_back(self, client, people):
        anna, _ben = people
        token = mailings.unsubscribe_token(anna)

        assert client.get(f"/api/v1/news/{token}").get_json()["subscribed"] is True
        assert client.put(f"/api/v1/news/{token}", json={"subscribed": False}).get_json()["subscribed"] is False
        assert anna.news_unsubscribed_at is not None
        assert client.put(f"/api/v1/news/{token}", json={"subscribed": True}).get_json()["subscribed"] is True

    def test_one_click_from_the_mail_program(self, client, people):
        anna, _ben = people
        response = client.post(f"/api/v1/news/unsubscribe/{mailings.unsubscribe_token(anna)}",
                               data="List-Unsubscribe=One-Click", content_type="application/x-www-form-urlencoded")
        assert response.status_code == 204 and anna.news_unsubscribed_at is not None

    def test_a_bad_link(self, client, app):
        assert client.get("/api/v1/news/not-a-token").status_code == 404

    def test_in_my_account(self, client, people):
        anna, _ben = people
        _login(client, anna.id)
        assert client.put("/api/v1/account/news", json={"subscribed": False}).get_json()["subscribed"] is False
        assert client.get("/api/v1/account/news").get_json()["subscribed"] is False


class TestTeamMailings:
    def test_a_lead_writes_to_the_teams_active_members(self, client, switched_on, people, outbox):
        team, lead = _led()
        anna, ben = people
        _in_team(anna, team)
        db.session.add(TeamMembership(team=team, user=ben, status=APPLIED))
        mailings.set_news(anna, False, how="test")
        db.session.commit()
        _login(client, lead.id)

        response = client.post(f"/api/v1/teams/{team.slug}/manage/mailings",
                               json={"subject": "Meeting", "body": "Monday 18:00"})

        assert response.status_code == 201, response.get_json()
        mailing = db.session.query(Mailing).one()
        assert _addresses(mailing) == sorted(["anna@example.com", lead.email]), "news switched off is no matter"
        mailings.process()
        assert outbox[0]["reply_to"] == lead.email
        assert outbox[0]["from_name"] == f"{team.name} via Joanneum Aeronautics"

    def test_a_member_may_not(self, client, switched_on, people):
        team, _lead = _led()
        _in_team(people[0], team)
        _login(client, people[0].id)
        response = client.post(f"/api/v1/teams/{team.slug}/manage/mailings", json={"subject": "x", "body": "y"})
        assert response.status_code == 403


class TestSettings:
    def test_limits_and_sender_checked(self, client, admin, monkeypatch):
        monkeypatch.setattr(mailings, "load_mail_accounts_config", lambda: {"office": {}, "news": {}})
        _login(client, admin.id)
        saved = client.put("/api/v1/admin/settings/mailings", json={"sender": "news", "per_hour": 50, "per_day": 200})
        assert saved.status_code == 200, saved.get_json()
        assert mailings.settings() == {"sender": "news", "per_hour": 50, "per_day": 200}
        bad = client.put("/api/v1/admin/settings/mailings", json={"sender": "nope", "per_hour": 50, "per_day": 200})
        assert "sender" in bad.get_json()["error"]["fields"]
        bad = client.put("/api/v1/admin/settings/mailings", json={"sender": "", "per_hour": 500, "per_day": 200})
        assert "per_hour" in bad.get_json()["error"]["fields"]

    def test_announcements_need_the_permission(self, client, app):
        _login(client, _staff("money@example.org", "treasurer").id)
        assert client.get(API).status_code == 403


class TestTheEmailsAsSent:
    """Through the real send_mail and templates, to a stand-in mail server."""

    def test_a_news_email(self, admin, people, monkeypatch):
        from test_emails import FakeSMTP, _parts

        from aeronautics_members import mail_utils

        FakeSMTP.sent = []
        monkeypatch.setattr(mail_utils, "load_mail_accounts_config",
                            lambda required=False: {"office": {"host": "h", "port": 465, "user": "office@example.org",
                                                               "pass": "p"}})
        monkeypatch.setattr(mail_utils.smtplib, "SMTP_SSL", FakeSMTP)
        monkeypatch.setattr(mailings, "sender_account", lambda: "office")
        mailings.announce(admin, kind="news", audience={"scope": "kinds", "kinds": ["alumni"]},
                          subject="Summer party", body="We meet on **Friday**.\n\n- Food\n- Drinks")
        mailings.process()

        message = FakeSMTP.sent[-1]
        html = _parts(message, "text/html")[0].get_payload(decode=True).decode()
        assert "<strong>Friday</strong>" in html and "<li>Food</li>" in html
        assert "No more news by email" in html and "/unsubscribe/" in html
        assert message["List-Unsubscribe"].startswith("<http") and message["To"] == "ben@example.com"
        text = _parts(message, "text/plain")[0].get_payload(decode=True).decode()
        assert "Friday" in text

    def test_raw_html_in_the_text_stays_text(self):
        assert "<script>" not in str(mailings.render_body("<script>alert(1)</script>"))
