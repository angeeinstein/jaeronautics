"""The legal texts: Markdown files in ``legal/``, by text, language and version.

    legal/privacy-policy/de/2026-10-04.md
    legal/privacy-policy/en/2026-10-04.md
    legal/statutes/de/2019-03-17.md

Every file starts with YAML front matter, which is metadata and never shown:

    ---
    title: "Datenschutzerklärung"
    document: "privacy-policy"      # the text's folder
    language: "de"                  # the language folder
    version: "2026-10-04"           # the file's name
    effective_from: "2026-10-04"    # the day it applies from
    status: "published"             # or "draft"
    source_revision: "Rev 4"        # optional: the text's own revision, shown with the date
    ---

The version in force is the newest ``published`` one whose ``effective_from``
has come, so a version can be committed early and switches over by itself, or
wait as a ``draft`` until it is approved. Older versions stay readable. The
texts are changed in the repository and arrive with an update -- reviewed,
with their history -- rather than edited on a page. ``problems()`` checks the
files and is run by the tests, so a mistake fails CI instead of the page.

German is authoritative. A text is in force when its German version is; an
English one is a translation of a German version (the same ``version``), shown
with a notice saying so, and only while it translates the version in force --
an English file left behind by a newer German one is not passed off as the
current text.

The body is rendered as Markdown with raw HTML switched off, so a text can
shape itself (headings, lists, bold, links, tables) but never put anything
else on the page. Which texts there are is the registry below; a text without
a file is simply not shown. Those accepted at signup are recorded with the
member, by version, see ``versions_to_accept``.

A team's own rules are kept the same way, under the team's slug, and are
approved by the association like its own texts:

    legal/teams/rocket/team-rules/de/2026-10-05.md

with ``team: "rocket"`` in the front matter as well. Every function here takes
``team=`` for them; without it, it is about the association's texts.

**Versions uploaded in the portal** (docs/legal-texts-in-portal-plan.md) are
kept in the database, ``LegalTextVersion``, not in legal/: drafts, and what
was published from Admin > Legal Texts. Everything here reads both, and to
the rest of the portal a version is a version wherever it is kept: the
newest published one whose day has come is in force. A version from the
database has a ``record_id`` instead of a ``path``; the uploading,
publishing and correcting is services/legal_uploads.py.

Optional in the front matter of either: ``approved_on`` and ``approved_by``,
the day the text was approved and by whom ("Vorstand"), shown with it.
"""

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml
from flask import current_app
from markdown_it import MarkdownIt
from markupsafe import Markup

from .clock import get_membership_today


@dataclass(frozen=True)
class LegalText:
    slug: str
    title: str  # German, as the text calls itself
    english: str  # what the English portal calls it in links
    accepted_at_signup: bool


#: Every legal text the portal knows, in the order they are listed.
LEGAL_TEXTS = (
    LegalText("statutes", "Statuten", "Statutes", True),
    LegalText("rules-of-procedure", "Geschäftsordnung", "Rules of Procedure", True),
    LegalText("membership-terms", "Mitgliedschaftsbedingungen", "Membership Terms and Conditions", True),
    LegalText("privacy-policy", "Datenschutzerklärung", "Privacy Policy", True),
    LegalText("webshop-event-terms", "AGB für Webshop und Events", "Terms and Conditions for Webshop and Events", False),
    LegalText("legal-notice", "Impressum", "Legal Notice", False),
)
BY_SLUG = {text.slug: text for text in LEGAL_TEXTS}

#: What a team can have in legal/teams/<team>/: its rules, accepted when joining.
TEAM_RULES = "team-rules"
TEAM_TEXTS = (LegalText(TEAM_RULES, "Teamordnung", "Team Rules", False),)
TEAM_BY_SLUG = {text.slug: text for text in TEAM_TEXTS}
TEAMS_FOLDER = "teams"
TEAM_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")

AUTHORITATIVE = "de"
LANGUAGES = ("de", "en")
STATUSES = ("draft", "published")
REQUIRED_FIELDS = ("title", "document", "language", "version", "effective_from", "status")

VERSION_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)

_markdown = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False}).enable("table")
_parsed = {}
_rendered = {}


