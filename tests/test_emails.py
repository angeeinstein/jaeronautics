"""What leaves the portal by email, sent through a fake SMTP server.

Found by rendering every email the portal sends: three of them had never been
sent at all, the admin digests and member notifications carried a logo that
could not be seen on their black header, and every email went out as HTML
only.
"""
import email
import types
from pathlib import Path

import pytest

from conftest import app_module, db, make_member
from aeronautics_members import mail_utils
from aeronautics_members.db_models import MailAccount, NotificationEvent, Setting

STATIC = Path(__file__).resolve().parent.parent / "aeronautics_members" / "static"
LIGHT_LOGO = (STATIC / "logo_joanneum_aeronautics_negativ.png").read_bytes()


class FakeSMTP:
    sent = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, *a):
        pass

    def starttls(self, **k):
        pass

    def sendmail(self, sender, recipients, raw):
        FakeSMTP.sent.append(email.message_from_string(raw))


@pytest.fixture
def outbox(app, monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(
        mail_utils, "load_mail_accounts_config",
        lambda required=False: {"office": {"host": "h", "port": 465, "user": "office@example.org", "pass": "p"}},
    )
    monkeypatch.setattr(mail_utils.smtplib, "SMTP_SSL", FakeSMTP)
    db.session.add(Setting(key="welcome_email_sender", value="office"))
    db.session.add(MailAccount(account_key="office", host="h", port=465, username="u", password="p"))
    db.session.commit()
    return FakeSMTP.sent


def _parts(message, content_type):
    return [part for part in message.walk() if part.get_content_type() == content_type]


def _user_status_event(event_type, **payload):
    return NotificationEvent(
        channel="user_status", audience="user", event_type=event_type, summary="x",
        payload={"first_name": "Anna", **payload}, recipient_email="anna@example.com",
    )


MEMBER_NOTIFICATIONS = [
    "forum_avatar_approved",
    "forum_avatar_rejected",
    "identity_request_approved",
    "identity_request_rejected",
]


class TestMemberNotificationsAreSent:
    @pytest.mark.parametrize("event_type", MEMBER_NOTIFICATIONS)
    def test_it_goes_out(self, app, outbox, event_type):
        """Three of these could not be rendered -- the footer's year had no
        date to come from -- so nobody was ever told their picture was rejected."""
        from aeronautics_members.services.notifications import get_notification_service

        service = get_notification_service()
        event = _user_status_event(event_type)
        with app.test_request_context():
            subject, template_vars = service._build_user_status_message(event)
            ok, error = service._send_user_status_mail(event, subject, template_vars)

        assert (ok, error) == (True, None)
        assert "Hello Anna," in _parts(outbox[-1], "text/plain")[0].get_payload(decode=True).decode()

    def test_a_rejection_without_a_note_does_not_refer_to_one(self, app):
        from aeronautics_members.services.notifications import get_notification_service

        with app.test_request_context():
            _subject, template_vars = get_notification_service()._build_user_status_message(
                _user_status_event("identity_request_rejected")
            )
        text = " ".join(line for line in template_vars["body_lines"] if line)
        assert "note" not in text.lower()

    def test_the_reason_is_given_when_there_is_one(self, app):
        from aeronautics_members.services.notifications import get_notification_service

        with app.test_request_context():
            _subject, template_vars = get_notification_service()._build_user_status_message(
                _user_status_event("forum_avatar_rejected", review_note="Please show your face.")
            )
        assert "Reason: Please show your face." in template_vars["body_lines"]


class TestEveryEmail:
    def test_carries_the_logo_that_can_be_seen_on_black(self, app, outbox):
        """The digests used the signature logo: dark lettering on a black header."""
        from datetime import datetime, timezone

        from aeronautics_members.notification_service import ADMIN_ERROR_CHANNEL
        from aeronautics_members.services.notifications import get_notification_service

        service = get_notification_service()
        event = NotificationEvent(
            channel=ADMIN_ERROR_CHANNEL, audience="admins", severity="warning",
            event_type="something", summary="Something happened.",
            payload={"what_to_do": "Do this about it."}, queued_at=datetime.now(timezone.utc),
        )
        with app.test_request_context():
            subject, template_vars = service._build_admin_digest_message(
                ADMIN_ERROR_CHANNEL, [event], datetime.now(timezone.utc)
            )
            ok, _error = service._send_admin_digest_mail(["admin@example.org"], subject, template_vars)

        assert ok
        (logo,) = _parts(outbox[-1], "image/png")
        assert logo.get_payload(decode=True) == LIGHT_LOGO
        # And what to do about it, which the digest used to leave out.
        assert "Do this about it." in _parts(outbox[-1], "text/plain")[0].get_payload(decode=True).decode()

    def test_has_a_plain_text_part_without_the_hidden_preview(self, app, outbox):
        from aeronautics_members.services.identity import send_password_reset_email

        member = make_member(email="reset@example.com")
        with app.test_request_context():
            assert send_password_reset_email(app, member.user)

        message = outbox[-1]
        (text,) = _parts(message, "text/plain")
        (html,) = _parts(message, "text/html")
        body = text.get_payload(decode=True).decode()
        assert "Reset your password" in body
        assert "/reset-password/" in body  # the link survives, with its address
        # The inbox preview line is hidden in the HTML and absent from the text.
        assert "Use this link to choose a new password" not in body
        assert "Use this link to choose a new password" in html.get_payload(decode=True).decode()


class TestTheApprovalEmail:
    """Members waited for their picture to be approved and were only ever told
    when it was not."""

    @pytest.fixture
    def approve(self, app, client, monkeypatch):
        from aeronautics_members.blueprints import admin as admin_module
        from aeronautics_members.db_models import ForumAccount, ForumAvatarSubmission

        queued = []
        monkeypatch.setattr(
            admin_module, "queue_user_status_notification",
            lambda event_type, *a, **k: queued.append(event_type),
        )

        class FakeForum:
            def approve_avatar_submission(self, submission, reviewer=None, review_note=None):
                submission.status = "approved"
                return types.SimpleNamespace(error=None, desired_state="active", changed=True, forum_account=None)

        monkeypatch.setattr(admin_module, "get_forum_service", lambda: FakeForum())

        admin = make_member(email="admin@example.org")
        admin.user.grant_role(app_module.get_role("superadmin"))
        db.session.commit()

        def run(state):
            member = make_member(email=f"photo-{state}@example.com")
            db.session.add(ForumAccount(user=member.user, member=member, provider="discourse",
                                        external_id=str(member.user.id), state=state))
            submission = ForumAvatarSubmission(user_id=member.user.id, member_id=member.id,
                                               status="pending", public_token=f"t-{state}")
            db.session.add(submission)
            db.session.commit()
            with client.session_transaction() as session:
                session["_user_id"] = str(admin.user.id)
            client.post(f"/admin/forum/submissions/{submission.id}/approve", data={})
            return queued

        return run

    def test_it_is_sent_when_access_becomes_complete(self, approve):
        assert approve("onboarding") == ["forum_avatar_approved"]

    def test_not_to_somebody_replacing_a_picture(self, approve):
        assert approve("active") == []


def test_the_layout_is_not_offered_as_a_template(app):
    from aeronautics_members.services.notifications import get_email_template_choices

    names = [name for name, _label in get_email_template_choices(app)]
    assert "welcome_email.html" in names
    assert not any(name.startswith("_") for name in names)
