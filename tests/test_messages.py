# ruff: noqa: F811 -- the fixture imported below is taken as an argument
"""Messages from one person (services/messages.py, api/messages.py,
docs/messages-plan.md): the contact form, to the admins and super admins --
not the treasurer -- and a message to a team's leads; kept, marked done,
forgotten a year after.
"""
from datetime import timedelta

import pytest

from conftest import db, make_member
from aeronautics_members.db_models import ContactMessage
from aeronautics_members.services import messages
from aeronautics_members.services.clock import get_now_utc
from aeronautics_members.services.privacy import erase_account, export_account_data
from api_helpers import signed_in
from test_admin_reviews import _staff
from test_teams_flow import _led, _login, switched_on  # noqa: F401 -- a fixture
from test_teams_foundation import _association_member

CONTACT = "/api/v1/contact"
FORM = {"topic": "membership", "subject": "My fee", "message": "Was it paid?", "seconds": 12}


@pytest.fixture
def mail(monkeypatch):
    """Every email the messages send: (to, subject, reply_to, variables)."""
    sent = []

    def send_mail(**kwargs):
        sent.append(kwargs)
        return True, None

    monkeypatch.setattr(messages, "send_mail", send_mail)
    monkeypatch.setattr(messages, "_sender_account", lambda: "office")
    return sent


def _verified(user):
    user.email_verified_at = get_now_utc()
    db.session.commit()
    return user


@pytest.fixture
def admins(app):
    return [_verified(_staff("admin@example.org", "admin")), _verified(_staff("root@example.org", "superadmin")),
            _verified(_staff("money@example.org", "treasurer"))]


class TestTheContactForm:
    def test_a_visitor_writes_to_the_admins_not_the_treasurer(self, client, admins, mail):
        response = client.post(CONTACT, json={**FORM, "name": "Vera Visitor", "email": "vera@example.net"})

        assert response.status_code == 200, response.get_json()
        assert sorted(m["to_email"] for m in mail) == ["admin@example.org", "root@example.org"]
        assert {m["reply_to"] for m in mail} == {"vera@example.net"}
        message = db.session.query(ContactMessage).one()
        assert (message.sender_name, message.topic, message.delivered_at is not None) == (
            "Vera Visitor", "membership", True)

    def test_signed_in_writes_as_the_account(self, client, admins, mail):
        user = _association_member("anna@example.com")
        signed_in(client, user)

        client.post(CONTACT, json={**FORM, "name": "Somebody Else", "email": "x@example.net", "page": "/account"})

        message = db.session.query(ContactMessage).one()
        assert message.sender_email == "anna@example.com" and message.sender_user_id == user.id
        assert message.context["account_id"] == user.id and message.context["membership"] == "active"
        assert message.context["page"] == "/account"

    def test_the_form_says_who_is_writing(self, client, app):
        assert client.get(CONTACT).get_json()["signed_in"] is False
        _login(client, _association_member("anna@example.com").id)
        form = client.get(CONTACT).get_json()
        assert form["email"] == "anna@example.com" and len(form["topics"]) == 5

    @pytest.mark.parametrize("change, field", [
        ({"topic": "nonsense"}, "topic"),
        ({"email": "not an address"}, "email"),
        ({"subject": "  "}, "subject"),
        ({"message": ""}, "message"),
        ({"name": ""}, "name"),
    ])
    def test_what_is_missing_is_said_by_field(self, client, admins, mail, change, field):
        response = client.post(CONTACT, json={**FORM, "name": "Vera", "email": "vera@example.net", **change})
        assert response.status_code == 400
        assert field in response.get_json()["error"]["fields"]

    @pytest.mark.parametrize("change", [{"website": "http://spam.example"}, {"seconds": 0.5}])
    def test_a_bot_is_refused(self, client, admins, mail, change):
        response = client.post(CONTACT, json={**FORM, "name": "Bot", "email": "bot@example.net", **change})
        assert response.status_code == 400 and not mail
        assert db.session.query(ContactMessage).count() == 0

    def test_an_email_that_failed_is_tried_again(self, client, admins, monkeypatch):
        monkeypatch.setattr(messages, "_sender_account", lambda: "office")
        monkeypatch.setattr(messages, "send_mail", lambda **kwargs: (False, "server down"))
        client.post(CONTACT, json={**FORM, "name": "Vera", "email": "vera@example.net"})
        message = db.session.query(ContactMessage).one()
        assert message.delivered_at is None and message.delivery_error == "server down"

        monkeypatch.setattr(messages, "send_mail", lambda **kwargs: (True, None))
        assert messages.deliver_waiting() == 1
        assert message.delivered_at is not None