@dataclass(frozen=True)
class Version:
    """One file: a version of a text in one language."""

    slug: str
    language: str
    version: date
    effective_from: date
    status: str
    title: str
    path: Path
    revision: str = None
    team: str = None  # the team's slug, for a team's own text
    approved_on: date = None
    approved_by: str = None
    #: Kept in the database (LegalTextVersion.id) rather than in legal/; ``path`` is None.
    record_id: int = None
    #: What changes when the text does: the file's mtime, or the record's correction.
    stamp: str = ""

    @property
    def is_translation(self):
        return self.language != AUTHORITATIVE

    @property
    def key(self):
        """Which version this is, for a cache: the file, or the record."""
        return str(self.path) if self.path is not None else f"record:{self.record_id}"

    @property
    def label(self):
        """For a log line: the file, or the record."""
        return str(self.path) if self.path is not None else f"{self.slug} (uploaded, #{self.record_id})"


def texts_dir():
    configured = current_app.config.get("LEGAL_TEXTS_DIR")
    return Path(configured) if configured else Path(current_app.root_path).parent / "legal"


def _as_date(value):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _read(path):
    """(front matter, body, problems) of a file, cached until it changes."""
    key = (str(path), path.stat().st_mtime_ns)
    if key not in _parsed:
        text = path.read_text(encoding="utf-8")
        match = FRONT_MATTER.match(text)
        if not match:
            _parsed[key] = ({}, text, ["no front matter (--- ... ---) at the top"])
        else:
            try:
                meta = yaml.safe_load(match.group(1)) or {}
                problems = [] if isinstance(meta, dict) else ["front matter is not a list of fields"]
            except yaml.YAMLError as error:
                meta, problems = {}, [f"front matter is not valid YAML: {error}"]
            _parsed[key] = (meta if isinstance(meta, dict) else {}, text[match.end():], problems)
    return _parsed[key]


def forget(paths):
    """Drop what was read from ``paths`` -- files that are gone again, such as a preview's."""
    names = {str(path) for path in paths}
    for cache in (_parsed, _rendered):
        for key in [key for key in cache if key[0] in names]:
            del cache[key]


def _check(path, slug, language, team=None):
    """The Version a file describes, and what is wrong with it."""
    meta, _body, problems = _read(path)
    problems = list(problems)
    if team is not None and meta and str(meta.get("team") or "") != team:
        problems.append(f'team is "{meta.get("team") or ""}", but the file is in {TEAMS_FOLDER}/{team}/')
    name = VERSION_FILE.match(path.name)
    from_name = _as_date(name.group(1)) if name else None
    if from_name is None:
        problems.append("the file name is not a day, <YYYY-MM-DD>.md")
    if meta:
        for field in REQUIRED_FIELDS:
            if meta.get(field) in (None, ""):
                problems.append(f"{field} is missing")
        if meta.get("document") not in (None, "", slug):
            problems.append(f'document is "{meta["document"]}", but the file is in {slug}/')
        if meta.get("language") not in (None, "", language):
            problems.append(f'language is "{meta["language"]}", but the file is in {language}/')
        if meta.get("version") not in (None, "") and _as_date(meta["version"]) != from_name:
            problems.append(f'version is "{meta["version"]}", but the file is named {path.name}')
        if meta.get("effective_from") not in (None, "") and _as_date(meta["effective_from"]) is None:
            problems.append(f'effective_from "{meta["effective_from"]}" is not a day (YYYY-MM-DD)')
        if meta.get("status") not in (None, "") and meta["status"] not in STATUSES:
            problems.append(f'status is "{meta["status"]}"; it can be {" or ".join(STATUSES)}')
        if meta.get("approved_on") not in (None, "") and _as_date(meta["approved_on"]) is None:
            problems.append(f'approved_on "{meta["approved_on"]}" is not a day (YYYY-MM-DD)')
    if problems:
        return None, problems
    return Version(
        slug=slug, language=language, version=from_name,
        effective_from=_as_date(meta["effective_from"]), status=meta["status"],
        title=str(meta["title"]).strip(), path=path,
        revision=str(meta["source_revision"]).strip() if meta.get("source_revision") else None,
        team=team,
        approved_on=_as_date(meta["approved_on"]) if meta.get("approved_on") else None,
        approved_by=str(meta["approved_by"]).strip() if meta.get("approved_by") else None,
        stamp=str(path.stat().st_mtime_ns),
    ), []


