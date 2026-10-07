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


class TestUpdates:
    def test_while_one_runs_how_far_it_is(self, boss, state_dir):
        (state_dir / system_update.STATUS_FILENAME).write_text(json.dumps(
            {"state": "running", "started_at": "2999-01-01T00:00:00+00:00", "steps_expected": 4}))
        (state_dir / system_update.LOG_FILENAME).write_text("[STEP] Fetching\n[STEP] Installing\n")

        body = boss.get(UPDATES).get_json()

        assert body["in_progress"] is True
        assert body["progress"] == {"steps_done": 2, "steps_expected": 4, "percent": 50,
                                    "current_step": "Installing"}
        assert "Installing" in body["last_run"]["log_tail"]

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
