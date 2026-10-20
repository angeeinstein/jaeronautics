# ruff: noqa: F811 -- the fixtures imported below are taken as arguments
"""Answering contact messages from the portal (services/messages.py ``reply``,
docs/messages-plan.md, "Answering from the portal"): written in Admin ›
Messages, emailed to the sender from the mail account chosen for replies --
with, if chosen, a blind copy to that account's own address -- then done.
Only the contact form's: a message to a team's leads is answered by email.
"""
import pytest

from conftest import db
from aeronautics_members.db_models import AuditLog, ContactReply, MailAccount
from aeronautics_members.services import messages, settings_sections
from aeronautics_members.services.privacy import erase_account, export_account_data
from api_helpers import send
from test_messages import FORM, _verified, admins  # noqa: F401
from test_teams_flow import _led, _login, switched_on  # noqa: F401
from test_teams_foundation import _association_member


@pytest.fixture
def accounts(app):
    db.session.add_all([
        MailAccount(account_key="office", host="smtp.example.org", port=465, username="office@example.org",
                    password="x"),
        MailAccount(account_key="support", host="smtp.example.org", port=465, username="u-123",
                    password="x", from_email="support@example.org", from_name="Joanneum Aeronautics"),
    ])
    db.session.commit()


@pytest.fixture
def mail(monkeypatch):
    """Every email sent (the arguments), and a switch to make sending fail."""
    class Sent(list):
        state = {"fail": False}

    sent = Sent()
    state = sent.state

    def send_mail(**kwargs):
        if state["fail"]:
            return False, "SMTP said no"
        sent.append(kwargs)
        return True, None

    monkeypatch.setattr(messages, "send_mail", send_mail)
    return sent


@pytest.fixture
def written(app, admins, accounts, mail):  # noqa: F811
    """Anna's message through the contact form, its email to the admins already out."""
    anna = _association_member("anna@example.com")
    message = messages.send_contact(user=anna, topic=FORM["topic"], subject=FORM["subject"], body=FORM["message"],
                                    seconds=FORM["seconds"])
    mail.clear()
    return message


def _reply_settings(sender="support", copy=False):
    settings_sections.save_senders(None, default="", welcome="", notifications="", mailings="office",
                                   replies=sender, reply_copy=copy)
    db.session.commit()


