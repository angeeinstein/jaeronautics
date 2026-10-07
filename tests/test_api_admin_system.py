"""Settings -> System health and Updates (api/admin_system.py,
services/maintenance.py). The report and the update machinery have tests of
their own (test_diagnostics.py, test_system_update.py); these are about what
the pages are told and what their buttons do.
"""
import json

import pytest

from api_helpers import send, signed_in
from conftest import db
from aeronautics_members.db_models import AuditLog
from aeronautics_members.services import system_update
from test_admin_reviews import _staff

HEALTH = "/api/v1/admin/settings/health"
UPDATES = "/api/v1/admin/settings/updates"


@pytest.fixture
def boss(client):
    return signed_in(client, _staff("boss@example.org", "superadmin"))


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    directory = tmp_path / "updates"
    directory.mkdir()
    monkeypatch.setattr(system_update, "UPDATE_STATE_DIR", directory)
    return directory


class TestHealth:
    def test_a_clean_installation(self, boss):
        body = boss.get(HEALTH).get_json()

        assert body["undelivered"] == [] and body["queues"]["external_work_failed"] == 0
        assert set(body["membership"]) == {"members", "currently_covered", "coverage_periods", "revoked_periods",
                                           "erased_members"}

    def test_nothing_to_send_again_is_said_and_not_logged(self, boss):
        response = send(boss, "POST", f"{HEALTH}/forum-tasks/retry")

        assert response.get_json() == {"count": 0}
        assert db.session.query(AuditLog).filter_by(event_type="forum_tasks_retried").count() == 0


def _ran(state_dir, state, log, action="update"):
    """A run of the update runner, as its files say: its status and its output."""
    (state_dir / system_update.STATUS_FILENAME).write_text(json.dumps(
        {"state": state, "action": action, "started_at": "2999-01-01T00:00:00+00:00",
         "finished_at": None if state == "running" else f"2999-01-01T00:{len(log) % 60:02d}:00+00:00"}))
    (state_dir / system_update.LOG_FILENAME).write_text(log)


