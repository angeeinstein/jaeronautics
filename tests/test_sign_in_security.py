"""What keeps an account its owner's: a new password or login address ends
every other session, changing an address asks for the password and tells the
old one, and a sign-in only ever goes on to a page of this site."""
import pytest
from flask import g

from api_helpers import send, signed_in
from conftest import app_module, db, make_member
from aeronautics_members.db_models import NotificationEvent, User
from aeronautics_members.services import identity, workflows

CONTACT = {"street": "Main Street", "house_number": "1", "postal_code": "8010", "city": "Graz",
           "country": "Austria", "phone_private": "+43000000000", "email_private": "anna@example.com",
           "email_work": ""}


@pytest.fixture(autouse=True)
def no_stripe(monkeypatch):
    monkeypatch.setattr(workflows, "refresh_member_billing_state", lambda *a, **k: (False, None, None))


def _anna(confirmed=False):
    member = make_member(email="anna@example.com", member_category="alumni")
    if confirmed:
        from datetime import datetime, timezone

        member.user.email_verified_at = datetime.now(timezone.utc)
        db.session.commit()
    return member


def _still_in(client):
    # The app fixture keeps one context open, and Flask-Login the user it found on it.
    g.pop("_login_user", None)
    return client.get("/api/v1/account").status_code == 200


def _contact(client, **changes):
    return send(client, "PUT", "/api/v1/account/contact", {**CONTACT, **changes})


class TestANewPassword:
    def test_ends_the_other_sessions_and_keeps_this_one(self, app, client):
        member = _anna()
        elsewhere = signed_in(app.test_client(), member.user)
        here = signed_in(client, member.user)
        assert _still_in(elsewhere)

        response = send(here, "PUT", "/api/v1/account/password",
                        {"current_password": "initial-password", "new_password": "a-new-password"})

        assert response.status_code == 200
        assert not _still_in(elsewhere)
        assert _still_in(here)

    def test_by_the_emailed_link_ends_every_session(self, app, client):
        member = _anna()
        elsewhere = signed_in(app.test_client(), member.user)
        token = identity.build_password_reset_token(member.user)
        db.session.commit()

        client.put(f"/api/v1/password-reset/{token}", json={"password": "a-new-password"})

        assert not _still_in(elsewhere)

    def test_a_wrong_current_password_ends_nothing(self, app, client):
        member = _anna()
        elsewhere = signed_in(app.test_client(), member.user)

        send(signed_in(client, member.user), "PUT", "/api/v1/account/password",
             {"current_password": "a-guess", "new_password": "a-new-password"})

        assert _still_in(elsewhere)

    def test_a_session_from_before_generations_works_until_the_first_change(self, app, client):
        member = _anna()
        with client.session_transaction() as session:
            session["_user_id"] = str(member.user_id)
        assert _still_in(client)

        user = db.session.get(User, member.user_id)
        user.set_password("a-new-password")
        db.session.commit()

        assert not _still_in(client)

    def test_a_made_up_generation_is_nobody(self, app, client):
        member = _anna()
        with client.session_transaction() as session:
            session["_user_id"] = str(member.user_id)
            session["_session_generation"] = "x"

        assert not _still_in(client)

    def test_an_older_version_still_reads_the_session(self, app, client):
        """Should the update be rolled back: the old code reads the plain id."""
        member = _anna()
        here = signed_in(client, member.user)

        send(here, "PUT", "/api/v1/account/password",
             {"current_password": "initial-password", "new_password": "a-new-password"})

        with here.session_transaction() as session:
            assert int(session["_user_id"]) == member.user_id
            assert session["_session_generation"] == 1


