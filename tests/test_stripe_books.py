"""Stripe's books in the portal (services/stripe_books.py), the overview read from them
(services/money_overview.py) and the check against the portal's records
(services/money_checks.py) -- docs/money-ledger-plan.md.

Stripe is faked: one account with a membership fee (and one from last year), team fees
(by subscription, and paid once and refunded), a credit top-up, a webshop payment, a SEPA
debit that bounced, Stripe billing its own fee and a payout to the bank.
"""
from datetime import date, datetime, time, timedelta, timezone

import pytest
import stripe

from conftest import db, make_member
from aeronautics_members.config import MEMBERSHIP_TIMEZONE
from aeronautics_members.db_models import (
    ExternalWorkItem, MembershipPeriod, MoneyDifference, Payment, StripeBooking, TeamPayout,
)
from aeronautics_members.services import money_checks, money_overview, stripe_books
from aeronautics_members.services.clock import get_membership_today
from test_admin_reviews import _login, _staff
from test_teams_foundation import _team

TODAY = get_membership_today()
YEAR = TODAY.year


def _unix(day, hour=1):
    return int(datetime.combine(day, time(hour, 0), MEMBERSHIP_TIMEZONE).timestamp())


THIS_YEAR = _unix(date(YEAR, 1, 1))
LAST_YEAR = _unix(date(YEAR - 1, 6, 1))


class _Listing:
    def __init__(self, items):
        self.items = items

    def auto_paging_iter(self):
        return iter(self.items)

    def get(self, key, default=None):
        return self.items if key == "data" else default


def _txn(txn_id, kind, amount, source, fee=0, created=THIS_YEAR):
    return {"id": txn_id, "type": kind, "amount": amount, "fee": fee, "net": amount - fee, "currency": "eur",
            "source": source, "created": created}


def _charge(charge_id, payment_intent, **metadata):
    return {"id": charge_id, "object": "charge", "payment_intent": payment_intent, "metadata": metadata}


def _invoice(invoice_id, amount, purpose, created=THIS_YEAR):
    return {"id": invoice_id, "amount_paid": amount, "status": "paid", "customer_email": f"{invoice_id}@example.com",
            "metadata": {}, "created": created,
            "parent": {"subscription_details": {"subscription": f"sub_{invoice_id}", "metadata": {"purpose": purpose}}}}


@pytest.fixture
def stripe_account(monkeypatch):
    account = {
        "txns": [
            _txn("txn_old", "charge", 1500, _charge("ch_old", "pi_old"), fee=50, created=LAST_YEAR),
            _txn("txn_m1", "charge", 1500, _charge("ch_m1", "pi_m1"), fee=50),       # a membership
            _txn("txn_t1", "charge", 1000, _charge("ch_t1", "pi_t1"), fee=30),       # a team fee the portal has
            _txn("txn_t2", "charge", 1000, _charge("ch_t2", "pi_t2"), fee=30),       # a team fee the portal lacks
            _txn("txn_t3", "charge", 1000, _charge("ch_t3", "pi_t3", purpose="team"), fee=30),  # paid once
            _txn("txn_re1", "refund", -1000, {"id": "re_1", "object": "refund", "charge": "ch_t3",
                                              "payment_intent": "pi_t3"}),
            _txn("txn_c1", "charge", 2000, _charge("ch_c1", "pi_c1"), fee=55),       # a credit top-up
            _txn("txn_shop", "payment", 2000, _charge("py_shop", "pi_shop")),        # the webshop
            _txn("txn_bounce", "payment", 500, _charge("py_bounce", "pi_bounce")),   # a SEPA debit that bounced
            _txn("txn_pfr", "payment_failure_refund", -500, {"id": "pfr_1", "object": "refund",
                                                             "charge": "py_bounce"}),
            _txn("txn_fee", "stripe_fee", -20, "fee_billing"),
            _txn("txn_po", "payout", -3000, "po_1"),
        ],
        "invoices": {i["id"]: i for i in (_invoice("in_old", 1500, "membership", created=LAST_YEAR),
                                          _invoice("in_m1", 1500, "membership"), _invoice("in_t1", 1000, "team"),
                                          _invoice("in_t2", 1000, "team"))},
        "paid_by": {"pi_old": "in_old", "pi_m1": "in_m1", "pi_t1": "in_t1", "pi_t2": "in_t2"},
        "balance": {"available": [{"amount": 1000, "currency": "eur"}],
                    "pending": [{"amount": 4735, "currency": "eur"}]},
        "asked": 0,
    }

    def balance_transactions(created=None, **kwargs):
        account["asked"] += 1
        since = (created or {}).get("gte", 0)
        return _Listing([txn for txn in account["txns"] if txn["created"] >= since])

    def invoice_payments(payment=None, **kwargs):
        invoice = account["paid_by"].get((payment or {}).get("payment_intent"))
        return _Listing([{"invoice": invoice}] if invoice else [])

    def invoice(invoice_id, **kwargs):
        return account["invoices"].get(invoice_id) or {"id": invoice_id, "status": "open", "metadata": {}}

    def charge(charge_id, **kwargs):
        for txn in account["txns"]:
            source = txn["source"]
            if isinstance(source, dict) and source.get("id") == charge_id:
                return source
        raise stripe.InvalidRequestError("No such charge", "id")

    monkeypatch.setattr(stripe.BalanceTransaction, "list", staticmethod(balance_transactions))
    monkeypatch.setattr(stripe.InvoicePayment, "list", staticmethod(invoice_payments))
    monkeypatch.setattr(stripe.Invoice, "retrieve", staticmethod(invoice))
    monkeypatch.setattr(stripe.Charge, "retrieve", staticmethod(charge))
    monkeypatch.setattr(stripe.PaymentIntent, "retrieve",
                        staticmethod(lambda intent_id, **k: {"id": intent_id, "metadata": {}}))
    monkeypatch.setattr(stripe.Balance, "retrieve", staticmethod(lambda **k: account["balance"]))
    monkeypatch.setattr(stripe_books.payments, "apply_runtime_stripe_config", lambda: {})
    return account


