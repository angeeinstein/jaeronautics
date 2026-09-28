"""Static guard: every url_for()/build_public_url() endpoint literal resolves.

This catches endpoint-name mistakes (e.g. after a blueprint rename) before they
become runtime BuildErrors. Unlike a naive check, it inspects *every* quoted
string on a url_for/build_public_url line, so conditional expressions like
``url_for('a.x' if cond else 'b.y')`` are fully covered.
"""
import re
from pathlib import Path

from conftest import app_module

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "aeronautics_members"

# Names that look like endpoints on a url_for line but are not (dict keys, etc.).
# 'admin' is a real (aliased) endpoint, so it is intentionally allowed.
_KNOWN_NON_ENDPOINTS = {"logout"}  # dict key in build_public_url payloads


def _endpoint_literals():
    """Yield (file, lineno, endpoint) for every string on a url_for/bpu line."""
    files = list(PKG.rglob("*.py")) + list((PKG / "templates").rglob("*.html"))
    for path in files:
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if "url_for(" not in line and "build_public_url(" not in line:
                continue
            for token in re.findall(r"['\"]([a-zA-Z0-9_.]+)['\"]", line):
                yield path, lineno, token


def test_all_endpoint_references_resolve():
    app = app_module.create_app()
    endpoints = set(app.url_map._rules_by_endpoint.keys())
    # Only consider tokens that plausibly name an endpoint: either already
    # blueprint-qualified (contain a dot) or match a known view function name.
    view_names = {ep.split(".")[-1] for ep in endpoints}

    unresolved = []
    for path, lineno, token in _endpoint_literals():
        if token in _KNOWN_NON_ENDPOINTS:
            continue
        # An endpoint candidate is either already registered, or its final
        # segment is a known view name (so 'account.account' / 'account' match,
        # but static filenames like 'style.css' do not).
        looks_like_endpoint = token in endpoints or token.split(".")[-1] in view_names
        if looks_like_endpoint and token not in endpoints:
            unresolved.append(f"{path.name}:{lineno} -> {token!r}")

    assert not unresolved, "Unresolved endpoint references:\n" + "\n".join(unresolved)