class TestChangingAnAddress:
    def test_other_details_need_no_password(self, app, client):
        member = _anna()

        response = _contact(signed_in(client, member.user), city="Vienna")

        assert response.status_code == 200

    def test_the_login_address_needs_the_password(self, app, client):
        member = _anna()

        response = _contact(signed_in(client, member.user), email_private="someone@example.org")

        assert response.status_code == 400
        assert "current password" in response.get_json()["error"]["fields"]["current_password"]
        assert db.session.get(User, member.user_id).email == "anna@example.com"

    def test_not_a_wrong_one(self, app, client):
        member = _anna()

        response = _contact(signed_in(client, member.user), email_private="someone@example.org",
                            current_password="a-guess")

        assert response.get_json()["error"]["fields"] == {"current_password": "This is not your current password."}
        assert db.session.get(User, member.user_id).email == "anna@example.com"

    def test_the_university_address_too(self, app, client):
        """A confirmed one signs in as well."""
        member = _anna()

        response = _contact(signed_in(client, member.user), email_work="anna@edu.fh-joanneum.at")

        assert "current_password" in response.get_json()["error"]["fields"]

    def test_with_it_the_address_changes_and_the_other_sessions_end(self, app, client):
        member = _anna()
        elsewhere = signed_in(app.test_client(), member.user)
        here = signed_in(client, member.user)

        response = _contact(here, email_private="anna.new@example.com", current_password="initial-password")

        assert response.status_code == 200
        assert db.session.get(User, member.user_id).email == "anna.new@example.com"
        assert not _still_in(elsewhere)
        assert _still_in(here)

    def test_a_new_university_address_ends_the_other_sessions_too(self, app, client):
        """A confirmed one signs in, so whoever signed in with the old one is out."""
        member = _anna()
        elsewhere = signed_in(app.test_client(), member.user)
        here = signed_in(client, member.user)

        response = _contact(here, email_work="anna@work.example", current_password="initial-password")

        assert response.status_code == 200
        assert not _still_in(elsewhere)
        assert _still_in(here)

    def test_a_confirmed_old_address_is_told(self, app, client):
        member = _anna(confirmed=True)

        _contact(signed_in(client, member.user), email_private="anna.new@example.com",
                 current_password="initial-password")

        [notice] = db.session.query(NotificationEvent).filter_by(event_type="account_email_changed").all()
        assert notice.recipient_email == "anna@example.com"
        assert notice.payload["new_email_masked"] == "an…@example.com"

    def test_one_never_confirmed_is_nobody_to_tell(self, app, client):
        member = _anna()

        _contact(signed_in(client, member.user), email_private="anna.new@example.com",
                 current_password="initial-password")

        assert not db.session.query(NotificationEvent).filter_by(event_type="account_email_changed").all()

    def test_by_an_admin_ends_the_members_sessions(self, app, client):
        member = _anna()
        theirs = signed_in(app.test_client(), member.user)
        boss = make_member(email="boss@example.org").user
        boss.grant_role(app_module.get_role("superadmin"))
        db.session.commit()

        send(signed_in(client, boss), "PUT", f"/api/v1/admin/accounts/{member.user_id}/email",
             {"email": "anna.right@example.com"})

        assert not _still_in(theirs)


class TestWhereASignInGoesOn:
    @pytest.mark.parametrize("target", ["/account", "/forum/discourse/connect?sso=x&sig=y", "/link/abc#top"])
    def test_a_page_of_this_site(self, app, target):
        assert app_module.is_safe_next_url(target)

    @pytest.mark.parametrize("target", [
        "/\\evil.example", "/\\/evil.example", "//evil.example", "/\t/evil.example", "/\n/evil.example",
        "https://evil.example/", "http://localhost/account", "javascript:alert(1)", "evil.example", "", None,
    ])
    def test_nothing_a_browser_reads_as_another_site(self, app, target):
        assert not app_module.is_safe_next_url(target)

    def test_after_signing_in(self, app, client):
        make_member(email="anna@example.com")

        answer = client.post("/api/v1/session", json={
            "email": "anna@example.com", "password": "initial-password", "next": "/\\evil.example"}).get_json()

        assert answer == {"go_to": "/account"}
