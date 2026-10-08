"""What every page shows whoever is looking: the footer's links -- the
Impressum, privacy policy and statutes (the portal's own legal texts, unless
an address elsewhere is configured in config.py), all legal texts, contact
and the association's website -- and what teams are called, when they are switched
on. Public. Drawn by frontend/src/frame/Footer.tsx.
"""

from flask import url_for

from ..config import ASSOCIATION_WEBSITE_URL, CONTACT_EMAIL, IMPRESSUM_URL, PRIVACY_URL, STATUTES_URL
from ..services.clock import get_membership_today
from ..services.teams import team_labels, teams_enabled
from ._core import Model, endpoint


class FooterLinkOut(Model):
    label: str
    url: str
    #: Leaves the portal: opens in a new tab.
    external: bool


class SiteOut(Model):
    footer: list[FooterLinkOut]
    copyright: str
    #: What teams are called here (plural), when they are switched on.
    teams_label: str | None


def _legal(label, configured, slug):
    if configured:
        return FooterLinkOut(label=label, url=configured, external=True)
    return FooterLinkOut(label=label, url=url_for("public.legal_text", slug=slug), external=False)


@endpoint("GET", "/site", response=SiteOut, public=True, tag="Session")
def site():
    """The footer: the legal texts, contact and the association's website; what teams are called."""
    return SiteOut(
        footer=[
            _legal("Impressum", IMPRESSUM_URL, "legal-notice"),
            _legal("Privacy", PRIVACY_URL, "privacy-policy"),
            _legal("Statutes", STATUTES_URL, "statutes"),
            FooterLinkOut(label="Legal texts", url=url_for("public.legal_texts"), external=False),
            FooterLinkOut(label="Contact", url=f"mailto:{CONTACT_EMAIL}", external=False),
            FooterLinkOut(label="Website", url=ASSOCIATION_WEBSITE_URL, external=True),
        ],
        copyright=f"© {get_membership_today().year} Joanneum Aeronautics",
        teams_label=team_labels()[1] if teams_enabled() else None,
    )
