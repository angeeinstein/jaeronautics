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


def _sections(german, versions=None):
    sections = []
    for version in versions or parts(german):
        rendered = legal.render(version)
        prefix = version.language
        sections.append({
            "version": version,
            "html": _with_prefix(rendered["html"], prefix),
            "contents": [(f"{prefix}-{anchor}", label, level) for anchor, label, level in rendered["contents"]],
            "id": f"part-{prefix}",
        })
    return sections


def _source_hash(german, team=None, versions=None, watermark=None):
    """Everything the PDF is made from, as one hash."""
    digest = hashlib.sha256(LAYOUT_VERSION.encode())
    if team is not None:
        digest.update(team.name.encode())
        logo = _team_logo(team)
        digest.update(logo.read_bytes() if logo is not None else b"no logo")
    for version in versions or parts(german):
        digest.update(version.path.read_bytes())
    digest.update((watermark or "").encode())
    source, _name, _uptodate = current_app.jinja_loader.get_source(current_app.jinja_env, TEMPLATE)
    digest.update(source.encode())
    digest.update((_static_dir() / "logo_joanneum_aeronautics_positiv.svg").read_bytes())
    return digest.hexdigest()[:20]


def build(german, team=None, versions=None, watermark=None):
    """The PDF of a German version, made now. Bytes. ``team``: the team a team's text belongs to.

    ``versions`` and ``watermark`` are for a preview: the files to lay out, and
    the word across every page.
    """
    from weasyprint import HTML

    logo = _team_logo(team)
    html = render_template(
        TEMPLATE, sections=_sections(german, versions), german=german, association=ASSOCIATION,
        team=team, team_logo=logo.as_uri() if logo is not None else None, watermark=watermark,
    )
    # The PDF's title, author and language come from the HTML's <title>, <meta name=author> and lang.
    document = HTML(string=html, base_url=_static_dir().as_uri() + "/", url_fetcher=_static_only_fetcher(logo))
    return document.write_pdf()


def _stem(german, watermark=None):
    stem = f"{german.slug}_{german.version.isoformat()}"
    if german.team:
        stem = f"team-{german.team}_{stem}"
    return f"{watermark.lower()}_{stem}" if watermark else stem


def ready(german, team=None, versions=None, watermark=None):
    """The kept file of this PDF, made now if it is not there yet. ``(path, hash)``.

    The hash names what the file was made from; it goes into the address the
    PDF is opened at, so a browser never shows a copy of an earlier one.
    """
    folder = cache_dir()
    stem = _stem(german, watermark)
    digest = _source_hash(german, team, versions, watermark)
    path = folder / f"{stem}_{digest}.pdf"
    if path.is_file():
        return path, digest
    data = build(german, team, versions=versions, watermark=watermark)
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
    return path, digest


def pdf_for(german, team=None):
    """The PDF of a German version, from storage or made and kept. Bytes."""
    return ready(german, team)[0].read_bytes()


def forget_all():
    """Remove every kept PDF, so each is made again when next asked for. How many there were."""
    folder = cache_dir()
    if not folder.is_dir():
        return 0
    removed = 0
    for path in folder.glob("*.pdf"):  # not a .part another worker is still writing
        path.unlink(missing_ok=True)
        removed += 1
    return removed


def remake(german, team=None):
    """Make this version's PDF again, even when a kept one looks current. Its size in bytes."""
    for path in cache_dir().glob(f"{_stem(german)}_*.pdf"):
        path.unlink(missing_ok=True)
    return ready(german, team)[0].stat().st_size


def job_key(version):
    """How one PDF of ``all_jobs`` is named in a request: ``team/document/version``."""
    return f"{version.team or ''}/{version.slug}/{version.version.isoformat()}"


def all_jobs():
    """Every version whose PDF may be shown, the teams' too. ``[(version, team or error)]``.

    A team folder whose slug names no team comes with an error: its rules
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
    return jobs


def build_all(again=False):
    """Make the PDF of every version that may be shown. ``[(version, size or error)]``.

    ``again`` makes each anew, even when a kept one looks current.
    """
    made = []
    for version, team in all_jobs():
        if isinstance(team, Exception):
            made.append((version, team))
            continue
        try:
            made.append((version, remake(version, team) if again else len(pdf_for(version, team))))
        except Exception as exc:  # noqa: BLE001 -- report every text, not just the first that fails
            made.append((version, exc))
    return made


# --- Attached to the welcome emails ---------------------------------------------
#
# Behind a switch in Admin -> Settings -> General. The exact versions a person
# accepted, not whatever is in force when the mail goes out: the PDF is their
# copy of what they agreed to. A PDF that cannot be made is left out and
# logged; the welcome email goes all the same.

SETTING_KEY = "legal_pdfs_in_welcome_emails"


def attach_to_welcome_emails():
    from .services.settings import get_settings_map

    return get_settings_map([SETTING_KEY]).get(SETTING_KEY) == "True"


def _as_file(german, team=None):
    try:
        return {"filename": filename(german, team), "data": pdf_for(german, team), "mimetype": "application/pdf",
                "title": german.title}
    except Exception:  # noqa: BLE001 -- one PDF missing must not stop a welcome email
        current_app.logger.exception("Could not make the PDF of %s %s to attach", german.slug, german.version)
        return None


def files_for_member(member):
    """The texts a member accepted at signup, as attachments.

    By the version kept with them; for somebody who signed up before versions
    were kept, the version in force on their signup day; failing both, the
    one in force now.
    """
    from datetime import date

    accepted = getattr(member, "legal_versions_accepted", None) or {}
    signed_up = getattr(member, "created_at", None)
    files = []
    for text in legal.LEGAL_TEXTS:
        if not text.accepted_at_signup:
            continue
        german = None
        try:
            if accepted.get(text.slug):
                german = legal.find(text.slug, legal.AUTHORITATIVE, date.fromisoformat(accepted[text.slug]))
            elif signed_up is not None:
                german = legal.current_version(text.slug, today=signed_up.date())
        except ValueError:
            german = None
        german = german or legal.current_version(text.slug)
        document = _as_file(german) if german is not None else None
        if document is not None:
            files.append(document)
    return files


def files_for_team_membership(membership):
    """The team's rules the member accepted, as an attachment -- when they are a file."""
    if membership is None or membership.terms_version is None or membership.team is None:
        return []
    german = legal.find(legal.TEAM_RULES, legal.AUTHORITATIVE, membership.terms_version.date(),
                        team=membership.team.slug)
    document = _as_file(german, membership.team) if german is not None else None
    return [document] if document is not None else []