def _from_record(record):
    return Version(
        slug=record.document, language=record.language, version=record.version,
        effective_from=record.effective_from, status=record.status, title=record.title, path=None,
        revision=record.revision, team=record.team_slug, approved_on=record.approved_on,
        approved_by=record.approved_by, record_id=record.id,
        stamp=(record.corrected_at or record.published_at or record.uploaded_at).isoformat(),
    )


def _records(slug, language, team=None):
    """The versions of a text uploaded in the portal, as Versions: drafts too."""
    from ..db_models import LegalTextVersion, db

    query = db.select(LegalTextVersion).where(LegalTextVersion.document == slug,
                                              LegalTextVersion.language == language)
    query = query.where(LegalTextVersion.team_slug.is_(None) if team is None
                        else LegalTextVersion.team_slug == team)
    return [_from_record(record) for record in db.session.execute(query).scalars()]


def record(record_id):
    """A version uploaded in the portal, by its id, as a Version; None if there is none."""
    from ..db_models import LegalTextVersion, db

    found = db.session.get(LegalTextVersion, record_id)
    return _from_record(found) if found is not None else None


def body(version):
    """The Markdown of a version, without its front matter."""
    if version.path is not None:
        return _read(version.path)[1]
    from ..db_models import LegalTextVersion, db

    return db.session.get(LegalTextVersion, version.record_id).body


def source_bytes(version):
    """Everything a version's PDF is made from, as bytes: the file, or the record's text and fields."""
    if version.path is not None:
        return version.path.read_bytes()
    fields = (version.title, version.revision, version.effective_from, version.version, version.status,
              version.approved_on, version.approved_by)
    return repr(fields).encode() + b"\n" + body(version).encode("utf-8")


def _files(slug, language, team=None):
    if team is None:
        known, folder = slug in BY_SLUG, texts_dir() / slug / language
    else:
        known = slug in TEAM_BY_SLUG and bool(TEAM_SLUG.match(team))
        folder = texts_dir() / TEAMS_FOLDER / team / slug / language
    if not known or language not in LANGUAGES or not folder.is_dir():
        return []
    return sorted(path for path in folder.iterdir() if path.is_file())


def versions(slug, language=AUTHORITATIVE, today=None, team=None):
    """The versions that may be shown -- published, their day come -- newest first.

    A file with something wrong with it is left out (``problems()`` names it).
    """
    today = today or get_membership_today()
    found = []
    for path in _files(slug, language, team):
        version, problems = _check(path, slug, language, team)
        if version and version.status == "published" and version.effective_from <= today:
            found.append(version)
    if _known(slug, language, team):
        found += [v for v in _records(slug, language, team)
                  if v.status == "published" and v.effective_from <= today]
    return sorted(found, key=lambda v: (v.effective_from, v.version), reverse=True)


def _known(slug, language, team=None):
    if language not in LANGUAGES:
        return False
    return slug in BY_SLUG if team is None else (slug in TEAM_BY_SLUG and bool(TEAM_SLUG.match(team)))


def current_version(slug, today=None, team=None):
    """The version in force: the newest German one that may be shown, or None."""
    shown = versions(slug, AUTHORITATIVE, today, team)
    return shown[0] if shown else None


def find(slug, language, version_day, today=None, team=None):
    """A version that may be shown, by language and version day, or None."""
    return next((v for v in versions(slug, language, today, team) if v.version == version_day), None)


def translation(version, language="en", today=None):
    """The translation of a German version into ``language``, if there is one."""
    return find(version.slug, language, version.version, today, version.team)


def has_language(slug, language, today=None, team=None):
    return bool(versions(slug, language, today, team))


def relative(path):
    """A file's place in legal/: "privacy-policy/de/2026-10-08.md"."""
    return path.relative_to(texts_dir()).as_posix()


def _imported():
    """The draft files in legal/ copied into the portal for the editor: no longer listed."""
    from ..db_models import LegalTextVersion, db

    return set(db.session.execute(db.select(LegalTextVersion.imported_from).where(
        LegalTextVersion.imported_from.is_not(None))).scalars())


