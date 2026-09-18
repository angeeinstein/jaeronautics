"""Dates and times in the association's calendar.

Membership runs on a single shared calendar year in one fixed timezone, so
"today" is a domain question, not a request-scoped one: the answer must be the
same for a browser in another timezone, a Stripe webhook, a nightly job and a
future mobile client. These helpers are the one place that decides it.

Timestamps that come back from Stripe are UTC epochs; they are converted into
the membership timezone before a calendar date is taken, because a payment at
23:30 UTC on Dec 31 is still Dec 31 locally -- and the year it lands in decides
which membership period it pays for.
"""

from datetime import date, datetime, timezone

from ..config import MEMBERSHIP_TIMEZONE


def get_membership_now():
    return datetime.now(timezone.utc).astimezone(MEMBERSHIP_TIMEZONE)


def get_membership_today():
    return get_membership_now().date()


def get_now_utc():
    return datetime.now(timezone.utc)


def parse_iso_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def to_membership_date(unix_timestamp):
    if not unix_timestamp:
        return get_membership_today()
    return datetime.fromtimestamp(unix_timestamp, timezone.utc).astimezone(MEMBERSHIP_TIMEZONE).date()


def first_day_of_year(year):
    return date(year, 1, 1)


def last_day_of_year(year):
    return date(year, 12, 31)


def start_of_day_unix(day_value):
    local_start = datetime.combine(day_value, datetime.min.time(), tzinfo=MEMBERSHIP_TIMEZONE)
    return int(local_start.astimezone(timezone.utc).timestamp())
