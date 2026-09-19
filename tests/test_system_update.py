"""The admin update button, and the privilege boundary underneath it.

The web application runs unprivileged and must stay that way, so it cannot
install an update itself. It writes a request that a root-owned watcher acts on.
These tests cover both halves of that: only an administrator may write a
request, and the request itself carries no say over what gets deployed.
"""
import json
import re
from pathlib import Path

import pytest

from conftest import app_module, db, make_member
from aeronautics_members.db_models import AuditLog, User
from aeronautics_members.services import ConflictError, ServiceError, system_update


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """Point the service at a throwaway state directory."""
    directory = tmp_path / "updates"
    directory.mkdir()
    monkeypatch.setattr(system_update, "UPDATE_STATE_DIR", directory)
    return directory


@pytest.fixture
def admin_user(app):
    user = User(email="updateadmin@example.com")
    user.set_password("x")
    user.grant_role(app_module.get_role("admin"))
    db.session.add(user)
    db.session.commit()
    return user


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)


def _request_file(state_dir):
    return state_dir / system_update.REQUEST_FILENAME


class TestAuthorisation:
    """Requesting an update is a privileged action and must be gated."""

    def test_anonymous_cannot_request_an_update(self, client, state_dir):
        response = client.post("/admin/system-update")
        assert response.status_code in (302, 401, 403)
        assert not _request_file(state_dir).exists()

    def test_ordinary_member_cannot_request_an_update(self, client, state_dir):
        member = make_member(email="plain@example.com")
        _login(client, member.user_id)

        response = client.post("/admin/system-update")

        assert response.status_code in (302, 403)
        assert not _request_file(state_dir).exists()

    def test_ordinary_member_cannot_read_the_status_endpoint(self, client, state_dir):
        member = make_member(email="plain2@example.com")
        _login(client, member.user_id)
        assert client.get("/admin/system-update/status").status_code in (302, 403)

    def test_admin_may_request_an_update(self, client, state_dir, admin_user):
        _login(client, admin_user.id)

        response = client.post("/admin/system-update", follow_redirects=False)

        assert response.status_code == 302
        assert _request_file(state_dir).exists()

    def test_get_is_not_accepted(self, client, state_dir, admin_user):
        # A state-changing action must not be reachable by navigation.
        _login(client, admin_user.id)
        assert client.get("/admin/system-update").status_code == 405


class TestRequestContents:
    def test_request_names_the_requester_but_not_what_to_deploy(self, app, state_dir):
        """The privilege boundary: the request cannot choose the code.

        What gets installed comes from the installer's own state on the root
        side. If this file could name a branch or a remote, an attacker who
        reached the endpoint would have arbitrary code execution as root.
        """
        system_update.request_update(requested_by_user_id=7)

        payload = json.loads(_request_file(state_dir).read_text())

        assert payload["requested_by_user_id"] == 7
        assert "requested_at" in payload
        forbidden = {"branch", "remote", "repo", "url", "command", "ref", "revision"}
        assert not (forbidden & set(payload)), f"request must not steer the deploy: {payload}"

    def test_second_request_while_one_is_pending_is_refused(self, app, state_dir):
        system_update.request_update(requested_by_user_id=1)
        with pytest.raises(ConflictError):
            system_update.request_update(requested_by_user_id=1)

    def test_request_refused_while_an_update_is_running(self, app, state_dir):
        (state_dir / system_update.STATUS_FILENAME).write_text(json.dumps({"state": "running"}))
        with pytest.raises(ConflictError):
            system_update.request_update(requested_by_user_id=1)

    def test_missing_runner_is_reported_rather_than_silently_ignored(self, app, monkeypatch, tmp_path):
        # Without the privileged side installed the button would appear to work
        # and do nothing at all.
        monkeypatch.setattr(system_update, "UPDATE_STATE_DIR", tmp_path / "absent")
        with pytest.raises(ServiceError) as excinfo:
            system_update.request_update(requested_by_user_id=1)
        assert excinfo.value.code == "update_runner_missing"


