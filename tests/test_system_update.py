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

from api_helpers import send
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
    """Installing and rolling back a version is super-admin work."""
    user = User(email="updateadmin@example.com")
    user.set_password("x")
    db.session.add(user)
    user.grant_role(app_module.get_role("admin"))
    user.grant_role(app_module.get_role("superadmin"))
    db.session.commit()
    return user


@pytest.fixture
def plain_admin_user(app):
    user = User(email="plainadmin@example.com")
    user.set_password("x")
    db.session.add(user)
    user.grant_role(app_module.get_role("admin"))
    db.session.commit()
    return user


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)


API = "/api/v1/admin/settings/updates"


def _ask(client, action="update"):
    return send(client, "POST", API, {"action": action})


def _request_file(state_dir):
    return state_dir / system_update.REQUEST_FILENAME


class TestAuthorisation:
    """Requesting an update is a privileged action and must be gated."""

    def test_anonymous_cannot_request_an_update(self, client, state_dir):
        response = _ask(client)
        assert response.status_code in (401, 403)
        assert not _request_file(state_dir).exists()

    def test_ordinary_member_cannot_request_an_update(self, client, state_dir):
        member = make_member(email="plain@example.com")
        _login(client, member.user_id)

        response = _ask(client)

        assert response.status_code == 403
        assert not _request_file(state_dir).exists()

    def test_ordinary_member_cannot_read_the_status_endpoint(self, client, state_dir):
        member = make_member(email="plain2@example.com")
        _login(client, member.user_id)
        assert client.get("/admin/system-update/status").status_code in (302, 403)

    def test_superadmin_may_request_an_update(self, client, state_dir, admin_user):
        _login(client, admin_user.id)

        response = _ask(client)

        assert response.status_code == 202 and response.get_json()["in_progress"] is True
        assert _request_file(state_dir).exists()

    def test_an_ordinary_admin_cannot_request_an_update(self, client, state_dir, plain_admin_user):
        """Installing a version is the one thing the admin role no longer carries."""
        _login(client, plain_admin_user.id)

        response = _ask(client)

        assert response.status_code == 403
        assert not _request_file(state_dir).exists()

    def test_an_ordinary_admin_cannot_roll_back(self, client, state_dir, plain_admin_user):
        _login(client, plain_admin_user.id)

        _ask(client, "rollback")

        assert not _request_file(state_dir).exists()

    def test_an_ordinary_admin_cannot_read_the_status_endpoint(self, client, state_dir, plain_admin_user):
        _login(client, plain_admin_user.id)
        assert client.get("/admin/system-update/status").status_code == 302
        assert client.get(API).status_code == 403

    def test_reading_requests_nothing(self, client, state_dir, admin_user):
        # A state-changing action must not be reachable by navigation.
        _login(client, admin_user.id)
        assert client.get(API).status_code == 200
        assert client.get("/admin/system-update").status_code == 404
        assert not _request_file(state_dir).exists()

    def test_only_the_two_actions(self, client, state_dir, admin_user):
        _login(client, admin_user.id)
        assert _ask(client, "rm -rf /").status_code == 400
        assert not _request_file(state_dir).exists()


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


def test_the_old_status_endpoint_still_answers(client, state_dir, admin_user):
    """A Maintenance page opened before the update that brought the new front
    end asks here until the update is done; without it, it would wait for ever."""
    _login(client, admin_user.id)

    response = client.get("/admin/system-update/status")

    assert response.status_code == 200
    assert response.is_json
    assert set(response.get_json()) >= {"local", "update_available", "runner_installed"}


def test_requesting_an_update_is_audited(client, state_dir, admin_user):
    _login(client, admin_user.id)

    _ask(client)

    entry = db.session.execute(
        db.select(AuditLog).filter_by(event_type="update_requested")
    ).scalar_one()
    assert entry.actor_user_id == admin_user.id


def test_the_pages_are_the_apps(client, state_dir, admin_user):
    _login(client, admin_user.id)

    assert client.get("/admin/settings/updates").status_code == 200
    assert client.get("/admin/settings/health").status_code == 200


