"""The coverage ledger: access should have evidence behind it.

``payment_status`` records what a member's state is; these rows record why. The
distinction is what the invoice-billing bug came down to -- a member could be
marked paid with nothing behind it, and no field could contradict that.
"""
from datetime import date, datetime, timezone

import pytest

from conftest import app_module, db, make_member, periods
from aeronautics_members.db_models import MembershipPeriod
from aeronautics_members.services import ValidationError

CUR = date.today().year


def make_returning_member(**kwargs):
    """A member who joined in an earlier year.

    A full Jan-Dec paid grant only makes sense for someone who was already a
    member when the year began; for a first year the grant is clamped to the
    join date, because that is all the member paid for. Tests about projection
    and access want the renewal case, so they say so explicitly rather than
    relying on a member created "today" being granted the whole year.
    """
    kwargs.setdefault("created_at", datetime(CUR - 1, 3, 1, tzinfo=timezone.utc))
    return make_member(**kwargs)


class TestGranting:
    def test_grant_records_the_reason(self, app):
        member = make_member(email="g@example.com")
        period = periods.grant_period(
            member, date(CUR, 1, 1), date(CUR, 12, 31),
            MembershipPeriod.REASON_PAID, stripe_invoice_id="in_1",
        )
        db.session.commit()

        assert period.reason == "paid"
        assert period.stripe_invoice_id == "in_1"
        assert periods.has_coverage(member, on_date=date(CUR, 6, 1)) is True

    def test_grant_is_idempotent_per_invoice(self, app):
        """A redelivered invoice.paid must not grant coverage twice."""
        member = make_member(email="idem@example.com")
        first = periods.grant_period(
            member, date(CUR, 1, 1), date(CUR, 12, 31),
            MembershipPeriod.REASON_PAID, stripe_invoice_id="in_dup")
        db.session.commit()
        second = periods.grant_period(
            member, date(CUR, 1, 1), date(CUR, 12, 31),
            MembershipPeriod.REASON_PAID, stripe_invoice_id="in_dup")
        db.session.commit()

        assert first.id == second.id
        assert len(member.membership_periods) == 1

    def test_calendar_year_grant_spans_the_whole_year(self, app):
        member = make_returning_member(email="year@example.com")
        period = periods.grant_calendar_year(member, CUR, MembershipPeriod.REASON_PAID)
        db.session.commit()
        assert (period.starts_on, period.ends_on) == (date(CUR, 1, 1), date(CUR, 12, 31))

    @pytest.mark.parametrize("kwargs", [
        {"starts_on": date(CUR, 12, 31), "ends_on": date(CUR, 1, 1)},   # ends before it starts
        {"starts_on": None, "ends_on": date(CUR, 12, 31)},              # missing bound
    ])
    def test_invalid_window_is_rejected(self, app, kwargs):
        member = make_member(email="bad@example.com")
        with pytest.raises(ValidationError):
            periods.grant_period(member, reason=MembershipPeriod.REASON_PAID, **kwargs)

    def test_unknown_reason_is_rejected(self, app):
        # A grant must state grounds the system recognises, so an unsupported
        # one cannot be created by accident.
        member = make_member(email="reason@example.com")
        with pytest.raises(ValidationError):
            periods.grant_period(member, date(CUR, 1, 1), date(CUR, 12, 31), "because")


class TestRevocation:
    def test_revoked_period_stops_granting_access_but_is_kept(self, app):
        member = make_member(email="rev@example.com")
        period = periods.grant_period(
            member, date(CUR, 1, 1), date(CUR, 12, 31), MembershipPeriod.REASON_PAID)
        db.session.commit()

        assert periods.revoke_period(period, "Chargeback lost.") is True
        db.session.commit()

        assert periods.has_coverage(member, on_date=date(CUR, 6, 1)) is False
        # Kept on the record, so an admin can still see what was believed.
        assert len(member.membership_periods) == 1
        assert member.membership_periods[0].revoked_reason == "Chargeback lost."

    def test_revoking_twice_is_a_no_op(self, app):
        member = make_member(email="rev2@example.com")
        period = periods.grant_period(
            member, date(CUR, 1, 1), date(CUR, 12, 31), MembershipPeriod.REASON_PAID)
        db.session.commit()
        periods.revoke_period(period, "first")
        assert periods.revoke_period(period, "second") is False
        assert period.revoked_reason == "first"

    def test_only_paid_periods_of_that_subscription_are_revoked(self, app):
        # A lost dispute invalidates what that payment bought, not a separate
        # free grant the member also holds.
        member = make_member(email="mix@example.com")
        periods.grant_period(member, date(CUR, 1, 1), date(CUR, 6, 30),
                             MembershipPeriod.REASON_PAID, stripe_subscription_id="sub_a")
        periods.grant_period(member, date(CUR, 7, 1), date(CUR, 12, 31),
                             MembershipPeriod.REASON_FREE_PERIOD, stripe_subscription_id="sub_a")
        periods.grant_period(member, date(CUR + 1, 1, 1), date(CUR + 1, 12, 31),
                             MembershipPeriod.REASON_PAID, stripe_subscription_id="sub_b")
        db.session.commit()

        revoked = periods.revoke_periods_for_subscription(member, "sub_a", "dispute lost")
        db.session.commit()

        assert revoked == 1
        remaining = {(p.reason, p.is_revoked) for p in member.membership_periods}
        assert ("free_period", False) in remaining
        assert ("paid", True) in remaining


