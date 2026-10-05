"""The legal texts as PDFs, made from the same Markdown files as the pages.

One PDF per version: the German text, which is the one that applies, and --
where there is one -- the English translation of that same version after it,
starting on a page of its own with the notice that it is a translation. A4,
with the association's logo, the title and the version on the first page, and
the text, version and page number in the footer of every page.

Made with WeasyPrint from HTML and CSS (templates/legal/pdf.html), so the text
is the same rendering the legal pages show -- headings, lists, tables -- only
laid out for paper. Made when first asked for and kept in storage/legal_pdf,
under a name that includes a hash of everything it was made from: a changed
text, translation, template or logo makes a new file, and the old one is
removed. ``flask build-legal-pdfs`` makes them all ahead, and the tests make
every one of them, so a text that cannot be laid out fails CI.

Only files from the static folder are read while laying out -- the fonts and
the logo. Anything else a text might point at (an image on another server, a
file elsewhere on the disk) is refused.
"""

import hashlib
import os
import re
import tempfile
from pathlib import Path

from flask import current_app, render_template
from markupsafe import Markup

from .services import legal_texts as legal

#: Raised when the layout changes in a way the hash of the template would not
#: show (a change in WeasyPrint, say), so every PDF is made again.
LAYOUT_VERSION = "1"

TEMPLATE = "legal/pdf.html"
ASSOCIATION = "Joanneum Aeronautics"

_SAFE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})


def cache_dir():
    configured = current_app.config.get("LEGAL_PDF_DIR")
    return Path(configured) if configured else Path(current_app.root_path).parent / "storage" / "legal_pdf"


def parts(german):
    """What goes in the PDF of a German version: it, and its English translation if there is one."""
    english = legal.translation(german)
    return [german] + ([english] if english is not None else [])


def _safe(text):
    return re.sub(r"[^A-Za-z0-9]+", "-", text.translate(_SAFE)).strip("-")


def filename(german, team=None):
    """``Joanneum-Aeronautics_Statuten_2019-03-17.pdf``; a team's ``Rocket-Team_Teamordnung_….pdf``."""
    owner = team.name if team is not None else ASSOCIATION
    title = _safe(german.title)
    prefix = "" if _safe(owner).lower() in title.lower() else f"{_safe(owner)}_"
    return f"{prefix}{title}_{german.version.isoformat()}.pdf"


def _team_logo(team):
    """The team's logo file, if it has one -- read while laying out its rules."""
    if team is None:
        return None
    from .services.teams import logo_file

    path = logo_file(team)
    return path.resolve() if path is not None else None


def _static_dir():
    return Path(current_app.static_folder).resolve()


def _static_only_fetcher(also=None):
    """WeasyPrint's fetcher, for files in the static folder only -- and ``also``, a team's logo."""
    from urllib.parse import unquote, urlsplit

    from weasyprint.urls import URLFetcher

    static = _static_dir()

    class StaticOnly(URLFetcher):
        def fetch(self, url, headers=None):
            parts = urlsplit(url)
            path = Path(unquote(parts.path)).resolve() if parts.scheme == "file" else None
            if path is None or not (path == static or static in path.parents or (also and path == also)):
                raise ValueError(f"Not fetched while making a legal PDF: {url}")
            return super().fetch(url, headers)

    return StaticOnly(allowed_protocols=("file",))


def _with_prefix(html, prefix):
    """The ids and in-text links of one language, kept apart from the other's:
    both have a ``paragraph-5``."""
    html = re.sub(r'(<h[1-6][^>]*\bid=")', rf"\g<1>{prefix}-", str(html))
    return Markup(re.sub(r'(<a href="#)', rf"\g<1>{prefix}-", html))


def _sections(german):
    sections = []
    for version in parts(german):
        rendered = legal.render(version)
        prefix = version.language
        sections.append({
            "version": version,
            "html": _with_prefix(rendered["html"], prefix),
            "contents": [(f"{prefix}-{anchor}", label, level) for anchor, label, level in rendered["contents"]],
            "id": f"part-{prefix}",
        })
    return sections


def _source_hash(german, team=None):
    """Everything the PDF is made from, as one hash."""
    digest = hashlib.sha256(LAYOUT_VERSION.encode())
    if team is not None:
        digest.update(team.name.encode())
        logo = _team_logo(team)
        digest.update(logo.read_bytes() if logo is not None else b"no logo")
    for version in parts(german):
        digest.update(version.path.read_bytes())
    source, _name, _uptodate = current_app.jinja_loader.get_source(current_app.jinja_env, TEMPLATE)
    digest.update(source.encode())
    digest.update((_static_dir() / "logo_joanneum_aeronautics_positiv.svg").read_bytes())
    return digest.hexdigest()[:20]


def build(german, team=None):
    """The PDF of a German version, made now. Bytes. ``team``: the team a team's text belongs to."""
    from weasyprint import HTML

    logo = _team_logo(team)
    html = render_template(
        TEMPLATE, sections=_sections(german), german=german, association=ASSOCIATION,
        team=team, team_logo=logo.as_uri() if logo is not None else None,
    )
    # The PDF's title, author and language come from the HTML's <title>, <meta name=author> and lang.
    document = HTML(string=html, base_url=_static_dir().as_uri() + "/", url_fetcher=_static_only_fetcher(logo))
    return document.write_pdf()


def pdf_for(german, team=None):
    """The PDF of a German version, from storage or made and kept. Bytes."""
    folder = cache_dir()
    stem = f"{german.slug}_{german.version.isoformat()}"
    if german.team:
        stem = f"team-{german.team}_{stem}"
    path = folder / f"{stem}_{_source_hash(german, team)}.pdf"
    if path.is_file():
        return path.read_bytes()
    data = build(german, team)
    folder.mkdir(parents=True, exist_ok=True)
    # Written aside and moved into place, so two workers making it at once
    # cannot hand anybody half a file.
    handle, temporary = tempfile.mkstemp(dir=folder, suffix=".part")
    with os.fdopen(handle, "wb") as out:
        out.write(data)
    os.replace(temporary, path)
    for older in folder.glob(f"{stem}_*.pdf"):
        if older != path:
            older.unlink(missing_ok=True)
    return data


def build_all():
    """Make the PDF of every version that may be shown, the teams' too. ``[(version, size or error)]``.

    A team folder whose slug names no team is reported as an error: its rules
    would be shown nowhere.
    """
    from .services import NotFoundError
    from .services.teams import get_team

    jobs = [(version, None) for text in legal.LEGAL_TEXTS
            for version in legal.versions(text.slug, legal.AUTHORITATIVE)]
    for slug in legal.teams_with_texts():
        try:
            team = get_team(slug)
        except NotFoundError:
            team = None
        for text in legal.TEAM_TEXTS:
            for version in legal.versions(text.slug, legal.AUTHORITATIVE, team=slug):
                jobs.append((version, team if team is not None else LookupError(
                    f"no team has the slug {slug!r}; its folder legal/teams/{slug}/ is shown nowhere")))
    made = []
    for version, team in jobs:
        if isinstance(team, Exception):
            made.append((version, team))
            continue
        try:
            made.append((version, len(pdf_for(version, team))))
        except Exception as exc:  # noqa: BLE001 -- report every text, not just the first that fails
            made.append((version, exc))
    return made
