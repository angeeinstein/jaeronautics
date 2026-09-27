"""Coming back after a membership has ended.

Stripe cannot restart a cancelled subscription, and the billing portal only
reopens one that has not ended yet. Before the rejoin button, somebody whose
membership lapsed had no way back at all: the signup page sent them to log in,
and their account page offered only a billing portal with nothing in it to
renew.
"""
from datetime import date

import pytest

from conftest import billing, clock, db, make_member
from aeronautics_members.db_models import AuditLog, Member, Setting

TODAY = clock.get_membership_today()
LAST_YEAR_END = date(TODAY.year - 1, 12, 31)


class FakeSession(dict):
    @property
    def url(self):
        return self["url"]


def _ended(email="lapsed@example.com", **overrides):
    fields = dict(
        payment_status="canceled", is_active=False,
        stripe_customer_id="cus_old", stripe_subscription_id="sub_old",
        membership_starts_on=date(TODAY.year - 1, 3, 1), membership_ends_on=LAST_YEAR_END,
    )
    fields.update(overrides)
    return make_member(email=email, **fields)


def _sign_in(client, member):
    with client.session_transaction() as session:
        session["_user_id"] = str(member.user.id)


@pytest.fixture
def stripe_stub(monkeypatch):
    """Records what would be sent to Stripe; what Stripe answers is set per test."""
    calls = {"checkout": [], "modify": [], "customer_create": [], "subscription_create": []}
    state = {"subscriptions": [], "list_error": None, "modify_error": None}

    def list_subscriptions(**kwargs):
        if state["list_error"] is not None:
            raise state["list_error"]
        return {"data": state["subscriptions"]}

    def modify_customer(customer_id, **kwargs):
        if state["modify_error"] is not None:
            raise state["modify_error"]
        calls["modify"].append((customer_id, kwargs))
        return {"id": customer_id}

    def create_checkout(**kwargs):
        calls["checkout"].append(kwargs)
        return FakeSession(id="cs_rejoin", url="https://checkout.stripe.test/cs_rejoin", status="open")

    def create_customer(**kwargs):
        calls["customer_create"].append(kwargs)
        return type("Customer", (), {"id": "cus_brand_new"})()

    def create_subscription(**kwargs):
        calls["subscription_create"].append(kwargs)
        return type("Subscription", (), {"id": "sub_new"})()

    monkeypatch.setattr(billing.stripe.Subscription, "list", staticmethod(list_subscriptions))
    monkeypatch.setattr(billing.stripe.Subscription, "create", staticmethod(create_subscription))
    monkeypatch.setattr(billing.stripe.Customer, "modify", staticmethod(modify_customer))
    monkeypatch.setattr(billing.stripe.Customer, "create", staticmethod(create_customer))
    monkeypatch.setattr(billing.stripe.checkout.Session, "create", staticmethod(create_checkout))
    monkeypatch.setattr(billing, "apply_runtime_stripe_config", lambda: {"stripe_price_id": "price_test"})
    monkeypatch.setattr(
        billing, "get_stripe_membership_price",
        lambda: {"id": "price_test", "currency": "eur", "unit_amount": 3000,
                 "interval": "year", "interval_count": 1},
    )
    calls["state"] = state
    return calls


class TestWhoIsOfferedIt:
    def test_a_cancelled_membership_that_has_run_out(self, app):
        assert billing.can_rejoin(_ended()) is True

    def test_one_stripe_gave_up_on(self, app):
        assert billing.can_rejoin(_ended(payment_status="failed")) is True

    def test_not_while_the_membership_still_runs(self, app):
        member = _ended(payment_status="paid", is_active=True,
                        membership_ends_on=date(TODAY.year, 12, 31))
        assert billing.can_rejoin(member) is False

    def test_not_a_cancelled_one_that_still_has_its_year(self, app):
        """They paid through Dec 31; rejoining now would charge them twice."""
        member = _ended(membership_ends_on=date(TODAY.year, 12, 31))
        assert billing.can_rejoin(member) is False

    def test_not_a_signup_that_was_never_finished(self, app):
        """That is what "Resume Payment" is for."""
        assert billing.can_rejoin(_ended(payment_status="pending_checkout")) is False

    def test_not_an_erased_account(self, app):
        member = _ended()
        member.deleted_at = clock.get_now_utc()
        db.session.commit()
        assert billing.can_rejoin(member) is False


class TestTheAccountPage:
    def test_it_offers_rejoining(self, app, client):
        member = _ended()
        _sign_in(client, member)

        body = client.get("/account", follow_redirects=True).get_data(as_text=True)

        assert "/account/rejoin" in body
        assert 'name="payment_method"' not in body  # invoices are off by default

    def test_with_the_invoice_choice_when_invoices_are_on(self, app, client):
        db.session.add(Setting(key="invoice_payments_enabled", value="True"))
        member = _ended()
        _sign_in(client, member)

        body = client.get("/account", follow_redirects=True).get_data(as_text=True)

        assert 'name="payment_method"' in body

    def test_not_to_a_member_whose_membership_runs(self, app, client):
        member = _ended(payment_status="paid", is_active=True,
                        membership_ends_on=date(TODAY.year, 12, 31))
        _sign_in(client, member)

        body = client.get("/account", follow_redirects=True).get_data(as_text=True)

        assert "/account/rejoin" not in body


