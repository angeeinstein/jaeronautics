"""Signing in with the university address.

Nearly everybody at the first logins typed their university address, because
that is the one they think of as theirs here. Once confirmed it now signs in.
Unconfirmed it proves nothing, and the latest confirmation of an address wins,
so it always leads to one account. A password reset asked for with it goes to
the private address, the mailbox the account belongs to.
"""
from datetime import datetime

import pytest

from conftest import db, make_member
from aeronautics_members.blueprints import auth as auth_module
from aeronautics_members.services import identity

UNI = "a.huber@edu.fh-joanneum.at"


def _member(email="huber@example.com", work=UNI, confirmed=True):
    member = make_member(email=email)
    member.email_work = work
    member.email_work_verified_at = datetime(2026, 9, 1) if confirmed else None
    db.session.commit()
    return member


def _login(client, address, password="initial-password"):
    return client.post("/login", data={"email": address, "password": password})


def _signed_in_as(client):
    with client.session_transaction() as session:
        return session.get("_user_id")


def test_a_confirmed_university_address_signs_in(app, client):
    member = _member()

    _login(client, UNI.upper())

    assert _signed_in_as(client) == str(member.user_id)


def test_the_private_address_still_does(app, client):
    member = _member()

    _login(client, "huber@example.com")

    assert _signed_in_as(client) == str(member.user_id)


def test_an_unconfirmed_one_does_not(app, client):
    _member(confirmed=False)

    _login(client, UNI)

    assert _signed_in_as(client) is None


def test_the_right_address_with_the_wrong_password_does_not(app, client):
    _member()

    _login(client, UNI, password="wrong-password")

    assert _signed_in_as(client) is None


def test_the_latest_confirmation_wins(app):
    """A reissued address, or a second account: whoever confirmed it last reads that mailbox."""
    first = _member()
    second = _member(email="second@example.com", confirmed=False)
    second.email_work_verification_nonce = "n"
    db.session.commit()

    assert identity.mark_work_email_verified_from_token(
        {"email": UNI, "nonce": "n"}, second)
    db.session.commit()

    assert db.session.get(type(first), first.id).email_work_verified_at is None
    assert identity.user_for_login_address(UNI).id == second.user_id


def test_an_address_confirmed_on_two_accounts_signs_in_nowhere(app, client):
    """From before the latest confirmation won: ambiguous, so neither."""
    _member()
    _member(email="other@example.com")

    _login(client, UNI)

    assert _signed_in_as(client) is None


@pytest.fixture
def reset_mails(monkeypatch):
    sent = []
    monkeypatch.setattr(auth_module, "send_password_reset_email",
                        lambda app, user, requested_with=None:
                        sent.append(identity.password_reset_address(user, requested_with)))
    return sent


def test_a_reset_asked_with_the_university_address_goes_to_the_private_one(app, client, reset_mails):
    member = _member()
    member.user.email_verified_at = datetime(2026, 9, 1)  # the mailbox the account belongs to
    db.session.commit()

    client.post("/forgot-password", data={"email": UNI})

    assert reset_mails == ["huber@example.com"]


def test_no_reset_for_an_unconfirmed_university_address(app, client, reset_mails):
    _member(confirmed=False)

    client.post("/forgot-password", data={"email": UNI})

    assert reset_mails == []
