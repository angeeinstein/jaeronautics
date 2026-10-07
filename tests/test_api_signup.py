"""Becoming a member through the API (api/signup.py): the public signup and a
membership for a login without one.

What happens after the membership is saved is services/signup.py's, tested
with both doors in test_create_membership_profile.py and test_member_journey.py
(TestTheSignupSentTwice); the versions of the legal texts ticked, in
test_legal_texts.py. Here is what the API adds.
"""
import types

import pytest
import stripe

from api_helpers import said, signed_in
from conftest import db, make_member
from aeronautics_members.db_models import AuditLog, Member, Setting, User
from aeronautics_members.services import signup as signup_service

FORM = {
    "salutation": "Ms", "title": None, "first_name": "Nora", "last_name": "New", "street": "Main",
    "house_number": "1", "postal_code": "8010", "city": "Graz", "country": "Austria",
    "phone_private": "+43123", "phone_work": None, "email_private": "nora@example.com",
    "email_work": "nora.new@edu.fh-joanneum.at", "member_category": "student", "year_group": "lav25",
    "password": "a-good-password", "payment_method": "checkout", "terms_accepted": True,
}
#: The same without the login: a membership for somebody signed in.
PROFILE = {key: value for key, value in FORM.items() if key not in ("email_private", "password")}


@pytest.fixture
def stripe_ok(monkeypatch):
    opened = []

    def checkout(member):
        opened.append(member.id)
        return types.SimpleNamespace(url=f"https://checkout.stripe.test/{member.id}"), {}

    def invoice(member):
        return object(), {"free_period": True, "thank_you_phase": "free_period"}

    monkeypatch.setattr(signup_service, "create_checkout_session_for_member", checkout)
    monkeypatch.setattr(signup_service, "create_invoice_membership_for_member", invoice)
    monkeypatch.setattr(signup_service, "send_email_verification_email", lambda *a, **k: True)
    monkeypatch.setattr(signup_service, "send_work_email_verification_email", lambda *a, **k: True)
    monkeypatch.setattr(signup_service, "sync_member_forum_state", lambda member: (None, None))
    monkeypatch.setattr(signup_service, "send_member_welcome_email", lambda *a, **k: None)
    return opened


def _invoices(on):
    db.session.merge(Setting(key="invoice_payments_enabled", value=str(on)))
    db.session.commit()


def _member(address="nora@example.com"):
    return db.session.execute(db.select(Member).filter_by(email_private=address)).scalar_one_or_none()


class TestJoining:
    def test_makes_the_login_and_the_membership_and_goes_on_to_pay(self, app, client, stripe_ok):
        response = client.post("/api/v1/signup", json=FORM)

        member = _member()
        assert response.status_code == 200
        assert response.get_json() == {"go_to": f"https://checkout.stripe.test/{member.id}"}
        assert member.user.email == "nora@example.com" and member.user.check_password("a-good-password")
        assert member.year_group == "LAV25"  # in capitals, as the form has it
        assert member.payment_status == "pending_checkout" and not member.is_active
        assert db.session.query(AuditLog).filter_by(event_type="public_membership_signup_started").count() == 1

    def test_signed_in_afterwards(self, app, client, stripe_ok):
        client.post("/api/v1/signup", json=FORM)

        assert client.get("/api/v1/account").status_code == 200

    def test_the_address_is_kept_in_small_letters(self, app, client, stripe_ok):
        client.post("/api/v1/signup", json={**FORM, "email_private": "Nora@Example.com"})

        assert _member().user.email == "nora@example.com"

    def test_by_invoice_to_the_thank_you_page(self, app, client, stripe_ok):
        _invoices(True)

        response = client.post("/api/v1/signup", json={**FORM, "payment_method": "invoice"})

        assert response.get_json() == {"go_to": "/thank-you?method=invoice&phase=free_period"}
        assert stripe_ok == []

    def test_when_paying_cannot_start_to_my_account_which_says_so(self, app, client, stripe_ok, monkeypatch):
        def broken(member):
            raise stripe.APIConnectionError("down")

        monkeypatch.setattr(signup_service, "create_checkout_session_for_member", broken)

        response = client.post("/api/v1/signup", json=FORM)

        assert response.get_json() == {"go_to": "/account"}
        assert _member() is not None  # the membership stands
        assert "payment could not be started" in said(client)