def test_only_for_whoever_may_update(client, state_dir, plain_admin_user):
    _login(client, plain_admin_user.id)

    assert client.get("/admin/settings/updates").headers["Location"].endswith("/admin/settings/general")
    assert client.get("/admin/settings/health").headers["Location"].endswith("/admin/settings/general")


def test_what_the_page_is_told(client, state_dir, admin_user, monkeypatch):
    monkeypatch.setattr(system_update, "get_local_version", lambda: {
        "revision": "a" * 40, "short_revision": "aaaaaaaa", "branch": "main",
        "committed_at": "2026-10-01T12:00:00+02:00", "subject": "Fix a thing"})
    monkeypatch.setattr(system_update, "get_remote_version", lambda force=False: "b" * 40)
    _login(client, admin_user.id)

    body = client.get(API).get_json()

    assert body["installed"] == {"short_revision": "aaaaaaaa", "branch": "main", "rolled_back": False,
                                 "committed_at": "2026-10-01T10:00:00Z", "subject": "Fix a thing"}
    assert body["latest"] == "bbbbbbbb" and body["update_available"] is True


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

        response = _ask(client, "rollback")

        assert response.status_code == 403
        assert not (state_dir / system_update.REQUEST_FILENAME).exists()

    def test_admin_rollback_is_audited_distinctly(self, client, state_dir, tmp_path, monkeypatch, admin_user):
        self._record_point(tmp_path, monkeypatch)
        _login(client, admin_user.id)

        _ask(client, "rollback")

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


class TestTheRemoteCheckSurvivesARollback:
    """A rollback detaches HEAD, and the update check used to give up.

    `git rev-parse --abbrev-ref HEAD` returns the literal "HEAD" on a detached
    checkout, so the page reported "Latest available: could not be checked" --
    at exactly the moment an administrator has just gone back a version and
    most needs to be told a newer one exists.
    """

    def test_a_detached_head_falls_back_to_the_recorded_branch(self, app, tmp_path, monkeypatch):
        rollback_file = tmp_path / "rollback.conf"
        rollback_file.write_text(
            'ROLLBACK_REVISION="' + "a" * 40 + '"\n'
            'ROLLBACK_BRANCH="main"\n'
        )
        monkeypatch.setattr(system_update, "ROLLBACK_FILE", rollback_file)
        monkeypatch.setattr(system_update, "_remote_cache", {"checked_at": None, "value": None})

        asked = []

        def fake_git(args, timeout=None):
            if args[:2] == ["rev-parse", "--abbrev-ref"]:
                return "HEAD"          # detached, as after a rollback
            asked.append(args)
            return "b" * 40 + "\trefs/heads/main"

        monkeypatch.setattr(system_update, "_run_git", fake_git)
        with app.test_request_context("/"):
            revision = system_update.get_remote_version(force=True)

        assert revision == "b" * 40
        assert any("refs/heads/main" in " ".join(a) for a in asked), asked

    def test_without_a_recorded_branch_it_still_gives_up_quietly(self, app, tmp_path, monkeypatch):
        monkeypatch.setattr(system_update, "ROLLBACK_FILE", tmp_path / "absent.conf")
        monkeypatch.setattr(system_update, "_remote_cache", {"checked_at": None, "value": None})
        monkeypatch.setattr(
            system_update, "_run_git",
            lambda args, timeout=None: "HEAD" if args[:2] == ["rev-parse", "--abbrev-ref"] else "",
        )
        with app.test_request_context("/"):
            assert system_update.get_remote_version(force=True) is None

    def test_the_rollback_point_exposes_the_branch(self, app, tmp_path, monkeypatch):
        rollback_file = tmp_path / "rollback.conf"
        rollback_file.write_text(
            'ROLLBACK_REVISION="' + "c" * 40 + '"\nROLLBACK_BRANCH="release"\n'
        )
        monkeypatch.setattr(system_update, "ROLLBACK_FILE", rollback_file)
        with app.test_request_context("/"):
            assert system_update.read_rollback_point()["branch"] == "release"

    def test_the_page_does_not_call_a_detached_head_a_branch(self, client, admin_user, monkeypatch):
        monkeypatch.setattr(system_update, "get_local_version", lambda: {
            "revision": "a" * 40, "short_revision": "aaaaaaaa", "branch": "HEAD",
            "committed_at": None, "subject": None})
        _login(client, admin_user.id)

        installed = client.get(API).get_json()["installed"]

        assert installed["branch"] is None and installed["rolled_back"] is True


