"""A renewal Stripe is still collecting does not end the membership.

Everybody renews on Jan 1. A SEPA debit started then is confirmed days later,
and the ledger only records the new year once it is -- so everybody paying by
SEPA stopped being a member at midnight: shut out of the forum, moved to its
inactive group and back, and then welcomed to the association all over again
when the money arrived.
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from conftest import Member, clock, db, make_member, periods
from test_webhook import post_event
from aeronautics_members.blueprints import webhook as webhook_module
from aeronautics_members.db_models import MembershipPeriod
from aeronautics_members.services.membership import (
    RENEWAL_GRACE_DAYS,
    member_has_active_access,
)

YEAR_END = date(2026, 12, 31)
NEW_YEAR = date(2027, 1, 1)


def _ts(day):
    return int(datetime(day.year, day.month, day.day, 6, tzinfo=timezone.utc).timestamp())


@pytest.fixture
def today(monkeypatch):
    """The membership date, settable. Everything reads it through the clock."""
    current = {"day": date(2026, 12, 20)}
    monkeypatch.setattr(
        clock, "get_membership_now",
        lambda: datetime(current["day"].year, current["day"].month, current["day"].day, 12,
                         tzinfo=ZoneInfo("Europe/Vienna")),
    )
    return current


@pytest.fixture
def welcomes(monkeypatch):
    sent = []
    monkeypatch.setattr(
        webhook_module, "send_member_welcome_email", lambda app, member, *a, **k: sent.append(member.id),
    )
    return sent


def _renewing_member(**overrides):
    fields = dict(
        payment_status="paid", is_active=True,
        stripe_customer_id="cus_s", stripe_subscription_id="sub_s",
        membership_starts_on=date(2026, 3, 1), membership_ends_on=YEAR_END,
        renewal_due_on=NEW_YEAR,
    )
    fields.update(overrides)
    member = make_member(email="sepa@example.com", **fields)
    member.created_at = datetime(2026, 3, 1, tzinfo=timezone.utc)
    periods.grant_period(
        member, date(2026, 3, 1), YEAR_END, MembershipPeriod.REASON_PAID,
        stripe_invoice_id="in_2026", stripe_subscription_id="sub_s",
    )
    db.session.commit()
    return member


def _renewed_by_stripe(client, monkeypatch):
    post_event(client, monkeypatch, {
        "id": "evt_renewed", "type": "customer.subscription.updated", "created": _ts(NEW_YEAR),
        "data": {"object": {
            "id": "sub_s", "customer": "cus_s", "status": "active", "cancel_at_period_end": False,
            "collection_method": "charge_automatically",
            "current_period_start": _ts(NEW_YEAR), "current_period_end": _ts(date(2028, 1, 1)),
            "items": {"data": [{"current_period_start": _ts(NEW_YEAR),
                                "current_period_end": _ts(date(2028, 1, 1))}]},
            "metadata": {"activation_mode": "paid_now"},
        }},
    })
    post_event(client, monkeypatch, {
        "id": "evt_debit_started", "type": "payment_intent.processing", "created": _ts(NEW_YEAR),
        "data": {"object": {"id": "pi_2027", "customer": "cus_s"}},
    })


def _access(member):
    return member_has_active_access(db.session.get(Member, member.id))


class TestWhileTheDebitClears:
    def test_they_stay_a_member(self, app, client, monkeypatch, today, welcomes):
        member = _renewing_member()
        today["day"] = NEW_YEAR
        _renewed_by_stripe(client, monkeypatch)
        assert _access(member) is True

        today["day"] = date(2027, 1, 8)
        assert _access(member) is True

    def test_and_are_not_welcomed_again_when_it_clears(self, app, client, monkeypatch, today, welcomes):
        member = _renewing_member()
        today["day"] = NEW_YEAR
        _renewed_by_stripe(client, monkeypatch)
        today["day"] = date(2027, 1, 8)

        post_event(client, monkeypatch, {
            "id": "evt_cleared", "type": "invoice.paid", "created": _ts(date(2027, 1, 8)),
            "data": {"object": {
                "id": "in_2027", "customer": "cus_s", "subscription": "sub_s",
                "billing_reason": "subscription_cycle", "total": 3000, "amount_paid": 3000,
                "status_transitions": {"paid_at": _ts(date(2027, 1, 8))}, "created": _ts(NEW_YEAR),
                "lines": {"data": [{"period": {"start": _ts(NEW_YEAR)}}]},
            }},
        })

        assert _access(member) is True
        assert welcomes == []

    def test_a_failed_debit_ends_it_at_once(self, app, client, monkeypatch, today, welcomes):
        member = _renewing_member()
        today["day"] = NEW_YEAR
        _renewed_by_stripe(client, monkeypatch)
        today["day"] = date(2027, 1, 7)

        post_event(client, monkeypatch, {
            "id": "evt_bounced", "type": "invoice.payment_failed", "created": _ts(date(2027, 1, 7)),
            "data": {"object": {"id": "in_2027", "customer": "cus_s", "subscription": "sub_s"}},
        })

        assert _access(member) is False

    def test_not_for_ever(self, app, client, monkeypatch, today, welcomes):
        member = _renewing_member()
        today["day"] = NEW_YEAR
        _renewed_by_stripe(client, monkeypatch)

        today["day"] = YEAR_END + timedelta(days=RENEWAL_GRACE_DAYS)
        assert _access(member) is True
        today["day"] = YEAR_END + timedelta(days=RENEWAL_GRACE_DAYS + 1)
        assert _access(member) is False


class TestWhoDoesNotGetIt:
    def test_not_before_stripe_has_renewed_anything(self, app, today):
        member = _renewing_member()
        today["day"] = NEW_YEAR
        assert _access(member) is False

    def test_not_somebody_who_cancelled(self, app, today):
        member = _renewing_member(cancel_at_period_end=True, membership_ends_on=date(2027, 12, 31))
        today["day"] = NEW_YEAR
        assert _access(member) is False

    def test_not_after_a_revoked_year(self, app, today):
        """A lost chargeback must still end access."""
        member = _renewing_member(membership_ends_on=date(2027, 12, 31))
        periods.revoke_period(member.membership_periods[0], "chargeback")
        db.session.commit()
        today["day"] = NEW_YEAR
        assert _access(member) is False

    def test_not_without_a_subscription(self, app, today):
        """Nobody is collecting anything."""
        member = _renewing_member(stripe_subscription_id=None, membership_ends_on=date(2027, 12, 31))
        today["day"] = NEW_YEAR
        assert _access(member) is False


class TestTheWelcomeIsForJoining:
    def test_a_first_invoice_still_welcomes(self, app, client, monkeypatch, today, welcomes):
        today["day"] = date(2026, 5, 4)
        member = make_member(email="first@example.com", payment_status="unpaid",
                             stripe_customer_id="cus_f", stripe_subscription_id="sub_f")
        member.created_at = datetime(2026, 5, 4, tzinfo=timezone.utc)
        db.session.commit()

        post_event(client, monkeypatch, {
            "id": "evt_first", "type": "invoice.paid", "created": _ts(date(2026, 5, 4)),
            "data": {"object": {
                "id": "in_first", "customer": "cus_f", "subscription": "sub_f",
                "billing_reason": "subscription_create", "total": 2000, "amount_paid": 2000,
                "status_transitions": {"paid_at": _ts(date(2026, 5, 4))}, "created": _ts(date(2026, 5, 4)),
                "lines": {"data": [{"period": {"start": _ts(date(2026, 5, 4))}}]},
            }},
        })

        assert welcomes == [member.id]
