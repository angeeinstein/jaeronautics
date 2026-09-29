"""A payment for something other than the membership must not extend it.

The association's Stripe account will sell more than the membership: the
teams' fees, a payment link for an event. The portal receives every event of
that account and used to match any paid invoice to a member by customer or
email -- so a student paying their team fee would have been given a year of
association membership, and a team subscription ending would have cancelled
it.

Everything recognisably the membership's is handled as before, and anything
that cannot be told apart is too, with an administrator told: ignoring a real
membership payment is the worse mistake.
"""
from datetime import date

import pytest
import stripe

from conftest import Member, clock, db, make_member, webhook_inbox
from aeronautics_members.db_models import MembershipPeriod, NotificationEvent, Setting
from aeronautics_members.services import billing, stripe_scope
from test_webhook import post_event

TODAY = clock.get_membership_today()
MEMBER_PRICE = "price_member_2026"
MEMBER_PRODUCT = "prod_membership"


@pytest.fixture(autouse=True)
def membership_price(app, monkeypatch):
    db.session.add(Setting(key="stripe_price_id", value=MEMBER_PRICE))
    db.session.commit()
    monkeypatch.setattr(stripe_scope, "_product_of_price", {})
    monkeypatch.setattr(
        stripe.Price, "retrieve",
        staticmethod(lambda price_id, **_: {"id": price_id, "product": MEMBER_PRODUCT}),
    )
    monkeypatch.setattr(billing, "send_member_welcome_email", lambda *a, **k: None, raising=False)
    from aeronautics_members.blueprints import webhook as webhook_module

    monkeypatch.setattr(webhook_module, "send_member_welcome_email", lambda *a, **k: None)


@pytest.fixture
def student(app):
    return make_member(email="student@example.com", stripe_customer_id="cus_student")


def _paid_invoice(invoice_id, *, metadata=None, subscription=None, lines=None):
    return {
        "id": "evt_" + invoice_id, "type": "invoice.paid", "created": 1_790_000_000,
        "data": {"object": {
            "id": invoice_id, "customer": "cus_student", "customer_email": "student@example.com",
            "billing_reason": "subscription_cycle", "total": 1000, "amount_paid": 1000,
            "status_transitions": {"paid_at": 1_790_000_000}, "created": 1_790_000_000,
            "parent": {"subscription_details": {
                "subscription": subscription, "metadata": metadata or {},
            }},
            "lines": {"data": lines or []},
        }},
    }


def _line(price, product):
    return {"pricing": {"price_details": {"price": price, "product": product}},
            "period": {"start": 1_790_000_000}}


def _periods(member):
    return db.session.query(MembershipPeriod).filter_by(member_id=member.id).all()


def _unclear_notices():
    return db.session.query(NotificationEvent).filter_by(event_type="stripe_event_scope_unclear").count()


