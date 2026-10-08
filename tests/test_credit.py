"""Credit: the settings, topping up through Stripe, what Stripe reports, cash,
corrections, refunds, erasure, and who sees what (services/credit.py,
api/credit.py).

Stripe is faked: every call the portal makes is recorded, and every event is
put through the real webhook route, marked ``purpose: credit``.
"""
import pytest
import stripe

from api_helpers import send, signed_in
from conftest import db
from aeronautics_members.db_models import CreditAccount, CreditEntry, NotificationEvent, Payment
from aeronautics_members.services import ConflictError, ValidationError, credit, privacy
from test_admin_reviews import _staff
from test_team_money import _treasurer
from test_teams_flow import _person
from test_teams_foundation import _association_member
from test_webhook import post_event

PRODUCT = "prod_credit"


class FakeStripe:
    def __init__(self):
        self.calls = []
        self.sessions = {}
        self.product = {"id": PRODUCT, "active": True}
        self.refusing = set()

    def named(self, name):
        return [call for call in self.calls if call[0] == name]


@pytest.fixture
def fake_stripe(monkeypatch):
    fake = FakeStripe()

    def create_session(idempotency_key=None, **params):
        fake.calls.append(("session.create", idempotency_key, params))
        number = len(fake.sessions) + 1
        session = {"id": f"cs_{number}", "url": f"https://checkout.example/{number}", "status": "open"}
        fake.sessions[session["id"]] = session
        return session

    def expire(session_id, **k):
        fake.calls.append(("session.expire", session_id))
        fake.sessions[session_id]["status"] = "expired"

    def refund(idempotency_key=None, **params):
        if params["payment_intent"] in fake.refusing:
            raise stripe.InvalidRequestError("Too old to refund.", param="payment_intent")
        fake.calls.append(("refund", idempotency_key, params))
        return {"id": f"re_{len(fake.calls)}", "amount": params["amount"]}

    monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(create_session))
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", staticmethod(lambda sid, **k: fake.sessions[sid]))
    monkeypatch.setattr(stripe.checkout.Session, "expire", staticmethod(expire))
    monkeypatch.setattr(stripe.Product, "retrieve", staticmethod(lambda pid, **k: dict(fake.product, id=pid)))
    monkeypatch.setattr(stripe.Refund, "create", staticmethod(refund))
    monkeypatch.setattr(stripe.Customer, "modify", staticmethod(lambda cid, **k: {"id": cid}))
    monkeypatch.setattr(stripe.Invoice, "retrieve",
                        staticmethod(lambda iid, **k: {"id": iid, "hosted_invoice_url": f"https://invoice/{iid}"}))
    return fake


@pytest.fixture
def credit_on(app, fake_stripe):
    credit.save_settings(None, enabled=True, product_id=PRODUCT, min_top_up_cents=1000, max_balance_cents=2000,
                         suggested_cents=[1000, 1500, 2000], eps=False)
    db.session.commit()
    return fake_stripe


def _paid_session(user, amount, number=1, *, status="paid", event="checkout.session.completed"):
    metadata = {"purpose": "credit", "user_id": str(user.id), "amount_cents": str(amount)}
    return {"id": f"evt_{event}_{number}", "type": event, "data": {"object": {
        "id": f"cs_{number}", "object": "checkout.session", "metadata": metadata, "payment_status": status,
        "amount_total": amount, "currency": "eur", "payment_intent": f"pi_{number}", "invoice": f"in_{number}",
        "customer": "cus_anna",
    }}}


def _topped_up(client, monkeypatch, user, amount=1500, number=1):
    assert post_event(client, monkeypatch, _paid_session(user, amount, number)).status_code == 200
    return credit.balance_of(user)


def _entries(user):
    return [(entry.kind, entry.amount_cents, entry.balance_after_cents) for entry in reversed(credit.history(user))]


