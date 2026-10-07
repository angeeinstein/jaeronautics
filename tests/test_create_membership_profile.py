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
from aeronautics_members.services import signup

FORM = {
    "salutation": "Mr", "first_name": "Angelo", "last_name": "Popovic",
    "street": "Dreierschützengasse", "house_number": "10", "postal_code": "8020",
    "city": "Graz", "country": "Austria", "phone_private": "+436601234567",
    "email_private": "login.only@example.com",
    "email_work": "angelo.popovic@edu.fh-joanneum.at",
    "member_category": "student", "year_group": "LAV23",
    "terms_accepted": "y",
}


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
    monkeypatch.setattr(signup, "create_invoice_membership_for_member", invoice)
    monkeypatch.setattr(signup, "send_work_email_verification_email", work_email)
    monkeypatch.setattr(signup, "send_email_verification_email", lambda *a, **k: True)
    return seen


class TestInvoicesFollowTheSetting:
    def test_switched_off_the_page_does_not_offer_it(self, logged_in, invoices):
        invoices(False)
        body = logged_in.get("/account/create-membership").get_data(as_text=True)
        assert 'value="invoice"' not in body

    def test_switched_on_it_does(self, logged_in, invoices):
        invoices(True)
        body = logged_in.get("/account/create-membership").get_data(as_text=True)
        assert 'value="invoice"' in body

    def test_asking_for_it_anyway_goes_to_card_payment(
            self, logged_in, invoices, starting):
        invoices(False)
        logged_in.post("/account/create-membership",
                       data={**FORM, "payment_method": "invoice"})
        assert starting == {"checkout": 1, "invoice": 0, "work_email": 1}

    def test_allowed_it_is_honoured(self, logged_in, invoices, starting):
        invoices(True)
        logged_in.post("/account/create-membership",
                       data={**FORM, "payment_method": "invoice"})
        assert starting["invoice"] == 1


class TestTheUniversityAddressIsConfirmed:
    def test_the_link_is_sent(self, logged_in, invoices, starting):
        """The link that reconnects a returning student's old forum account."""
        invoices(False)
        logged_in.post("/account/create-membership", data=FORM)
        assert starting["work_email"] == 1

    def test_without_one_nothing_is_sent(self, logged_in, invoices, starting):
        invoices(False)
        logged_in.post("/account/create-membership",
                       data={**FORM, "member_category": "partner",
                             "year_group": "", "email_work": ""})
        assert starting["work_email"] == 0


class TestTheErrorsAreSaid:
    def test_a_bad_phone_number_is_named(self, logged_in):
        body = logged_in.post(
            "/account/create-membership",
            data={**FORM, "phone_private": "call me"},
        ).get_data(as_text=True)
        assert "Private Phone" in body