def _all_versions(slug, language, team=None):
    """Every version with nothing wrong with it, whatever its status and day -- but a draft file
    copied into the portal (its copy is the draft now)."""
    found = []
    imported = None
    for path in _files(slug, language, team):
        version, _problems = _check(path, slug, language, team)
        if version is None:
            continue
        if version.status == "draft":
            imported = _imported() if imported is None else imported
            if relative(path) in imported:
                continue
        found.append(version)
    if _known(slug, language, team):
        found += _records(slug, language, team)
    return found


def waiting(slug, team=None, today=None):
    """German versions not shown yet: drafts, and published ones whose day has
    not come. Newest first. For the administrators, who approve them."""
    today = today or get_membership_today()
    return sorted(
        (v for v in _all_versions(slug, AUTHORITATIVE, team) if v.status == "draft" or v.effective_from > today),
        key=lambda v: (v.effective_from, v.version), reverse=True,
    )


def find_any(slug, language, version_day, team=None):
    """A version in legal/ by language and day, shown or not (a draft, one not in force yet), or None.
    One uploaded in the portal is found by ``record``: two drafts may share a day."""
    return next((v for v in _all_versions(slug, language, team)
                 if v.version == version_day and v.record_id is None), None)


def translation_any(version, language="en"):
    """The translation of a German version, shown or not: of a file by its day, of a record by its link."""
    if version.record_id is None:
        return find_any(version.slug, language, version.version, version.team)
    from ..db_models import LegalTextVersion, db

    found = db.session.execute(db.select(LegalTextVersion).where(
        LegalTextVersion.translation_of_id == version.record_id,
        LegalTextVersion.language == language)).scalars().first()
    return _from_record(found) if found is not None else None


def teams_with_texts():
    """The team slugs with rules of their own: a folder in legal/teams/, or uploaded in the portal."""
    from ..db_models import LegalTextVersion, db

    folder = texts_dir() / TEAMS_FOLDER
    found = set()
    if folder.is_dir():
        found = {path.name for path in folder.iterdir() if path.is_dir() and TEAM_SLUG.match(path.name)}
    found |= set(db.session.execute(db.select(LegalTextVersion.team_slug).where(
        LegalTextVersion.team_slug.is_not(None)).distinct()).scalars())
    return sorted(found)


def available(today=None):
    """The texts in force, with the version in force, in registry order."""
    result = []
    for text in LEGAL_TEXTS:
        version = current_version(text.slug, today)
        if version is not None:
            result.append((text, version))
    return result


def versions_to_accept(today=None):
    """What a new member accepts: each signup text in force, by its version day."""
    return {
        text.slug: version.version.isoformat()
        for text, version in available(today)
        if text.accepted_at_signup
    }


def problems():
    """Everything wrong with the files in ``legal/``, as "path: what" lines."""
    root = texts_dir()
    found = []
    if not root.is_dir():
        return found
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        relative = path.relative_to(root)
        parts = relative.parts
        if parts[0] == TEAMS_FOLDER:
            if (len(parts) != 5 or not TEAM_SLUG.match(parts[1]) or parts[2] not in TEAM_BY_SLUG
                    or parts[3] not in LANGUAGES):
                found.append(f"{relative}: not where a version goes, legal/{TEAMS_FOLDER}/<team>/<text>/"
                             f"<language>/<YYYY-MM-DD>.md (texts: {', '.join(TEAM_BY_SLUG)}; "
                             f"languages: {', '.join(LANGUAGES)})")
                continue
            _version, file_problems = _check(path, parts[2], parts[3], parts[1])
        elif len(parts) != 3 or parts[0] not in BY_SLUG or parts[1] not in LANGUAGES:
            found.append(f"{relative}: not where a version goes, legal/<text>/<language>/<YYYY-MM-DD>.md "
                         f"(texts: {', '.join(BY_SLUG)}; languages: {', '.join(LANGUAGES)})")
            continue
        else:
            _version, file_problems = _check(path, parts[0], parts[1])
        found.extend(f"{relative}: {problem}" for problem in file_problems)
    places = [(text.slug, None) for text in LEGAL_TEXTS]
    places += [(text.slug, team) for team in teams_with_texts() for text in TEAM_TEXTS]
    for slug, team in places:
        where = slug if team is None else f"{TEAMS_FOLDER}/{team}/{slug}"
        for language in LANGUAGES:
            published = {}
            for path in _files(slug, language, team):
                version, _ = _check(path, slug, language, team)
                if version and version.status == "published":
                    published.setdefault(version.effective_from, []).append(path.name)
            for day, names in published.items():
                if len(names) > 1:
                    found.append(f"{where}/{language}: {', '.join(names)} are all published "
                                 f"from {day}; only one can be in force")
            if language != AUTHORITATIVE:
                german = {v.version for v in _published(slug, AUTHORITATIVE, team)}
                found.extend(
                    f"{where}/{language}/{v.path.name}: a translation of a version that has no "
                    f"published German file, {where}/{AUTHORITATIVE}/{v.path.name}"
                    for v in _published(slug, language, team) if v.version not in german
                )
    return found


