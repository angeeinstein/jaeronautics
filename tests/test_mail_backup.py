"""The backup mail account: when the account an email goes out from cannot
send it, the backup does (docs/microsoft-mail-plan.md, "A backup account")."""
import smtplib

from conftest import db
from aeronautics_members.db_models import MailAccount
from aeronautics_members.mail_utils import send_mail
from aeronautics_members.services import mail_senders, mailings, settings_sections
from aeronautics_members.services.diagnostics import collect_system_health
from test_mail_sender import smtp  # noqa: F401  (fixture)
from test_microsoft_mail import _connected, _mailbox, microsoft  # noqa: F401  (fixture)

REFUSED = {"error": {"code": "ErrorAccessDenied", "message": "Access is denied."}}


def _brevo():
    db.session.add(MailAccount(account_key="noreply-brevo", host="smtp-relay.brevo.com", port=587,
                               username="bc6766001@smtp-brevo.com", password="key", starttls=True,
                               from_email="noreply@example.org", from_name="Joanneum Aeronautics"))
    db.session.commit()


def _senders(backup="noreply-brevo"):
    settings_sections.save_senders(None, default="support", backup=backup, welcome="", notifications="",
                                   mailings="", replies="", reply_copy=False)
    db.session.commit()


def _set_up(microsoft, refused=True):  # noqa: F811
    _connected()
    _mailbox()
    _brevo()
    _senders()
    if refused:
        microsoft.answer("POST", "/users/support@example.org/sendMail", 403, REFUSED)


def _send(**extra):
    return send_mail(from_account="support", to_email="anna@example.com", subject="Your new password",
                     body="<p>The link</p>", return_error=True, **extra)


def test_an_account_that_cannot_send_is_stood_in_for(app, microsoft, smtp):  # noqa: F811
    _set_up(microsoft)

    assert _send(bcc_emails=["support@example.org"]) == (True, None)

    envelope_sender, recipients, message = smtp.sent[-1]
    assert envelope_sender == "noreply@example.org" and recipients == ["anna@example.com", "support@example.org"]
    assert message["From"] == "Joanneum Aeronautics <noreply@example.org>"
    # Answers still go where the email should have come from.
    assert message["Reply-To"] == "support@example.org"
    assert message["Bcc"] is None
    assert message["Message-ID"]


def test_a_reply_to_already_named_is_kept(app, microsoft, smtp):  # noqa: F811
    _set_up(microsoft)

    _send(reply_to="anna@example.com")

    assert smtp.sent[-1][2]["Reply-To"] == "anna@example.com"


def test_system_health_says_so_until_the_account_sends_again(app, microsoft, smtp):  # noqa: F811
    _set_up(microsoft)
    _send()
    db.session.commit()

    [problem] = [line for line in collect_system_health(check_pages=False)["problems"] if "cannot send" in line]
    assert problem.startswith("support cannot send since ")
    assert "the backup (noreply-brevo) sends its emails" in problem

    microsoft.answers.clear()
    assert _send() == (True, None)
    db.session.commit()

    assert not mail_senders.failing()


def test_without_a_backup_it_fails_as_before(app, microsoft, smtp):  # noqa: F811
    _set_up(microsoft)
    _senders(backup="")

    sent, error = _send()

    assert not sent and "does not let the app send from support@example.org" in error
    assert not smtp.sent


def test_the_backup_failing_too_says_both(app, microsoft, monkeypatch):  # noqa: F811
    _set_up(microsoft)

    class Down:
        def __init__(self, *a, **k):
            raise OSError("Connection refused")

    monkeypatch.setattr(smtplib, "SMTP", Down)

    sent, error = _send()

    assert not sent
    assert "does not let the app send" in error and "the backup could not send it either: Connection refused" in error


def test_an_address_nobody_takes_goes_nowhere_else(app, microsoft, smtp):  # noqa: F811
    _set_up(microsoft, refused=False)
    microsoft.answer("POST", "/users/support@example.org/sendMail", 400,
                     {"error": {"code": "ErrorInvalidRecipients", "message": "Bad recipient."}})

    sent, _error = _send()

    assert not sent and not smtp.sent


def test_announcements_wait_instead(app, microsoft, smtp, monkeypatch):  # noqa: F811
    _set_up(microsoft)

    assert _send(backup=False)[0] is False
    assert not smtp.sent
    # And the mailings say so.
    import inspect

    assert "backup=False" in inspect.getsource(mailings._send)


def test_the_test_email_tests_the_account_it_names(app, microsoft, smtp):  # noqa: F811
    from aeronautics_members.services.mail_accounts import send_test_email

    _set_up(microsoft)

    sent, error = send_test_email("support", "anna@example.com", "test_email.html")

    assert not sent and "does not let the app send" in error
    assert not smtp.sent


def test_removing_the_backup_account_clears_the_choice(app, microsoft, smtp):  # noqa: F811
    _set_up(microsoft)

    assert "backup" in mail_senders.forget_account("noreply-brevo")
    db.session.commit()
    assert mail_senders.backup_account() == ""
