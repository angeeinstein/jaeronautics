"""The association's money on Stripe, for a period, by product, and the check against the portal.

Stripe is faked: one account with a membership fee, team fees (one by
subscription, one paid once), a webshop payment, a refund, a SEPA debit that
bounced and a payout to the bank -- and one membership fee from last year.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
import stripe

from conftest import db, make_member
from aeronautics_members.db_models import MembershipPeriod, Payment, TeamPayout
from aeronautics_members.services import money_overview
from aeronautics_members.services.clock import get_membership_today
from test_admin_reviews import _login, _staff
from test_teams_foundation import _team

TODAY = get_membership_today()
YEAR = TODAY.year
THIS_YEAR = money_overview._unix(date(YEAR, 1, 1)) + 3600
LAST_YEAR = money_overview._unix(date(YEAR - 1, 6, 1))


class _Listing:
    def __init__(self, items):
        self.items = items

    def auto_paging_iter(self):
        return iter(self.items)


def _txn(kind, amount, source, fee=0, created=THIS_YEAR):
    return {"type": kind, "amount": amount, "fee": fee, "net": amount - fee, "currency": "eur",
            "source": source, "created": created}


def _charge(charge_id, payment_intent, **metadata):
    return {"id": charge_id, "object": "charge", "payment_intent": payment_intent, "metadata": metadata}


def _invoice(invoice_id, amount, purpose, created=THIS_YEAR, **metadata):
    return {"id": invoice_id, "amount_paid": amount, "status": "paid", "customer_email": f"{invoice_id}@example.com",
            "metadata": metadata, "created": created,
            "parent": {"subscription_details": {"subscription": f"sub_{invoice_id}", "metadata": {"purpose": purpose}}}}


def _paid_by(invoice_id, payment_intent):
    return {"invoice": invoice_id, "payment": {"type": "payment_intent", "payment_intent": payment_intent}}


@pytest.fixture
def stripe_account(monkeypatch):
    account = {
        "txns": [
            _txn("charge", 1500, _charge("ch_old", "pi_old"), fee=50, created=LAST_YEAR),  # last year's membership
            _txn("charge", 1500, _charge("ch_m1", "pi_m1"), fee=50),     # a membership
            _txn("charge", 1000, _charge("ch_t1", "pi_t1"), fee=30),     # a team fee the portal has
            _txn("charge", 1000, _charge("ch_t2", "pi_t2"), fee=30),     # a team fee the portal lacks
            _txn("charge", 1000, _charge("ch_t3", "pi_t3", purpose="team"), fee=30),  # paid once
            _txn("refund", -1000, {"id": "re_1", "object": "refund", "charge": "ch_t3", "payment_intent": None}),
            _txn("payment", 2000, _charge("py_shop", "pi_shop")),        # the webshop
            _txn("payment", 500, _charge("py_bounce", "pi_bounce")),     # a SEPA debit that bounced
            _txn("payment_failure_refund", -500, {"id": "pfr_1", "charge": "py_bounce"}),
            _txn("stripe_fee", -20, "fee_billing"),
            _txn("payout", -3000, "po_1"),
        ],
        "invoices": [_invoice("in_old", 1500, "membership", created=LAST_YEAR),
                     _invoice("in_m1", 1500, "membership"), _invoice("in_t1", 1000, "team"),
                     _invoice("in_t2", 1000, "team")],
        "invoice_payments": [_paid_by("in_old", "pi_old"), _paid_by("in_m1", "pi_m1"),
                             _paid_by("in_t1", "pi_t1"), _paid_by("in_t2", "pi_t2")],
        "elsewhere": {"in_m_gone": "open"},
        "balance": {"available": [{"amount": 2000, "currency": "eur"}], "pending": [{"amount": 1790, "currency": "eur"}]},
    }
    monkeypatch.setattr(stripe.BalanceTransaction, "list", staticmethod(
        lambda created, **k: _Listing([t for t in account["txns"] if t["created"] < created["lt"]])))
    monkeypatch.setattr(stripe.Invoice, "list", staticmethod(
        lambda created, **k: _Listing([i for i in account["invoices"] if i["created"] < created["lt"]])))
    monkeypatch.setattr(stripe.InvoicePayment, "list", staticmethod(lambda **k: _Listing(account["invoice_payments"])))
    monkeypatch.setattr(stripe.Invoice, "retrieve",
                        staticmethod(lambda invoice_id, **k: {"id": invoice_id,
                                                              "status": account["elsewhere"].get(invoice_id, "paid")}))
    monkeypatch.setattr(stripe.Balance, "retrieve", staticmethod(lambda **k: account["balance"]))
    monkeypatch.setattr(money_overview.payments, "apply_runtime_stripe_config", lambda: {})
    return account


def _portal_records():
    member = make_member(email="anna@example.com")
    now = datetime.now(timezone.utc)
    db.session.add(MembershipPeriod(member_id=member.id, starts_on=date(YEAR, 1, 1), ends_on=date(YEAR, 12, 31),
                                    reason=MembershipPeriod.REASON_PAID, stripe_invoice_id="in_m1"))
    other = make_member(email="bert@example.com")
    db.session.add(MembershipPeriod(member_id=other.id, starts_on=date(YEAR, 1, 1), ends_on=date(YEAR, 12, 31),
                                    reason=MembershipPeriod.REASON_PAID, stripe_invoice_id="in_m_gone"))
    db.session.add(Payment(purpose="team", stripe_invoice_id="in_t1", amount_cents=1000, currency="eur", paid_at=now))
    db.session.commit()


def test_this_years_money_by_product(app, stripe_account):
    _portal_records()

    result = money_overview.overview(date(YEAR, 1, 1), TODAY)

    by_product = result["books"]["by_product"]
    assert by_product["membership"] == {"received": 1500, "refunded": 0, "returned": 0, "taken_back": 0,
                                        "fees": 50, "net": 1450}
    # The one-time team fee is told by its purpose, and its refund follows it by the charge.
    assert by_product["team"] == {"received": 3000, "refunded": 1000, "returned": 0, "taken_back": 0,
                                  "fees": 90, "net": 1910}
    assert by_product["other"] == {"received": 2500, "refunded": 0, "returned": 500, "taken_back": 0,
                                   "fees": 0, "net": 2000}
    total = result["books"]["total"]
    assert (total["received"], total["fees"], total["net"]) == (7000, 160, 5340)
    assert result["books"]["paid_out"] == 3000
    assert result["teams_share"] == 2000
    assert result["association_own"] == 3340


def test_from_the_start_includes_everything_and_adds_up_to_stripes_balance(app, stripe_account):
    result = money_overview.overview(None, TODAY)

    assert result["books"]["by_product"]["membership"]["received"] == 3000
    # Every booking ever, payouts included, is what Stripe holds.
    assert result["books"]["balance_at_end"] == 2000 + 1790 == sum(t["net"] for t in stripe_account["txns"])
    assert result["in_stripe_now"] == {"available": 2000, "pending": 1790}


def test_on_a_day_in_the_past(app, stripe_account):
    result = money_overview.overview(None, date(YEAR - 1, 12, 31))

    assert result["books"]["total"]["received"] == 1500
    assert result["books"]["balance_at_end"] == 1450
    assert result["in_stripe_now"] is None  # what Stripe holds now says nothing about then


def test_owed_to_the_teams_on_a_day(app):
    now = datetime.now(timezone.utc)
    db.session.add(Payment(purpose="team", stripe_invoice_id="in_a", amount_cents=1000, currency="eur",
                           paid_at=now - timedelta(days=10)))
    db.session.add(Payment(purpose="team", stripe_invoice_id="in_b", amount_cents=1000, currency="eur", paid_at=now))
    db.session.add(TeamPayout(team_id=_team().id, amount_cents=400, currency="eur", paid_on=TODAY,
                              reference="x"))
    db.session.commit()

    assert money_overview.owed_to_teams_on(TODAY) == 1600
    assert money_overview.owed_to_teams_on(TODAY - timedelta(days=5)) == 1000


def test_the_check_finds_what_does_not_match(app, stripe_account):
    _portal_records()

    checks = money_overview.overview(date(YEAR, 1, 1), TODAY)["checks"]

    assert [(p["what"], p["invoice"]) for p in checks["teams"]] == [("Paid in Stripe, missing in the portal", "in_t2")]
    assert [(p["what"], p["invoice"]) for p in checks["membership"]] == [
        ("Membership period counted as paid, invoice not paid in Stripe", "in_m_gone")]


def test_all_matching_says_so(app, client, stripe_account):
    _portal_records()
    stripe_account["invoices"] = stripe_account["invoices"][:3]
    stripe_account["elsewhere"] = {}
    _login(client, _staff("treasurer@example.org", "treasurer").id)

    body = client.get(f"/api/v1/admin/money/overview?since={YEAR}-01-01").get_json()

    assert body["since"] == f"{YEAR}-01-01" and body["until"] == TODAY.isoformat()
    assert body["mismatches"] == []
    assert body["association_own"] == body["books"]["total"]["net"] - body["teams_share"]
    now = body["in_stripe_now"]
    assert now["available"] + now["pending"] == body["books"]["balance_at_end"], "Stripe's books add up"


def test_a_balance_that_does_not_add_up_is_shown(app, client, stripe_account):
    stripe_account["balance"]["pending"][0]["amount"] = 999
    _login(client, _staff("treasurer@example.org", "treasurer").id)

    body = client.get("/api/v1/admin/money/overview").get_json()

    assert body["since"] is None
    now = body["in_stripe_now"]
    assert now["available"] + now["pending"] != body["books"]["balance_at_end"]


def test_the_mismatches_name_their_kind(app, client, stripe_account):
    _portal_records()
    _login(client, _staff("treasurer@example.org", "treasurer").id)

    mismatches = client.get("/api/v1/admin/money/overview").get_json()["mismatches"]

    assert {(m["kind"], m["invoice"]) for m in mismatches} >= {("team", "in_t2"), ("membership", "in_m_gone")}


def test_stripe_out_of_reach_is_said_so(app, client, monkeypatch):
    def unreachable(**kwargs):
        raise stripe.APIConnectionError("no network")

    monkeypatch.setattr(stripe.BalanceTransaction, "list", staticmethod(unreachable))
    monkeypatch.setattr(stripe.Invoice, "list", staticmethod(unreachable))
    monkeypatch.setattr(money_overview.payments, "apply_runtime_stripe_config", lambda: {})
    _login(client, _staff("treasurer@example.org", "treasurer").id)

    response = client.get("/api/v1/admin/money/overview")

    assert response.status_code == 502 and response.get_json()["error"]["code"] == "stripe_unavailable"


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


def test_a_day_that_is_no_day_is_refused(app, client):
    _login(client, _staff("treasurer@example.org", "treasurer").id)

    assert client.get("/api/v1/admin/money/overview?since=nonsense").status_code == 400


def test_without_asking_nothing_is_fetched(app, client, monkeypatch):
    monkeypatch.setattr(stripe.BalanceTransaction, "list", staticmethod(lambda **k: pytest.fail("asked Stripe")))
    _login(client, _staff("treasurer@example.org", "treasurer").id)

    assert client.get("/admin/money").status_code == 200
    assert client.get("/api/v1/admin/money").status_code == 200
