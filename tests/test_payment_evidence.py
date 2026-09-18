"""Reconciliation must not mistake subscription state for proof of payment.

An invoice-billed subscription (``collection_method="send_invoice"``) is created
with a trial until Jan 1 and goes ``active`` when that trial ends -- while the
prorated invoice may still be unpaid, with 30 days to settle. Stripe documents
that ``active`` does not imply every invoice was paid, so reconciliation must
never promote such a member to paid/active on subscription status alone.

Automatically charged subscriptions (Checkout) are different: Stripe collects
the money itself and moves the subscription out of ``active`` when payment
fails, so there the lifecycle *is* usable evidence.
"""
from datetime import date

from conftest import app_module, db, make_member
from aeronautics_members.db_models import Member

CUR = date.today().year


def _subscription(status, activation_mode, collection_method, sub_id="sub_1", cus="cus_1"):
    return {
        "id": sub_id,
        "customer": cus,
        "status": status,
        "collection_method": collection_method,
        "cancel_at_period_end": False,
        "cancel_at": None,
        "metadata": {
            "activation_mode": activation_mode,
            "membership_ends_on": f"{CUR}-12-31",
            "renewal_due_on": f"{CUR + 1}-01-01",
        },
    }


def _invoice_member(email, status="unpaid", active=False, sub_id="sub_1", cus="cus_1"):
    """A member as create_invoice_membership_for_member leaves them: coverage
    dates for the current year, but unpaid and inactive until the invoice lands."""
    return make_member(
        email=email,
        payment_status=status,
        is_active=active,
        stripe_customer_id=cus,
        stripe_subscription_id=sub_id,
        membership_starts_on=date(CUR, 6, 1),
        membership_ends_on=date(CUR, 12, 31),
        renewal_due_on=date(CUR + 1, 1, 1),
    )


class TestInvoiceBilledNotTreatedAsPaid:
    def test_trialing_invoice_member_is_not_activated(self, app):
        # The prorated invoice is outstanding; a trialing send_invoice
        # subscription must not grant paid access.
        member = _invoice_member("inv_trial@example.com")
        app_module.sync_member_subscription_state_from_subscription(
            member, _subscription("trialing", "paid_now", "send_invoice"))
        db.session.commit()

        refreshed = db.session.get(Member, member.id)
        assert refreshed.payment_status == "unpaid"
        assert refreshed.is_active is False

    def test_active_invoice_member_is_not_activated(self, app):
        # The trial ended so Stripe reports "active", but the invoice may still
        # be unpaid -- status alone is not payment evidence.
        member = _invoice_member("inv_active@example.com")
        app_module.sync_member_subscription_state_from_subscription(
            member, _subscription("active", "paid_now", "send_invoice"))
        db.session.commit()

        refreshed = db.session.get(Member, member.id)
        assert refreshed.payment_status == "unpaid"
        assert refreshed.is_active is False

    def test_active_invoice_member_coverage_is_not_advanced(self, app):
        # The missed-renewal safety net must not extend coverage from an
        # invoice-billed subscription's period, which can be unpaid.
        member = make_member(
            email="inv_cov@example.com", payment_status="unpaid", is_active=False,
            stripe_customer_id="cus_c", stripe_subscription_id="sub_c",
            membership_ends_on=date(CUR - 1, 12, 31),
        )
        sub = _subscription("active", "paid_now", "send_invoice", "sub_c", "cus_c")
        sub["items"] = {"data": [{"current_period_start": app_module.start_of_day_unix(date(CUR, 1, 1))}]}
        sub["metadata"]["membership_ends_on"] = f"{CUR - 1}-12-31"

        app_module.sync_member_subscription_state_from_subscription(member, sub)
        db.session.commit()

        assert db.session.get(Member, member.id).membership_ends_on == date(CUR - 1, 12, 31)

    def test_paid_invoice_member_keeps_paid_status(self, app):
        # Once invoice.paid established payment, reconciliation preserves it.
        member = _invoice_member("inv_paid@example.com", status="paid", active=True)
        app_module.sync_member_subscription_state_from_subscription(
            member, _subscription("active", "paid_now", "send_invoice"))
        db.session.commit()

        refreshed = db.session.get(Member, member.id)
        assert refreshed.payment_status == "paid"
        assert refreshed.is_active is True

    def test_free_period_invoice_member_stays_free(self, app):
        # An Oct+ joiner billed by invoice owes nothing this year.
        member = _invoice_member("inv_free@example.com", status="free_period", active=True)
        app_module.sync_member_subscription_state_from_subscription(
            member, _subscription("trialing", "free_period", "send_invoice"))
        db.session.commit()

        refreshed = db.session.get(Member, member.id)
        assert refreshed.payment_status == "free_period"
        assert refreshed.is_active is True