class TestUpdates:
    def test_while_one_runs_how_far_it_is(self, boss, state_dir):
        (state_dir / system_update.STATUS_FILENAME).write_text(json.dumps(
            {"state": "running", "started_at": "2999-01-01T00:00:00+00:00", "steps_expected": 4}))
        (state_dir / system_update.LOG_FILENAME).write_text("[STEP] Fetching\n[STEP] Installing\n")

        body = boss.get(UPDATES).get_json()

        assert body["in_progress"] is True
        assert body["progress"] == {"steps_done": 2, "steps_expected": 4, "percent": 50,
                                    "current_step": "Installing", "steps": [
                                        {"label": "Fetching", "state": "done", "detail": None},
                                        {"label": "Installing", "state": "running", "detail": None}]}
        assert "Installing" in body["last_run"]["log_tail"]

    def test_the_steps_still_to_come_as_the_last_good_update_went(self, boss, state_dir):
        _ran(state_dir, "completed", "[STEP] Fetching\n[STEP] Installing\n[STEP] Restarting\n[STEP] Verifying\n")
        boss.get(UPDATES)  # the page sees it finished, and keeps its steps
        _ran(state_dir, "running", "[STEP] Fetching\n[INFO] 12 files\n[STEP] Installing\n")

        steps = boss.get(UPDATES).get_json()["progress"]["steps"]

        assert [(step["label"], step["state"]) for step in steps] == [
            ("Fetching", "done"), ("Installing", "running"), ("Restarting", "pending"), ("Verifying", "pending")]

    def test_a_step_not_planned_does_not_upset_the_list(self, boss, state_dir):
        _ran(state_dir, "completed", "[STEP] Fetching\n[STEP] Installing\n[STEP] Verifying\n")
        boss.get(UPDATES)
        _ran(state_dir, "running", "[STEP] Fetching\n[STEP] Building here instead\n")

        steps = boss.get(UPDATES).get_json()["progress"]["steps"]

        assert [(step["label"], step["state"]) for step in steps] == [
            ("Fetching", "done"), ("Building here instead", "running"), ("Installing", "pending"),
            ("Verifying", "pending")]

    def test_a_rollback_is_not_what_the_next_update_expects(self, boss, state_dir):
        _ran(state_dir, "completed", "[STEP] Rolling back to abcdef12\n[STEP] Verifying\n", action="rollback")
        boss.get(UPDATES)
        _ran(state_dir, "running", "[STEP] Fetching\n")

        steps = boss.get(UPDATES).get_json()["progress"]["steps"]

        assert [step["label"] for step in steps] == ["Fetching"]

    def test_what_went_wrong_and_what_was_warned_is_said_with_its_step(self, boss, state_dir):
        _ran(state_dir, "failed", "[STEP] Fetching\n[WARN] CI is slow\n[WARN] still slow\n[WARN] very slow\n"
                                  "[STEP] Installing\npip says no\n[ERR] Python dependencies failed.\n")

        steps = boss.get(UPDATES).get_json()["progress"]["steps"]

        assert steps == [
            {"label": "Fetching", "state": "done", "detail": "CI is slow · still slow (and 1 more)"},
            {"label": "Installing", "state": "failed", "detail": "Python dependencies failed."}]

    def test_a_run_that_failed_before_its_first_step_says_why(self, boss, state_dir):
        _ran(state_dir, "failed", "[ERROR] The update was stopped before it finished (SIGTERM).\n")

        steps = boss.get(UPDATES).get_json()["progress"]["steps"]

        assert steps == [{"label": "Starting the update", "state": "failed",
                          "detail": "The update was stopped before it finished (SIGTERM)."}]

    def test_a_new_request_does_not_show_the_last_runs_steps(self, boss, state_dir):
        _ran(state_dir, "completed", "[STEP] Fetching\n[STEP] Verifying\n")
        boss.get(UPDATES)
        (state_dir / system_update.REQUEST_FILENAME).write_text(json.dumps(
            {"requested_at": "2999-01-01T00:00:00+00:00", "action": "update"}))

        steps = boss.get(UPDATES).get_json()["progress"]["steps"]

        assert [(step["label"], step["state"]) for step in steps] == [
            ("Starting the update", "running"), ("Fetching", "pending"), ("Verifying", "pending")]

    def test_the_whole_output_without_the_terminal_colours(self, boss, state_dir):
        lines = [f"line {number}" for number in range(100)]
        _ran(state_dir, "completed", "\x1b[0;34m[STEP]\x1b[0m Fetching\n" + "\n".join(lines) + "\n")

        body = boss.get(f"{UPDATES}/log").get_json()

        assert body["cut"] is False
        assert body["text"].startswith("[STEP] Fetching\nline 0\n") and "line 99" in body["text"]
        assert boss.get(UPDATES).get_json()["progress"]["steps"][0]["label"] == "Fetching"

    def test_a_very_long_output_is_given_from_its_end(self, boss, state_dir, monkeypatch):
        monkeypatch.setattr(system_update, "LOG_READ_LIMIT", 100)
        _ran(state_dir, "completed", "".join(f"line {number}\n" for number in range(100)))

        body = boss.get(f"{UPDATES}/log").get_json()

        assert body["cut"] is True
        assert body["text"].startswith("line ") and body["text"].rstrip().endswith("line 99")

    def test_no_output_before_the_first_update(self, boss, state_dir):
        assert boss.get(f"{UPDATES}/log").get_json() == {"text": None, "cut": False}

    def test_a_second_one_while_one_runs_is_refused(self, boss, state_dir):
        assert send(boss, "POST", UPDATES, {"action": "update"}).status_code == 202

        response = send(boss, "POST", UPDATES, {"action": "update"})

        assert response.status_code == 409 and response.get_json()["error"]["code"] == "update_already_running"

    def test_without_the_runner_it_says_so(self, boss, tmp_path, monkeypatch):
        monkeypatch.setattr(system_update, "UPDATE_STATE_DIR", tmp_path / "missing")

        response = send(boss, "POST", UPDATES, {"action": "update"})

        assert response.status_code == 503 and response.get_json()["error"]["code"] == "update_runner_missing"

    def test_no_rollback_without_a_point(self, boss, state_dir, tmp_path, monkeypatch):
        monkeypatch.setattr(system_update, "ROLLBACK_FILE", tmp_path / "none.conf")

        response = send(boss, "POST", UPDATES, {"action": "rollback"})

        assert response.status_code == 409 and response.get_json()["error"]["code"] == "no_rollback_point"
