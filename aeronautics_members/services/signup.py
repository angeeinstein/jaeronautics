"""Starting a membership once its profile exists -- one way, from both doors.

There are two ways in: the public signup, which makes the login and the
membership together, and "create membership profile", for somebody who already
has a login. Each used to carry its own copy of what happens next, and the
copies drifted: the second never sent the confirmation link to the university
or company address -- the link that shows somebody studies or works here, and
the one that gives a returning student their old forum account back. Nobody
coming in through that door could ever be reconnected.

So what happens after the profile is saved lives in one place, and both routes
call it: the payment-method decision here, the rest in blueprints/_signup.py,
because it flashes and redirects and services do not.
"""

from .settings import get_settings_map


def invoice_payments_allowed():
    """Whether paying by invoice is offered. Asked by the forms and the routes."""
    setting = get_settings_map(["invoice_payments_enabled"]).get("invoice_payments_enabled")
    return setting == "True"


def chosen_payment_method(requested):
    """What the person asked for, unless it is not on offer."""
    if requested == "invoice" and invoice_payments_allowed():
        return "invoice"
    return "checkout"
