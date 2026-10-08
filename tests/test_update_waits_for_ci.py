"""An update is offered once CI has passed for it (services/system_update.py).

The installer only installs a version whose CI passed -- it takes CI's build
of the front end -- and waits for CI while it runs. Offered before that, the
update sat waiting for up to 25 minutes; the maintainer wanted "a quick update
in one process". So the page asks GitHub first, the same question the
installer asks (ci_state in install.sh).
"""
import pytest

from aeronautics_members.services import system_update

LOCAL, REMOTE = "a" * 40, "b" * 40
CI = ".github/workflows/ci.yml"


def _run(status="completed", conclusion="success", number=7, path=CI):
    return {"path": path, "status": status, "conclusion": conclusion, "run_number": number, "run_attempt": 1,
            "html_url": f"https://github.com/o/r/actions/runs/{number}",
            "run_started_at": "2026-10-08T10:00:00Z", "updated_at": "2026-10-08T10:12:00Z"}


@pytest.fixture
def github(app, monkeypatch, tmp_path):
    """GitHub, answering from ``github.runs``; ``github.asked`` counts the questions."""
    class GitHub:
        runs = [_run()]
        reachable = True
        asked = []

    def answer(path):
        GitHub.asked.append(path)
        if not GitHub.reachable:
            return None
        return {"workflow_runs": GitHub.runs}

    app.config["UPDATE_CI_CHECK"] = True
    monkeypatch.setattr(system_update, "UPDATE_STATE_DIR", tmp_path / "updates")
    (tmp_path / "updates").mkdir()
    monkeypatch.setattr(system_update, "github_slug", lambda: "o/r")
    monkeypatch.setattr(system_update, "_github_json", answer)
    monkeypatch.setattr(system_update, "get_local_version", lambda: {
        "revision": LOCAL, "short_revision": LOCAL[:8], "branch": "main", "committed_at": None, "subject": None})
    monkeypatch.setattr(system_update, "get_remote_version", lambda force=False: REMOTE)
    system_update._ci_cache.clear()
    system_update._typical_cache.update(checked_at=None, value=None)
    yield GitHub
    system_update._ci_cache.clear()


def _state(app):
    with app.test_request_context():
        return system_update.describe_update_state()


def test_offered_once_ci_passed(app, github):
    state = _state(app)

    assert state["update_available"] is True and state["remote_ci"]["state"] == "success"
    assert github.asked[0].startswith(f"/repos/o/r/actions/runs?head_sha={REMOTE}")


def test_not_while_ci_runs_and_with_how_long_it_usually_takes(app, github):
    github.runs = [_run(status="in_progress", conclusion=None)]

    state = _state(app)

    assert state["newer_version"] is True and state["update_available"] is False
    assert state["remote_ci"]["state"] == "running"
    assert state["remote_ci"]["typical_minutes"] == 12


def test_not_before_ci_has_started(app, github):
    github.runs = [_run(path=".github/workflows/other.yml")]

    assert _state(app)["remote_ci"]["state"] == "none"
    assert _state(app)["update_available"] is False


def test_never_when_ci_failed(app, github):
    github.runs = [_run(number=7), _run(number=8, conclusion="failure")]

    state = _state(app)

    assert state["update_available"] is False and state["remote_ci"]["state"] == "failure"
    assert state["remote_ci"]["url"].endswith("/runs/8")


def test_github_out_of_reach_offers_it_as_before(app, github):
    github.reachable = False

    assert _state(app)["update_available"] is True


def test_the_answer_is_kept(app, github):
    _state(app)
    _state(app)

    assert len(github.asked) == 1


def test_nothing_asked_when_up_to_date(app, github, monkeypatch):
    monkeypatch.setattr(system_update, "get_remote_version", lambda force=False: LOCAL)

    state = _state(app)

    assert state["remote_ci"] is None and github.asked == []


def test_what_the_page_is_told(app, github, client):
    from api_helpers import signed_in
    from test_admin_reviews import _staff

    github.runs = [_run(status="queued", conclusion=None)]
    body = signed_in(client, _staff("boss@example.org", "superadmin")).get("/api/v1/admin/settings/updates").get_json()

    assert body["update_available"] is False and body["newer_version"] is True
    assert body["latest_check"]["state"] == "running" and body["latest_check"]["typical_minutes"] == 12


@pytest.mark.parametrize("url, slug", [
    ("https://github.com/angeeinstein/jaeronautics.git", "angeeinstein/jaeronautics"),
    ("git@github.com:angeeinstein/jaeronautics.git", "angeeinstein/jaeronautics"),
    ("https://github.com/angeeinstein/jaeronautics", "angeeinstein/jaeronautics"),
    ("https://gitlab.com/someone/elsewhere.git", None),
])
def test_where_the_code_is(app, monkeypatch, url, slug):
    monkeypatch.setattr(system_update, "_run_git", lambda args, timeout=10: url)

    assert system_update.github_slug() == slug
