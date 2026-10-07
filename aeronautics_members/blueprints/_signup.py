"""What happens once a membership profile is saved, for the pages: the
service's answer (services/signup.py, begin_membership) as a redirect, and
its problem as a message on the account page.
"""

from flask import flash, redirect, url_for

from ..services import ExternalServiceError
from ..services.signup import begin_membership
from ._email_cooldown import remember_sent


def start_membership(member, payment_method, *, what):
    """Send the confirmation links, then start paying. Returns the response."""
    try:
        url = begin_membership(member, payment_method, what=what, sent=remember_sent)
    except ExternalServiceError as exc:
        flash(exc.message, "warning")
        return redirect(url_for("account.account"))
    return redirect(url, code=303 if url.startswith("http") else 302)
