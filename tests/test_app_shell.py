"""Flask handing the new front end its pages (blueprints/app_shell.py).

Every address in frontend/src/app/paths.json is answered with the built
index.html, the page's nonce in it; the route's own checks (signed in,
permissions) still come first. Run against a stand-in build, so these tests
do not need ``npm run build``.
"""
import json
import re
from pathlib import Path

import pytest

from conftest import app_module, db, make_member
from test_admin_reviews import _login, _staff
from test_teams_foundation import _team

PATHS = json.loads((Path(__file__).resolve().parent.parent / "frontend/src/app/paths.json").read_text())["paths"]

INDEX = """<!doctype html><html><head>
<meta name="csp-nonce" content="__CSP_NONCE__" />
<script type="module" crossorigin src="/static/app/assets/index-test.js"></script>
</head><body><div id="root"></div></body></html>"""


@pytest.fixture
def built(app, tmp_path):
    folder = tmp_path / "app"
    folder.mkdir()
    (folder / "index.html").write_text(INDEX)
    app.config["FRONTEND_DIST_DIR"] = str(folder)
    return folder


#: An example for each part of an address that changes; anything else is 1.
EXAMPLES = {"slug": "rocket-team", "language": "de", "version": "2026-01-01"}


def _concrete(pattern):
    """/admin/accounts/:userId -> /admin/accounts/1, /teams/:slug -> /teams/rocket-team"""
    return re.sub(r":(\w+)", lambda part: EXAMPLES.get(part.group(1), "1"), pattern)


#: Pages for somebody not signed in: a signed-in visitor is sent on (below).
SIGNED_OUT = {"/login", "/forgot-password"}


@pytest.mark.parametrize("pattern", PATHS)
def test_every_app_address_gets_the_app(app, client, built, pattern):
    from aeronautics_members.services import teams

    teams.save_team_settings(None, enabled=True, label_singular="", label_plural="")
    teams.set_access_list_enabled(None, _team(slug="rocket-team"), True)
    # A member too: /forum sends an account without a membership to the membership form.
    boss = make_member(email="boss@example.org").user
    boss.grant_role(app_module.get_role("superadmin"))
    db.session.commit()
    if pattern not in SIGNED_OUT:
        _login(client, boss.id)

    response = client.get(_concrete(pattern))

    assert response.status_code == 200 and response.mimetype == "text/html"
    assert '<div id="root"></div>' in response.get_data(as_text=True)


def test_signed_in_the_sign_in_page_goes_on(app, client, built):
    _login(client, _staff("boss@example.org", "admin").id)

    assert client.get("/login").headers["Location"] == "/admin"
    assert client.get("/login?next=/teams").headers["Location"] == "/teams"
    assert client.get("/login?next=https://elsewhere.example/").headers["Location"] == "/admin"
    assert client.get("/forgot-password").headers["Location"] == "/admin"


def test_the_nonce_in_the_page_is_the_policys(app, client, built):
    _login(client, _staff("boss@example.org", "admin").id)

    response = client.get("/admin")

    in_page = re.search(r'name="csp-nonce" content="([^"]+)"', response.get_data(as_text=True)).group(1)
    assert in_page != "__CSP_NONCE__"
    assert f"'nonce-{in_page}'" in response.headers["Content-Security-Policy"]


def test_signed_out_goes_to_the_login_first(client, built):
    response = client.get("/admin")

    assert response.status_code == 302 and "/login" in response.headers["Location"]


def test_without_the_permission_no_app(app, client, built):
    _login(client, make_member().user.id)

    assert client.get("/admin").status_code == 302


def test_a_new_build_is_picked_up(app, client, built):
    _login(client, _staff("boss@example.org", "admin").id)
    client.get("/admin")

    (built / "index.html").write_text(INDEX.replace("index-test.js", "index-newer.js") + "\n")

    assert "index-newer.js" in client.get("/admin").get_data(as_text=True)


def test_not_built_says_so(app, client, tmp_path):
    app.config["FRONTEND_DIST_DIR"] = str(tmp_path / "missing")
    _login(client, _staff("boss@example.org", "admin").id)

    response = client.get("/admin")

    assert response.status_code == 503 and "npm run build" in response.get_data(as_text=True)


def test_the_app_shell_is_never_cached(app, client, built):
    """A cached page would load the scripts of the build before."""
    _login(client, _staff("boss@example.org", "admin").id)

    assert "no-store" in client.get("/admin").headers["Cache-Control"]
