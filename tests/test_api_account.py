"""My Account's API (api/account.py), what the member forms offer
(api/form_options.py), the footer (api/site.py), and what Flask said before
sending the browser to one of the app's pages (GET /api/v1/messages).

What the page does with each answer is in frontend/src/pages/account/;
the behaviour carried over from the old page is tested where it always was
(test_member_journey.py, test_rejoin.py, test_email_confirmation_ux.py ...).
"""
from datetime import date

import pytest

from api_helpers import send, signed_in
from conftest import db, make_member
from aeronautics_members.db_models import AuditLog, MemberProfileChangeRequest, User
from aeronautics_members.services import workflows

CONTACT = {"street": "Main Street", "house_number": "1", "postal_code": "8010", "city": "Graz",
           "country": "Austria", "phone_private": "+43000000000", "email_private": "anna@example.com",
           "email_work": "anna@edu.fh-joanneum.at"}
IDENTITY = {"salutation": "Ms", "first_name": "Anna", "last_name": "Berger",
            "member_category": "student", "year_group": "LAV25"}


@pytest.fixture(autouse=True)
def no_stripe(monkeypatch):
    """The page asks Stripe for the latest on every load; there is no Stripe here."""
    monkeypatch.setattr(workflows, "refresh_member_billing_state", lambda *a, **k: (False, None, None))


def _anna(**overrides):
    fields = dict(salutation="Ms", first_name="Anna", last_name="Berger", year_group="LAV25",
                  member_category="student", email_work="anna@edu.fh-joanneum.at")
    fields.update(overrides)
    return make_member(email="anna@example.com", **fields)


class TestTheFormChoices:
    def test_open_to_anybody(self, app, client):
        options = client.get("/api/v1/forms/options").get_json()

        assert {"value": "Austria", "label": "Austria"} in options["countries"]
        assert all(choice["value"] for choice in options["countries"]), "no '-- Select --' entry"
        assert [choice["value"] for choice in options["salutations"]] == ["Mr", "Ms", "Diverse"]
        assert options["invoice_payments"] is False

    def test_each_kind_of_member_says_what_it_is_asked(self, app, client):
        kinds = {kind["value"]: kind for kind in client.get("/api/v1/forms/options").get_json()["member_categories"]}

        assert (kinds["student"]["year_group"], kinds["student"]["university_email_required"]) == ("required", True)
        assert (kinds["alumni"]["year_group"], kinds["alumni"]["university_email_required"]) == ("optional", False)
        assert kinds["partner"]["year_group"] == "hidden"
        assert list(kinds)[0] == "student", "the common case first"


class TestWhatFlaskSaid:
    def test_handed_out_once(self, app, client):
        with client.session_transaction() as session:
            session["_flashes"] = [("warning", "This deletion link has expired."), ("message", "Plain.")]

        first = client.get("/api/v1/messages").get_json()["messages"]
        second = client.get("/api/v1/messages").get_json()["messages"]

        assert first == [{"tone": "warning", "text": "This deletion link has expired."},
                         {"tone": "info", "text": "Plain."}]
        assert second == []


class TestTheFooter:
    def test_the_portals_own_legal_texts(self, app, client):
        links = {link["label"]: link for link in client.get("/api/v1/site").get_json()["footer"]}

        assert links["Impressum"] == {"label": "Impressum", "url": "/legal/legal-notice", "external": False}
        assert links["Contact"]["url"].startswith("mailto:")
        assert links["Website"]["external"] is True

    def test_or_an_address_elsewhere(self, app, client, monkeypatch):
        from aeronautics_members.api import site

        monkeypatch.setattr(site, "PRIVACY_URL", "https://example.org/privacy")

        links = {link["label"]: link for link in client.get("/api/v1/site").get_json()["footer"]}

        assert links["Privacy"] == {"label": "Privacy", "url": "https://example.org/privacy", "external": True}