class TestStateReporting:
    def test_describe_reports_versions_and_runner_presence(self, app, state_dir):
        described = system_update.describe_update_state()

        assert set(described) >= {
            "local", "remote_revision", "update_available", "runner_installed",
            "in_progress", "last_run",
        }
        assert described["runner_installed"] is True
        # Running inside the repo, so the local revision resolves.
        assert described["local"]["revision"]

    def test_update_available_when_remote_differs(self, app, state_dir, monkeypatch):
        monkeypatch.setattr(system_update, "get_remote_version", lambda force=False: "f" * 40)
        described = system_update.describe_update_state()
        assert described["update_available"] is True
        assert described["remote_short_revision"] == "f" * 8

    def test_no_update_when_revisions_match(self, app, state_dir, monkeypatch):
        local = system_update.get_local_version()["revision"]
        monkeypatch.setattr(system_update, "get_remote_version", lambda force=False: local)
        assert system_update.describe_update_state()["update_available"] is False

    def test_unreachable_remote_is_reported_not_raised(self, app, state_dir, monkeypatch):
        monkeypatch.setattr(system_update, "get_remote_version", lambda force=False: None)
        described = system_update.describe_update_state()
        assert described["remote_check_failed"] is True
        assert described["update_available"] is False

    def test_last_run_is_surfaced_from_the_runner(self, app, state_dir):
        (state_dir / system_update.STATUS_FILENAME).write_text(json.dumps({
            "state": "failed", "exit_code": 3, "log_tail": "boom",
            "revision_after": "a" * 40,
        }))
        last_run = system_update.describe_update_state()["last_run"]
        assert (last_run["state"], last_run["exit_code"], last_run["log_tail"]) == ("failed", 3, "boom")

    def test_unreadable_status_does_not_break_the_page(self, app, state_dir):
        (state_dir / system_update.STATUS_FILENAME).write_text("{not json")
        assert system_update.describe_update_state()["last_run"]["state"] is None


def test_status_endpoint_returns_the_same_data_as_the_page(client, state_dir, admin_user):
    """One service call behind both, so the API and the HTML cannot drift."""
    _login(client, admin_user.id)

    response = client.get("/admin/system-update/status")

    assert response.status_code == 200
    assert response.is_json
    assert set(response.get_json()) >= {"local", "update_available", "runner_installed"}


def test_requesting_an_update_is_audited(client, state_dir, admin_user):
    _login(client, admin_user.id)

    client.post("/admin/system-update")

    entry = db.session.execute(
        db.select(AuditLog).filter_by(event_type="update_requested")
    ).scalar_one()
    assert entry.actor_user_id == admin_user.id


def test_settings_page_renders_the_maintenance_panel(client, state_dir, admin_user):
    _login(client, admin_user.id)

    response = client.get("/admin/settings")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "settings-maintenance" in body
    assert "Install update now" in body


