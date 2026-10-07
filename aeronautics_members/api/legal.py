"""A legal text -- one of the association's, or a team's rules -- as the
pages show it: the version in force or an earlier one, the German text or
its English translation, with its contents and the other versions.

Not an endpoint of its own: each kind of text has its address (a team's
rules: api/teams.py) and passes in where its pages and PDFs are. The texts
are services/legal_texts.py; Markdown with HTML switched off, so the
rendered text is safe to show as it is.
"""

from datetime import date

from ..services import NotFoundError
from ..services import legal_texts as legal
from ._core import Model


class SectionOut(Model):
    anchor: str
    label: str
    #: 1 for a part ("Teil A"), 2 for a section.
    level: int


class OtherVersionOut(Model):
    version: date
    in_force: bool
    url: str


class LegalTextOut(Model):
    title: str
    #: de or en.
    language: str
    #: An English translation: the German text applies.
    is_translation: bool
    version: date
    revision: str | None
    effective_from: date
    #: The day of the version in force -- this one, or a later one.
    in_force: date
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
    if in_force is None:
        raise NotFoundError("There is no such text.", code="no_text")
    if version is None:
        german = in_force
    else:
        german = legal.find(slug, legal.AUTHORITATIVE, version, team=team)
        if german is None:
            raise NotFoundError("There is no version of that day.", code="no_version")
    english = legal.translation(german)
    shown = german if language == legal.AUTHORITATIVE or english is None else english

    def address(of):
        return page(of.language, None if of.version == in_force.version else of.version.isoformat())

    rendered = legal.render(shown)
    return LegalTextOut(
        title=shown.title, language=shown.language, is_translation=shown.is_translation,
        version=shown.version, revision=shown.revision, effective_from=shown.effective_from,
        in_force=in_force.version, html=str(rendered["html"]),
        contents=[SectionOut(anchor=anchor, label=label, level=level) for anchor, label, level in rendered["contents"]],
        german_url=address(german), english_url=address(english) if english is not None else None,
        english_elsewhere=english is None and legal.has_language(slug, "en", team=team),
        others=[OtherVersionOut(version=other.version, in_force=other.version == in_force.version, url=address(other))
                for other in legal.versions(slug, shown.language, team=team) if other.version != shown.version],
        pdf_url=pdf(None if german.version == in_force.version else german.version.isoformat()),
        pdf_has_english=english is not None,
    )
