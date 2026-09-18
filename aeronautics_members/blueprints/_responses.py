"""Small response helpers shared by the blueprints.

These turn service results into HTTP responses, which is why they live here
rather than in ``services/``: a service returns data, and only the transport
layer knows it is being sent to a browser as a file.
"""

import json

from flask import Response


def json_download_response(payload, filename):
    """Serve ``payload`` as a downloadable, pretty-printed JSON file.

    Pretty-printed because a data export is meant to be *read* by the person who
    asked for it, not only parsed. ``Content-Disposition: attachment`` keeps the
    browser from rendering it inline, and ``no-store`` keeps a copy of someone's
    personal data out of shared caches on the way.
    """
    body = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False)
    response = Response(body, mimetype="application/json")
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["Cache-Control"] = "no-store"
    return response
