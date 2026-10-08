"""A legal text -- the association's, or a team's rules -- as a PDF, made
before it is opened (``?prepare=1``). The pages are the app's (api/legal.py).
"""

import io
from datetime import date
from urllib.parse import urlencode

from flask import abort, current_app, flash, jsonify, redirect, request, send_file
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


def preparing():
    """``?prepare=1``: the page's script asks for the PDF to be made, before opening it."""
    return bool(request.args.get("prepare"))


def prepared(digest):
    """The answer to ``?prepare=1`` once the PDF is made: the address to open it at.

    The address carries the PDF's hash (``v``), so it changes with the PDF and
    a phone or browser holding an earlier copy cannot show that one instead.
    """
    args = request.args.to_dict()
    args.pop("prepare", None)
    args["v"] = digest
    return jsonify(url=f"{request.path}?{urlencode(args)}")


def not_prepared():
    return jsonify(error=_("The PDF could not be made just now.")), 500


def pdf(slug, version, *, back, team=None, owner=None):
    """The PDF of a text: the German version, then its English translation.

    ``owner`` is the team a team's text belongs to, for its name and logo.
    When it cannot be made, the text is shown instead (``back``).
    """
    german = _german(slug, version, team)
    try:
        path, digest = legal_pdf.ready(german, team=owner)
        data = None if preparing() else path.read_bytes()
    except Exception:  # noqa: BLE001 -- the text itself is still there to read
        current_app.logger.exception("Could not make the PDF of %s %s %s", team or "", slug, german.version)
        if preparing():
            return not_prepared()
        flash(_("The PDF could not be made just now. The text is below."), "warning")
        return redirect(back)
    if data is None:
        return prepared(digest)
    return send_file(io.BytesIO(data), mimetype="application/pdf",
                     download_name=legal_pdf.filename(german, team=owner))
