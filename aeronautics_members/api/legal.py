"""A legal text -- one of the association's, or a team's rules -- as the
pages show it: the version in force or an earlier one, the German text or
its English translation, with its contents and the other versions.

The association's texts are here (GET /legal, GET /legal/<slug>, public: the
signup links to them); a team's rules are in api/teams.py. Each passes in
where its pages and PDFs are. The texts are services/legal_texts.py; Markdown
with HTML switched off, so the rendered text is safe to show as it is.
"""

from datetime import date
from typing import Literal

from flask import url_for

from ..services import NotFoundError
from ..services import legal_texts as legal
from ..services.clock import get_membership_today
from ._core import Model, endpoint


class SectionOut(Model):
    anchor: str
    label: str
    #: 1 for a part ("Teil A"), 2 for a section.
    level: int


class OtherVersionOut(Model):
    version: date
    in_force: bool
    url: str
    #: Published, applies from a later day (``effective_from``).
    upcoming: bool = False
    effective_from: date | None = None


class LegalTextOut(Model):
    title: str
    #: de or en.
    language: str
    #: An English translation: the German text applies.
    is_translation: bool
    version: date
    revision: str | None
    effective_from: date
    #: The day of the version in force -- this one, or a later one; this one's own day when none
    #: is in force yet.
    in_force: date
    #: Published, but applies only from ``effective_from``: the members are told ahead.
    upcoming: bool = False
    #: The line under the title, in the text's language: its revision, the day it
    #: applies from, who approved it and when ("Gültig ab 01.12.2026 · genehmigt …").
    validity: str
    #: The text itself, as HTML.
    html: str
    #: Its parts and sections, to jump to.
    contents: list[SectionOut]
    #: The page of the German text of this version.
    german_url: str
    #: The page of its English translation, if there is one.
    english_url: str | None
    #: Other versions have a translation; this one has none yet.
    english_elsewhere: bool
    others: list[OtherVersionOut]
    pdf_url: str
    #: The PDF has the English translation after the German text.
    pdf_has_english: bool


def legal_text(slug, language, version, *, page, pdf, team=None):
    """``page(language, version)`` and ``pdf(version)`` are the addresses; ``version``
    None for the version in force. Without a language, the English translation
    where there is one, else the German text."""
    in_force = legal.current_version(slug, team=team)
    if version is None:
        if in_force is None:
            raise NotFoundError("There is no such text.", code="no_text")
        german = in_force
    else:
        # A version announced for a later day is readable at its address already.
        german = legal.find_published(slug, legal.AUTHORITATIVE, version, team=team)
        if german is None:
            raise NotFoundError("There is no version of that day.", code="no_version")
    english = legal.find_published(slug, "en", german.version, team=team)
    shown = german if language == legal.AUTHORITATIVE or english is None else english
    current = in_force.version if in_force is not None else None

    def address(of):
        return page(of.language, None if of.version == current else of.version.isoformat())

    upcoming = [v for v in legal.announced(slug, shown.language, team=team) if v.version != shown.version]

    rendered = legal.render(shown)
    return LegalTextOut(
        title=shown.title, language=shown.language, is_translation=shown.is_translation,
        version=shown.version, revision=shown.revision, effective_from=shown.effective_from,
        in_force=current or german.version, upcoming=german.effective_from > get_membership_today(),
        validity=legal.validity(shown), html=str(rendered["html"]),
        contents=[SectionOut(anchor=anchor, label=label, level=level) for anchor, label, level in rendered["contents"]],
        german_url=address(german), english_url=address(english) if english is not None else None,
        english_elsewhere=english is None and legal.has_language(slug, "en", team=team),
        others=[OtherVersionOut(version=other.version, in_force=False, url=address(other), upcoming=True,
                                effective_from=other.effective_from) for other in reversed(upcoming)]
        + [OtherVersionOut(version=other.version, in_force=other.version == current, url=address(other))
           for other in legal.versions(slug, shown.language, team=team) if other.version != shown.version],
        pdf_url=pdf(None if german.version == current else german.version.isoformat()),
        pdf_has_english=english is not None,
    )


class TextQuery(Model):
    #: de or en; without it the English translation where there is one.
    language: Literal["de", "en"] | None = None
    #: The day of an earlier version; without it the version in force.
    version: date | None = None


class LegalTextLinkOut(Model):
    slug: str
    #: As the text calls itself, in German.
    title: str
    #: What the portal calls it in English.
    name: str
    in_force_since: date
    #: Accepted when joining.
    accepted_at_signup: bool
    url: str
    pdf_url: str


class LegalTextsOut(Model):
    texts: list[LegalTextLinkOut]


@endpoint("GET", "/legal", response=LegalTextsOut, public=True, tag="Legal")
def legal_texts():
    """Every legal text in force, with the day its version took effect."""
    return LegalTextsOut(texts=[
        LegalTextLinkOut(slug=text.slug, title=text.title, name=text.english,
                         in_force_since=version.effective_from, accepted_at_signup=text.accepted_at_signup,
                         url=url_for("public.legal_text", slug=text.slug),
                         pdf_url=url_for("public.legal_text_pdf", slug=text.slug))
        for text, version in legal.available()
    ])


@endpoint("GET", "/legal/<slug>", response=LegalTextOut, query=TextQuery, public=True, tag="Legal")
def legal_text_page(slug, query):
    """One of the association's texts: the version in force or an earlier one, in German or English."""
    if slug not in legal.BY_SLUG:
        raise NotFoundError("There is no such text.", code="no_text")
    return legal_text(slug, query.language, query.version,
                      page=lambda language, version: url_for("public.legal_text", slug=slug, language=language,
                                                             version=version),
                      pdf=lambda version: url_for("public.legal_text_pdf", slug=slug, version=version))
