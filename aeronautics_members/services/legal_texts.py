"""The legal texts: Markdown files in ``legal/``, one folder per text, one file per version.

    legal/statutes/2019-03-17.md
    legal/membership-terms/2026-11-01.md

A file's name is the day it takes effect. The portal shows the newest version
whose day has come, so a new version can be put in early and switches over by
itself; older versions stay, so what applied when can always be read. They are
changed in the repository and arrive with an update -- reviewed, with their
history -- rather than edited on a page.

The texts are German; the portal around them is English. They are rendered as
Markdown with raw HTML switched off, so a text can shape itself (headings,
lists, bold, links) but never put anything else on the page.

Which texts there are is the registry below. A text without a file yet is
simply not shown. Those accepted at signup are recorded with the member, by
version (the day of the version shown), see ``versions_to_accept``.
"""

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

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
    LegalText("privacy", "Datenschutzerklärung", "Privacy Policy", True),
    LegalText("withdrawal", "Rücktrittsbelehrung", "Right of Withdrawal", False),
    LegalText("legal-notice", "Impressum", "Legal Notice", False),
)
BY_SLUG = {text.slug: text for text in LEGAL_TEXTS}

VERSION_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")

_markdown = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False}).enable("table")
_cache = {}


def texts_dir():
    configured = current_app.config.get("LEGAL_TEXTS_DIR")
    return Path(configured) if configured else Path(current_app.root_path).parent / "legal"


def versions(slug):
    """Every version of a text, newest first, as dates."""
    folder = texts_dir() / slug
    if slug not in BY_SLUG or not folder.is_dir():
        return []
    found = []
    for path in folder.iterdir():
        match = VERSION_FILE.match(path.name)
        if match and path.is_file():
            try:
                found.append(date.fromisoformat(match.group(1)))
            except ValueError:
                continue
    return sorted(found, reverse=True)


def current_version(slug, today=None):
    """The version in force: the newest whose day has come, or None."""
    today = today or get_membership_today()
    return next((version for version in versions(slug) if version <= today), None)


def available(today=None):
    """The texts that have a version in force, with it, in registry order."""
    result = []
    for text in LEGAL_TEXTS:
        version = current_version(text.slug, today)
        if version is not None:
            result.append((text, version))
    return result


def versions_to_accept(today=None):
    """What a new member accepts: each signup text in force, by its version day."""
    return {
        text.slug: version.isoformat()
        for text, version in available(today)
        if text.accepted_at_signup
    }


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


def render(slug, version):
    """A version as {"title", "html", "contents", "version"}, or None if it does not exist.

    The first top-level heading is the text's title and is taken out of the
    body; the second-level headings get anchors and make the contents list.
    """
    path = texts_dir() / slug / f"{version.isoformat()}.md"
    if slug not in BY_SLUG or not path.is_file():
        return None
    key = (str(path), path.stat().st_mtime_ns)
    if key in _cache:
        return _cache[key]

    tokens = _markdown.parse(path.read_text(encoding="utf-8"))
    title = BY_SLUG[slug].title
    contents, taken, body = [], set(), []
    skip_until = None
    for index, token in enumerate(tokens):
        if skip_until is not None:
            if index <= skip_until:
                continue
            skip_until = None
        if token.type == "heading_open" and token.tag == "h1" and not contents and not body:
            title = tokens[index + 1].content.strip() or title
            skip_until = index + 2
            continue
        if token.type == "heading_open" and token.tag == "h2":
            label = tokens[index + 1].content.strip()
            anchor = _anchor(label, taken)
            token.attrSet("id", anchor)
            contents.append((anchor, label))
        body.append(token)

    rendered = {
        "title": title,
        "html": Markup(_markdown.renderer.render(body, _markdown.options, {})),
        "contents": contents,
        "version": version,
    }
    _cache[key] = rendered
    return rendered