class TestRunnerScript:
    """Checks on the privileged half that the Python tests cannot reach."""

    RUNNER = Path(__file__).resolve().parent.parent / "deploy" / "update-runner.sh"

    def test_runner_exists_and_is_valid_shell(self):
        import subprocess

        assert self.RUNNER.exists(), "the privileged runner is missing"
        result = subprocess.run(["bash", "-n", str(self.RUNNER)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    def test_runner_does_not_read_a_target_from_the_request(self):
        """The request must not be able to influence what is deployed."""
        source = self.RUNNER.read_text()
        for field in ("branch", "remote", "repo_url", "REPO_URL"):
            assert f"read_request_field {field}" not in source, (
                f"runner reads {field!r} from the unprivileged request file"
            )

    def test_runner_claims_the_request_before_acting(self):
        # Without an atomic claim, a slow update could be started twice by
        # consecutive timer ticks.
        assert "mv -n" in self.RUNNER.read_text()


class TestRollback:
    """Rolling back is a second fixed action, not a free choice of revision."""

    def _record_point(self, tmp_path, monkeypatch, revision="a" * 40):
        rollback_file = tmp_path / "rollback.conf"
        rollback_file.write_text(
            f'ROLLBACK_REVISION="{revision}"\n'
            'ROLLBACK_SCHEMA_REVISION="b7e2d15a4c83"\n'
            'ROLLBACK_DB_BACKUP="/var/backups/jaeronautics/db.sql.gz"\n'
            'ROLLBACK_RECORDED_AT="2026-09-18T20:00:00+00:00"\n'
        )
        monkeypatch.setattr(system_update, "ROLLBACK_FILE", rollback_file)
        return rollback_file

    def test_rollback_point_is_read_for_display(self, app, tmp_path, monkeypatch):
        self._record_point(tmp_path, monkeypatch)
        point = system_update.read_rollback_point()
        assert point["short_revision"] == "a" * 8
        assert point["database_backup"].endswith("db.sql.gz")

    def test_missing_rollback_point_is_not_an_error(self, app, tmp_path, monkeypatch):
        monkeypatch.setattr(system_update, "ROLLBACK_FILE", tmp_path / "absent.conf")
        assert system_update.read_rollback_point() is None

    def test_rollback_request_names_the_action_only(self, app, state_dir, tmp_path, monkeypatch):
        """The request must not be able to choose which revision to land on.

        It selects between two operations whose targets both come from the
        privileged side. If a revision could be named here, reaching this
        endpoint would mean running any code as root.
        """
        self._record_point(tmp_path, monkeypatch)

        system_update.request_update(requested_by_user_id=3, action="rollback")

        payload = json.loads((state_dir / system_update.REQUEST_FILENAME).read_text())
        assert payload["action"] == "rollback"
        assert not ({"revision", "target", "ref", "branch", "commit"} & set(payload))

    def test_rollback_is_refused_without_a_recorded_point(self, app, state_dir, tmp_path, monkeypatch):
        monkeypatch.setattr(system_update, "ROLLBACK_FILE", tmp_path / "absent.conf")
        with pytest.raises(ConflictError):
            system_update.request_update(requested_by_user_id=3, action="rollback")

    def test_unknown_actions_are_rejected(self, app, state_dir):
        from aeronautics_members.services import ValidationError

        with pytest.raises(ValidationError):
            system_update.request_update(requested_by_user_id=3, action="rm -rf /")

    def test_only_an_admin_may_roll_back(self, client, state_dir, tmp_path, monkeypatch):
        self._record_point(tmp_path, monkeypatch)
        member = make_member(email="notadmin@example.com")
        _login(client, member.user_id)

        response = client.post("/admin/system-update", data={"action": "rollback"})

        assert response.status_code in (302, 403)
        assert not (state_dir / system_update.REQUEST_FILENAME).exists()

    def test_admin_rollback_is_audited_distinctly(self, client, state_dir, tmp_path, monkeypatch, admin_user):
        self._record_point(tmp_path, monkeypatch)
        _login(client, admin_user.id)

        client.post("/admin/system-update", data={"action": "rollback"})

        entry = db.session.execute(
            db.select(AuditLog).filter_by(event_type="rollback_requested")
        ).scalar_one()
        assert entry.actor_user_id == admin_user.id


class TestRunnerRollbackHandling:
    RUNNER = Path(__file__).resolve().parent.parent / "deploy" / "update-runner.sh"

    def test_runner_only_honours_the_known_action(self):
        source = self.RUNNER.read_text()
        assert 'action="$(read_request_field action)"' in source
        assert '"${action}" == "rollback"' in source

    def test_runner_does_not_take_a_revision_from_the_request(self):
        source = self.RUNNER.read_text()
        for field in ("revision", "target", "commit", "ref"):
            assert f"read_request_field {field}" not in source


class TestAHungUpdateCannotWedgeTheButton:
    """An update that never finishes must not disable updates permanently.

    request_update refuses while the recorded state is "running", so a hang --
    an unresponsive package mirror, a held dpkg lock -- used to leave the admin
    page unable to start another update at all, recoverable only from a shell.
    Seen live: apt stopped responding mid-update and the run sat at 0 of 16
    steps indefinitely.
    """

    RUNNER = Path(__file__).resolve().parent.parent / "deploy" / "update-runner.sh"
    INSTALLER = Path(__file__).resolve().parent.parent / "install.sh"

    def test_the_runner_bounds_the_update(self):
        source = self.RUNNER.read_text()
        assert "UPDATE_TIMEOUT" in source
        assert "timeout --signal=TERM" in source, "the update command is not run under a timeout"

    def test_a_timeout_is_explained_in_the_log(self):
        """Exit 124 on its own tells an administrator nothing."""
        source = self.RUNNER.read_text()
        assert "124" in source
        assert "mirror" in source.lower() or "lock" in source.lower()

    def test_the_timeout_is_longer_than_a_normal_update(self):
        source = self.RUNNER.read_text()
        match = re.search(r"UPDATE_TIMEOUT=\"\$\{UPDATE_TIMEOUT:-(\d+)\}\"", source)
        assert match, "no default timeout found"
        # Real updates here take well under a minute; allow a wide margin so a
        # slow-but-working run is never cut short.
        assert int(match.group(1)) >= 900, match.group(1)

    def test_apt_cannot_wait_forever_on_a_mirror(self):
        """apt has no default network timeout at all."""
        source = self.INSTALLER.read_text()
        assert "Acquire::http::Timeout" in source
        assert "Acquire::Retries" in source
        assert "DPkg::Lock::Timeout" in source

    def test_the_apt_options_are_actually_passed(self):
        source = self.INSTALLER.read_text()
        assert 'apt-get "${APT_NETWORK_OPTS[@]}" update' in source
        assert 'apt-get "${APT_NETWORK_OPTS[@]}" install' in source
