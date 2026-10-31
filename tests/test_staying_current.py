"""Staying current, and telling a person only when something is stuck
(services/staying_current.py, docs/automatic-updates-plan.md, part 4): what
counts as stuck, Ubuntu read without root, and the email -- once, to whoever
may install updates.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import db
from aeronautics_members.db_models import NotificationEvent
from aeronautics_members.notification_service import NotificationService
from aeronautics_members.services import automatic_updates, staying_current, system_update
from test_admin_reviews import _staff

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


def _parts(**changes):
    """Everything as it should be; ``changes`` replaces a part, or fields of one."""
    parts = {
        "portal": {"newer_version": False, "newest": None, "newest_failed": False, "newest_checks_url": None,
                   "automatic": True, "automatic_since": NOW - timedelta(days=30),
                   "last_check_at": NOW - timedelta(hours=6)},
        "ubuntu": {"name": "Ubuntu 24.04 LTS", "codename": "noble", "supported_until": date(2029, 5, 31),
                   "updates_installed_at": NOW - timedelta(hours=8), "restart_pending_since": None,
                   "restart_packages": []},
        "node": {"major": "24", "supported_until": date(2028, 4, 30)},
        "waiting": [],
    }
    for name, value in changes.items():
        parts[name] = {**parts[name], **value} if isinstance(value, dict) and parts[name] else value
    return parts


def _keys(**changes):
    return [item["key"] for item in staying_current.stuck(NOW, parts=_parts(**changes))]


class TestWhatIsStuck:
    def test_all_well_is_nothing(self, app):
        assert _keys() == []

    def test_an_update_waiting_for_a_person_over_two_weeks(self, app):
        waiting = [{"number": 51, "title": "Bump typescript to 7.0.2", "url": "https://github.com/o/r/pull/51",
                    "days": 15}]

        [item] = staying_current.stuck(NOW, parts=_parts(waiting=waiting))

        assert item["key"] == "waiting:51" and item["link"].endswith("/pull/51")
        assert "15 days" in item["text"] and "typescript" in item["text"]

    def test_the_newest_version_failed_its_checks(self, app):
        assert _keys(portal={"newer_version": True, "newest": "abcd1234", "newest_failed": True}) == [
            "newest_failed:abcd1234"]

    def test_the_night_has_not_checked_for_over_a_day(self, app):
        assert _keys(portal={"last_check_at": NOW - timedelta(hours=27)}) == ["check_missing"]
        # Counted from switching on, until the first check.
        assert _keys(portal={"last_check_at": None, "automatic_since": NOW - timedelta(days=2)}) == [
            "check_missing"]
        assert _keys(portal={"automatic": False, "last_check_at": NOW - timedelta(days=9)}) == []

    def test_ubuntu_not_updating_or_a_restart_waiting(self, app):
        assert _keys(ubuntu={"updates_installed_at": NOW - timedelta(days=4)}) == ["ubuntu_updates"]
        assert _keys(ubuntu={"restart_pending_since": NOW - timedelta(days=4)}) == ["restart"]
        assert _keys(ubuntu={"restart_pending_since": NOW - timedelta(days=1)}) == []

    def test_support_ending_within_a_year(self, app):
        assert _keys(ubuntu={"supported_until": date(2027, 10, 1)}) == ["support:ubuntu:noble"]
        assert _keys(node={"supported_until": date(2027, 4, 30)}) == ["support:node:24"]


class TestReadWithoutRoot:
    def test_ubuntus_own_files(self, app, tmp_path, monkeypatch):
        (tmp_path / "os-release").write_text('ID=ubuntu\nPRETTY_NAME="Ubuntu 24.04.3 LTS"\nVERSION_CODENAME=noble\n')
        (tmp_path / "ubuntu.csv").write_text(
            "version,codename,series,created,release,eol,eol-server,eol-esm\n"
            "24.04 LTS,Noble Numbat,noble,2023-10-12,2024-04-25,2029-05-31,2029-05-31,2034-04-25\n")
        (tmp_path / "upgrade-stamp").touch()
        (tmp_path / "reboot-required").touch()
        (tmp_path / "reboot-required.pkgs").write_text("linux-image-6.8.0-90-generic\nlinux-base\n")
        for name, file in (("OS_RELEASE", "os-release"), ("DISTRO_INFO", "ubuntu.csv"),
                           ("UPGRADE_STAMP", "upgrade-stamp"), ("REBOOT_REQUIRED", "reboot-required"),
                           ("REBOOT_PACKAGES", "reboot-required.pkgs")):
            monkeypatch.setattr(staying_current, name, tmp_path / file)

        system = staying_current.ubuntu()

        assert system["name"] == "Ubuntu 24.04.3 LTS" and system["supported_until"] == date(2029, 5, 31)
        assert system["updates_installed_at"] is not None and system["restart_pending_since"] is not None
        assert system["restart_packages"] == ["linux-base", "linux-image-6.8.0-90-generic"]

    def test_not_ubuntu_is_nothing_to_say(self, app, tmp_path, monkeypatch):
        (tmp_path / "os-release").write_text("ID=fedora\n")
        monkeypatch.setattr(staying_current, "OS_RELEASE", tmp_path / "os-release")

        assert staying_current.ubuntu() is None

    def test_the_node_that_builds_the_front_end(self, app):
        assert staying_current.node() == {"major": "24", "supported_until": date(2028, 4, 30)}


class TestWaitingForAPerson:
    def test_only_dependabots_and_only_over_two_weeks(self, app, monkeypatch):
        app.config["UPDATE_CI_CHECK"] = True
        monkeypatch.setattr(system_update, "github_slug", lambda: "o/r")
        monkeypatch.setattr(system_update, "_github_json", lambda path: [
            {"number": 51, "title": "Bump typescript", "html_url": "u51", "user": {"login": "dependabot[bot]"},
             "created_at": "2026-10-01T10:00:00Z"},
            {"number": 60, "title": "Bump certifi", "html_url": "u60", "user": {"login": "dependabot[bot]"},
             "created_at": "2026-11-01T10:00:00Z"},
            {"number": 61, "title": "A feature", "html_url": "u61", "user": {"login": "someone"},
             "created_at": "2026-09-01T10:00:00Z"},
        ])
        staying_current._waiting_cache.update(at=None, value=None)

        assert staying_current.waiting_updates(NOW) == [
            {"number": 51, "title": "Bump typescript", "url": "u51", "days": 31}]


@pytest.fixture
def stuck_now(app, monkeypatch):
    """What is stuck, as the test says: ``stuck_now.items``."""
    class Stuck:
        items = [{"key": "restart", "text": "An update has waited 4 days for the server to restart.", "link": None}]

    monkeypatch.setattr(staying_current, "stuck", lambda now=None, parts=None: Stuck.items)
    return Stuck


def _emails():
    return db.session.query(NotificationEvent).filter_by(event_type=staying_current.EMAIL_KIND).all()


def _verified(email, role):
    user = _staff(email, role)
    user.email_verified_at = NOW
    db.session.commit()
    return user


class TestToldOnce:
    def test_to_whoever_may_install_updates_only(self, stuck_now):
        _verified("boss@example.org", "superadmin")
        _verified("helper@example.org", "admin")

        assert staying_current.tell(NOW) == 1
        db.session.commit()

        assert [event.recipient_email for event in _emails()] == ["boss@example.org"]
        assert _emails()[0].payload["items"] == ["An update has waited 4 days for the server to restart."]

    def test_once_then_again_only_after_it_resolved_and_came_back(self, stuck_now):
        _verified("boss@example.org", "superadmin")
        staying_current.tell(NOW)
        staying_current.tell(NOW + timedelta(hours=2))
        assert len(_emails()) == 1

        stuck_now.items = []
        staying_current.tell(NOW + timedelta(hours=4))
        stuck_now.items = [{"key": "restart", "text": "Again.", "link": None}]
        staying_current.tell(NOW + timedelta(hours=6))

        assert len(_emails()) == 2

    def test_asked_at_most_once_an_hour(self, stuck_now):
        _verified("boss@example.org", "superadmin")
        stuck_now.items = []
        staying_current.tell(NOW)
        stuck_now.items = [{"key": "restart", "text": "Waiting.", "link": None}]

        assert staying_current.tell(NOW + timedelta(minutes=20)) == 0
        assert staying_current.tell(NOW + timedelta(minutes=61)) == 1

    def test_the_email_says_what_and_where(self, app):
        event = NotificationEvent(event_type=staying_current.EMAIL_KIND, payload={"items": ["Waiting."]})

        subject, values = NotificationService()._build_user_status_message(event)

        assert subject == "Updates need a person" and "Waiting." in values["body_lines"]
        assert values["action_url"].endswith("/admin/settings/health")


def test_switching_on_is_remembered_for_the_missing_check(app):
    automatic_updates.set_enabled(True)
    db.session.commit()

    assert automatic_updates.enabled_since() is not None
    automatic_updates.set_enabled(False)
    assert automatic_updates.enabled_since() is None
