"""Becoming a member: the public signup, which makes the login and the
membership together (POST /signup), and a membership for somebody signed in
without one (POST /account/membership). Both are checked by the same WTForms
rules as before (forms.py through api/_forms.py) -- the kind of member, the
university address, the year group, the tick for the legal texts -- and both
answer where the browser goes next: Stripe's payment page, the thank-you page
for an invoice, or My Account when paying could not start (it says why).

The choices the form offers are GET /api/v1/forms/options; the texts to accept,
GET /api/v1/legal (``accepted_at_signup``). Drawn by frontend/src/pages/public/Join.tsx.
"""

from typing import Literal

from flask import flash, url_for
from flask_login import current_user, login_user
from pydantic import Field

from ..app import limiter, rate_limit_network, rate_limit_network_and_address
from ..blueprints._email_cooldown import remember_sent
from ..config import RATELIMIT_MEMBERSHIP, RATELIMIT_MEMBERSHIP_PER_IP
from ..forms import CreateMembershipProfileForm, MembershipForm
from ..services import ExternalServiceError
from ..services import signup as signup_service
from ..services.account import resume_payment_url
from ..services.billing import can_resume_payment
from ._core import Model, endpoint
from ._forms import checked
from .sign_in import GoOnOut

TAG = "Account"

#: What a membership profile is made of; the rest of a form is about the login and paying.
PROFILE_FIELDS = ("salutation", "title", "first_name", "last_name", "street", "house_number", "postal_code",
                  "city", "country", "phone_private", "phone_work", "email_work", "member_category",
                  "year_group")


class MembershipIn(Model):
    salutation: str = Field(max_length=20)
    title: str | None = Field(None, max_length=50)
    first_name: str = Field(max_length=100)
    last_name: str = Field(max_length=100)
    street: str = Field(max_length=255)
    house_number: str = Field(max_length=20)
    postal_code: str = Field(max_length=20)
    city: str = Field(max_length=100)
    country: str = Field(max_length=100)
    phone_private: str = Field(max_length=50)
    phone_work: str | None = Field(None, max_length=50)
    email_work: str | None = Field(None, max_length=255)
    member_category: str = Field(max_length=20)
    year_group: str | None = Field(None, max_length=50)
    payment_method: Literal["checkout", "invoice"] = "checkout"
    #: The one tick for the legal texts accepted at signup.
    terms_accepted: bool


class SignupIn(MembershipIn):
    #: The login, and where the association writes.
    email_private: str = Field(max_length=255)
    password: str = Field(max_length=128)


def _form_data(body, **more):
    data = body.model_dump(exclude={"terms_accepted", "payment_method", "password"})
    # The tick as a browser would send it; unticked, it is refused by the form's own rule.
    return {**data, **more, "terms_accepted": "y" if body.terms_accepted else ""}


def _start(member, payment_method, what):
    """Where to go next; when paying cannot start, My Account, which says so (flashed)."""
    try:
        return signup_service.begin_membership(member, payment_method, what=what, sent=remember_sent)
    except ExternalServiceError as exc:
        flash(exc.message, "warning")
        return url_for("account.account")


@endpoint("POST", "/signup", response=GoOnOut, body=SignupIn, public=True, tag=TAG)
@limiter.limit(RATELIMIT_MEMBERSHIP_PER_IP, key_func=rate_limit_network)
@limiter.limit(RATELIMIT_MEMBERSHIP, key_func=rate_limit_network_and_address)
def sign_up(body):
    """Join: the login and the membership, then on to paying. Signed in afterwards."""
    values = checked(MembershipForm, _form_data(body, password=body.password, confirm_password=body.password))
    profile = {name: values.get(name) for name in PROFILE_FIELDS}
    profile["email_private"] = values["email_private"]
    user, member, continuing = signup_service.sign_up(profile, body.password, body.payment_method)
    login_user(user)
    if continuing:
        try:
            return GoOnOut(go_to=signup_service.continue_signup(member))
        except ExternalServiceError as exc:
            flash(exc.message, "warning")
            return GoOnOut(go_to=url_for("account.account"))
    method = signup_service.chosen_payment_method(body.payment_method)
    return GoOnOut(go_to=_start(member, method, "account"))


@endpoint("POST", "/account/membership", response=GoOnOut, body=MembershipIn, tag=TAG)
def become_member(body):
    """A membership for this login, then on to paying."""
    values = checked(CreateMembershipProfileForm, _form_data(body, email_private=current_user.email))
    profile = {name: values.get(name) for name in PROFILE_FIELDS}
    member = signup_service.become_member(current_user, profile, body.payment_method)
    method = signup_service.chosen_payment_method(body.payment_method)
    if member is None:
        # Sent twice -- a double click while Stripe was asked for the payment
        # page: the first made the membership. On to that payment page.
        existing = current_user.member
        if can_resume_payment(existing) and method == "checkout":
            try:
                return GoOnOut(go_to=resume_payment_url(existing))
            except ExternalServiceError as exc:
                flash(exc.message, "warning")
        return GoOnOut(go_to=url_for("account.account"))
    return GoOnOut(go_to=_start(member, method, "profile"))