def _portal_records():
    now = datetime.now(timezone.utc)
    member = make_member(email="anna@example.com")
    db.session.add(MembershipPeriod(member_id=member.id, starts_on=date(YEAR, 1, 1), ends_on=date(YEAR, 12, 31),
                                    reason=MembershipPeriod.REASON_PAID, stripe_invoice_id="in_m1"))
    other = make_member(email="bert@example.com")
    db.session.add(MembershipPeriod(member_id=other.id, starts_on=date(YEAR, 1, 1), ends_on=date(YEAR, 12, 31),
                                    reason=MembershipPeriod.REASON_PAID, stripe_invoice_id="in_m_gone"))
    team = _team()
    db.session.add(Payment(purpose="team", team_id=team.id, stripe_invoice_id="in_t1", amount_cents=1000,
                           currency="eur", paid_at=now))
    db.session.add(Payment(purpose="team", team_id=team.id, stripe_payment_intent_id="pi_t3", amount_cents=1000,
                           refunded_cents=1000, currency="eur", paid_at=now))
    db.session.add(Payment(purpose="credit", user_id=member.user.id, stripe_payment_intent_id="pi_c1",
                           amount_cents=2000, currency="eur", paid_at=now))
    db.session.commit()
    return team


def _booking(stripe_id):
    return db.session.execute(db.select(StripeBooking).filter_by(stripe_id=stripe_id)).scalars().one()