def _published(slug, language, team=None):
    """Every published version, whatever its day."""
    found = []
    for path in _files(slug, language, team):
        version, _ = _check(path, slug, language, team)
        if version and version.status == "published":
            found.append(version)
    return found


_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _anchor(text, taken):
    """``#paragraph-5`` for "§5. …", so a link can name the paragraph; else from the words."""
    number = re.match(r"\s*§\s*(\d+[a-z]?)", text)
    if number:
        base = f"paragraph-{number.group(1)}"
    else:
        base = re.sub(r"[^a-z0-9]+", "-", text.lower().translate(_UMLAUTS)).strip("-") or "section"
    anchor, n = base, 2
    while anchor in taken:
        anchor, n = f"{base}-{n}", n + 1
    taken.add(anchor)
    return anchor


def render(version):
    """A version's body as {"html", "contents"}.

    The front matter is metadata and not shown; the title comes from it, so a
    top-level heading opening the body would say it twice and is left out.
    Top-level headings further down divide the text into parts ("Teil A");
    they and the second-level headings get anchors and make the contents
    list, as ``(anchor, label, level)``.
    """
    key = (version.key, version.stamp)
    if key in _rendered:
        return _rendered[key]

    tokens = _markdown.parse(body(version))
    contents, taken, kept = [], set(), []
    skip_until, title_dropped = None, False
    for index, token in enumerate(tokens):
        if skip_until is not None:
            if index <= skip_until:
                continue
            skip_until = None
        if token.type == "heading_open" and token.tag == "h1" and not kept and not title_dropped:
            # Only the one title: a part heading right after it ("Teil A") stays.
            skip_until, title_dropped = index + 2, True
            continue
        if token.type == "heading_open" and token.tag in ("h1", "h2"):
            label = tokens[index + 1].content.strip()
            anchor = _anchor(label, taken)
            token.attrSet("id", anchor)
            if token.tag == "h1":
                token.attrSet("class", "legal-part")
            contents.append((anchor, label, int(token.tag[1])))
        kept.append(token)

    rendered = {
        "html": Markup(_markdown.renderer.render(kept, _markdown.options, {})),
        "contents": contents,
    }
    _rendered[key] = rendered
    return rendered


#: Who approved a text, as the line under its title says it; anybody else is named in brackets.
APPROVED_BY_PHRASES = {
    "de": {"Vorstand": "vom Vorstand", "Generalversammlung": "von der Generalversammlung"},
    "en": {"Vorstand": "by the board", "Generalversammlung": "by the general assembly"},
}


def validity(version):
    """The line under a text's title, in its language: its revision, the day it applies from,
    and who approved it and when -- "Version 2026.2 · Gültig ab 01.12.2026 · genehmigt vom
    Vorstand am 10.11.2026". The version's own day stays in the footer of its PDF."""
    from .membership import format_date_display

    german = version.language == AUTHORITATIVE
    parts = [version.revision] if version.revision else []
    parts.append(("Gültig ab " if german else "In force from ") + format_date_display(version.effective_from))
    if version.approved_by or version.approved_on:
        phrases = APPROVED_BY_PHRASES["de" if german else "en"]
        by = phrases.get(version.approved_by) if version.approved_by else None
        approved = "genehmigt" if german else "approved"
        if by:
            approved += f" {by}"
        if version.approved_on:
            approved += (" am " if german else " on ") + format_date_display(version.approved_on)
        if version.approved_by and not by:
            approved += f" ({version.approved_by})"
        parts.append(approved)
    return " · ".join(parts)
