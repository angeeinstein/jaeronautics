"""Signing in and out, and a new password by email (api/sign_in.py).

The behaviour carried over from the old forms is tested where it always was
(test_login_university_address.py, test_rate_limits.py, test_account_disabled.py,
test_email_verification_binding.py ...); here is what the API adds.
"""
from api_helpers import said
from conftest import db, make_member
from aeronautics_members.api import sign_in as sign_in_module
from aeronautics_members.db_models import User
from aeronautics_members.services import identity


def _sign_in(client, **body):
    return client.post("/api/v1/session", json={"email": "anna@example.com", "password": "initial-password", **body})


class TestSigningIn:
    def test_goes_on_to_the_start_page(self, app, client):
        make_member(email="anna@example.com")

        assert _sign_in(client).get_json() == {"go_to": "/account"}
        with client.session_transaction() as session:
            assert session["_user_id"]

    def test_or_to_the_page_that_asked(self, app, client):
        make_member(email="anna@example.com")

        answer = _sign_in(client, next="/forum/discourse/connect?sso=x&sig=y").get_json()

        assert answer == {"go_to": "/forum/discourse/connect?sso=x&sig=y"}

    def test_never_off_the_site(self, app, client):
        make_member(email="anna@example.com")

        assert _sign_in(client, next="https://evil.example/").get_json() == {"go_to": "/account"}

    def test_a_wrong_password_says_nothing_about_which_was_wrong(self, app, client):
        make_member(email="anna@example.com")

        error = _sign_in(client, password="a-guess").get_json()["error"]

        assert (error["code"], error["message"]) == ("invalid_credentials", "Invalid email or password.")

    def test_a_switched_off_account_is_told_so(self, app, client):
        member = make_member(email="anna@example.com")
        member.user.disabled_at = member.created_at
        db.session.commit()

        response = _sign_in(client)

        assert (response.status_code, response.get_json()["error"]["code"]) == (403, "account_disabled")

    def test_a_field_it_does_not_know_is_refused(self, app, client):
        make_member(email="anna@example.com")

        assert _sign_in(client, remember="yes").status_code == 400


class TestSigningOut:
    def test_signed_out_and_the_start_page_says_so(self, app, client):
        member = make_member(email="anna@example.com")
        with client.session_transaction() as session:
            session["_user_id"] = str(member.user_id)

        answer = client.delete("/api/v1/session").get_json()

        assert answer == {"go_to": "/"}
        with client.session_transaction() as session:
            assert "_user_id" not in session
        assert "signed out" in said(client)


class TestANewPassword:
    def test_the_same_answer_for_an_unknown_address(self, app, client, monkeypatch):
        sent = []
        monkeypatch.setattr(sign_in_module, "send_password_reset_email",
                            lambda app, user, requested_with=None: sent.append(user.email))
        make_member(email="anna@example.com")

        known = client.post("/api/v1/password-reset", json={"email": "anna@example.com"}).get_json()
        unknown = client.post("/api/v1/password-reset", json={"email": "nobody@example.com"}).get_json()

        assert known == unknown
        assert sent == ["anna@example.com"]

    def test_too_short_is_said_at_the_field(self, app, client):
        member = make_member(email="anna@example.com")
        token = identity.build_password_reset_token(member.user)
        db.session.commit()

        response = client.put(f"/api/v1/password-reset/{token}", json={"password": "short"})

        assert "password" in response.get_json()["error"]["fields"]
        assert db.session.get(User, member.user_id).check_password("initial-password")
