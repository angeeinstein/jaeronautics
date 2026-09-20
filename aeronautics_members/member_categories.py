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


# Who has to give an institutional address, and whose is checked against the
# allowed domains.
#
# Only students, and for one reason: a live @edu.fh-joanneum.at address is what
# says somebody is a student right now. A partner gives a company address that
# no list here could anticipate, and an alumnus's university address has
# usually stopped working -- which is the whole reason a private address is
# collected as well.
INSTITUTIONAL_EMAIL_RULES = {
    MemberCategory.STUDENT: {"required": True, "domain_checked": True},
    MemberCategory.ALUMNI: {"required": False, "domain_checked": False},
    MemberCategory.STAFF: {"required": False, "domain_checked": False},
    MemberCategory.PARTNER: {"required": False, "domain_checked": False},
    MemberCategory.HONORARY: {"required": False, "domain_checked": False},
}


def is_valid(category):
    return category in CATEGORY_LABELS


def requires_institutional_email(category):
    """Whether this category cannot be saved without a university/company address."""
    return INSTITUTIONAL_EMAIL_RULES.get(category, {}).get("required", False)


def checks_institutional_domain(category):
    """Whether that address must be on the allowed-domain list."""
    return INSTITUTIONAL_EMAIL_RULES.get(category, {}).get("domain_checked", False)


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


def categories_showing_year_group():
    """The categories whose forms include the year group, for the browser.

    Handed to the page so the show/hide rule is not written down a second time
    in JavaScript, where it would quietly disagree with this module.
    """
    return tuple(category for category in CATEGORY_ORDER if shows_year_group(category))


def category_choices():
    """(value, label) pairs in display order, for a form's radio field."""
    return [(category, CATEGORY_LABELS[category]) for category in CATEGORY_ORDER]