class TestProjection:
    def test_cached_fields_follow_the_ledger(self, app):
        member = make_returning_member(email="proj@example.com", payment_status="unpaid", is_active=False)
        periods.grant_calendar_year(member, CUR, MembershipPeriod.REASON_PAID)
        db.session.commit()

        assert periods.project_coverage_onto_member(member, on_date=date(CUR, 6, 1)) is True
        assert member.membership_ends_on == date(CUR, 12, 31)
        assert member.renewal_due_on == date(CUR + 1, 1, 1)
        assert member.payment_status == "paid"
        assert member.is_active is True

    def test_projection_never_shortens_existing_coverage(self, app):
        # A ledger that has not caught up with a renewal must not pull a paid
        # member's coverage backwards.
        member = make_member(
            email="noregress@example.com",
            membership_ends_on=date(CUR + 1, 12, 31), payment_status="paid", is_active=True)
        periods.grant_calendar_year(member, CUR, MembershipPeriod.REASON_PAID)
        db.session.commit()

        periods.project_coverage_onto_member(member)
        assert member.membership_ends_on == date(CUR + 1, 12, 31)

    def test_paid_evidence_outranks_a_free_grant(self, app):
        member = make_returning_member(email="rank@example.com", payment_status="unpaid", is_active=False)
        periods.grant_calendar_year(member, CUR, MembershipPeriod.REASON_FREE_PERIOD)
        periods.grant_calendar_year(member, CUR, MembershipPeriod.REASON_PAID)
        db.session.commit()

        periods.project_coverage_onto_member(member, on_date=date(CUR, 6, 1))
        assert member.payment_status == "paid"

    def test_empty_ledger_leaves_the_member_alone(self, app):
        member = make_member(email="none@example.com", payment_status="unpaid")
        assert periods.project_coverage_onto_member(member) is False
        assert member.payment_status == "unpaid"


def test_describe_coverage_is_plain_serializable_data(app):
    """The same description must serve an admin page and a JSON client."""
    member = make_returning_member(email="desc@example.com")
    periods.grant_calendar_year(
        member, CUR, MembershipPeriod.REASON_PAID, stripe_invoice_id="in_desc")
    revoked = periods.grant_calendar_year(member, CUR - 1, MembershipPeriod.REASON_FREE_PERIOD)
    periods.revoke_period(revoked, "superseded")
    db.session.commit()

    described = periods.describe_coverage(member, on_date=date(CUR, 6, 1))

    assert described["has_coverage"] is True
    assert described["coverage_end"] == date(CUR, 12, 31)
    assert len(described["periods"]) == 2
    current = next(p for p in described["periods"] if p["covers_today"])
    assert current["reason"] == "paid"
    assert current["stripe_invoice_id"] == "in_desc"
    assert any(p["revoked"] and p["revoked_reason"] == "superseded" for p in described["periods"])