class TestSomethingElseIsLeftAlone:
    def test_a_team_fee_marked_as_such_does_not_buy_a_membership(self, client, monkeypatch, student):
        response = post_event(client, monkeypatch, _paid_invoice(
            "in_team", subscription="sub_team", metadata={"purpose": "team:drones"},
            lines=[_line("price_team", "prod_team")],
        ))

        member = db.session.get(Member, student.id)
        assert response.status_code == 200
        assert _periods(member) == []
        assert member.is_active is False

    def test_another_product_does_not_buy_a_membership_even_unmarked(self, client, monkeypatch, student):
        """A payment link somebody made in the dashboard carries no metadata."""
        post_event(client, monkeypatch, _paid_invoice(
            "in_shirt", subscription="sub_shirt", lines=[_line("price_shirt", "prod_shirt")],
        ))

        assert _periods(db.session.get(Member, student.id)) == []

    def test_a_one_off_payment_with_no_invoice_is_not_the_membership(self, client, monkeypatch, student):
        """The membership is always billed through a subscription, so has an invoice."""
        monkeypatch.setattr(stripe.InvoicePayment, "list", staticmethod(lambda **_: {"data": []}))

        post_event(client, monkeypatch, {
            "id": "evt_pi", "type": "payment_intent.succeeded", "created": 1_790_000_000,
            "data": {"object": {"id": "pi_link", "customer": "cus_student", "created": 1_790_000_000}},
        })

        assert _periods(db.session.get(Member, student.id)) == []

    def test_a_team_subscription_ending_does_not_cancel_the_membership(self, client, monkeypatch, student):
        student.payment_status = "paid"
        student.is_active = True
        student.stripe_subscription_id = "sub_membership"
        db.session.commit()

        post_event(client, monkeypatch, {
            "id": "evt_team_end", "type": "customer.subscription.deleted", "created": 1_790_000_000,
            "data": {"object": {
                "id": "sub_team", "customer": "cus_student", "status": "canceled",
                "metadata": {"purpose": "team:drones"},
                "items": {"data": [{"price": {"id": "price_team", "product": "prod_team"}}]},
            }},
        })

        member = db.session.get(Member, student.id)
        assert member.payment_status == "paid"
        assert member.stripe_subscription_id == "sub_membership"

    def test_a_payment_link_checkout_for_another_product_is_left_alone(self, client, monkeypatch, student):
        monkeypatch.setattr(
            stripe.checkout.Session, "list_line_items",
            staticmethod(lambda session_id, **_: {"data": [{"price": {"id": "price_team", "product": "prod_team"}}]}),
        )

        post_event(client, monkeypatch, {
            "id": "evt_link", "type": "checkout.session.completed", "created": 1_790_000_000,
            "data": {"object": {
                "id": "cs_link", "customer": "cus_student", "subscription": "sub_team",
                "payment_status": "paid", "metadata": {},
                "customer_details": {"email": "student@example.com"},
            }},
        })

        member = db.session.get(Member, student.id)
        assert member.is_active is False
        assert member.stripe_subscription_id is None


class TestTheMembershipIsStillRecognised:
    def test_by_its_marker(self, client, monkeypatch, student):
        post_event(client, monkeypatch, _paid_invoice(
            "in_marked", subscription="sub_new", metadata={"purpose": "membership"},
        ))

        assert len(_periods(db.session.get(Member, student.id))) == 1

    def test_by_the_metadata_of_subscriptions_from_before_the_marker(self, client, monkeypatch, student):
        post_event(client, monkeypatch, _paid_invoice(
            "in_legacy", subscription="sub_october", metadata={"activation_mode": "paid_now"},
        ))

        assert len(_periods(db.session.get(Member, student.id))) == 1

    def test_by_a_subscription_the_portal_already_knows(self, client, monkeypatch, student):
        student.stripe_subscription_id = "sub_known"
        db.session.commit()

        post_event(client, monkeypatch, _paid_invoice("in_known", subscription="sub_known"))

        assert len(_periods(db.session.get(Member, student.id))) == 1

    def test_after_the_fee_changed_to_a_new_price(self, client, monkeypatch, student):
        """October's subscriptions stay on this year's price when next year's is set."""
        post_event(client, monkeypatch, _paid_invoice(
            "in_old_price", subscription="sub_unknown", lines=[_line("price_member_2025", MEMBER_PRODUCT)],
        ))

        assert len(_periods(db.session.get(Member, student.id))) == 1

    def test_a_first_invoice_with_the_prorated_line_beside_the_membership(self, client, monkeypatch, student):
        post_event(client, monkeypatch, _paid_invoice(
            "in_first", subscription="sub_unknown",
            lines=[_line(None, "prod_prorated_adhoc"), _line(MEMBER_PRICE, MEMBER_PRODUCT)],
        ))

        assert len(_periods(db.session.get(Member, student.id))) == 1

    def test_a_sepa_debit_starting_still_shows_as_on_its_way(self, client, monkeypatch, student):
        """Reported before its invoice can be found; marking it costs nothing."""
        monkeypatch.setattr(stripe.InvoicePayment, "list", staticmethod(lambda **_: {"data": []}))

        post_event(client, monkeypatch, {
            "id": "evt_processing", "type": "payment_intent.processing", "created": 1_790_000_000,
            "data": {"object": {"id": "pi_sepa", "customer": "cus_student"}},
        })

        assert db.session.get(Member, student.id).payment_status == "processing"