# --- A preview of a text not in legal/ yet ---------------------------------------
#
# For an administrator checking a new text, or a team's, before it is
# committed: the German file and, if there is one, its English translation are
# checked as CI checks them and laid out as the PDF would be -- with "ENTWURF"
# or "VORSCHAU" across every page, so the preview is never mistaken for the
# text in force. Nothing is kept: the files are written to a temporary folder
# for the checks and the layout, and deleted with it.

PREVIEW_MAX_BYTES = 1024 * 1024


def _uploaded(folder, data, name, role):
    """Write one uploaded file where it would go in legal/; ``(path, slug, language, team, problems)``."""
    import yaml

    if len(data) > PREVIEW_MAX_BYTES:
        return None, None, None, None, [f"The {role} file is larger than 1 MB."]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, None, None, None, [f"The {role} file is not UTF-8 text."]
    match = legal.FRONT_MATTER.match(text)
    try:
        meta = yaml.safe_load(match.group(1)) if match else None
    except yaml.YAMLError as error:
        return None, None, None, None, [f"{role}: the front matter is not valid YAML: {error}"]
    if not isinstance(meta, dict):
        return None, None, None, None, [f"{role}: no front matter (--- ... ---) at the top."]
    slug, language = str(meta.get("document") or ""), str(meta.get("language") or "")
    team = str(meta.get("team") or "") or None
    known = legal.TEAM_BY_SLUG if team else legal.BY_SLUG
    if slug not in known:
        return None, None, None, None, [
            f'{role}: document "{slug}" is not a text there is ({", ".join(known)}).']
    if team is not None and not legal.TEAM_SLUG.match(team):
        return None, None, None, None, [f'{role}: team "{team}" is not a short name (a-z, 0-9, -).']
    where = Path(folder) / (f"{legal.TEAMS_FOLDER}/{team}/" if team else "") / slug / language
    where.mkdir(parents=True, exist_ok=True)
    path = where / (Path(name or "").name or "upload.md")
    path.write_text(text, encoding="utf-8")
    return path, slug, language, team, []


def preview(german_upload, english_upload=None):
    """``(pdf bytes or None, problems, title)`` for uploaded files, each ``(data, filename)``."""
    from .services import NotFoundError
    from .services.teams import get_team

    with tempfile.TemporaryDirectory() as folder:
        uploads = [("German", german_upload, legal.AUTHORITATIVE)]
        if english_upload is not None:
            uploads.append(("English", english_upload, "en"))
        versions, problems, paths = [], [], []
        for role, (data, name), language in uploads:
            path, slug, found_language, team, found = _uploaded(folder, data, name, role)
            problems += found
            if path is None:
                continue
            paths.append(path)
            if found_language != language:
                problems.append(f'{role}: language is "{found_language}"; this file must be "{language}".')
                continue
            version, found = legal._check(path, slug, language, team)
            problems += [f"{role} ({path.name}): {problem}" for problem in found]
            if version is not None:
                versions.append(version)
        if len(versions) == 2:
            german, english = versions
            if (english.slug, english.team, english.version) != (german.slug, german.team, german.version):
                problems.append("The English file must be the translation of the German one: the same "
                                f"document, team and version ({german.version}).")
        if problems or not versions:
            legal.forget(paths)
            return None, problems, None
        german = versions[0]
        team = None
        if german.team:
            try:
                team = get_team(german.team)
            except NotFoundError:
                team = None  # laid out without a team's name and logo
        try:
            data = build(german, team, versions=versions,
                         watermark="ENTWURF" if german.status == "draft" else "VORSCHAU")
        finally:
            legal.forget(paths)
        return data, [], filename(german, team)


def _waiting_parts(german):
    english = legal.find_any(german.slug, "en", german.version, german.team)
    return [german] + ([english] if english is not None else []), (
        "ENTWURF" if german.status == "draft" else "VORSCHAU")


def waiting_ready(german, team=None):
    """``ready`` for a version not shown yet: ``(path, hash, download name)``."""
    versions, watermark = _waiting_parts(german)
    path, digest = ready(german, team, versions=versions, watermark=watermark)
    return path, digest, f"{watermark}_{filename(german, team)}"


def waiting_pdf(german, team=None):
    """A version not shown yet -- a draft, or one whose day has not come -- with
    its English file of the same version, if any, marked across every page.
    Kept like the others: a changed draft has another hash, so a new file."""
    path, _digest, name = waiting_ready(german, team)
    return path.read_bytes(), name
