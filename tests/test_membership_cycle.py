"""Unit tests for the pure billing/proration logic.

These exercise ``build_membership_cycle`` and the small date helpers with no
database or Flask context. They pin down the calendar-year membership model:

* Membership always runs through Dec 31 of the join year.
* Joining on/after Oct 1 grants a free rest-of-year period (no charge today).
* Joining before Oct 1 costs a prorated share of the annual fee for the days
  remaining in the year, rounded half-up to whole cents.
* The renewal / trial boundary is Jan 1 of the following year.
"""

from datetime import date

import pytest

from conftest import app_module

build_membership_cycle = app_module.build_membership_cycle
first_day_of_year = app_module.first_day_of_year
last_day_of_year = app_module.last_day_of_year
parse_iso_date = app_module.parse_iso_date
to_membership_date = app_module.to_membership_date

ANNUAL = 12000  # cents


class TestFreePeriodBoundary:
    def test_sep_30_is_not_free_period(self):
        cycle = build_membership_cycle(date(2025, 9, 30), ANNUAL)
        assert cycle["free_period"] is False
        assert cycle["prorated_amount_cents"] > 0
        assert cycle["thank_you_phase"] == "prorated"

    def test_oct_1_is_free_period(self):
        cycle = build_membership_cycle(date(2025, 10, 1), ANNUAL)
        assert cycle["free_period"] is True
        assert cycle["prorated_amount_cents"] == 0
        assert cycle["thank_you_phase"] == "free_period"

    def test_dec_31_is_free_period(self):
        cycle = build_membership_cycle(date(2025, 12, 31), ANNUAL)
        assert cycle["free_period"] is True
        assert cycle["prorated_amount_cents"] == 0


class TestCoverageWindow:
    def test_coverage_runs_to_year_end_and_renews_next_jan(self):
        cycle = build_membership_cycle(date(2025, 7, 1), ANNUAL)
        assert cycle["coverage_start"] == date(2025, 7, 1)
        assert cycle["coverage_end"] == date(2025, 12, 31)
        assert cycle["renewal_due_on"] == date(2026, 1, 1)
        assert cycle["current_year"] == 2025

    def test_trial_end_iso_matches_renewal(self):
        cycle = build_membership_cycle(date(2025, 3, 15), ANNUAL)
        assert cycle["trial_end_iso"] == "2026-01-01"
        assert isinstance(cycle["trial_end_unix"], int)


class TestProrationAmounts:
    def test_join_jan_1_pays_full_year(self):
        cycle = build_membership_cycle(date(2025, 1, 1), ANNUAL)
        assert cycle["remaining_days"] == 365
        assert cycle["total_days"] == 365
        assert cycle["prorated_amount_cents"] == ANNUAL

    def test_mid_year_non_leap_proration(self):
        # 2025 is not a leap year: 184 days remain from Jul 1 through Dec 31.
        # 12000 * 184 / 365 = 6049.31.. -> 6049 (half-up).
        cycle = build_membership_cycle(date(2025, 7, 1), ANNUAL)
        assert cycle["total_days"] == 365
        assert cycle["remaining_days"] == 184
        assert cycle["prorated_amount_cents"] == 6049

    def test_mid_year_leap_year_uses_366_day_base(self):
        # 2024 is a leap year: total base is 366 days; 184 days remain from Jul 1.
        # 10000 * 184 / 366 = 5027.32.. -> 5027 (half-up).
        cycle = build_membership_cycle(date(2024, 7, 1), 10000)
        assert cycle["total_days"] == 366
        assert cycle["remaining_days"] == 184
        assert cycle["prorated_amount_cents"] == 5027

    def test_leap_year_full_year_join(self):
        cycle = build_membership_cycle(date(2024, 1, 1), ANNUAL)
        assert cycle["remaining_days"] == 366
        assert cycle["prorated_amount_cents"] == ANNUAL

    def test_rounding_is_half_up(self):
        # Leap year (total_days = 366) lets the quotient land on an exact .5.
        # Jul 2, 2024 leaves 183 covered days; 101 * 183 / 366 = 50.5, which
        # must round UP to 51 (round-half-up), not down to 50.
        cycle = build_membership_cycle(date(2024, 7, 2), 101)
        assert cycle["total_days"] == 366
        assert cycle["remaining_days"] == 183
        assert cycle["free_period"] is False
        assert cycle["prorated_amount_cents"] == 51

    def test_prorated_amount_never_exceeds_annual(self):
        for month in range(1, 10):  # Jan..Sep are the paying months
            cycle = build_membership_cycle(date(2025, month, 1), ANNUAL)
            assert 0 < cycle["prorated_amount_cents"] <= ANNUAL


class TestDateHelpers:
    def test_first_and_last_day_of_year(self):
        assert first_day_of_year(2025) == date(2025, 1, 1)
        assert last_day_of_year(2025) == date(2025, 12, 31)

    def test_parse_iso_date_valid(self):
        assert parse_iso_date("2025-07-01") == date(2025, 7, 1)

    @pytest.mark.parametrize("bad", [None, "", "not-a-date", "2025-13-01"])
    def test_parse_iso_date_invalid_returns_none(self, bad):
        assert parse_iso_date(bad) is None

    def test_to_membership_date_from_unix(self):
        # 2025-07-01T12:00:00Z -> in Europe/Vienna this is still Jul 1.
        assert to_membership_date(1751371200) == date(2025, 7, 1)

    def test_to_membership_date_falsy_returns_today(self):
        assert to_membership_date(0) == app_module.get_membership_today()
        assert to_membership_date(None) == app_module.get_membership_today()
