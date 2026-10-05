"""The association's money on Stripe, for a year, and the check against the portal.

Stripe is faked: one account with membership fees, team fees, a webshop
payment, a SEPA debit that bounced and a payout to the bank.
"""
from datetime import date, datetime, timezone

import pytest
import stripe

from conftest import db, make_member
from aeronautics_members.db_models import MembershipPeriod, Payment
from aeronautics_members.services import money_overview
from aeronautics_members.services.clock import get_membership_today
from test_admin_reviews import _login, _staff

YEAR = get_membership_today().year


class _Listing:
    def __init__(self, items):
        self.items = items

    def auto_paging_iter(self):
        return iter(self.items)


def _txn(kind, amount, fee=0):
    return {"type": kind, "amount": amount, "fee": fee, "net": amount - fee, "currency": "eur"}


def _invoice(invoice_id, amount, purpose, **metadata):
    return {"id": invoice_id, "amount_paid": amount, "status": "paid", "customer_email": f"{invoice_id}@example.com",
            "metadata": metadata,
            "parent": {"subscription_details": {"subscription": f"sub_{invoice_id}", "metadata": {"purpose": purpose}}}}


@pytest.fixture
def stripe_account(monkeypatch):
    account = {
        "txns": [
            _txn("charge", 1500, fee=50),       # a membership
            _txn("charge", 1000, fee=30),       # a team fee the portal has
            _txn("charge", 1000, fee=30),       # a team fee the portal lacks
            _txn("payment", 2000),              # the webshop
            _txn("payment", 500), _txn("payment_failure_refund", -500),  # a SEPA debit that bounced
            {"type": "payout", "amount": -3000, "fee": 0, "net": -3000, "currency": "eur"},
        ],
        "invoices": [_invoice("in_m1", 1500, "membership"), _invoice("in_t1", 1000, "team"),
                     _invoice("in_t2", 1000, "team")],
        "elsewhere": {"in_m_gone": "open"},
    }
    monkeypatch.setattr(stripe.BalanceTransaction, "list", staticmethod(lambda **k: _Listing(account["txns"])))
    monkeypatch.setattr(stripe.Invoice, "list", staticmethod(lambda **k: _Listing(account["invoices"])))
    monkeypatch.setattr(stripe.Invoice, "retrieve",
                        staticmethod(lambda invoice_id, **k: {"id": invoice_id,
                                                              "status": account["elsewhere"].get(invoice_id, "paid")}))
    monkeypatch.setattr(stripe.Balance, "retrieve", staticmethod(lambda **k: {
        "available": [{"amount": 1890, "currency": "eur"}], "pending": [{"amount": 500, "currency": "eur"}]}))
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


def test_the_years_money_split_and_whose_it_is(app, stripe_account):
    _portal_records()

    result = money_overview.overview(YEAR)

    books = result["books"]
    assert (books["received"], books["returned"], books["fees"], books["net"], books["paid_out"]) == (
        6000, 500, 110, 5390, 3000)
    assert result["split"] == {"membership": 1500, "teams": 2000, "other": 2000}
    assert result["in_stripe_now"] == {"available": 1890, "pending": 500}
    assert result["teams_share"] == 1000
    assert result["association_own"] == 4390


def test_the_check_finds_what_does_not_match(app, stripe_account):
    _portal_records()

    checks = money_overview.overview(YEAR)["checks"]

    assert [(p["what"], p["invoice"]) for p in checks["teams"]] == [("Paid in Stripe, missing in the portal", "in_t2")]
    assert [(p["what"], p["invoice"]) for p in checks["membership"]] == [
        ("Membership period counted as paid, invoice not paid in Stripe", "in_m_gone")]


def test_all_matching_says_so(app, client, stripe_account):
    _portal_records()
    stripe_account["invoices"] = stripe_account["invoices"][:2]
    stripe_account["elsewhere"] = {}
    _login(client, _staff("treasurer@example.org", "treasurer").id)

    body = client.get(f"/admin/money?check=1&year={YEAR}").get_data(as_text=True)

    assert "matches the portal" in body and "of which the association" in body


def test_without_asking_nothing_is_fetched(app, client, monkeypatch):
    monkeypatch.setattr(stripe.BalanceTransaction, "list", staticmethod(lambda **k: pytest.fail("asked Stripe")))
    _login(client, _staff("treasurer@example.org", "treasurer").id)

    body = client.get("/admin/money").get_data(as_text=True)

    assert "Check against Stripe" in body