class TestFetching:
    def test_every_booking_once_sorted_by_what_it_was_for(self, app, stripe_account):
        _portal_records()

        assert stripe_books.sync()["added"] == len(stripe_account["txns"])
        db.session.commit()

        products = {b.stripe_id: b.product for b in db.session.query(StripeBooking)}
        assert products == {
            "txn_old": "membership", "txn_m1": "membership", "txn_t1": "team", "txn_t2": "team", "txn_t3": "team",
            "txn_re1": "team", "txn_c1": "credit", "txn_shop": "other", "txn_bounce": "other", "txn_pfr": "other",
            "txn_fee": None, "txn_po": None,
        }
        # Matched to the portal's records, and their fee filled in from the booking.
        credit_payment = db.session.query(Payment).filter_by(purpose="credit").one()
        assert _booking("txn_c1").payment_id == credit_payment.id and credit_payment.fee_cents == 55
        assert _booking("txn_re1").payment_id == _booking("txn_t3").payment_id is not None
        assert _booking("txn_m1").invoice_id == "in_m1"

    def test_the_next_fetch_asks_only_for_what_is_new(self, app, stripe_account):
        stripe_books.sync()
        db.session.commit()
        stripe_account["txns"].append(_txn("txn_new", "charge", 700, _charge("ch_new", "pi_new", purpose="team"),
                                           fee=20, created=int(datetime.now(timezone.utc).timestamp())))

        assert stripe_books.sync()["added"] == 1
        assert db.session.query(StripeBooking).count() == len(stripe_account["txns"])

    def test_the_balance_is_compared_with_stripes(self, app, stripe_account):
        stripe_books.sync()
        state = stripe_books.state()
        assert state.books_balance_cents == sum(txn["net"] for txn in stripe_account["txns"]) == 5735
        assert state.stripe_balance_cents == 5735

    def test_a_portal_record_written_after_the_booking_is_matched_on_the_next_fetch(self, app, stripe_account):
        stripe_books.sync()
        db.session.commit()
        assert _booking("txn_c1").product == "other" and _booking("txn_c1").payment_id is None

        _portal_records()
        stripe_books.sync()

        assert _booking("txn_c1").product == "credit" and _booking("txn_c1").payment_id is not None

    def test_stripe_out_of_reach_leaves_the_copy_as_it_was(self, app, monkeypatch):
        def unreachable(**kwargs):
            raise stripe.APIConnectionError("no network")

        monkeypatch.setattr(stripe.BalanceTransaction, "list", staticmethod(unreachable))
        monkeypatch.setattr(stripe_books.payments, "apply_runtime_stripe_config", lambda: {})

        with pytest.raises(stripe.APIConnectionError):
            stripe_books._handle_fetch(None)
        assert stripe_books.state().last_error == "no network"
        assert db.session.query(StripeBooking).count() == 0


class TestWhenItIsFetched:
    def test_a_webhook_queues_one_fetch_for_many_events(self, app):
        stripe_books.after_webhook("charge.succeeded")
        stripe_books.after_webhook("invoice.paid")
        stripe_books.after_webhook("customer.updated")  # nothing new in the book
        db.session.commit()

        items = db.session.query(ExternalWorkItem).filter_by(kind=ExternalWorkItem.KIND_STRIPE_BOOKS).all()
        assert len(items) == 1 and items[0].status == "pending"

    def test_hourly_when_nothing_else_asked(self, app, monkeypatch):
        monkeypatch.setattr(stripe_books, "payments_configured", lambda: True)
        now = datetime.now(timezone.utc)
        stripe_books.state().fetched_at = now - timedelta(minutes=20)

        assert stripe_books.request_sync_if_due(now) is None
        stripe_books.state().fetched_at = now - timedelta(minutes=61)
        assert stripe_books.request_sync_if_due(now) is not None


class TestTheOverview:
    def test_this_years_money_by_product(self, app, stripe_account):
        _portal_records()
        stripe_books.sync()

        result = money_overview.overview(date(YEAR, 1, 1), TODAY)

        by_product = result["books"]["by_product"]
        assert by_product["membership"] == {"received": 1500, "refunded": 0, "returned": 0, "taken_back": 0,
                                            "fees": 50, "net": 1450}
        assert by_product["team"] == {"received": 3000, "refunded": 1000, "returned": 0, "taken_back": 0,
                                      "fees": 90, "net": 1910}
        assert by_product["credit"]["received"] == 2000 and by_product["credit"]["fees"] == 55
        assert by_product["other"] == {"received": 2500, "refunded": 0, "returned": 500, "taken_back": 0,
                                       "fees": 0, "net": 2000}
        total = result["books"]["total"]
        assert (total["received"], total["fees"]) == (9000, 215)
        assert result["books"]["paid_out"] == 3000

    def test_from_the_start_it_adds_up_to_stripes_balance(self, app, stripe_account):
        stripe_books.sync()

        result = money_overview.overview(None, TODAY)

        assert result["books"]["balance_at_end"] == 5735 == result["stripe_balance"]

    def test_on_a_day_in_the_past(self, app, stripe_account):
        stripe_books.sync()

        result = money_overview.overview(None, date(YEAR - 1, 12, 31))

        assert result["books"]["total"]["received"] == 1500
        assert result["books"]["balance_at_end"] == 1450

    def test_the_associations_own_is_what_is_left(self, app, stripe_account):
        team = _portal_records()
        stripe_books.sync()

        result = money_overview.overview(None, TODAY)

        assert result["owed_to_teams"] == 1000  # in_t1; pi_t3 was refunded in full
        assert result["association_own"] == result["books"]["balance_at_end"] - 1000 - result["credit_held"]["top_ups"]
        db.session.add(TeamPayout(team_id=team.id, amount_cents=400, currency="eur", paid_on=TODAY, reference="x"))
        assert money_overview.owed_to_teams_on(TODAY) == 600
        assert money_overview.owed_to_teams_on(TODAY - timedelta(days=1)) == 0