class TestAdminMessages:
    def test_the_admins_read_and_mark_them_done(self, client, admins, mail):
        client.post(CONTACT, json={**FORM, "name": "Vera", "email": "vera@example.net"})
        _login(client, admins[0].id)

        listed = client.get("/api/v1/admin/messages").get_json()
        assert listed["open_count"] == 1 and listed["items"][0]["subject"] == "My fee"
        message_id = listed["items"][0]["id"]

        done = client.put(f"/api/v1/admin/messages/{message_id}", json={"done": True}).get_json()
        assert done["done_at"] and done["done_by"]
        assert client.get("/api/v1/admin/messages").get_json()["items"] == []
        assert len(client.get("/api/v1/admin/messages?state=done").get_json()["items"]) == 1

    def test_not_for_the_treasurer(self, client, admins):
        _login(client, admins[2].id)
        assert client.get("/api/v1/admin/messages").status_code == 403

    def test_a_message_done_a_year_ago_is_forgotten(self, app, admins, mail):
        old = messages.send_contact(user=None, name="Old", email="old@example.net", topic="other",
                                    subject="Then", body="Long ago", seconds=10)
        fresh = messages.send_contact(user=None, name="New", email="new@example.net", topic="other",
                                      subject="Now", body="Today", seconds=10)
        messages.mark_done(admins[0], old)
        old.done_at = get_now_utc() - timedelta(days=366)
        db.session.commit()

        assert messages.forget_old() == 1
        db.session.commit()
        assert db.session.query(ContactMessage).all() == [fresh]


class TestTeamMessages:
    def test_a_member_writes_to_the_leads(self, client, switched_on, admins, mail):
        team, lead = _led()
        lead.email_verified_at = get_now_utc()
        writer = _association_member("anna@example.com")
        _login(client, writer.id)

        response = client.post(f"/api/v1/teams/{team.slug}/message",
                               json={"subject": "Joining", "message": "Can I come on Monday?", "seconds": 8})

        assert response.status_code == 200, response.get_json()
        assert [m["to_email"] for m in mail] == [lead.email]
        assert mail[0]["reply_to"] == "anna@example.com" and mail[0]["subject"].startswith(f"[{team.name}]")

        _login(client, lead.id)
        listed = client.get(f"/api/v1/teams/{team.slug}/manage/messages").get_json()
        assert [item["subject"] for item in listed["items"]] == ["Joining"]
        _login(client, admins[0].id)
        assert client.get("/api/v1/admin/messages").get_json()["items"] == [], "the admins' inbox is their own"

    def test_without_a_lead_it_goes_to_the_admins(self, client, switched_on, admins, mail):
        from test_teams_foundation import _team

        team = _team("Glider Team")
        _login(client, _association_member("anna@example.com").id)
        client.post(f"/api/v1/teams/{team.slug}/message", json={"subject": "Hi", "message": "Hello", "seconds": 8})
        assert sorted(m["to_email"] for m in mail) == ["admin@example.org", "root@example.org"]

    def test_only_signed_in(self, client, switched_on):
        team, _lead = _led()
        response = client.post(f"/api/v1/teams/{team.slug}/message", json={"subject": "Hi", "message": "x"})
        assert response.status_code == 401

    def test_a_member_of_the_team_may_not_read_them(self, client, switched_on, mail):
        team, _lead = _led()
        _login(client, _association_member("anna@example.com").id)
        assert client.get(f"/api/v1/teams/{team.slug}/manage/messages").status_code == 403


class TestPrivacy:
    def test_the_export_has_what_somebody_wrote_and_erasure_takes_it(self, app, admins, mail):
        user = make_member(email="anna@example.com").user
        messages.send_contact(user=user, topic="account", subject="Forum", body="Cannot sign in", seconds=10)

        exported = export_account_data(user)
        assert exported["messages"][0]["subject"] == "Forum"
        assert exported["news_by_email"]["subscribed"] is True

        erase_account(user, actor_user=admins[1])
        db.session.commit()
        assert db.session.query(ContactMessage).count() == 0