class TestAnswering:
    def test_an_admin_answers_from_the_support_account_and_it_is_done(self, client, admins, written, mail):  # noqa: F811
        _reply_settings()
        _login(client, admins[0].id)

        response = send(client, "POST", f"/api/v1/admin/messages/{written.id}/replies",
                        {"body": "It was paid on **Monday**."})

        assert response.status_code == 201, response.get_json()
        [email] = mail
        assert (email["from_account"], email["to_email"], email["subject"]) == (
            "support", "anna@example.com", "Re: My fee")
        assert "<strong>Monday</strong>" in str(email["body_html"])
        [quoted] = email["quoted"]
        assert quoted["text"] == "Was it paid?" and "wrote:" in quoted["intro"]
        assert email.get("bcc_emails") is None and email.get("reply_to") is None
        out = response.get_json()
        assert out["done_at"] is not None and out["can_reply"] is True
        [answer] = out["replies"]
        assert answer["body"] == "It was paid on **Monday**." and answer["sent_at"] is not None
        assert answer["author"] is not None
        assert db.session.query(AuditLog).filter_by(event_type="message_replied").count() == 1

    def test_the_copy_goes_blind_to_the_accounts_own_address(self, client, admins, written, mail):  # noqa: F811
        _reply_settings(copy=True)
        _login(client, admins[0].id)

        send(client, "POST", f"/api/v1/admin/messages/{written.id}/replies", {"body": "Yes."})

        assert mail[0]["bcc_emails"] == ["support@example.org"]

    def test_without_a_reply_account_the_mailings_one_sends(self, client, admins, written, mail):  # noqa: F811
        _reply_settings(sender="")
        _login(client, admins[0].id)

        send(client, "POST", f"/api/v1/admin/messages/{written.id}/replies", {"body": "Yes."})

        assert mail[0]["from_account"] == "office"
        listed = client.get("/api/v1/admin/messages?state=all").get_json()
        assert listed["reply_from"] == "office@example.org"

    def test_an_answer_that_could_not_go_out_is_kept_and_sent_later(self, client, admins, written, mail):  # noqa: F811
        _reply_settings()
        _login(client, admins[0].id)
        mail.state["fail"] = True

        out = send(client, "POST", f"/api/v1/admin/messages/{written.id}/replies", {"body": "Yes."}).get_json()

        assert out["replies"][0]["sent_at"] is None and out["replies"][0]["error"] == "SMTP said no"
        mail.state["fail"] = False
        assert messages.deliver_waiting() == 1
        assert db.session.query(ContactReply).one().sent_at is not None
        assert len(mail) == 1

    def test_a_waiting_answer_whose_account_was_removed_goes_from_the_one_answering_now(
            self, client, admins, written, mail):  # noqa: F811
        """Found in review (PR #39): retried with the removed account's name, it
        failed every time, and the sender never got the answer."""
        _reply_settings("support")
        _login(client, admins[0].id)
        mail.state["fail"] = True
        send(client, "POST", f"/api/v1/admin/messages/{written.id}/replies", {"body": "Yes."})
        support = db.session.query(MailAccount).filter_by(account_key="support").one()
        _login(client, admins[0].id)
        db.session.delete(support)
        db.session.commit()
        mail.state["fail"] = False

        assert messages.deliver_waiting() == 1

        assert mail[-1]["from_account"] == "office"
        assert db.session.query(ContactReply).one().account_key == "office"

    def test_an_empty_answer_is_refused(self, client, admins, written, mail):  # noqa: F811
        _login(client, admins[0].id)
        response = send(client, "POST", f"/api/v1/admin/messages/{written.id}/replies", {"body": "  "})
        assert response.status_code == 400 and not mail

    def test_a_message_to_a_teams_leads_is_not_answered_here(self, app, admins, accounts, mail):  # noqa: F811
        team, _lead = _led()
        message = messages.send_to_team(user=_association_member("anna@example.com"), team=team,
                                        subject="Hi", body="Hello", seconds=10)
        mail.clear()
        with pytest.raises(messages.ValidationError):
            messages.reply(admins[0], message, "Hello back")
        assert not mail

    def test_only_who_receives_the_messages_answers(self, client, written, mail):
        _login(client, _verified(_association_member("bob@example.com")).id)
        response = send(client, "POST", f"/api/v1/admin/messages/{written.id}/replies", {"body": "Yes."})
        assert response.status_code == 403 and not mail


class TestTheirData:
    def test_answers_are_in_the_export_and_go_with_the_message(self, client, admins, written, mail):  # noqa: F811
        _reply_settings()
        messages.reply(admins[0], written, "It was paid.")
        anna = written.sender

        exported = export_account_data(anna)
        [sent] = exported["messages"]
        assert [answer["answer"] for answer in sent["answers"]] == ["It was paid."]

        erase_account(anna, actor_user=admins[1])
        db.session.commit()
        assert db.session.query(ContactReply).count() == 0


