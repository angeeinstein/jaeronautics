"""At most one email of a kind per minute, from one browser, to one address.

The buttons that send an email -- "send the confirmation again", "forgot
password" -- sent one per click, and an email takes a moment to arrive, which
is exactly when people click again. For a password reset the second one was
worse than noise: each request replaces the link, so the first email, the one
somebody opens, said "invalid or expired".

Kept in the session, which is per browser. That is the double click and the
impatient retry; somebody set on sending many is what the rate limits on the
routes are for.

Here rather than in services because it reads the session.
"""

from flask import session

from ..services.clock import get_now_utc

EMAIL_COOLDOWN_SECONDS = 60


def _key(kind):
    return f"email-sent:{kind}"


def sent_just_now(kind, address):
    """Whether this browser had this email sent to this address within the minute."""
    record = session.get(_key(kind)) or {}
    if record.get("to") != (address or "").strip().lower():
        return False
    age = int(get_now_utc().timestamp()) - int(record.get("at") or 0)
    return 0 <= age < EMAIL_COOLDOWN_SECONDS


def remember_sent(kind, address):
    session[_key(kind)] = {
        "to": (address or "").strip().lower(),
        "at": int(get_now_utc().timestamp()),
    }