class TestSettings:
    def test_off_until_switched_on(self, app):
        current = credit.settings()
        assert not current.enabled
        assert (current.min_top_up_cents, current.max_balance_cents) == (1000, 2000)
        assert current.suggested_cents == (1000, 1500, 2000)

    def test_switching_on_needs_the_product(self, app, fake_stripe):
        with pytest.raises(ValidationError) as refused:
            credit.save_settings(None, enabled=True, product_id="", min_top_up_cents=1000, max_balance_cents=2000,
                                 suggested_cents=[1000], eps=False)
        assert "product_id" in refused.value.details["fields"]

    @pytest.mark.parametrize("fields, field", [
        ({"product_id": "price_1"}, "product_id"),
        ({"min_top_up_cents": 50}, "min_top_up_cents"),
        ({"max_balance_cents": 500}, "max_balance_cents"),
        ({"suggested_cents": [3000]}, "suggested_cents"),
        ({"suggested_cents": []}, "suggested_cents"),
    ])
    def test_what_is_refused(self, app, fake_stripe, fields, field):
        values = dict(enabled=False, product_id=PRODUCT, min_top_up_cents=1000, max_balance_cents=2000,
                      suggested_cents=[1000], eps=False)
        values.update(fields)
        with pytest.raises(ValidationError) as refused:
            credit.save_settings(None, **values)
        assert field in refused.value.details["fields"]

    def test_an_archived_product_is_refused(self, app, fake_stripe):
        fake_stripe.product["active"] = False
        with pytest.raises(ValidationError):
            credit.save_settings(None, enabled=True, product_id=PRODUCT, min_top_up_cents=1000,
                                 max_balance_cents=2000, suggested_cents=[1000], eps=False)

    def test_through_the_api(self, client, fake_stripe):
        signed_in(client, _staff("admin@example.org", "admin"))

        response = send(client, "PUT", "/api/v1/admin/settings/credit", {
            "enabled": True, "product_id": PRODUCT, "min_top_up_cents": 1000, "max_balance_cents": 3000,
            "suggested_cents": [2000, 1000], "eps": True,
        })

        assert response.status_code == 200, response.get_json()
        body = client.get("/api/v1/admin/settings/credit").get_json()
        assert body["enabled"] and body["eps"] and body["max_balance_cents"] == 3000
        assert body["suggested_cents"] == [1000, 2000]


class TestTopUp:
    def test_checkout_for_the_amount_on_the_product(self, credit_on):
        anna = _person()

        url = credit.start_top_up(anna, 1500)
        db.session.commit()

        assert url == "https://checkout.example/1"
        [(_name, key, params)] = credit_on.named("session.create")
        assert key.startswith(f"checkout:credit:{anna.id}:")
        assert params["mode"] == "payment" and params["payment_method_types"] == ["card"]
        assert params["line_items"] == [{"quantity": 1, "price_data": {
            "currency": "eur", "product": PRODUCT, "unit_amount": 1500}}]
        assert params["metadata"]["purpose"] == "credit"
        assert params["payment_intent_data"]["metadata"]["purpose"] == "credit"
        assert params["invoice_creation"]["enabled"]
        assert params["success_url"].endswith("/account/credit?topped_up=1")
        assert db.session.get(CreditAccount, anna.id).checkout_amount_cents == 1500

    def test_eps_beside_cards_when_switched_on(self, credit_on):
        credit.save_settings(None, enabled=True, product_id=PRODUCT, min_top_up_cents=1000,
                             max_balance_cents=2000, suggested_cents=[1000], eps=True)
        credit.start_top_up(_person(), 1000)
        assert credit_on.named("session.create")[0][2]["payment_method_types"] == ["card", "eps"]

    def test_the_same_amount_again_is_the_same_page_another_closes_it(self, credit_on):
        anna = _person()
        first = credit.start_top_up(anna, 1500)
        assert credit.start_top_up(anna, 1500) == first
        second = credit.start_top_up(anna, 1000)

        assert second != first
        assert credit_on.named("session.expire") == [("session.expire", "cs_1")]

    def test_back_to_an_amount_after_another_is_a_new_page(self, credit_on):
        anna = _person()
        credit.start_top_up(anna, 1500)
        credit.start_top_up(anna, 1000)
        credit.start_top_up(anna, 1500)

        keys = [key for _name, key, _params in credit_on.named("session.create")]
        assert len(keys) == len(set(keys)) == 3

    @pytest.mark.parametrize("amount, code", [(500, "credit_below_least"), (2500, "credit_above_most")])
    def test_amounts_outside_the_limits(self, credit_on, amount, code):
        with pytest.raises(ValidationError) as refused:
            credit.start_top_up(_person(), amount)
        assert refused.value.code == code

    def test_never_past_the_most_a_person_holds(self, client, monkeypatch, credit_on):
        anna = _person()
        _topped_up(client, monkeypatch, anna, 1500)

        assert credit.top_up_room(anna) == 500
        assert credit.top_up_choices(anna) == []
        assert "below" in credit.may_top_up(anna)
        with pytest.raises(ConflictError):
            credit.start_top_up(anna, 1000)

    def test_only_for_members(self, credit_on):
        former = _association_member("former@example.com", active=False)
        assert credit.may_top_up(former) == "Topping up is for members of the association."

    def test_not_while_switched_off(self, app, fake_stripe):
        assert credit.may_top_up(_person()) == "Credit is switched off."


