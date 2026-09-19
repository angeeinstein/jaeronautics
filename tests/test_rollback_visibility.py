"""The rollback panel has to be able to appear.

It never could: install.sh wrote the rollback point as root with mode 0600, and
the web process runs unprivileged, so reading it raised PermissionError. That
was handled in the same branch as "file does not exist" -- the normal state
before the first update -- so the page simply showed nothing, and the feature
looked absent rather than broken.
"""
import re
from pathlib import Path

import pytest

from conftest import app_module  # noqa: F401  (ensures config is loaded)
from aeronautics_members.services import system_update

REPO = Path(__file__).resolve().parent.parent
INSTALLER = (REPO / "install.sh").read_text()
SETTINGS_TEMPLATE = (REPO / "aeronautics_members" / "templates" / "admin_settings.html").read_text()


class TestTheFileIsReadableByTheApp:
    def test_the_rollback_point_is_not_left_root_only(self):
        block = re.search(r"record_rollback_point\(\) \{(.*?)\n\}", INSTALLER, re.S)
        assert block, "record_rollback_point not found"
        body = block.group(1)
        assert "chmod 600" not in body, "0600 makes it unreadable by the web process"
        assert "chmod 640" in body
        assert "APP_GROUP" in body, "it must be group-readable by the application user"

    def test_the_installer_still_records_a_rollback_point_on_update(self):
        assert "record_rollback_point" in INSTALLER
        assert INSTALLER.count("record_rollback_point") >= 2, "defined and called"


class TestAnUnreadableFileIsReported:
    def test_a_missing_file_is_silent(self, app, tmp_path, monkeypatch):
        monkeypatch.setattr(system_update, "ROLLBACK_FILE", tmp_path / "absent.conf")
        with app.test_request_context("/"):
            assert system_update.read_rollback_point() is None

    def test_an_unreadable_file_is_logged(self, app, tmp_path, monkeypatch, caplog):
        """Silence here is what hid the bug; it must say something."""
        class Unreadable:
            def read_text(self):
                raise PermissionError(13, "Permission denied")

            def __str__(self):
                return "/etc/jaeronautics/rollback.conf"

        monkeypatch.setattr(system_update, "ROLLBACK_FILE", Unreadable())
        with app.test_request_context("/"):
            with caplog.at_level("WARNING"):
                assert system_update.read_rollback_point() is None

        assert any("not readable" in r.message or "not readable" in r.getMessage()
                   for r in caplog.records), caplog.text

    def test_a_readable_file_is_parsed(self, app, tmp_path, monkeypatch):
        point = tmp_path / "rollback.conf"
        point.write_text(
            'ROLLBACK_REVISION="' + "a" * 40 + '"\n'
            'ROLLBACK_SCHEMA_REVISION="e81a47c2f905"\n'
            'ROLLBACK_DB_BACKUP="/var/backups/jaeronautics/db.sql.gz"\n'
            'ROLLBACK_RECORDED_AT="2026-09-19T13:10:19+00:00"\n'
        )
        monkeypatch.setattr(system_update, "ROLLBACK_FILE", point)
        with app.test_request_context("/"):
            described = system_update.read_rollback_point()

        assert described["short_revision"] == "a" * 8
        assert described["database_backup"].endswith("db.sql.gz")


class TestThePageExplainsItsAbsence:
    def test_the_template_has_an_else_branch(self):
        """Otherwise 'no panel' means both 'not yet' and 'broken'."""
        assert "No rollback point has been recorded yet" in SETTINGS_TEMPLATE

    @pytest.mark.parametrize("marker", ["rollback_point", "action\" value=\"rollback"])
    def test_the_panel_is_still_wired_up(self, marker):
        assert marker in SETTINGS_TEMPLATE