class TestAnUpdateThatNeverFinishes:
    """A runner killed mid-update left the page saying "Update running" for good.

    Seen live, twice: apt stalled, systemd's 30-minute limit killed the runner
    before its own 45-minute one could record a failure, and neither a reboot
    nor a successful `update` from the shell changed the status file -- so the
    page kept showing a frozen update and refused to start another.
    """

    RUNNER = Path(__file__).resolve().parent.parent / "deploy" / "update-runner.sh"
    INSTALLER = Path(__file__).resolve().parent.parent / "install.sh"

    def _run_runner(self, state_dir, command, wait=True):
        import os
        import subprocess

        env = {**os.environ, "UPDATE_STATE_DIR": str(state_dir), "UPDATE_COMMAND": str(command),
               "INSTALL_DIR": str(state_dir), "UPDATE_TIMEOUT": "120"}
        process = subprocess.Popen(["bash", str(self.RUNNER)], env=env, start_new_session=True)
        if wait:
            process.wait(timeout=60)
        return process

    def _command(self, tmp_path, body):
        command = tmp_path / "fake-update"
        command.write_text("#!/usr/bin/env bash\n" + body + "\n")
        command.chmod(0o755)
        return command

    def _status(self, state_dir):
        return json.loads((state_dir / "status.json").read_text())

    def test_systemd_gives_the_runner_time_to_record_a_timeout(self):
        timeout = int(re.search(r'UPDATE_TIMEOUT="\$\{UPDATE_TIMEOUT:-(\d+)\}"', self.RUNNER.read_text()).group(1))
        unit_limit = int(re.search(r"ExecStart=\$\{UPDATE_RUNNER_SCRIPT\}\n(?:#.*\n)*TimeoutStartSec=(\d+)",
                                   self.INSTALLER.read_text()).group(1))
        assert unit_limit > timeout + 60  # the runner's own limit, plus its --kill-after

    def test_a_normal_update_completes(self, tmp_path):
        state_dir = tmp_path / "updates"
        state_dir.mkdir()
        (state_dir / "request.json").write_text('{"requested_at": "x", "action": "update"}')
        self._run_runner(state_dir, self._command(tmp_path, 'echo "[STEP] one"'))

        status = self._status(state_dir)
        assert status["state"] == "completed" and status["boot_id"]
        assert not (state_dir / "request.processing.json").exists()

    def test_a_runner_that_is_stopped_says_so(self, tmp_path):
        """What systemd does at its time limit, on a service stop or a reboot."""
        import os
        import signal
        import time

        state_dir = tmp_path / "updates"
        state_dir.mkdir()
        (state_dir / "request.json").write_text('{"requested_at": "x", "action": "update"}')
        process = self._run_runner(state_dir, self._command(tmp_path, 'echo "Get:1 http://archive"; sleep 60'),
                                   wait=False)
        for _ in range(100):
            if (state_dir / "status.json").exists() and self._status(state_dir)["state"] == "running":
                break
            time.sleep(0.1)
        time.sleep(0.5)
        os.kill(process.pid, signal.SIGTERM)  # the runner itself; systemd also signals the rest
        process.wait(timeout=30)

        status = self._status(state_dir)
        assert status["state"] == "failed"
        assert "stopped before it finished" in status["log_tail"]
        assert not (state_dir / "request.processing.json").exists()

    def test_a_run_that_died_without_a_word_is_cleaned_up_by_the_next(self, tmp_path):
        """SIGKILL or power loss: no trap runs, so the next tick recovers."""
        state_dir = tmp_path / "updates"
        state_dir.mkdir()
        (state_dir / "status.json").write_text(json.dumps({"state": "running", "started_at": "2026-09-27T12:00:00+02:00"}))
        (state_dir / "request.processing.json").write_text("{}")

        self._run_runner(state_dir, self._command(tmp_path, "exit 0"))

        assert self._status(state_dir)["state"] == "failed"
        assert not (state_dir / "request.processing.json").exists()

    def test_the_page_does_not_believe_a_run_from_before_a_reboot(self, app, state_dir, monkeypatch):
        (state_dir / "status.json").write_text(json.dumps({
            "state": "running", "started_at": system_update.get_now_utc().isoformat(), "boot_id": "an-older-boot",
        }))
        monkeypatch.setattr(system_update, "_current_boot_id", lambda: "this-boot")

        state = system_update.describe_update_state()
        assert not state["in_progress"]
        assert state["last_run"]["state"] == "failed" and "restarted" in state["last_run"]["interrupted"]

    def test_the_page_does_not_believe_a_run_older_than_any_update(self, app, state_dir):
        """Status files written before this fix carry no boot id: the age decides."""
        (state_dir / "status.json").write_text(json.dumps({
            "state": "running", "started_at": "2026-09-27T09:00:00+02:00",
        }))
        state = system_update.describe_update_state()
        assert not state["in_progress"]
        assert "more than an hour" in state["last_run"]["interrupted"]
        system_update.request_update(requested_by_user_id=None)  # and a new one can start

    def test_a_request_nobody_picks_up_does_not_block_forever(self, app, state_dir):
        (state_dir / "request.json").write_text(json.dumps({"requested_at": "2026-09-27T09:00:00+00:00"}))
        state = system_update.describe_update_state()
        assert not state["in_progress"] and state["request_never_picked_up"]

    def test_a_fresh_run_still_counts_as_running(self, app, state_dir, monkeypatch):
        (state_dir / "status.json").write_text(json.dumps({
            "state": "running", "started_at": system_update.get_now_utc().isoformat(), "boot_id": "this-boot",
        }))
        monkeypatch.setattr(system_update, "_current_boot_id", lambda: "this-boot")
        assert system_update.describe_update_state()["in_progress"]


