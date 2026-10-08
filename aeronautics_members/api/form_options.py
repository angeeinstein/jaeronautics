"""The choices the member forms offer -- countries, salutations, the kinds of
member and what each is asked -- for My Account and the signup. Public: the
signup is filled in before there is an account. The rules themselves live in
member_categories.py and forms.py; the browser only shows and hides by them,
and every save checks them again.
"""

from typing import Literal

from ..forms import COUNTRIES, SALUTATION_CHOICES
from ..member_categories import (
    CATEGORY_ORDER,
    asks_company_name,
    category_description,
    category_label,
    is_joinable,
    requires_institutional_email,
    requires_year_group,
    shows_year_group,
)
from ..services.institutional_email import get_institutional_domains
from ..services.signup import invoice_payments_allowed
from ._core import Model, endpoint


class ChoiceOut(Model):
    value: str
    label: str


class MemberCategoryOut(Model):
    value: str
    label: str
    description: str
    year_group: Literal["required", "optional", "hidden"]
    #: Whether the university or company address must be given.
    university_email_required: bool
    #: Whether the company joined for is asked (and then must be given).
    company_name: bool
    #: Whether somebody may choose it when joining; an honorary member is appointed.
    joinable: bool


class FormOptionsOut(Model):
    countries: list[ChoiceOut]
    salutations: list[ChoiceOut]
    member_categories: list[MemberCategoryOut]
    #: Whether paying by invoice is offered besides card and SEPA Direct Debit.
    invoice_payments: bool
    #: The domains a student's university address is on ("edu.fh-joanneum.at"), subdomains
    #: included: for saying so while it is typed. The save checks it again.
    university_domains: list[str]


def _year_group(category):
    if not shows_year_group(category):
        return "hidden"
    return "required" if requires_year_group(category) else "optional"


@endpoint("GET", "/forms/options", response=FormOptionsOut, public=True, tag="Account")
def form_options():
    """The choices of the member forms, and what each kind of member is asked."""
    return FormOptionsOut(
        countries=[ChoiceOut(value=value, label=str(label)) for value, label in COUNTRIES if value],
        salutations=[ChoiceOut(value=value, label=str(label)) for value, label in SALUTATION_CHOICES if value],
        member_categories=[
            MemberCategoryOut(value=category, label=str(category_label(category)),
                              description=str(category_description(category)), year_group=_year_group(category),
                              university_email_required=requires_institutional_email(category),
                              company_name=asks_company_name(category), joinable=is_joinable(category))
            for category in CATEGORY_ORDER
        ],
        invoice_payments=invoice_payments_allowed(),
        university_domains=list(get_institutional_domains()),
    )