class TestAutomaticPaymentStillTrusted:
    def test_checkout_subscription_active_is_paid(self, app):
        member = _invoice_member("auto@example.com", sub_id="sub_a", cus="cus_a")
        app_module.sync_member_subscription_state_from_subscription(
            member, _subscription("active", "paid_now", "charge_automatically", "sub_a", "cus_a"))
        db.session.commit()

        refreshed = db.session.get(Member, member.id)
        assert refreshed.payment_status == "paid"
        assert refreshed.is_active is True

    def test_missing_collection_method_defaults_to_automatic(self, app):
        # Stripe always returns collection_method, but older fixtures/objects may
        # omit it; the Checkout flow is the default, so assume automatic.
        member = _invoice_member("auto2@example.com", sub_id="sub_b", cus="cus_b")
        sub = _subscription("active", "paid_now", "charge_automatically", "sub_b", "cus_b")
        del sub["collection_method"]
        app_module.sync_member_subscription_state_from_subscription(member, sub)
        db.session.commit()

        assert db.session.get(Member, member.id).payment_status == "paid"


class TestSubscriptionPeriodBounds:
    """Basil (2025-03-31) moved the billing period onto subscription items."""

    def test_reads_item_level_period(self):
        sub = {"items": {"data": [{"current_period_start": 111, "current_period_end": 222}]}}
        assert app_module.subscription_period_bounds(sub) == (111, 222)

    def test_falls_back_to_legacy_top_level_period(self):
        sub = {"current_period_start": 333, "current_period_end": 444}
        assert app_module.subscription_period_bounds(sub) == (333, 444)

    def test_item_level_wins_over_top_level(self):
        sub = {
            "current_period_start": 1, "current_period_end": 2,
            "items": {"data": [{"current_period_start": 111, "current_period_end": 222}]},
        }
        assert app_module.subscription_period_bounds(sub) == (111, 222)

    def test_empty_subscription_returns_none(self):
        assert app_module.subscription_period_bounds({}) == (None, None)
        assert app_module.subscription_period_bounds(None) == (None, None)


def test_missed_renewal_recovered_from_item_level_period(app):
    """The renewal safety net must work on the current Stripe API shape.

    The previous implementation only read the removed top-level field, so on a
    Basil-or-later API it silently did nothing and a paid member whose renewal
    webhook was missed would be expired on the next reconcile.
    """
    member = make_member(
        email="itemperiod@example.com", payment_status="paid", is_active=True,
        stripe_customer_id="cus_i", stripe_subscription_id="sub_i",
        membership_starts_on=date(CUR - 1, 1, 1),
        membership_ends_on=date(CUR - 1, 12, 31),
        renewal_due_on=date(CUR, 1, 1),
    )
    sub = {
        "id": "sub_i", "customer": "cus_i", "status": "active",
        "collection_method": "charge_automatically",
        "cancel_at_period_end": False, "cancel_at": None,
        # Basil shape: period lives on the item, not the subscription.
        "items": {"data": [{"current_period_start": app_module.start_of_day_unix(date(CUR, 1, 1))}]},
        "metadata": {"activation_mode": "free_period", "membership_ends_on": f"{CUR - 1}-12-31"},
    }

    app_module.sync_member_subscription_state_from_subscription(member, sub)
    db.session.commit()

    refreshed = db.session.get(Member, member.id)
    assert refreshed.membership_ends_on == date(CUR, 12, 31)
    assert refreshed.is_active is True
    assert refreshed.payment_status == "paid"