class TestWhatStripeReports:
    def test_a_paid_top_up_is_added_once(self, client, monkeypatch, credit_on):
        anna = _person()
        credit.start_top_up(anna, 1500)
        db.session.commit()

        assert _topped_up(client, monkeypatch, anna, 1500) == 1500
        assert post_event(client, monkeypatch, _paid_session(anna, 1500)).status_code == 200  # again

        assert _entries(anna) == [("top_up", 1500, 1500)]
        payment = db.session.query(Payment).filter_by(purpose="credit").one()
        assert (payment.amount_cents, payment.stripe_payment_intent_id, payment.stripe_invoice_id) == (
            1500, "pi_1", "in_1")
        assert db.session.get(CreditAccount, anna.id).stripe_checkout_session_id is None

    def test_money_on_its_way_counts_once_it_arrived(self, client, monkeypatch, credit_on):
        anna = _person()
        post_event(client, monkeypatch, _paid_session(anna, 1000, status="unpaid"))
        assert credit.balance_of(anna) == 0

        post_event(client, monkeypatch, _paid_session(anna, 1000, event="checkout.session.async_payment_succeeded"))

        assert credit.balance_of(anna) == 1000

    def test_added_even_when_switched_off_since(self, client, monkeypatch, credit_on):
        anna = _person()
        credit.save_settings(None, enabled=False, product_id=PRODUCT, min_top_up_cents=1000,
                             max_balance_cents=2000, suggested_cents=[1000], eps=False)
        db.session.commit()

        assert _topped_up(client, monkeypatch, anna, 1000) == 1000

    def test_never_reaches_the_membership(self, client, monkeypatch, credit_on):
        anna = _person()
        before = anna.member.payment_status
        _topped_up(client, monkeypatch, anna, 1000)
        db.session.refresh(anna.member)
        assert anna.member.payment_status == before

    def test_a_refund_made_in_stripe_comes_off(self, client, monkeypatch, credit_on):
        anna = _person()
        _topped_up(client, monkeypatch, anna, 1500)
        charge = {"id": "ch_1", "object": "charge", "payment_intent": "pi_1", "amount_refunded": 500,
                  "metadata": {"purpose": "credit"}}

        for _twice in range(2):
            post_event(client, monkeypatch, {"id": "evt_r1", "type": "charge.refunded", "data": {"object": charge}})
            post_event(client, monkeypatch, {"id": "evt_r2", "type": "charge.refunded", "data": {"object": charge}})

        assert _entries(anna) == [("top_up", 1500, 1500), ("refund", -500, 1000)]

    def test_a_lost_chargeback_comes_off(self, client, monkeypatch, credit_on):
        anna = _person()
        _topped_up(client, monkeypatch, anna, 1500)
        monkeypatch.setattr(stripe.Charge, "retrieve", staticmethod(
            lambda cid, **k: {"id": cid, "payment_intent": "pi_1", "metadata": {"purpose": "credit"}}))
        monkeypatch.setattr(stripe.PaymentIntent, "retrieve", staticmethod(
            lambda pid, **k: {"id": pid, "metadata": {"purpose": "credit"}}))
        dispute = {"id": "dp_1", "object": "dispute", "status": "lost", "charge": "ch_1",
                   "payment_intent": {"id": "pi_1", "metadata": {"purpose": "credit"}}, "amount": 1500}

        post_event(client, monkeypatch, {"id": "evt_d", "type": "charge.dispute.closed", "data": {"object": dispute}})

        assert _entries(anna)[-1] == ("reversal", -1500, 0)


