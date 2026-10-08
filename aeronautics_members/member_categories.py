"""What kind of member somebody is.

Membership is not only for students -- the statutes have always allowed
otherwise -- and the kinds differ in ways the rest of the system needs to know
about. Alumni and staff are members in full; a partner joins to reach students;
an honorary member is appointed rather than signed up.

The year group cannot stand in for this. An alumnus has one, and so may a
lecturer who studied here, so the category carries something the year group
does not and has to be stored in its own right.

Everything about a category lives in this module, so adding one is an edit
here plus a migration for the stored value -- never a hunt through forms,
templates and validators. Deliberately *not* here: anything about money. All
categories currently pay the same annual fee, and if that ever changes, the
fee belongs with the billing code keyed off the value stored here.
"""

from flask_babel import lazy_gettext as _l


class MemberCategory:
    STUDENT = "student"
    ALUMNI = "alumni"
    STAFF = "staff"
    PARTNER = "partner"
    HONORARY = "honorary"


# Order matters: this is the order the choices appear on every form, and the
# common case goes first.
CATEGORY_ORDER = (
    MemberCategory.STUDENT,
    MemberCategory.ALUMNI,
    MemberCategory.STAFF,
    MemberCategory.PARTNER,
    MemberCategory.HONORARY,
)

DEFAULT_CATEGORY = MemberCategory.STUDENT

CATEGORY_LABELS = {
    MemberCategory.STUDENT: _l("Student"),
    MemberCategory.ALUMNI: _l("Alumni"),
    MemberCategory.STAFF: _l("Staff or lecturer"),
    MemberCategory.PARTNER: _l("Company or partner"),
    MemberCategory.HONORARY: _l("Honorary member"),
}

CATEGORY_DESCRIPTIONS = {
    MemberCategory.STUDENT: _l("Currently studying at FH Joanneum."),
    MemberCategory.ALUMNI: _l("Studied here and is still a member."),
    MemberCategory.STAFF: _l("Works at the institute, for example as a lecturer."),
    MemberCategory.PARTNER: _l("Joined on behalf of a company."),
    MemberCategory.HONORARY: _l("Appointed by the association."),
}

# Who is asked for a year group, and who must give one.
#
# Only students must: their cohort decides the forum username and is how the
# association organises them. Alumni are offered the field because most know
# their cohort and it is worth keeping, but a member who studied here in 2006
# may have no idea, and refusing their membership over it would be absurd.
# Nobody else is asked at all.
YEAR_GROUP_RULES = {
    MemberCategory.STUDENT: {"shown": True, "required": True},
    MemberCategory.ALUMNI: {"shown": True, "required": False},
    MemberCategory.STAFF: {"shown": False, "required": False},
    MemberCategory.PARTNER: {"shown": False, "required": False},
    MemberCategory.HONORARY: {"shown": False, "required": False},
}


# Who has to give an institutional address, and on whose domains.
#
# A student must, always: a live @edu.fh-joanneum.at address is what says
# somebody is a student right now. Staff give their institute address
# (@fh-joanneum.at) when they join -- members from before that rule are not
# asked again for theirs -- and it must be a staff address, not a student's. A
# partner's company address is one no list could anticipate, and an alumnus's
# university address has usually stopped working -- which is the whole reason a
# private address is collected as well.
INSTITUTIONAL_EMAIL_RULES = {
    MemberCategory.STUDENT: {"required": True, "at_joining": True, "domains": "student"},
    MemberCategory.ALUMNI: {"required": False, "at_joining": False, "domains": None},
    MemberCategory.STAFF: {"required": False, "at_joining": True, "domains": "staff"},
    MemberCategory.PARTNER: {"required": False, "at_joining": False, "domains": None},
    MemberCategory.HONORARY: {"required": False, "at_joining": False, "domains": None},
}