class TestThePage:
    def test_signing_in_is_required(self, app, client):
        assert client.get("/api/v1/account").status_code == 401
        assert client.get("/account").status_code == 302

    def test_what_is_on_it(self, app, client):
        member = _anna(payment_status="paid", is_active=True, membership_starts_on=date(2026, 1, 1),
                       membership_ends_on=date(2099, 12, 31), renewal_due_on=date(2099, 12, 31))
        account = signed_in(client, member.user).get("/api/v1/account").get_json()

        assert account["export_url"] == "/account/data-export"
        assert account["member"]["contact"] == {**CONTACT, "phone_work": None}
        assert account["member"]["identity"] == {**IDENTITY, "title": None, "member_category_label": "Student"}
        membership = account["member"]["membership"]
        assert (membership["status_label"], membership["tone"], membership["active"]) == ("Paid", "active", True)
        assert (membership["renews_on"], membership["auto_renew"]) == ("2099-12-31", True)
        assert membership["note"] is None

    def test_a_cancelled_renewal_says_until_when(self, app, client):
        member = _anna(payment_status="cancel_scheduled", is_active=True, cancel_at_period_end=True,
                       membership_ends_on=date(2099, 12, 31))
        membership = signed_in(client, member.user).get("/api/v1/account").get_json()["member"]["membership"]

        assert membership["renews_on"] is None
        assert membership["note"] == {"tone": "warning",
                                      "text": "Your membership stays active until 31.12.2099, but it will not renew."}

    def test_deleting_says_what_it_costs_while_paid_up(self, app, client):
        member = _anna(payment_status="paid", is_active=True, stripe_customer_id="cus_a",
                       stripe_subscription_id="sub_a", membership_ends_on=date(2099, 12, 31))
        account = signed_in(client, member.user).get("/api/v1/account").get_json()["member"]

        assert "already paid until 31.12.2099" in account["deletion_note"]["text"]
        assert account["may_cancel_instead"] is True


class TestContactDetails:
    def test_saved_with_the_page_back(self, app, client):
        member = _anna()

        response = send(signed_in(client, member.user), "PUT", "/api/v1/account/contact",
                        {**CONTACT, "city": "Vienna"})

        assert response.status_code == 200
        answer = response.get_json()
        assert answer["messages"] == [{"tone": "success", "text": "Saved."}]
        assert answer["account"]["member"]["contact"]["city"] == "Vienna"
        assert db.session.execute(db.select(AuditLog).filter_by(event_type="contact_details_updated")).scalar_one()

    def test_the_rules_of_the_kind_of_member_apply(self, app, client):
        """A student cannot move their university address to a private one here either."""
        member = _anna()

        response = send(signed_in(client, member.user), "PUT", "/api/v1/account/contact",
                        {**CONTACT, "email_work": "anna@gmail.com"})

        assert response.status_code == 400
        assert "university address" in response.get_json()["error"]["fields"]["email_work"]

    def test_a_field_it_does_not_know_is_refused(self, app, client):
        member = _anna()

        response = send(signed_in(client, member.user), "PUT", "/api/v1/account/contact",
                        {**CONTACT, "first_name": "Someone else"})

        assert response.status_code == 400
        assert db.session.get(User, member.user_id).member.first_name == "Anna"

    def test_an_account_without_a_membership_has_none(self, app, client):
        staff = User(email="staff@example.org")
        staff.set_password("a-password-1")
        db.session.add(staff)
        db.session.commit()

        response = send(signed_in(client, staff), "PUT", "/api/v1/account/contact", CONTACT)

        assert (response.status_code, response.get_json()["error"]["code"]) == (409, "no_membership")


