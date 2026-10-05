"""Locked out by a wrong private address, with the password forgotten too.

The private address is the login and where password resets go. Mistyped at
signup -- or changed to a wrong one later -- and nothing that goes there
arrives. Two ways back in:

- A member whose university address is confirmed asks for the reset with it,
  and since the private one was never confirmed, the link goes to the
  university address: reading that inbox is proof enough.
- Anybody else asks an admin, who corrects the address on the account page.
  That hands the account to whoever reads the new address, so it is logged,
  the new address must be confirmed, and the old one is told if it was ever
  confirmed. Accounts with a role only by somebody who manages access.
"""
from datetime import datetime

import pytest

from conftest import app_module, db, make_member
from aeronautics_members.db_models import AuditLog, NotificationEvent, User
from aeronautics_members.services import identity
from test_admin_reviews import _login, _staff


def _member(email="anna.wrnog@example.com", confirmed=False, work=None, work_confirmed=False):
    member = make_member(email=email)
    member.user.email_verified_at = datetime.utcnow() if confirmed else None
    if work:
        member.email_work = work
        member.email_work_verified_at = datetime.utcnow() if work_confirmed else None
    db.session.commit()
    return member


class TestTheResetLink:
    @pytest.mark.parametrize("confirmed, work_confirmed, asked_with, goes_to", [
        (False, True, "anna.berger@edu.fh-joanneum.at", "anna.berger@edu.fh-joanneum.at"),
        (True, True, "anna.berger@edu.fh-joanneum.at", "anna.wrnog@example.com"),
        (False, True, "anna.wrnog@example.com", "anna.wrnog@example.com"),
        (False, False, "anna.berger@edu.fh-joanneum.at", "anna.wrnog@example.com"),
    ])
    def test_goes_to_the_confirmed_university_address_only_when_the_private_one_never_was(
            self, app, confirmed, work_confirmed, asked_with, goes_to):
        member = _member(confirmed=confirmed, work="anna.berger@edu.fh-joanneum.at", work_confirmed=work_confirmed)

        assert identity.password_reset_address(member.user, asked_with) == goes_to

    def test_asking_with_the_university_address_sends_it_there(self, app, client, monkeypatch):
        sent = []
        monkeypatch.setattr(identity, "send_account_action_email",
                            lambda app, to_email, **kwargs: sent.append(to_email) or True)
        _member(work="anna.berger@edu.fh-joanneum.at", work_confirmed=True)

        client.post("/forgot-password", data={"email": "Anna.Berger@edu.fh-joanneum.at"})

        assert sent == ["anna.berger@edu.fh-joanneum.at"]


@pytest.fixture
def emails(monkeypatch):
    sent = []
    monkeypatch.setattr(identity, "send_email_verification_email", lambda app, user: sent.append(user.email))
    return sent


class TestAnAdminCorrectsIt:
    def test_the_address_changes_and_has_to_be_confirmed(self, app, client, emails):
        member = _member(confirmed=False)
        user_id = member.user_id
        _login(client, _staff("boss@example.org", "admin").id)

        response = client.post(f"/admin/accounts/{user_id}/email", data={"new_email": "Anna.Right@example.com"})

        assert response.status_code == 302
        user = db.session.get(User, user_id)
        assert (user.email, user.member.email_private) == ("anna.right@example.com", "anna.right@example.com")
        assert user.email_verified_at is None
        assert emails == ["anna.right@example.com"]
        log = db.session.query(AuditLog).filter_by(event_type="private_email_corrected_by_admin").one()
        assert log.target_user_id == user_id and log.actor_user_id is not None
        # Never confirmed, the old address is nobody's to tell.
        assert not db.session.query(NotificationEvent).filter_by(event_type="account_email_changed_by_admin").all()

    def test_an_old_address_once_confirmed_is_told(self, app, client, emails):
        member = _member(email="anna.old@example.com", confirmed=True)
        _login(client, _staff("boss@example.org", "admin").id)

        client.post(f"/admin/accounts/{member.user_id}/email", data={"new_email": "anna.new@example.com"})

        [notice] = db.session.query(NotificationEvent).filter_by(event_type="account_email_changed_by_admin").all()
        assert notice.recipient_email == "anna.old@example.com"

    def test_not_an_address_another_account_has(self, app, client, emails):
        member = _member()
        make_member(email="taken@example.com")
        _login(client, _staff("boss@example.org", "admin").id)

        client.post(f"/admin/accounts/{member.user_id}/email", data={"new_email": "taken@example.com"})

        assert db.session.get(User, member.user_id).email == "anna.wrnog@example.com" and not emails

    def _with_a_role(self):
        member = _member()
        member.user.grant_role(app_module.get_role("admin"))
        db.session.commit()
        return member

    def test_not_an_account_with_a_role(self, app, client, emails):
        member = self._with_a_role()
        _login(client, _staff("boss@example.org", "admin").id)

        client.post(f"/admin/accounts/{member.user_id}/email", data={"new_email": "anna.new@example.com"})

        db.session.expire_all()
        assert db.session.get(User, member.user_id).email == "anna.wrnog@example.com" and not emails

    def test_unless_one_manages_access(self, app, client, emails):
        member = self._with_a_role()
        _login(client, _staff("root@example.org", "superadmin").id)

        client.post(f"/admin/accounts/{member.user_id}/email", data={"new_email": "anna.new@example.com"})

        db.session.expire_all()
        assert db.session.get(User, member.user_id).email == "anna.new@example.com"

    def test_the_account_page_offers_it(self, app, client):
        member = _member()
        _login(client, _staff("boss@example.org", "admin").id)

        body = client.get(f"/admin/accounts/{member.user_id}").get_data(as_text=True)

        assert "Correct the Private Email Address" in body and "never confirmed" in body