class TestTheFormIsChecked:
    def test_each_refused_field_by_name(self, app, client, stripe_ok):
        response = client.post("/api/v1/signup", json={
            **FORM, "first_name": "", "phone_private": "call me", "year_group": None, "terms_accepted": False,
        })

        assert response.status_code == 400
        assert set(response.get_json()["error"]["fields"]) == {
            "first_name", "phone_private", "year_group", "terms_accepted"}
        assert _member() is None and stripe_ok == []

    def test_a_short_password(self, app, client, stripe_ok):
        response = client.post("/api/v1/signup", json={**FORM, "password": "short"})

        assert list(response.get_json()["error"]["fields"]) == ["password"]

    def test_a_university_address_is_not_a_login(self, app, client, stripe_ok):
        response = client.post("/api/v1/signup", json={**FORM, "email_private": "nora@edu.fh-joanneum.at"})

        assert "university address" in response.get_json()["error"]["fields"]["email_private"]

    def test_a_partner_needs_no_year_group_or_university_address(self, app, client, stripe_ok):
        response = client.post("/api/v1/signup", json={
            **FORM, "member_category": "partner", "year_group": "LAV25", "email_work": None})

        assert response.status_code == 200
        assert _member().year_group is None  # a leftover from before the choice was switched

    def test_nothing_unknown_is_taken(self, app, client, stripe_ok):
        assert client.post("/api/v1/signup", json={**FORM, "is_active": True}).status_code == 400


class TestAnAccountThereAlready:
    def test_says_to_sign_in(self, app, client, stripe_ok):
        make_member(email="nora@example.com", payment_status="paid", is_active=True)

        response = client.post("/api/v1/signup", json=FORM)

        assert response.status_code == 409
        assert response.get_json()["error"]["code"] == "account_exists"

    def test_a_login_without_a_membership_too(self, app, client, stripe_ok):
        user = User(email="nora@example.com")
        user.set_password("x")
        db.session.add(user)
        db.session.commit()

        response = client.post("/api/v1/signup", json=FORM)

        assert response.get_json()["error"]["code"] == "account_exists"
        assert user.member is None


class TestAMembershipForALoginWithoutOne:
    @pytest.fixture
    def staff(self, app, client):
        user = User(email="staff@example.org")
        user.set_password("x")
        db.session.add(user)
        db.session.commit()
        signed_in(client, user)
        return user

    def test_with_the_logins_address(self, app, client, staff, stripe_ok):
        response = client.post("/api/v1/account/membership", json=PROFILE)

        member = _member("staff@example.org")
        assert response.get_json() == {"go_to": f"https://checkout.stripe.test/{member.id}"}
        assert member.user_id == staff.id and member.first_name == "Nora"
        assert staff.forum_username

    def test_no_address_of_its_own_is_taken(self, app, client, staff, stripe_ok):
        assert client.post("/api/v1/account/membership",
                           json={**PROFILE, "email_private": "other@example.com"}).status_code == 400

    def test_not_signed_in(self, app, client, stripe_ok):
        assert client.post("/api/v1/account/membership", json=PROFILE).status_code == 401

    def test_an_address_some_membership_has_already(self, app, client, staff, stripe_ok):
        make_member(email="elsewhere@example.org").email_private = "staff@example.org"
        db.session.commit()

        response = client.post("/api/v1/account/membership", json=PROFILE)

        assert response.status_code == 409 and response.get_json()["error"]["code"] == "profile_exists"
