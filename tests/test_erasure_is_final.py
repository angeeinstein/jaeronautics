"""Erasure must not undo itself, and a first year must stay prorated.

Both faults here were found by running a real Stripe payment against the live
test server, not by the suite: one needed an actual subscription cancellation to
come back as a webhook, the other needed a real prorated invoice.
"""
from datetime import date

import pytest

from conftest import db, make_member, privacy
from aeronautics_members.db_models import MembershipPeriod
from aeronautics_members.services.billing import backfill_member_stripe_references
from aeronautics_members.services.membership import (
    sync_member_active_state,
    update_member_paid_coverage,
)


def _fake_cancel(member, reason=None):
    """Stand in for the real cancel, including its local side effect.

    The real one clears the reference once Stripe has cancelled; a stub that
    only returns True would leave the test asserting against a state the
    application never actually reaches.
    """
    if not member.stripe_subscription_id:
        return False
    member.stripe_subscription_id = None
    member.cancel_at_period_end = False
    return True


@pytest.fixture(autouse=True)
def no_remote_calls(monkeypatch):
    monkeypatch.setattr(privacy, "cancel_member_subscription", _fake_cancel)
    monkeypatch.setattr(privacy, "anonymise_forum_account", lambda user: (False, False))


class TestErasureSurvivesTheCancellationWebhook:
    """Erasing cancels the subscription, and Stripe reports that back to us.

    The returning ``customer.subscription.deleted`` sets payment_status to
    "canceled" -- which counts as active, because a member who cancels keeps the
    coverage they paid for -- and the reconciliation that follows used to switch
    the erased member back on and re-attach the dead subscription id.
    """

    def _erased_member_with_coverage(self):
        member = make_member(email="resurrect@example.com", payment_status="paid", is_active=True)
        member.membership_starts_on = date(2026, 9, 19)
        member.membership_ends_on = date(2026, 12, 31)
        member.stripe_customer_id = "cus_live"
        member.stripe_subscription_id = "sub_live"
        db.session.add(
            MembershipPeriod(
                member=member, starts_on=date(2026, 9, 19), ends_on=date(2026, 12, 31),
                reason=MembershipPeriod.REASON_PAID, stripe_invoice_id="in_live",
            )
        )
        db.session.commit()
        privacy.erase_account(member.user, initiated_by=privacy.INITIATED_BY_ADMIN)
        db.session.commit()
        return member

    def test_the_cancellation_webhook_does_not_reactivate(self, app):
        member = self._erased_member_with_coverage()
        assert member.is_active is False

        # What the handler does on customer.subscription.deleted:
        member.payment_status = "canceled"
        changed = sync_member_active_state(member, on_date=date(2026, 10, 1))

        assert member.is_active is False, "an erased member was switched back on"
        assert changed is False

    def test_a_dead_subscription_id_is_not_written_back(self, app):
        member = self._erased_member_with_coverage()
        assert member.stripe_subscription_id is None

        backfill_member_stripe_references(
            member, customer_id="cus_live", subscription_id="sub_live"
        )

        assert member.stripe_subscription_id is None, "the cancelled subscription came back"

    def test_a_living_member_is_still_reconciled_normally(self, app):
        """The guard must not freeze reconciliation for everybody else."""
        member = make_member(email="living@example.com", payment_status="paid", is_active=False)
        member.membership_ends_on = date(2026, 12, 31)
        db.session.commit()

        changed = sync_member_active_state(member, on_date=date(2026, 10, 1))

        assert changed is True
        assert member.is_active is True

    def test_a_living_member_still_gets_its_references(self, app):
        member = make_member(email="living2@example.com")

        backfill_member_stripe_references(member, customer_id="cus_x", subscription_id="sub_x")

        assert member.stripe_customer_id == "cus_x"
        assert member.stripe_subscription_id == "sub_x"


class TestAFirstYearStaysProrated:
    """A member joining in September paid for September to December.

    The paid invoice arrives with an explicit coverage year, and the cached
    window used to be rewritten to 1 January -- claiming months the member was
    not a member and did not pay for, and contradicting both the coverage ledger
    and the invoice. It shows up on their account page and in the data export
    they can request, so it is a record error, not only a display one.
    """

    def test_a_prorated_start_is_not_widened_to_january(self, app):
        member = make_member(email="prorated@example.com")
        member.membership_starts_on = date(2026, 9, 19)
        member.membership_ends_on = date(2026, 12, 31)
        db.session.commit()

        update_member_paid_coverage(member, date(2026, 9, 19), coverage_year=2026)

        assert member.membership_starts_on == date(2026, 9, 19)
        assert member.membership_ends_on == date(2026, 12, 31)
        assert member.renewal_due_on == date(2027, 1, 1)

    def test_a_renewal_does_cover_the_whole_year(self, app):
        """The widening is right when the paid year really is a full one."""
        member = make_member(email="renewed@example.com")
        member.membership_starts_on = date(2026, 9, 19)
        member.membership_ends_on = date(2026, 12, 31)
        db.session.commit()

        update_member_paid_coverage(member, date(2027, 1, 1), coverage_year=2027)

        assert member.membership_starts_on == date(2027, 1, 1)
        assert member.membership_ends_on == date(2027, 12, 31)

    def test_the_cached_window_agrees_with_the_ledger(self, app):
        """The two must tell the same story; that is the point of the ledger."""
        member = make_member(email="agree@example.com")
        member.membership_starts_on = date(2026, 9, 19)
        member.membership_ends_on = date(2026, 12, 31)
        db.session.add(
            MembershipPeriod(
                member=member, starts_on=date(2026, 9, 19), ends_on=date(2026, 12, 31),
                reason=MembershipPeriod.REASON_PAID, stripe_invoice_id="in_agree",
            )
        )
        db.session.commit()

        update_member_paid_coverage(member, date(2026, 9, 19), coverage_year=2026)

        period = member.membership_periods[0]
        assert member.membership_starts_on == period.starts_on
        assert member.membership_ends_on == period.ends_on