class TestWhenStripeCannotBeAsked:
    """Decided later rather than guessed now: Stripe delivers the event again."""

    def _outage(self, monkeypatch):
        def unreachable(*a, **k):
            raise stripe.APIConnectionError("Stripe is down")

        monkeypatch.setattr(stripe.Price, "retrieve", staticmethod(unreachable))

    def test_the_event_is_refused_and_left_for_stripe_to_deliver_again(self, client, monkeypatch, student):
        self._outage(monkeypatch)

        response = post_event(client, monkeypatch, _paid_invoice(
            "in_during_outage", subscription="sub_unknown", lines=[_line("price_2025", "prod_x")],
        ))

        assert response.status_code == 503
        assert _periods(db.session.get(Member, student.id)) == []
        assert webhook_inbox.stripe_event_already_processed("evt_in_during_outage") is False

    def test_the_redelivery_is_decided_once_stripe_answers(self, client, monkeypatch, student):
        self._outage(monkeypatch)
        event = _paid_invoice("in_retried", subscription="sub_unknown",
                              lines=[_line("price_member_2025", MEMBER_PRODUCT)])
        post_event(client, monkeypatch, event)

        monkeypatch.setattr(
            stripe.Price, "retrieve",
            staticmethod(lambda price_id, **_: {"id": price_id, "product": MEMBER_PRODUCT}),
        )
        response = post_event(client, monkeypatch, event)

        assert response.status_code == 200
        assert len(_periods(db.session.get(Member, student.id))) == 1

    def test_something_nobody_foresaw_is_asked_again_too(self, client, monkeypatch, student):
        """Not taken for "nothing to go on", which would drop it for good."""
        def surprise(**_):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(stripe.InvoicePayment, "list", staticmethod(surprise))

        response = post_event(client, monkeypatch, {
            "id": "evt_pi_surprise", "type": "payment_intent.succeeded", "created": 1_790_000_000,
            "data": {"object": {"id": "pi_x", "customer": "cus_student", "created": 1_790_000_000}},
        })

        assert response.status_code == 503


class TestWhenThereIsNothingToGoOn:
    def test_it_is_left_alone_and_an_administrator_is_told(self, client, monkeypatch, student):
        """A membership payment never looks like this: its invoices carry the
        portal's metadata. One made by hand in Stripe might, and then an
        administrator grants it."""
        response = post_event(client, monkeypatch, _paid_invoice("in_bare", subscription="sub_unknown"))

        assert response.status_code == 200
        assert _periods(db.session.get(Member, student.id)) == []
        assert _unclear_notices() == 1

    def test_stripe_saying_it_does_not_exist_is_nothing_to_go_on(self, client, monkeypatch, student):
        def missing(**_):
            raise stripe.InvalidRequestError("No such payment", "payment")

        monkeypatch.setattr(stripe.InvoicePayment, "list", staticmethod(missing))

        response = post_event(client, monkeypatch, {
            "id": "evt_pi_missing", "type": "payment_intent.succeeded", "created": 1_790_000_000,
            "data": {"object": {"id": "pi_gone", "customer": "cus_student", "created": 1_790_000_000}},
        })

        assert response.status_code == 200
        assert _periods(db.session.get(Member, student.id)) == []


class TestLookingUpTheMembershipsSubscription:
    def _listing(self, *subscriptions):
        return staticmethod(lambda **_: {"data": list(subscriptions)})

    def test_a_running_team_fee_is_not_a_running_membership(self, app, monkeypatch, student):
        monkeypatch.setattr(stripe.Subscription, "list", self._listing(
            {"id": "sub_team", "status": "active", "metadata": {"purpose": "team:drones"}},
            {"id": "sub_membership_old", "status": "canceled", "metadata": {"purpose": "membership"}},
        ))

        assert billing.find_live_stripe_subscription(student) is None

    def test_the_latest_subscription_is_the_membership_not_a_newer_team_fee(self, app, monkeypatch, student):
        monkeypatch.setattr(stripe.Subscription, "list", self._listing(
            {"id": "sub_team", "status": "active", "metadata": {"purpose": "team:drones"}},
            {"id": "sub_membership", "status": "active", "metadata": {"purpose": "membership"}},
        ))

        assert billing.get_latest_stripe_subscription_for_member(student)["id"] == "sub_membership"


def test_the_membership_checkout_marks_what_it_sells(app):
    member = make_member(email="marked@example.com")
    cycle = {"coverage_start": date(2026, 10, 1), "coverage_end": date(2026, 12, 31),
             "renewal_due_on": date(2027, 1, 1)}

    assert billing.build_membership_metadata(member, cycle, "free_period")["purpose"] == "membership"