# Which address a member's account belongs to -- where the association writes,
# where a new password goes.
#
# A student's and an alumnus's must be private: a university address ends with
# the studies, and with it the way into the account. Staff may choose theirs or
# their institute address; somebody who leaves the institute changes it in My
# Account first. A company member's is their company address: they join for it.
ACCOUNT_EMAIL_RULES = {
    MemberCategory.STUDENT: "private",
    MemberCategory.ALUMNI: "private",
    MemberCategory.STAFF: "private_or_institute",
    MemberCategory.PARTNER: "any",
    MemberCategory.HONORARY: "private",
}


# A year group is a study programme's three-letter code and the two digits of
# the year somebody started it: LAV25. Every programme at the university has
# such a code; the ones most members study are offered by name, any other is
# typed. Membership is open to all of them, so this is a convenience, not a list
# of who may join.
YEAR_GROUP_PATTERN = r"^[A-Z]{3}[0-9]{2}$"
STUDY_PROGRAMMES = (
    ("LAV", "Aviation", "Bachelor"),
    ("MAV", "Aviation", "Master"),
)


# Who is asked for the company they join for: partners, and they must say.
COMPANY_NAME_RULES = {
    MemberCategory.STUDENT: {"shown": False, "required": False},
    MemberCategory.ALUMNI: {"shown": False, "required": False},
    MemberCategory.STAFF: {"shown": False, "required": False},
    MemberCategory.PARTNER: {"shown": True, "required": True},
    MemberCategory.HONORARY: {"shown": False, "required": False},
}

# Who may sign up as what. An honorary member is appointed by the association,
# never self-declared: an admin sets the category on an existing membership.
JOINABLE_CATEGORIES = (
    MemberCategory.STUDENT,
    MemberCategory.ALUMNI,
    MemberCategory.STAFF,
    MemberCategory.PARTNER,
)


def is_valid(category):
    return category in CATEGORY_LABELS


def requires_institutional_email(category, joining=False):
    """Whether this category cannot be saved without a university/company address --
    ``joining``: when the membership is made, which asks staff for theirs too."""
    rules = INSTITUTIONAL_EMAIL_RULES.get(category, {})
    return rules.get("at_joining" if joining else "required", False)


def institutional_domains_of(category):
    """Whose domains that address must be on: "student", "staff", or None for any."""
    return INSTITUTIONAL_EMAIL_RULES.get(category, {}).get("domains")


def checks_institutional_domain(category):
    """Whether that address must be on one of the allowed-domain lists."""
    return institutional_domains_of(category) is not None


def account_email_rule(category):
    """Which address the account may belong to: "private", "private_or_institute" or "any"."""
    return ACCOUNT_EMAIL_RULES.get(category, "private")


def category_label(category):
    """A readable name, falling back to the stored value for anything unknown.

    An unrecognised value should never reach a screen, but showing it raw beats
    a blank cell or an exception on a page an admin is using to fix it.
    """
    return CATEGORY_LABELS.get(category, category or "")


def category_description(category):
    return CATEGORY_DESCRIPTIONS.get(category, "")


def shows_year_group(category):
    """Whether the year group field is offered to this category at all."""
    return YEAR_GROUP_RULES.get(category, {}).get("shown", False)


def requires_year_group(category):
    """Whether this category cannot be saved without a year group."""
    return YEAR_GROUP_RULES.get(category, {}).get("required", False)


def asks_company_name(category):
    """Whether the company field is offered to this category at all."""
    return COMPANY_NAME_RULES.get(category, {}).get("shown", False)


def requires_company_name(category):
    """Whether this category cannot join without naming its company."""
    return COMPANY_NAME_RULES.get(category, {}).get("required", False)


def is_joinable(category):
    """Whether somebody may choose this category when joining."""
    return category in JOINABLE_CATEGORIES


def joinable_category_choices():
    """The choices of the signup forms: every category but the appointed ones."""
    return [(category, CATEGORY_LABELS[category]) for category in CATEGORY_ORDER if is_joinable(category)]


def category_choices():
    """(value, label) pairs in display order, for a form's radio field."""
    return [(category, CATEGORY_LABELS[category]) for category in CATEGORY_ORDER]