class TestByHand:
    def test_cash_in_and_out(self, credit_on):
        anna, treasurer = _person(), _treasurer()

        credit.book_cash(treasurer, anna, 1000, note="Handed over at the office")
        credit.book_cash(treasurer, anna, 400, paid_out=True)

        assert _entries(anna) == [("cash_in", 1000, 1000), ("cash_out", -400, 600)]
        assert credit.history(anna)[0].booked_by == treasurer
        assert credit.history(anna)[1].description == "Handed over at the office"

    def test_cash_respects_the_most_and_what_there_is(self, credit_on):
        anna = _person()
        with pytest.raises(ValidationError):
            credit.book_cash(None, anna, 2500)
        with pytest.raises(ValidationError):
            credit.book_cash(None, anna, 100, paid_out=True)

    def test_corrections_need_a_reason_and_stay_above_zero(self, credit_on):
        anna = _person()
        with pytest.raises(ValidationError):
            credit.correct(None, anna, 500, note=" ")
        with pytest.raises(ValidationError):
            credit.correct(None, anna, -100, note="Mistake")
        credit.correct(None, anna, 300, note="Coffee machine kept the coins")
        assert credit.balance_of(anna) == 300

    def test_spending(self, credit_on):
        anna = _person()
        credit.book_cash(None, anna, 1000)

        credit.spend(anna, 120, "Coffee")

        assert credit.balance_of(anna) == 880
        with pytest.raises(ConflictError):
            credit.spend(anna, 2000, "Everything")

    def test_the_balance_is_the_sum_of_the_entries(self, credit_on):
        anna = _person()
        credit.book_cash(None, anna, 1000)
        assert credit.mismatched_balances() == []
        db.session.get(CreditAccount, anna.id).balance_cents = 5
        assert credit.mismatched_balances() == [anna.id]


class TestRefunds:
    def test_newest_top_up_first_never_more_than_paid(self, client, monkeypatch, credit_on):
        anna = _person()
        _topped_up(client, monkeypatch, anna, 1000, number=1)
        credit.spend(anna, 300, "Coffee")
        _topped_up(client, monkeypatch, anna, 1000, number=2)
        db.session.commit()

        refunded, left = credit.refund_balance(anna)

        assert (refunded, left) == (1700, 0)
        refunds = [(params["payment_intent"], params["amount"]) for _n, _k, params in credit_on.named("refund")]
        assert refunds == [("pi_2", 1000), ("pi_1", 700)]
        assert credit.balance_of(anna) == 0

    def test_the_webhook_books_a_refund_made_here_no_second_time(self, client, monkeypatch, credit_on):
        anna = _person()
        _topped_up(client, monkeypatch, anna, 1000)
        credit.refund_balance(anna)
        db.session.commit()
        charge = {"id": "ch_1", "object": "charge", "payment_intent": "pi_1", "amount_refunded": 1000,
                  "metadata": {"purpose": "credit"}}

        post_event(client, monkeypatch, {"id": "evt_r", "type": "charge.refunded", "data": {"object": charge}})

        assert _entries(anna) == [("top_up", 1000, 1000), ("refund", -1000, 0)]

    def test_cash_and_old_payments_are_left_for_the_treasurer(self, client, monkeypatch, credit_on):
        anna = _person()
        _topped_up(client, monkeypatch, anna, 1000)
        credit.book_cash(None, anna, 500)
        credit_on.refusing.add("pi_1")

        assert credit.refund_balance(anna) == (0, 1500)

    def test_erasing_refunds_and_reports_the_rest(self, client, monkeypatch, credit_on):
        anna = _person()
        _topped_up(client, monkeypatch, anna, 1000)
        credit.book_cash(None, anna, 300)
        db.session.commit()
        assert privacy.describe_deletion_impact(anna)["credit_cents"] == 1300

        summary = privacy.erase_account(anna, initiated_by=privacy.INITIATED_BY_MEMBER)

        assert (summary["credit_refunded_cents"], summary["credit_left_cents"]) == (1000, 300)
        assert db.session.query(NotificationEvent).filter_by(event_type="credit_left_at_erasure").count() == 1
        assert db.session.query(CreditEntry).filter_by(user_id=anna.id).count() == 3  # kept: the bookkeeping