class TestTheConversationQuoted:
    """The email of an answer quotes the conversation before it, newest first,
    the message it began with always last (docs/messages-plan.md)."""

    def _reply_in(self, message, text):
        db.session.add(ContactReply(message=message, incoming=True, body=text, account_key="support",
                                    email_message_id=f"<in-{len(message.replies)}@example.com>"))
        db.session.commit()

    def test_newest_first_and_the_message_last(self, client, admins, written, mail):
        _reply_settings()
        messages.reply(admins[0], written, "First answer.")
        self._reply_in(written, "Thanks!\n\nOn Sun, 18 Oct 2026 at 16:23, Support <support@example.org> wrote:\n"
                                "> First answer.")
        mail.clear()

        messages.reply(admins[0], written, "Second answer.")

        quoted = mail[0]["quoted"]
        assert [step["text"] for step in quoted] == ["Thanks!", "First answer.", "Was it paid?"]
        assert quoted[1]["intro"].endswith("Joanneum Aeronautics wrote:")
        assert "wrote:" in quoted[0]["intro"] and "wrote:" in quoted[2]["intro"]

    def test_a_long_one_is_cut_but_keeps_its_first_message(self, client, admins, written, mail):
        _reply_settings()
        for number in range(messages.QUOTED_MOST + 2):
            messages.reply(admins[0], written, f"Answer {number}.")
        mail.clear()

        messages.reply(admins[0], written, "The last.")

        quoted = mail[0]["quoted"]
        assert len(quoted) == messages.QUOTED_MOST + 2
        assert quoted[0]["text"] == f"Answer {messages.QUOTED_MOST + 1}."
        assert quoted[-2] == {"intro": "2 earlier messages left out.", "text": ""}
        assert quoted[-1]["text"] == "Was it paid?"

    def test_their_reply_is_shown_without_what_it_quotes(self, client, admins, written, mail):
        _reply_settings()
        messages.reply(admins[0], written, "Paid on Monday.")
        self._reply_in(written, "Thank you!\n\nSupport <support@example.org> schrieb am So., 18. Okt. 2026, 16:23:\n"
                                "[Joanneum Aeronautics]\n\nPaid on Monday.")
        _login(client, admins[0].id)

        [shown] = [message for message in client.get("/api/v1/admin/messages?state=all").get_json()["items"]
                   if message["id"] == written.id]
        reply = shown["replies"][-1]

        assert reply["body"] == "Thank you!"
        assert reply["quoted"].startswith("Support <support@example.org> schrieb am")
        assert db.session.query(ContactReply).filter_by(incoming=True).one().body.endswith("Paid on Monday.")


@pytest.mark.parametrize(("text", "written"), [
    # Gmail, English, its "On ... wrote:" wrapped over two lines.
    ("Thanks!\n\nOn Sun, 18 Oct 2026 at 16:23, Support Team of the Association <support@example.org>\nwrote:\n> Hi",
     "Thanks!"),
    # Gmail, German.
    ("Danke!\n\nAm So., 18. Okt. 2026 um 16:23 Uhr schrieb Support <support@example.org>:\n> Hallo", "Danke!"),
    ("Danke!\n\nSupport <support@example.org> schrieb am So., 18. Okt. 2026, 16:23:\nHallo", "Danke!"),
    # Outlook.
    ("Thanks\n\nFrom: Support <support@example.org>\nSent: Sunday, 18 October 2026 16:23\nTo: Anna", "Thanks"),
    ("Thanks\n\n________________________________\nVon: Support", "Thanks"),
    ("Thanks\n\n-----Original Message-----\nFrom: Support", "Thanks"),
    # A quote at the end, line by line.
    ("Thanks\n\n> Hello\n> there", "Thanks"),
])
def test_where_a_mail_program_starts_quoting(text, written):
    assert messages.split_quote(text)[0] == written


@pytest.mark.parametrize("text", [
    "Just a reply, nothing quoted.",
    # Answered between the quoted lines: all of it is theirs.
    "> Was it paid?\nYes, on Monday.\n> And the receipt?\nAttached.",
    # Nothing written above the quote: kept whole rather than shown empty.
    "On Sun, 18 Oct 2026 at 16:23, Support <support@example.org> wrote:\n> Hi",
    # A sentence that only mentions someone writing.
    "From: my side, all good.\nThanks",
])
def test_left_whole(text):
    assert messages.split_quote(text) == (text, None)