class TestAnUpdateDoesNotNeedTheUbuntuMirror:
    """Every update re-downloaded Ubuntu's whole package index, then deleted it.

    So a portal update failed whenever archive.ubuntu.com had a bad moment --
    which is what froze the update on 2026-09-27: "Ign:" on two downloads, then
    apt's retry never returned. Updates now skip apt when nothing is missing.
    """

    INSTALLER = Path(__file__).resolve().parent.parent / "install.sh"

    def _run(self, tmp_path, mode, installed):
        import subprocess

        source = self.INSTALLER.read_text()
        functions = "\n".join(
            re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", source, re.M | re.S).group(0)
            for name in ("packages_missing", "install_packages")
        )
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "dpkg-query").write_text(
            "#!/usr/bin/env bash\n"
            f'case " {" ".join(installed)} " in *" ${{@: -1}} "*) printf "install ok installed";; esac\n'
        )
        (bin_dir / "dpkg-query").chmod(0o755)
        script = (
            f"{functions}\n"
            "info() { printf 'INFO %s\\n' \"$*\"; }\n"
            "update_package_index_once() { printf 'INDEX\\n'; }\n"
            "retry() { shift; printf 'RUN %s\\n' \"$*\"; }\n"
            "APT_NETWORK_OPTS=()\n"
            f"PACKAGE_MANAGER=apt MODE={mode}\n"
            "install_packages git nginx redis-server\n"
        )
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                              env={"PATH": f"{bin_dir}:/usr/bin:/bin"}, check=True).stdout

    def test_nothing_is_fetched_when_everything_is_installed(self, tmp_path):
        output = self._run(tmp_path, "update", ["git", "nginx", "redis-server"])
        assert "INDEX" not in output and "RUN" not in output

    def test_only_what_is_missing_is_installed(self, tmp_path):
        output = self._run(tmp_path, "update", ["git", "nginx"])
        assert "INDEX" in output
        assert "RUN apt-get install -y redis-server" in output

    def test_a_repair_still_refreshes_everything(self, tmp_path):
        output = self._run(tmp_path, "repair", ["git", "nginx", "redis-server"])
        assert "RUN apt-get install -y git nginx redis-server" in output

    def test_apt_does_not_pipeline_requests(self):
        assert "Acquire::http::Pipeline-Depth=0" in self.INSTALLER.read_text()