@pytest.fixture
def this_year_only(app):
    """The check looks back no further than the portal's start: here, the first of January."""
    app.config["MONEY_RECORDS_SINCE"] = date(YEAR, 1, 1)


@pytest.mark.usefixtures("this_year_only")
class TestTheCheck:
    def test_it_finds_what_does_not_match(self, app, stripe_account):
        _portal_records()
        stripe_books.sync()

        found = money_checks.run()

        assert sorted((row.what, row.reference) for row in found) == [
            (money_checks.PERIOD_NOT_PAID, "in_m_gone"),
            (money_checks.MISSING_IN_PORTAL, "in_t2"),
        ]

    def test_nothing_before_the_first_fetch(self, app):
        _portal_records()
        assert money_checks.run() == []

    def test_a_difference_that_goes_away_is_resolved(self, app, stripe_account):
        _portal_records()
        stripe_books.sync()
        money_checks.run()

        db.session.add(Payment(purpose="team", stripe_invoice_id="in_t2", amount_cents=1000, currency="eur",
                               paid_at=datetime.now(timezone.utc)))
        stripe_books.sync()
        money_checks.run()

        row = db.session.query(MoneyDifference).filter(MoneyDifference.reference == "in_t2").one()
        assert row.resolved_at is not None

    def test_a_refund_the_portal_missed(self, app, stripe_account):
        _portal_records()
        db.session.query(Payment).filter_by(stripe_payment_intent_id="pi_t3").one().refunded_cents = 0
        stripe_books.sync()

        found = {row.what: row for row in money_checks.run()}

        assert found[money_checks.REFUND_DIFFERS].stripe_amount_cents == 1000

    def test_emailed_once_after_an_hour(self, app, stripe_account, monkeypatch):
        _portal_records()
        _staff("money@example.org", "treasurer").email_verified_at = datetime.now(timezone.utc)
        sent = []
        monkeypatch.setattr("aeronautics_members.mail_utils.send_mail",
                            lambda **kwargs: sent.append(kwargs) or (True, None))
        stripe_books.sync()
        money_checks.run()

        assert money_checks.email_new() == 0, "not before it has lasted"
        later = datetime.now(timezone.utc) + timedelta(hours=2)
        assert money_checks.email_new(later) == 2
        assert sent[0]["to_email"] == "money@example.org" and len(sent[0]["lines"]) == 2
        assert money_checks.email_new(later) == 0, "once"

    def test_not_emailed_when_switched_off(self, app, stripe_account, monkeypatch):
        from aeronautics_members.services import money_settings

        _portal_records()
        _staff("money@example.org", "treasurer").email_verified_at = datetime.now(timezone.utc)
        monkeypatch.setattr("aeronautics_members.mail_utils.send_mail", lambda **kwargs: pytest.fail("emailed"))
        money_settings.save_money_settings(None, fee_payer="association", credit_share_bps=0,
                                           difference_emails=False)
        stripe_books.sync()
        money_checks.run()

        assert money_checks.email_new(datetime.now(timezone.utc) + timedelta(hours=2)) == 0


