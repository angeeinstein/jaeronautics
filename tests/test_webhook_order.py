"""The coverage record must not depend on which webhook Stripe delivers first.

One Checkout payment produces two events -- ``checkout.session.completed`` and
``invoice.paid`` -- and Stripe explicitly does not guarantee their order. They
describe the same money differently: checkout knows the prorated window starting
on the join date, the invoice knows only a calendar year.

Both orderings were observed on the live test server within an hour of each
other, and they produced different ledgers for the same kind of signup: one
correctly recorded 19 Sep - 31 Dec, the other claimed 1 Jan - 31 Dec for a
member who joined in September and paid EUR 2.85 for 104 days. That is a
seven-year accounting record decided by network timing.
"""
from datetime import date, datetime, timezone

from conftest import db, make_member, periods
from aeronautics_members.db_models import MembershipPeriod

JOINED = datetime(2026, 9, 19, 8, 30, tzinfo=timezone.utc)
PRORATED_START, YEAR_END = date(2026, 9, 19), date(2026, 12, 31)


def _september_member(email):
    member = make_member(email=email)
    member.created_at = JOINED
    db.session.commit()
    return member


def _checkout_grant(member):
    """What checkout.session.completed does: the real prorated window."""
    return periods.grant_period(
        member, PRORATED_START, YEAR_END, MembershipPeriod.REASON_PAID,
        stripe_subscription_id="sub_x",
    )


def _invoice_grant(member):
    """What invoice.paid does: a whole calendar year, plus the invoice id."""
    return periods.grant_calendar_year(
        member, 2026, MembershipPeriod.REASON_PAID, stripe_invoice_id="in_x",
    )


class TestEitherOrderGivesTheSameRecord:
    def test_checkout_then_invoice(self, app):
        member = _september_member("order_a@example.com")

        _checkout_grant(member)
        _invoice_grant(member)
        db.session.commit()

        assert len(member.membership_periods) == 1
        period = member.membership_periods[0]
        assert period.starts_on == PRORATED_START
        assert period.ends_on == YEAR_END
        assert period.stripe_invoice_id == "in_x"

    def test_invoice_then_checkout(self, app):
        """The ordering that produced the wrong record on the live server."""
        member = _september_member("order_b@example.com")

        _invoice_grant(member)
        _checkout_grant(member)
        db.session.commit()

        assert len(member.membership_periods) == 1
        period = member.membership_periods[0]
        assert period.starts_on == PRORATED_START, "coverage was claimed before the member joined"
        assert period.ends_on == YEAR_END
        assert period.stripe_invoice_id == "in_x"

    def test_the_two_orderings_agree(self, app):
        """Stated directly, because this is the property that failed."""
        first = _september_member("order_c@example.com")
        _checkout_grant(first)
        _invoice_grant(first)

        second = _september_member("order_d@example.com")
        _invoice_grant(second)
        _checkout_grant(second)
        db.session.commit()

        a, b = first.membership_periods[0], second.membership_periods[0]
        assert (a.starts_on, a.ends_on) == (b.starts_on, b.ends_on)


class TestCoverageNeverPrecedesTheMember:
    def test_an_invoice_alone_cannot_backdate_a_first_year(self, app):
        """The invoice names a calendar year; the member joined in September."""
        member = _september_member("backdate@example.com")

        period = _invoice_grant(member)
        db.session.commit()

        assert period.starts_on == PRORATED_START
        assert period.ends_on == YEAR_END

    def test_a_renewal_still_covers_the_whole_year(self, app):
        """The clamp must not shrink a year the member was there for all of."""
        member = _september_member("renewal@example.com")

        period = periods.grant_calendar_year(
            member, 2027, MembershipPeriod.REASON_PAID, stripe_invoice_id="in_2027",
        )
        db.session.commit()

        assert period.starts_on == date(2027, 1, 1)
        assert period.ends_on == date(2027, 12, 31)

    def test_a_member_who_joined_in_january_gets_the_whole_year(self, app):
        member = make_member(email="january@example.com")
        member.created_at = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
        db.session.commit()

        period = periods.grant_calendar_year(
            member, 2026, MembershipPeriod.REASON_PAID, stripe_invoice_id="in_jan",
        )
        db.session.commit()

        assert period.starts_on == date(2026, 1, 1)

    def test_the_clamp_leaves_an_unrelated_past_year_alone(self, app):
        """A grant entirely before the join date is a data question, not ours."""
        member = _september_member("past@example.com")

        period = periods.grant_calendar_year(
            member, 2025, MembershipPeriod.REASON_ADMIN_GRANT, note="historic",
        )
        db.session.commit()

        assert period.starts_on == date(2025, 1, 1)
        assert period.ends_on == date(2025, 12, 31)