class TestChangeRequests:
    def test_asked_for_and_shown_until_decided(self, app, client):
        member = _anna()

        response = send(signed_in(client, member.user), "POST", "/api/v1/account/change-request",
                        {**IDENTITY, "last_name": "Huber", "note": "Married."})

        assert response.status_code == 201
        waiting = response.get_json()["account"]["member"]["change_request"]
        assert (waiting["last_name"], waiting["note"], waiting["member_category_label"]) == (
            "Huber", "Married.", "Student")
        assert member.last_name == "Berger", "an admin decides"

    def test_one_at_a_time(self, app, client):
        member = _anna()
        signed_in(client, member.user)
        send(client, "POST", "/api/v1/account/change-request", {**IDENTITY, "last_name": "Huber"})

        response = send(client, "POST", "/api/v1/account/change-request", {**IDENTITY, "last_name": "Maier"})

        assert (response.status_code, response.get_json()["error"]["code"]) == (409, "request_pending")

    def test_nothing_different_is_not_a_request(self, app, client):
        member = _anna()

        response = send(signed_in(client, member.user), "POST", "/api/v1/account/change-request", IDENTITY)

        assert (response.status_code, response.get_json()["error"]["code"]) == (400, "nothing_changed")

    def test_the_year_group_rules_apply(self, app, client):
        member = _anna()

        response = send(signed_in(client, member.user), "POST", "/api/v1/account/change-request",
                        {**IDENTITY, "year_group": "2025"})

        assert response.get_json()["error"]["fields"] == {"year_group": "Letters and two digits, e.g. LAV25."}

    def test_withdrawn(self, app, client):
        member = _anna()
        signed_in(client, member.user)
        send(client, "POST", "/api/v1/account/change-request", {**IDENTITY, "last_name": "Huber"})
        request_id = db.session.execute(db.select(MemberProfileChangeRequest.id)).scalar_one()

        response = send(client, "DELETE", f"/api/v1/account/change-request/{request_id}")

        assert response.get_json()["account"]["member"]["change_request"] is None
        assert db.session.get(MemberProfileChangeRequest, request_id).status == "canceled"

    def test_not_somebody_elses(self, app, client):
        other = make_member(email="other@example.com")
        send(signed_in(client, other.user), "POST", "/api/v1/account/change-request",
             {"salutation": "Mr", "first_name": "Otto", "last_name": "Other", "member_category": "alumni"})
        request_id = db.session.execute(db.select(MemberProfileChangeRequest.id)).scalar_one()
        member = _anna()
        from flask import g

        g.pop("_login_user", None)
        response = send(signed_in(client, member.user), "DELETE", f"/api/v1/account/change-request/{request_id}")

        assert response.status_code == 404
        assert db.session.get(MemberProfileChangeRequest, request_id).status == "pending"


class TestPaying:
    def test_no_billing_page_without_a_stripe_customer(self, app, client):
        member = _anna()

        response = send(signed_in(client, member.user), "POST", "/api/v1/account/billing")

        assert (response.status_code, response.get_json()["error"]["code"]) == (409, "no_billing")

    def test_the_billing_page_comes_back_to_my_account(self, app, client, monkeypatch):
        from aeronautics_members.services import billing

        made = []
        monkeypatch.setattr(billing, "apply_runtime_stripe_config", lambda: {})
        monkeypatch.setattr(billing.stripe.billing_portal.Session, "create",
                            staticmethod(lambda **kwargs: made.append(kwargs) or type(
                                "Portal", (), {"url": "https://billing.stripe.test/p"})()))
        member = _anna(stripe_customer_id="cus_a")

        response = send(signed_in(client, member.user), "POST", "/api/v1/account/billing")

        assert response.get_json() == {"url": "https://billing.stripe.test/p"}
        assert made[0]["return_url"].endswith("/account")

    def test_nothing_to_resume_for_a_paid_membership(self, app, client):
        member = _anna(payment_status="paid", is_active=True, membership_ends_on=date(2099, 12, 31))

        response = send(signed_in(client, member.user), "POST", "/api/v1/account/payment")

        assert (response.status_code, response.get_json()["error"]["code"]) == (409, "nothing_to_resume")
