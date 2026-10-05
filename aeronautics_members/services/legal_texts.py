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

    @property
    def is_translation(self):
        return self.language != AUTHORITATIVE


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
    if problems:
        return None, problems
    return Version(
        slug=slug, language=language, version=from_name,
        effective_from=_as_date(meta["effective_from"]), status=meta["status"],
        title=str(meta["title"]).strip(), path=path,
        revision=str(meta["source_revision"]).strip() if meta.get("source_revision") else None,
        team=team,
    ), []


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
    return sorted(found, key=lambda v: (v.effective_from, v.version), reverse=True)


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


def teams_with_texts():
    """The team slugs that have a folder in legal/teams/."""
    folder = texts_dir() / TEAMS_FOLDER
    if not folder.is_dir():
        return []
    return sorted(path.name for path in folder.iterdir() if path.is_dir() and TEAM_SLUG.match(path.name))


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
    key = (str(version.path), version.path.stat().st_mtime_ns)
    if key in _rendered:
        return _rendered[key]

    _meta, body, _problems = _read(version.path)
    tokens = _markdown.parse(body)
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
