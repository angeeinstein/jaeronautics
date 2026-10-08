"""The university or company address, and what it is worth.

A student's ``@edu.fh-joanneum.at`` address is the only thing the association
holds that says they are a student *now*. It is deliberately not their login:
it stops working when they graduate, and a member who has graduated is still a
member the association needs to be able to reach -- which is what
``email_private`` is for.

Two jobs here. Deciding whether an address looks institutional, against a list
an admin can edit, because a partner company or a renamed university domain
must not need a code change. And being the one place that knows the list, so a
form, a settings page and the archive-claim all agree about it.
"""

from ..config import DEFAULT_INSTITUTIONAL_EMAIL_DOMAINS, DEFAULT_STAFF_EMAIL_DOMAINS
from .settings import get_settings_map

#: The students' domains. The key is from when there was one list for everybody.
SETTING_KEY = "institutional_email_domains"
#: The staff's: a lecturer's ``@fh-joanneum.at`` is not a student's ``@edu.fh-joanneum.at``.
STAFF_SETTING_KEY = "staff_email_domains"


def normalize_email(email):
    return (email or "").strip().lower()


def email_domain(email):
    normalized = normalize_email(email)
    return normalized.rsplit("@", 1)[1] if "@" in normalized else ""


def parse_domains(raw):
    """A stored setting into a tuple of domains. Tolerant of how people type lists.

    Commas, newlines, spaces, a stray ``@`` in front -- all of it is somebody
    filling in a text box, and none of it should silently produce a list that
    matches nothing.
    """
    if not raw:
        return ()
    separators = str(raw).replace("\n", ",").replace(";", ",").replace(" ", ",")
    domains = []
    for part in separators.split(","):
        cleaned = part.strip().lower().lstrip("@").strip(".")
        if cleaned and cleaned not in domains:
            domains.append(cleaned)
    return tuple(domains)


def get_student_domains():
    """The domains a student's address is on, from the admin setting, falling back to the default.

    An empty setting means the default rather than "nothing is allowed": a
    cleared box must not lock every student out of signing up.
    """
    stored = get_settings_map([SETTING_KEY]).get(SETTING_KEY)
    return parse_domains(stored) or parse_domains(DEFAULT_INSTITUTIONAL_EMAIL_DOMAINS)


def get_staff_domains():
    """The domains a staff member's address is on; the same fallback."""
    stored = get_settings_map([STAFF_SETTING_KEY]).get(STAFF_SETTING_KEY)
    return parse_domains(stored) or parse_domains(DEFAULT_STAFF_EMAIL_DOMAINS)


def get_institutional_domains():
    """Every domain the institutions own, students' and staff's."""
    return tuple(dict.fromkeys(get_student_domains() + get_staff_domains()))


def _on(domain, allowed):
    return any(domain == item or domain.endswith(f".{item}") for item in allowed)


def is_student_email(email):
    """Whether the address is a student's: what says somebody studies here now."""
    domain = email_domain(email)
    return bool(domain) and _on(domain, get_student_domains())


def is_staff_email(email):
    """Whether the address is a staff member's -- and not a student's, which is usually a
    subdomain of the staff's (edu.fh-joanneum.at under fh-joanneum.at)."""
    domain = email_domain(email)
    return bool(domain) and _on(domain, get_staff_domains()) and not _on(domain, get_student_domains())


def is_institutional_email(email, domains=None):
    """Whether the address is on an allowed domain, or a subdomain of one.

    Subdomains count so that a faculty or campus prefix does not have to be
    listed separately, and the boundary is checked on a dot so that
    ``notfh-joanneum.at`` cannot pass as ``fh-joanneum.at``.
    """
    domain = email_domain(email)
    if not domain:
        return False
    return _on(domain, domains if domains is not None else get_institutional_domains())
