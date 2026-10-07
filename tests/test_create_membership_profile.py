"""The second way in behaves like the first.

"Create membership profile" is for somebody who already has a login. It had its
own copy of what happens after the form is saved, and the copy drifted from the
public signup in three ways found on 2026-09-26:

* it offered payment by invoice while invoice payments were switched off, and
  then quietly took the person to card payment instead;
* it never sent the confirmation link to the university or company address --
  the link that shows somebody studies or works here, and the one that gives a
  returning student their old forum account back;
* it said "please correct the highlighted errors" and highlighted two fields of
  fourteen.
"""
import types

import pytest

from conftest import db

from aeronautics_members.db_models import Setting, User
from aeronautics_members.services import account, signup

FORM = {
    "salutation": "Mr", "first_name": "Angelo", "last_name": "Popovic",
    "street": "Dreierschützengasse", "house_number": "10", "postal_code": "8020",
    "city": "Graz", "country": "Austria", "phone_private": "+436601234567",
    "email_work": "angelo.popovic@edu.fh-joanneum.at",
    "member_category": "student", "year_group": "LAV23",
    "terms_accepted": True,
}
URL = "/api/v1/account/membership"


@pytest.fixture
def invoices(app):
    def switch(on):
        db.session.merge(Setting(key="invoice_payments_enabled", value=str(on)))
        db.session.commit()
    return switch


@pytest.fixture
def logged_in(app, client):
    user = User(email="login.only@example.com")
    user.set_password("password123")
    db.session.add(user)
    db.session.commit()
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
    return client


@pytest.fixture
def starting(monkeypatch):
    """Records how the membership was started, and sends nothing anywhere."""
    seen = {"checkout": 0, "invoice": 0, "work_email": 0}

    def checkout(member):
        seen["checkout"] += 1
        return types.SimpleNamespace(url="https://checkout.example/s"), {}

    def invoice(member):
        seen["invoice"] += 1
        return object(), {"free_period": False, "thank_you_phase": "prorated"}

    def work_email(app, member):
        seen["work_email"] += 1

    monkeypatch.setattr(signup, "create_checkout_session_for_member", checkout)
    monkeypatch.setattr(account, "create_checkout_session_for_member", checkout)
    monkeypatch.setattr(signup, "create_invoice_membership_for_member", invoice)
    monkeypatch.setattr(signup, "send_work_email_verification_email", work_email)
    monkeypatch.setattr(signup, "send_email_verification_email", lambda *a, **k: True)
    return seen


class TestInvoicesFollowTheSetting:
    def test_switched_off_the_form_does_not_offer_it(self, logged_in, invoices):
        invoices(False)
        assert logged_in.get("/api/v1/forms/options").get_json()["invoice_payments"] is False

    def test_switched_on_it_does(self, logged_in, invoices):
        invoices(True)
        assert logged_in.get("/api/v1/forms/options").get_json()["invoice_payments"] is True

    def test_asking_for_it_anyway_goes_to_card_payment(
            self, logged_in, invoices, starting):
        invoices(False)
        response = logged_in.post(URL, json={**FORM, "payment_method": "invoice"})
        assert response.get_json() == {"go_to": "https://checkout.example/s"}
        assert starting == {"checkout": 1, "invoice": 0, "work_email": 1}

    def test_allowed_it_is_honoured(self, logged_in, invoices, starting):
        invoices(True)
        response = logged_in.post(URL, json={**FORM, "payment_method": "invoice"})
        assert response.get_json()["go_to"] == "/thank-you?method=invoice&phase=prorated"
        assert starting["invoice"] == 1


class TestTheUniversityAddressIsConfirmed:
    def test_the_link_is_sent(self, logged_in, invoices, starting):
        """The link that reconnects a returning student's old forum account."""
        invoices(False)
        logged_in.post(URL, json=FORM)
        assert starting["work_email"] == 1

    def test_without_one_nothing_is_sent(self, logged_in, invoices, starting):
        invoices(False)
        logged_in.post(URL, json={**FORM, "member_category": "partner", "year_group": None, "email_work": None})
        assert starting["work_email"] == 0


class TestTheErrorsAreSaid:
    def test_each_refused_field_is_named(self, logged_in):
        response = logged_in.post(URL, json={**FORM, "phone_private": "call me", "city": "",
                                             "terms_accepted": False})
        assert response.status_code == 400
        assert set(response.get_json()["error"]["fields"]) == {"phone_private", "city", "terms_accepted"}
        assert db.session.execute(db.select(User).filter_by(email="login.only@example.com")).scalar_one().member is None

    def test_the_address_is_the_logins(self, logged_in, invoices, starting):
        invoices(False)
        logged_in.post(URL, json=FORM)
        user = db.session.execute(db.select(User).filter_by(email="login.only@example.com")).scalar_one()
        assert user.member.email_private == "login.only@example.com"


class TestTheSecondTime:
    def test_with_a_membership_already_nothing_new_is_made(self, logged_in, invoices, starting):
        """A double click: the second answer goes on from the first membership."""
        invoices(False)
        logged_in.post(URL, json=FORM)
        again = logged_in.post(URL, json=FORM)
        assert again.status_code == 200
        assert starting["checkout"] == 2  # the payment page again, not a second membership

    def test_the_page_sends_somebody_with_one_to_my_account(self, logged_in, invoices, starting):
        invoices(False)
        logged_in.post(URL, json=FORM)
        assert logged_in.get("/account/create-membership").headers["Location"] == "/account"
