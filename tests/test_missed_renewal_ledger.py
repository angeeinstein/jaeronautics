"""A renewal whose invoice.paid never arrived is repaired in the ledger too.

Found by the pre-deployment audit. The reconciliation already advanced the
cached dates from the renewed subscription -- but access is decided by the
ledger, and nothing wrote the new year into it. The member showed paid
through December and lost access three weeks into January, when the renewal
grace ran out. And the nightly run, seeing an end date far in the future,
never looked at them again.
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from conftest import Member, clock, db, make_member, periods
from aeronautics_members.db_models import MembershipPeriod
from aeronautics_members.services import billing, workflows
from aeronautics_members.services.membership import RENEWAL_GRACE_DAYS, member_has_active_access

YEAR_END = date(2026, 12, 31)
NEW_YEAR = date(2027, 1, 1)
AFTER_GRACE = YEAR_END + timedelta(days=RENEWAL_GRACE_DAYS + 1)


def _ts(day):
    return int(datetime(day.year, day.month, day.day, 6, tzinfo=timezone.utc).timestamp())


@pytest.fixture
def today(monkeypatch):
    current = {"day": NEW_YEAR + timedelta(days=2)}
    monkeypatch.setattr(
        clock, "get_membership_now",
        lambda: datetime(current["day"].year, current["day"].month, current["day"].day, 12,
                         tzinfo=ZoneInfo("Europe/Vienna")),
    )
    return current


RENEWED = {
    "id": "sub_r", "customer": "cus_r", "status": "active", "cancel_at_period_end": False,
    "collection_method": "charge_automatically",
    "items": {"data": [{"current_period_start": _ts(NEW_YEAR), "current_period_end": _ts(date(2028, 1, 1))}]},
    "metadata": {"activation_mode": "paid_now"},
}


def _renewal_invoice(invoice_id="in_2027", amount=3000):
    return {
        "id": invoice_id, "amount_paid": amount, "status": "paid",
        "lines": {"data": [{"period": {"start": _ts(NEW_YEAR)}}]},
        "status_transitions": {"paid_at": _ts(NEW_YEAR)},
    }


@pytest.fixture
def stripe_says(monkeypatch):
    """What Stripe answers: the renewed subscription, and its paid invoices."""
    state = {"invoices": [_renewal_invoice()], "asked": 0}
    monkeypatch.setattr(workflows, "get_latest_stripe_subscription_for_member", lambda member: RENEWED)

    def invoice_list(**params):
        assert params["subscription"] == "sub_r" and params["status"] == "paid"
        state["asked"] += 1
        return {"data": state["invoices"]}

    monkeypatch.setattr(billing.stripe.Invoice, "list", staticmethod(invoice_list))
    return state


@pytest.fixture
def renewing(app):
    member = make_member(
        email="renewing@example.com", payment_status="paid", is_active=True,
        stripe_customer_id="cus_r", stripe_subscription_id="sub_r",
        membership_starts_on=date(2026, 3, 1), membership_ends_on=YEAR_END, renewal_due_on=NEW_YEAR,
    )
    member.created_at = datetime(2026, 3, 1, tzinfo=timezone.utc)
    periods.grant_period(member, date(2026, 3, 1), YEAR_END, MembershipPeriod.REASON_PAID,
                         stripe_invoice_id="in_2026", stripe_subscription_id="sub_r")
    db.session.commit()
    return member


def _refresh(member):
    workflows.refresh_member_billing_state(member, force_stripe_sync=True)
    db.session.commit()
    return db.session.get(Member, member.id)


def test_the_missed_year_is_recorded_and_access_outlasts_the_grace(renewing, stripe_says, today):
    member = _refresh(renewing)

    years = sorted(p.ends_on.year for p in member.membership_periods if not p.is_revoked)
    assert years == [2026, 2027]
    assert any(p.stripe_invoice_id == "in_2027" for p in member.membership_periods)
    today["day"] = AFTER_GRACE
    assert member_has_active_access(member) is True


def test_running_it_again_records_nothing_twice(renewing, stripe_says, today):
    _refresh(renewing)
    member = _refresh(renewing)

    assert len(member.membership_periods) == 2


def test_an_unpaid_renewal_grants_nothing(renewing, stripe_says, today):
    """Still being collected, or failed: no paid invoice, no year."""
    stripe_says["invoices"] = []

    member = _refresh(renewing)

    assert len(member.membership_periods) == 1
    today["day"] = AFTER_GRACE
    assert member_has_active_access(member) is False


def test_the_free_period_invoice_is_not_a_payment(renewing, stripe_says, today):
    stripe_says["invoices"] = [_renewal_invoice("in_zero", amount=0)]

    member = _refresh(renewing)

    assert len(member.membership_periods) == 1


def test_a_year_revoked_for_a_lost_chargeback_is_not_granted_again(renewing, stripe_says, today):
    periods.grant_calendar_year(renewing, 2027, "paid", stripe_invoice_id="in_2027",
                                stripe_subscription_id="sub_r")
    periods.revoke_periods_for_payment(renewing, "in_2027", "sub_r", "Chargeback lost.")
    renewing.membership_ends_on = date(2027, 12, 31)
    db.session.commit()

    member = _refresh(renewing)

    assert [p.is_revoked for p in member.membership_periods if p.ends_on.year == 2027] == [True]


def test_stripe_is_not_asked_when_the_ledger_already_agrees(renewing, stripe_says, today):
    periods.grant_calendar_year(renewing, 2027, "paid", stripe_invoice_id="in_2027",
                                stripe_subscription_id="sub_r")
    db.session.commit()

    _refresh(renewing)

    assert stripe_says["asked"] == 0


def test_the_nightly_run_keeps_looking_at_a_member_the_ledger_disagrees_with(
        app, renewing, stripe_says, today, monkeypatch):
    """After the cache was repaired alone, its end date is far in the future
    and nothing else would select this member again."""
    renewing.membership_starts_on = date(2027, 1, 1)
    renewing.membership_ends_on = date(2027, 12, 31)
    renewing.renewal_due_on = date(2028, 1, 1)
    db.session.commit()
    seen = []
    real_refresh = workflows.refresh_member_billing_state

    def spy(member, **kwargs):
        seen.append(member.id)
        return real_refresh(member, **kwargs)

    import aeronautics_members.app as app_module

    monkeypatch.setattr(app_module, "refresh_member_billing_state", spy)
    monkeypatch.setattr(workflows, "get_forum_service", lambda: type("Off", (), {"is_enabled": lambda self: False})())
    result = app.test_cli_runner().invoke(args=["reconcile-billing"])

    assert result.exit_code == 0, result.output
    assert renewing.id in seen
    member = db.session.get(Member, renewing.id)
    assert any(p.stripe_invoice_id == "in_2027" for p in member.membership_periods)