class TestRejoining:
    def test_it_opens_checkout_for_the_same_stripe_customer(self, app, client, stripe_stub):
        member = _ended()
        _sign_in(client, member)

        response = client.post("/account/rejoin")

        assert response.status_code == 303
        assert response.headers["Location"] == "https://checkout.stripe.test/cs_rejoin"
        (sent,) = stripe_stub["checkout"]
        assert sent["customer"] == "cus_old"
        assert "customer_email" not in sent  # Stripe refuses both
        # A new key: the old one may still hold the first session of this year.
        assert sent["idempotency_key"].endswith(":after:sub_old")
        # Receipts go to the address Stripe holds, so it is brought up to date.
        assert stripe_stub["modify"] == [
            ("cus_old", {"email": "lapsed@example.com", "name": "Test Member"}),
        ]
        assert db.session.execute(
            db.select(AuditLog).filter_by(event_type="membership_rejoin_started")
        ).scalar_one() is not None

    def test_a_customer_gone_from_stripe_is_replaced(self, app, client, stripe_stub):
        stripe_stub["state"]["modify_error"] = billing.stripe.InvalidRequestError(
            "No such customer", param="id", code="resource_missing",
        )
        member = _ended()
        _sign_in(client, member)

        client.post("/account/rejoin")

        (sent,) = stripe_stub["checkout"]
        assert sent["customer_email"] == "lapsed@example.com"
        assert "customer" not in sent

    def test_paying_by_invoice_keeps_the_customer_too(self, app, client, stripe_stub):
        db.session.add(Setting(key="invoice_payments_enabled", value="True"))
        member = _ended()
        _sign_in(client, member)

        client.post("/account/rejoin", data={"payment_method": "invoice"})

        assert stripe_stub["customer_create"] == []
        (sent,) = stripe_stub["subscription_create"]
        assert sent["customer"] == "cus_old"
        refreshed = db.session.get(Member, member.id)
        assert refreshed.stripe_customer_id == "cus_old"
        assert refreshed.stripe_subscription_id == "sub_new"

    def test_a_subscription_still_running_is_not_doubled(self, app, client, stripe_stub):
        """On Jan 1 the portal can read "expired" while Stripe collects the renewal."""
        stripe_stub["state"]["subscriptions"] = [
            {"id": "sub_running", "status": "past_due", "customer": "cus_old", "metadata": {}},
        ]
        member = _ended(payment_status="expired")
        _sign_in(client, member)

        body = client.post("/account/rejoin", follow_redirects=True).get_data(as_text=True)

        assert stripe_stub["checkout"] == []
        assert "still running in Stripe" in body
        assert db.session.get(Member, member.id).stripe_subscription_id == "sub_running"

    def test_an_ended_subscription_in_stripe_does_not_stop_it(self, app, client, stripe_stub):
        stripe_stub["state"]["subscriptions"] = [
            {"id": "sub_old", "status": "canceled", "customer": "cus_old"},
        ]
        member = _ended()
        _sign_in(client, member)

        client.post("/account/rejoin")

        assert len(stripe_stub["checkout"]) == 1

    def test_stripe_unreachable_charges_nothing(self, app, client, stripe_stub):
        """"Could not tell" must not be taken for "nothing running"."""
        stripe_stub["state"]["list_error"] = billing.stripe.APIConnectionError("down")
        member = _ended()
        _sign_in(client, member)

        body = client.post("/account/rejoin", follow_redirects=True).get_data(as_text=True)

        assert stripe_stub["checkout"] == []
        assert "could not check your billing" in body

    def test_refused_while_the_membership_runs(self, app, client, stripe_stub):
        member = _ended(payment_status="paid", is_active=True,
                        membership_ends_on=date(TODAY.year, 12, 31))
        _sign_in(client, member)

        client.post("/account/rejoin")

        assert stripe_stub["checkout"] == []


class TestFinishingTheCheckout:
    def test_the_new_subscription_replaces_the_ended_one(self, app, client, monkeypatch):
        from test_webhook import checkout_event, post_event
        from aeronautics_members.blueprints import webhook as webhook_module

        monkeypatch.setattr(webhook_module, "send_member_welcome_email", lambda *a, **k: None)
        member = _ended(stripe_customer_id="cus_test_1")
        event = checkout_event(member, member.user, activation_mode="paid_now",
                               payment_status="paid", event_id="evt_rejoined")

        post_event(client, monkeypatch, event)

        refreshed = db.session.get(Member, member.id)
        assert refreshed.stripe_customer_id == "cus_test_1"
        assert refreshed.stripe_subscription_id == "sub_test_1"
        assert refreshed.payment_status == "paid"
        assert refreshed.is_active is True
        assert refreshed.membership_ends_on >= TODAY
