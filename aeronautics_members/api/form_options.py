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
    category_description,
    category_label,
    requires_institutional_email,
    requires_year_group,
    shows_year_group,
)
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


class FormOptionsOut(Model):
    countries: list[ChoiceOut]
    salutations: list[ChoiceOut]
    member_categories: list[MemberCategoryOut]
    #: Whether paying by invoice is offered besides card and SEPA Direct Debit.
    invoice_payments: bool


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
                              university_email_required=requires_institutional_email(category))
            for category in CATEGORY_ORDER
        ],
        invoice_payments=invoice_payments_allowed(),
    )