class TestOnePaymentOneRecord:
    """A single payment must leave one coverage record, not two.

    Found on the live deployment: a Checkout signup fires both
    checkout.session.completed and invoice.paid, so the ledger gained two rows
    for one payment -- and the second claimed a whole calendar year when only the
    prorated remainder had been bought.
    """

    def test_checkout_then_invoice_keeps_one_record(self, app):
        member = make_member(email="onepay@example.com")
        # checkout.session.completed: prorated window, invoice id not yet known.
        periods.grant_period(
            member, date(CUR, 9, 18), date(CUR, 12, 31),
            MembershipPeriod.REASON_PAID, stripe_subscription_id="sub_x")
        # invoice.paid for the same payment, arriving moments later.
        periods.grant_calendar_year(
            member, CUR, MembershipPeriod.REASON_PAID,
            stripe_invoice_id="in_x", stripe_subscription_id="sub_x")
        db.session.commit()

        assert len(member.membership_periods) == 1
        period = member.membership_periods[0]
        # The prorated window is kept: it is what was actually paid for.
        assert (period.starts_on, period.ends_on) == (date(CUR, 9, 18), date(CUR, 12, 31))
        # ...and the invoice that proves it is attached once it is known.
        assert period.stripe_invoice_id == "in_x"

    def test_next_year_renewal_is_a_separate_record(self, app):
        member = make_member(email="renewrec@example.com")
        periods.grant_calendar_year(member, CUR, MembershipPeriod.REASON_PAID,
                                    stripe_invoice_id="in_a")
        periods.grant_calendar_year(member, CUR + 1, MembershipPeriod.REASON_PAID,
                                    stripe_invoice_id="in_b")
        db.session.commit()

        assert len(member.membership_periods) == 2
        assert {p.ends_on.year for p in member.membership_periods} == {CUR, CUR + 1}

    def test_a_revoked_record_does_not_block_a_new_grant(self, app):
        # After a lost dispute the member may pay again for the same year.
        member = make_returning_member(email="regrant@example.com")
        first = periods.grant_calendar_year(member, CUR, MembershipPeriod.REASON_PAID,
                                            stripe_invoice_id="in_lost")
        periods.revoke_period(first, "chargeback")
        db.session.commit()

        periods.grant_calendar_year(member, CUR, MembershipPeriod.REASON_PAID,
                                    stripe_invoice_id="in_new")
        db.session.commit()

        assert len(member.membership_periods) == 2
        assert periods.has_coverage(member, on_date=date(CUR, 6, 1)) is True

    def test_free_then_paid_in_one_year_are_distinct(self, app):
        # Different grounds for coverage stay separately recorded.
        member = make_member(email="freethenpaid@example.com")
        periods.grant_calendar_year(member, CUR, MembershipPeriod.REASON_FREE_PERIOD)
        periods.grant_calendar_year(member, CUR, MembershipPeriod.REASON_PAID,
                                    stripe_invoice_id="in_f")
        db.session.commit()

        assert {p.reason for p in member.membership_periods} == {"free_period", "paid"}


class TestLedgerGovernsAccess:
    """The ledger decides access once it has something to say about a member."""

    def test_revoked_coverage_removes_access_despite_stale_flags(self, app):
        """The case the ledger exists for.

        After a lost chargeback the coverage is revoked. If access still came
        from the cached fields, a flag nobody updated would keep the member in.
        """
        member = make_returning_member(
            email="revokedaccess@example.com",
            payment_status="paid", is_active=True,          # stale, says active
            membership_ends_on=date(CUR, 12, 31),
        )
        period = periods.grant_calendar_year(member, CUR, MembershipPeriod.REASON_PAID)
        db.session.commit()

        assert app_module.member_has_active_access(member, on_date=date(CUR, 6, 1)) is True

        periods.revoke_period(period, "chargeback lost")
        db.session.commit()

        assert app_module.member_has_active_access(member, on_date=date(CUR, 6, 1)) is False

    def test_ledger_grants_access_before_the_cached_fields_catch_up(self, app):
        member = make_returning_member(
            email="ledgerfirst@example.com",
            payment_status="unpaid", is_active=False, membership_ends_on=None,
        )
        periods.grant_calendar_year(member, CUR, MembershipPeriod.REASON_PAID)
        db.session.commit()

        assert app_module.member_has_active_access(member, on_date=date(CUR, 6, 1)) is True

    def test_access_ends_when_the_recorded_period_does(self, app):
        member = make_member(email="expiring@example.com", payment_status="paid", is_active=True)
        periods.grant_period(member, date(CUR, 1, 1), date(CUR, 6, 30),
                             MembershipPeriod.REASON_PAID)
        db.session.commit()

        assert app_module.member_has_active_access(member, on_date=date(CUR, 6, 30)) is True
        assert app_module.member_has_active_access(member, on_date=date(CUR, 7, 1)) is False

    def test_members_without_records_still_use_the_cached_fields(self, app):
        # Accounts predating the ledger must not lose access.
        member = make_member(
            email="legacyaccess@example.com",
            payment_status="paid", is_active=True,
            membership_ends_on=date(CUR, 12, 31),
        )
        db.session.commit()

        assert member.membership_periods == []
        assert app_module.member_has_active_access(member, on_date=date(CUR, 6, 1)) is True