@pytest.mark.usefixtures("this_year_only")
class TestThePage:
    def test_the_overview_answers_without_asking_stripe(self, app, client, stripe_account):
        _portal_records()
        stripe_books.sync()
        money_checks.run()
        db.session.commit()
        asked = stripe_account["asked"]
        _login(client, _staff("treasurer@example.org", "treasurer").id)

        body = client.get(f"/api/v1/admin/money/overview?since={YEAR}-01-01").get_json()

        assert stripe_account["asked"] == asked
        assert body["since"] == f"{YEAR}-01-01" and body["books"]["credit"]["received"] == 2000
        assert {(d["product"], d["reference"]) for d in body["differences"]} == {("team", "in_t2"),
                                                                                  ("membership", "in_m_gone")}
        assert body["stripe_balance"] == body["books_balance"]

    def test_the_first_fetch_runs_in_the_background(self, app, client):
        _login(client, _staff("treasurer@example.org", "treasurer").id)

        assert client.post("/api/v1/admin/money/fetch").status_code == 200
        assert db.session.query(ExternalWorkItem).filter_by(kind=ExternalWorkItem.KIND_STRIPE_BOOKS).count() == 1

    def test_later_fetches_at_once(self, app, client, stripe_account):
        stripe_books.sync()
        db.session.commit()
        stripe_account["txns"].append(_txn("txn_new", "charge", 700, _charge("ch_new", "pi_new", purpose="team"),
                                           created=int(datetime.now(timezone.utc).timestamp())))
        _login(client, _staff("treasurer@example.org", "treasurer").id)

        body = client.post("/api/v1/admin/money/fetch").get_json()

        assert body["books"]["team"]["received"] == 3700

    def test_stripe_out_of_reach_is_said_so(self, app, client, stripe_account, monkeypatch):
        stripe_books.sync()
        db.session.commit()

        def unreachable(**kwargs):
            raise stripe.APIConnectionError("no network")

        monkeypatch.setattr(stripe.BalanceTransaction, "list", staticmethod(unreachable))
        _login(client, _staff("treasurer@example.org", "treasurer").id)

        response = client.post("/api/v1/admin/money/fetch")

        assert response.status_code == 502 and response.get_json()["error"]["code"] == "stripe_unavailable"

    def test_a_day_that_is_no_day_is_refused(self, app, client):
        _login(client, _staff("treasurer@example.org", "treasurer").id)
        assert client.get("/api/v1/admin/money/overview?since=nonsense").status_code == 400

    def test_not_for_a_lead(self, app, client):
        from test_teams_flow import _led

        _team_, lead = _led()
        _login(client, lead.id)
        assert client.get("/api/v1/admin/money/overview").status_code == 403


@pytest.mark.parametrize("since, until, expected", [
    (None, None, (None, TODAY)),
    (date(YEAR, 1, 1), date(YEAR, 1, 31), (date(YEAR, 1, 1), date(YEAR, 1, 31))),
    (date(2999, 1, 1), None, (None, TODAY)),          # after the end
    (None, date(2999, 1, 1), (None, TODAY)),          # no later than today
    (date(1999, 1, 1), None, (None, TODAY)),
])
def test_the_period_asked_for(app, since, until, expected):
    assert money_overview.period_asked(since, until, TODAY) == expected


def test_never_before_the_portal_took_over(app):
    app.config["MONEY_RECORDS_SINCE"] = date(2026, 10, 1)
    today = date(2026, 12, 31)

    assert money_overview.period_asked(None, None, today) == (date(2026, 10, 1), today)
    assert money_overview.period_asked(date(2025, 1, 1), None, today) == (date(2026, 10, 1), today)
    assert money_overview.period_asked(date(2026, 11, 1), None, today) == (date(2026, 11, 1), today)


def test_an_erased_account_leaves_no_name_and_old_ones_are_forgotten(app):
    from conftest import make_member as member_of

    anna = member_of(email="anna@example.com").user
    now = datetime.now(timezone.utc)
    db.session.add(MoneyDifference(key="team:missing:ch_1", product="team", what="x", who="anna@example.com",
                                   first_seen_at=now, last_seen_at=now))
    db.session.add(MoneyDifference(key="team:missing:ch_2", product="team", what="x", who="",
                                   first_seen_at=now, last_seen_at=now, resolved_at=now - timedelta(days=400)))
    db.session.commit()

    assert money_checks.forget_person(anna) == 1
    assert money_checks.forget_old() == 1
    assert [row.who for row in db.session.query(MoneyDifference)] == [""]


@pytest.mark.usefixtures("this_year_only")
def test_a_bounced_debit_paid_again_is_one_invoice_and_no_difference(app, stripe_account):
    _portal_records()
    # The first debit for in_m1 bounced; a second charge paid the same invoice.
    stripe_account["txns"] += [
        _txn("txn_m1b", "payment", 1500, _charge("py_m1b", "pi_m1b")),
        _txn("txn_m1b_back", "payment_failure_refund", -1500, {"id": "pfr_2", "object": "refund",
                                                               "charge": "py_m1b", "payment_intent": "pi_m1b"}),
    ]
    stripe_account["paid_by"]["pi_m1b"] = "in_m1"
    stripe_books.sync()

    found = [(row.what, row.reference) for row in money_checks.run()]

    assert (money_checks.TAKEN_BACK_IN_STRIPE, "in_m1") not in found
    assert not stripe_books.fetching()