class TestPages:
    def test_my_credit(self, client, monkeypatch, credit_on):
        anna = _person()
        _topped_up(client, monkeypatch, anna, 1000)
        signed_in(client, anna)

        body = client.get("/api/v1/account/credit").get_json()

        assert body["balance_cents"] == 1000
        assert body["top_up"] == {"refused": None, "choices": [1000], "least_cents": 1000, "most_cents": 1000,
                                  "max_balance_cents": 2000}
        [entry] = body["entries"]
        assert entry["kind"] == "top_up" and entry["receipt_url"] == f"/account/credit/receipt/{entry['id']}"
        assert client.get("/api/v1/me").get_json()["credit_area"] is True

    def test_receipt_and_spreadsheet(self, client, monkeypatch, credit_on):
        anna = _person()
        _topped_up(client, monkeypatch, anna, 1000)
        signed_in(client, anna)
        entry = credit.history(anna)[0]

        assert client.get(f"/account/credit/receipt/{entry.id}").headers["Location"] == "https://invoice/in_1"
        csv_text = client.get("/account/credit/history.csv").get_data(as_text=True)
        assert "Top-up" in csv_text and "10.00" in csv_text

    def test_somebody_elses_receipt_is_not_found(self, client, monkeypatch, credit_on):
        anna = _person()
        _topped_up(client, monkeypatch, anna, 1000)
        signed_in(client, _person("ben@example.com", "Ben", "Other"))
        assert client.get(f"/account/credit/receipt/{credit.history(anna)[0].id}").status_code == 404

    def test_top_up_through_the_api(self, client, credit_on):
        signed_in(client, _person())
        response = send(client, "POST", "/api/v1/account/credit/top-up", {"amount_cents": 2000})
        assert response.status_code == 200 and response.get_json()["url"] == "https://checkout.example/1"

    def test_hidden_while_switched_off(self, client, fake_stripe):
        signed_in(client, _person())
        assert client.get("/api/v1/account/credit").status_code == 404
        assert client.get("/api/v1/me").get_json()["credit_area"] is False

    def test_not_for_an_account_without_membership_or_credit(self, client, credit_on):
        signed_in(client, _association_member("former@example.com", active=False))
        assert client.get("/api/v1/account/credit").status_code == 404


class TestTheAssociationsSide:
    def test_only_with_the_permission(self, client, credit_on):
        signed_in(client, _person())
        assert client.get("/api/v1/admin/credit").status_code == 403

    def test_the_treasurer_sees_and_books(self, client, monkeypatch, credit_on):
        anna = _person()
        _topped_up(client, monkeypatch, anna, 1000)
        signed_in(client, _treasurer())

        overview = client.get("/api/v1/admin/credit").get_json()
        assert overview["totals"]["held_cents"] == 1000 and overview["totals"]["came_in_cents"] == 1000
        assert [person["name"] for person in overview["people"]] == ["Anna Berger"]
        assert client.get("/api/v1/me").get_json()["credit_admin"] is True

        holder = send(client, "POST", f"/api/v1/admin/credit/{anna.id}/cash",
                      {"amount_cents": 500, "note": "At the office"}).get_json()
        assert holder["balance_cents"] == 1500 and holder["entries"][0]["booked_by"] is not None
        assert holder["refundable_cents"] == 1000

        refunded = send(client, "POST", f"/api/v1/admin/credit/{anna.id}/refund").get_json()
        assert (refunded["refunded_cents"], refunded["left_cents"]) == (1000, 500)

        corrected = send(client, "POST", f"/api/v1/admin/credit/{anna.id}/correction",
                         {"amount_cents": -500, "note": "Paid out at the office"})
        assert corrected.status_code == 200 and corrected.get_json()["balance_cents"] == 0

    def test_the_spreadsheet_names_nobody(self, client, monkeypatch, credit_on):
        anna = _person()
        _topped_up(client, monkeypatch, anna, 1000)
        signed_in(client, _treasurer())

        text = client.get("/admin/credit/export.csv").get_data(as_text=True)

        assert f"#{anna.id}" in text and "Anna" not in text

    def test_the_admin_account_page_shows_the_balance(self, client, monkeypatch, credit_on):
        anna = _person()
        _topped_up(client, monkeypatch, anna, 1000)
        signed_in(client, _staff("admin@example.org", "admin"))

        assert client.get(f"/api/v1/admin/accounts/{anna.id}").get_json()["credit_cents"] == 1000
