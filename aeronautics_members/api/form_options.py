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
    STUDY_PROGRAMMES,
    account_email_rule,
    asks_company_name,
    category_description,
    category_label,
    institutional_domains_of,
    is_joinable,
    requires_institutional_email,
    requires_year_group,
    shows_year_group,
)
from ..services.institutional_email import get_staff_domains, get_student_domains
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
    #: Whether it must be given when joining: staff are asked for their institute address then.
    work_email_at_joining: bool
    #: Whose domains it must be on -- a student's, or staff's (which a student's never counts
    #: as) -- or None for any.
    work_email_whose: Literal["student", "staff"] | None
    #: Which address the account may belong to: a private one, a private one or the institute
    #: address given as such, or any (a company member's company address).
    account_email: Literal["private", "private_or_institute", "any"]
    #: Whether the company joined for is asked (and then must be given).
    company_name: bool
    #: Whether somebody may choose it when joining; an honorary member is appointed.
    joinable: bool


class ProgrammeOut(Model):
    #: "LAV": the first part of a year group.
    code: str
    #: "Aviation".
    name: str
    #: "Bachelor", "Master".
    degree: str


class FormOptionsOut(Model):
    countries: list[ChoiceOut]
    salutations: list[ChoiceOut]
    member_categories: list[MemberCategoryOut]
    #: Whether paying by invoice is offered besides card and SEPA Direct Debit.
    invoice_payments: bool
    #: The domains students' and staff's addresses are on, subdomains included: an address on
    #: either is not a private one. For saying so while it is typed; the save checks it again.
    student_domains: list[str]
    staff_domains: list[str]
    #: The study programmes offered by name; any other is typed as its three-letter code.
    programmes: list[ProgrammeOut]


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
                              work_email_at_joining=requires_institutional_email(category, joining=True),
                              work_email_whose=institutional_domains_of(category),
                              account_email=account_email_rule(category),
                              company_name=asks_company_name(category), joinable=is_joinable(category))
            for category in CATEGORY_ORDER
        ],
        invoice_payments=invoice_payments_allowed(),
        student_domains=list(get_student_domains()),
        staff_domains=list(get_staff_domains()),
        programmes=[ProgrammeOut(code=code, name=name, degree=degree) for code, name, degree in STUDY_PROGRAMMES],
    )
