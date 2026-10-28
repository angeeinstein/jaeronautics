"""Updates installed by themselves at night (services/automatic_updates.py,
docs/automatic-updates-plan.md, part 2): the switch on Settings › Updates and
the nightly job that asks for the newest version -- only when it is on, CI
passed for that very version, no update runs, and it was not tried before.
"""
import json

import pytest

from api_helpers import send, signed_in
from conftest import db
from aeronautics_members.db_models import AuditLog
from aeronautics_members.services import automatic_updates, background_jobs, system_update
from test_admin_reviews import _staff

UPDATES = "/api/v1/admin/settings/updates"
LOCAL, REMOTE, NEWER = "a" * 40, "b" * 40, "c" * 40


@pytest.fixture
def server(app, tmp_path, monkeypatch):
    """The deployment as the job sees it: a version running, the newest on the branch, CI's word on it."""
    class Server:
        local = LOCAL
        remote = REMOTE
        ci = "success"

    state_dir = tmp_path / "updates"
    state_dir.mkdir()
    monkeypatch.setattr(system_update, "UPDATE_STATE_DIR", state_dir)
    monkeypatch.setattr(system_update, "UPDATE_RUNNER_DIR", tmp_path / "update-runner")
    monkeypatch.setattr(system_update, "get_local_version", lambda: {"revision": Server.local})
    monkeypatch.setattr(system_update, "get_remote_version", lambda force=False: Server.remote)
    monkeypatch.setattr(system_update, "ci_state", lambda revision, force=False: {"state": Server.ci})
    Server.request = state_dir / system_update.REQUEST_FILENAME
    return Server


def _on():
    automatic_updates.set_enabled(True)
    db.session.commit()


class TestTheNightlyCheck:
    def test_off_asks_for_nothing(self, server):
        assert automatic_updates.run() == automatic_updates.OFF
        assert not server.request.exists()

    def test_a_newer_version_ci_passed_for_is_asked_for(self, server):
        _on()

        assert automatic_updates.run() == automatic_updates.REQUESTED
        # The request the button writes: no target, nobody behind it.
        assert json.loads(server.request.read_text())["requested_by_user_id"] is None
        assert db.session.query(AuditLog).filter_by(event_type="automatic_update_requested").count() == 1
        assert automatic_updates.last_run()["outcome"] == automatic_updates.REQUESTED

    @pytest.mark.parametrize("ci", ["running", "none", "failure", "unknown"])
    def test_not_before_ci_passed_for_it(self, server, ci):
        # "unknown" too: offered to a person on the page, never installed unattended.
        _on()
        server.ci = ci

        assert automatic_updates.run() == automatic_updates.CI_NOT_PASSED
        assert not server.request.exists()

    def test_up_to_date(self, server):
        _on()
        server.remote = LOCAL

        assert automatic_updates.run() == automatic_updates.UP_TO_DATE

    def test_not_while_an_update_runs(self, server):
        _on()
        server.request.write_text(json.dumps({"requested_at": "2999-01-01T00:00:00+00:00"}))

        assert automatic_updates.run() == automatic_updates.BUSY

    def test_the_remote_out_of_reach(self, server):
        _on()
        server.remote = None

        assert automatic_updates.run() == automatic_updates.UNREACHABLE

    def test_a_version_that_did_not_install_is_not_tried_night_after_night(self, server):
        _on()
        automatic_updates.run()
        server.request.unlink()  # the runner took it up -- and it failed, undid itself

        assert automatic_updates.run() == automatic_updates.FAILED_BEFORE
        assert not server.request.exists()

        server.remote = NEWER  # until a newer one comes
        assert automatic_updates.run() == automatic_updates.REQUESTED


class TestTheSwitch:
    def test_off_until_switched_on_and_each_change_logged(self, server, client):
        boss = signed_in(client, _staff("boss@example.org", "superadmin"))
        assert boss.get(UPDATES).get_json()["automatic"] == {"enabled": False, "at": "04:30", "last_run": None}

        body = send(boss, "PUT", f"{UPDATES}/automatic", json={"enabled": True}).get_json()

        assert body["automatic"]["enabled"] is True and automatic_updates.is_enabled()
        assert db.session.query(AuditLog).filter_by(event_type="automatic_updates_switched").count() == 1

    def test_what_the_last_night_came_to(self, server, client):
        boss = signed_in(client, _staff("boss@example.org", "superadmin"))
        _on()
        server.ci = "running"
        automatic_updates.run()
        db.session.commit()

        last = boss.get(UPDATES).get_json()["automatic"]["last_run"]

        assert last["outcome"] == "ci_not_passed" and last["revision"] == REMOTE[:8]

    def test_only_for_whoever_may_install_updates(self, server, client):
        member = signed_in(client, _staff("helper@example.org", "member"))

        assert send(member, "PUT", f"{UPDATES}/automatic", json={"enabled": True}).status_code == 403
        assert not automatic_updates.is_enabled()


class TestTheCommand:
    def test_switched_on_by_the_installer(self, app, server):
        result = app.test_cli_runner().invoke(args=["automatic-updates", "on"])

        assert "Automatic updates: on." in result.output and automatic_updates.is_enabled()

    def test_stands_down_while_background_jobs_are_paused(self, app, server):
        _on()
        background_jobs.pause("restore")
        db.session.commit()

        result = app.test_cli_runner().invoke(args=["automatic-updates", "run"])

        assert "paused" in result.output and not server.request.exists()
        assert background_jobs.last_check_in("automatic-updates")[0] is not None
