"""Showing a legal text -- the association's, or a team's rules -- as a page and as a PDF.

Both kinds are the same files (services/legal_texts.py) and are shown the same
way; only their addresses and the way back differ, which the caller passes in:
``url(language, version)`` and ``pdf_url(version)`` with ``version`` None for
the version in force, and ``crumbs``, the way back as (label, url) pairs.
"""

import io
from datetime import date

from flask import abort, current_app, flash, redirect, render_template, request, send_file
from flask_babel import _

from .. import legal_pdf
from ..services import legal_texts as legal


def _german(slug, version, team):
    """The German version asked for -- the one in force, or one by its day -- or 404."""
    if version is None:
        german = legal.current_version(slug, team=team)
    else:
        try:
            german = legal.find(slug, legal.AUTHORITATIVE, date.fromisoformat(version), team=team)
        except ValueError:
            abort(404)
    if german is None:
        abort(404)
    return german


def text_page(slug, language, version, *, url, pdf_url, crumbs, label, team=None):
    """One text: the version in force, or an earlier one by its day.

    Without a language, the English translation where there is one of the
    version shown, else the German text. A translation always says the German
    text is the one that applies. With ``?part=body`` only the text itself, for
    reading it in a window over a form without leaving it.
    """
    if language is not None and language not in legal.LANGUAGES:
        abort(404)
    in_force = legal.current_version(slug, team=team)
    if in_force is None:
        abort(404)
    german = _german(slug, version, team)
    english = legal.translation(german)
    if language == legal.AUTHORITATIVE:
        shown = german
    elif language is not None:
        if english is None:
            if version is None:
                return redirect(url(legal.AUTHORITATIVE, None))
            abort(404)
        shown = english
    else:
        shown = english or german

    def address(of):
        if of is None:
            return None
        return url(of.language, None if of.version == in_force.version else of.version.isoformat())

    template = "legal/_body.html" if request.args.get("part") == "body" else "legal/text.html"
    return render_template(
        template,
        label=label,
        crumbs=crumbs,
        shown=shown,
        rendered=legal.render(shown),
        in_force=in_force,
        german_url=address(german),
        english_url=address(english),
        english_elsewhere=english is None and legal.has_language(slug, "en", team=team),
        others=[(v, address(v)) for v in legal.versions(slug, shown.language, team=team)
                if v.version != shown.version],
        pdf_url=pdf_url(None if german.version == in_force.version else german.version.isoformat()),
        pdf_has_english=english is not None,
    )


def pdf(slug, version, *, back, team=None, owner=None):
    """The PDF of a text: the German version, then its English translation.

    ``owner`` is the team a team's text belongs to, for its name and logo.
    When it cannot be made, the text is shown instead (``back``).
    """
    german = _german(slug, version, team)
    try:
        data = legal_pdf.pdf_for(german, team=owner)
    except Exception:  # noqa: BLE001 -- the text itself is still there to read
        current_app.logger.exception("Could not make the PDF of %s %s %s", team or "", slug, german.version)
        flash(_("The PDF could not be made just now. The text is below."), "warning")
        return redirect(back)
    return send_file(io.BytesIO(data), mimetype="application/pdf",
                     download_name=legal_pdf.filename(german, team=owner))
