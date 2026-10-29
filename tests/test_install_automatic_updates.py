"""The installer's part of updates without a person (docs/automatic-updates-plan.md):
the night's steps one at a time behind one lock, in the portal's time zone --
billing, forum, log cleanup, the automatic-update check and the update, Ubuntu's
updates, a restart only if one needs it --, automatic updates switched on for a
new installation only.

install.sh's own functions, run in bash against stand-ins (test_install_frontend.py),
and the update runner itself.
"""
import os
import re
import signal
import subprocess
import time

from aeronautics_members.services import automatic_updates
from test_install_frontend import INSTALLER, lib, run, stubs  # noqa: F401 -- fixtures

RUNNER = INSTALLER.parent / "deploy" / "update-runner.sh"


def _function(name):
    text = INSTALLER.read_text(encoding="utf-8")
    start = text.index(f"\n{name}() {{") + 1
    following = re.search(r"\n[a-z_]+\(\) \{", text[start + 1:])
    return text[start: start + 1 + following.start()] if following else text[start:]


# --- When ------------------------------------------------------------------------------


def test_the_night_runs_in_the_portals_time_zone(lib, stubs):  # noqa: F811
    result = run(lib, stubs, "night_at 03:15", MEMBERSHIP_TIMEZONE="Europe/Vienna")

    assert result.stdout == "*-*-* 03:15 Europe/Vienna"


def test_a_time_zone_the_system_does_not_know_falls_back_to_its_clock(lib, stubs):  # noqa: F811
    # A timer naming an unknown zone would not start at all.
    result = run(lib, stubs, "night_at 03:15", MEMBERSHIP_TIMEZONE="Mars/Olympus")

    assert result.stdout == "*-*-* 03:15 UTC"


def test_the_order_of_the_night():
    text = INSTALLER.read_text(encoding="utf-8")
    at = {name: re.search(r"OnCalendar=\$\(night_at (\d\d:\d\d)\)", _function(name)).group(1)
          for name in ("render_billing_reconcile_timer_files", "render_forum_drift_timer_files",
                       "render_automatic_updates_timer_files")}

    # From 03:15: the clock changes between 02:00 and 03:00, and 03:15 ends an attendance day.
    assert at == {"render_billing_reconcile_timer_files": "03:15", "render_forum_drift_timer_files": "03:20",
                  "render_automatic_updates_timer_files": automatic_updates.AT}
    assert "OnCalendar=*-*-01 03:25:00 $(night_timezone)" in text
    assert "OnCalendar=$(night_at 03:40)" in text and "OnCalendar=$(night_at 04:00)" in text


# --- One at a time -----------------------------------------------------------------------


def test_every_nightly_job_queues_behind_the_lock_and_the_frequent_ones_do_not():
    for name in ("render_billing_reconcile_timer_files", "render_forum_drift_timer_files",
                 "render_cleanup_logs_timer_files", "render_automatic_updates_timer_files"):
        assert "ExecStart=$(night_queued " in _function(name), name
    for name in ("render_notifications_timer_files", "render_external_work_timer_files"):
        assert "night_queued" not in _function(name), name


def test_a_queued_command_waits_for_the_lock_then_has_its_own_time(lib, stubs):  # noqa: F811
    result = run(lib, stubs, 'NIGHT_LOCK=/x/night.lock; NIGHT_WAIT_SECONDS=3600; night_queued 180 "/bin/job --now"')

    assert result.stdout == "/usr/bin/flock --wait 3600 /x/night.lock /usr/bin/timeout 180 /bin/job --now"


def _runner(tmp_path, wait_seconds):
    state_dir = tmp_path / "updates"
    state_dir.mkdir(exist_ok=True)
    (state_dir / "request.json").write_text('{"requested_at": "x", "action": "update"}')
    command = tmp_path / "fake-update"
    command.write_text('#!/usr/bin/env bash\necho "UPDATED NIGHT_LOCK_HELD=${NIGHT_LOCK_HELD:-0}"\n')
    command.chmod(0o755)
    lock = tmp_path / "night.lock"
    lock.touch()
    return state_dir, lock, {
        **os.environ, "UPDATE_STATE_DIR": str(state_dir), "UPDATE_COMMAND": str(command),
        "UPDATE_RUNNER_DIR": str(tmp_path / "update-runner"), "INSTALL_DIR": str(state_dir),
        "UPDATE_TIMEOUT": "120", "NIGHT_LOCK": str(lock), "NIGHT_WAIT_SECONDS": str(wait_seconds),
    }


def test_the_update_waits_for_a_nightly_job_still_at_work(tmp_path):
    _state, lock, env = _runner(tmp_path, wait_seconds=30)
    job = subprocess.Popen(["flock", str(lock), "sleep", "2"])
    time.sleep(0.3)
    started = time.monotonic()

    subprocess.run(["bash", str(RUNNER)], timeout=60, start_new_session=True, env=env)

    log = (tmp_path / "update-runner" / "last-run.log").read_text()
    assert "Waiting for the night's other work to finish first." in log
    # After the job, and telling the installer it holds the lock.
    assert "UPDATED NIGHT_LOCK_HELD=1" in log and time.monotonic() - started >= 1.5
    job.wait()


def test_one_that_waits_too_long_gives_up_without_starting(tmp_path):
    _state, lock, env = _runner(tmp_path, wait_seconds=1)
    job = subprocess.Popen(["flock", str(lock), "sleep", "5"])
    time.sleep(0.3)

    subprocess.run(["bash", str(RUNNER)], timeout=60, start_new_session=True, env=env)

    log = (tmp_path / "update-runner" / "last-run.log").read_text()
    assert "UPDATED" not in log and "not started" in log
    assert '"state": "failed"' in (tmp_path / "update-runner" / "status.json").read_text()
    job.kill()


def test_a_crashed_job_leaves_nothing_locked(tmp_path):
    """The lock is the kernel's, on the open file: it goes with the process."""
    _state, lock, env = _runner(tmp_path, wait_seconds=30)
    job = subprocess.Popen(["flock", str(lock), "sleep", "60"], start_new_session=True)
    time.sleep(0.3)
    # SIGKILL to flock and the job under it, no clean-up: as systemd ends a
    # service's whole group when it crashes or runs out of time.
    os.killpg(job.pid, signal.SIGKILL)
    job.wait()
    time.sleep(0.3)

    subprocess.run(["bash", str(RUNNER)], timeout=60, start_new_session=True, env=env)

    log = (tmp_path / "update-runner" / "last-run.log").read_text()
    assert "Waiting" not in log and "UPDATED" in log


def test_an_update_under_the_lock_does_not_wait_for_the_jobs_queued_behind_it(lib, stubs):  # noqa: F811
    script = """
BACKGROUND_JOBS=(billing-reconcile notifications)
BACKGROUND_JOB_WAIT_SECONDS=2
SERVICE_NAME=portal
background_job_running() { return 0; }
pause_background_jobs 2>&1
"""
    under_lock = run(lib, stubs, script, NIGHT_LOCK_HELD="1").stdout
    without = run(lib, stubs, script).stdout

    # The billing check is queued behind the update; email delivery is at work.
    assert "portal-billing-reconcile.service" not in under_lock
    assert "portal-notifications.service" in under_lock
    assert "portal-billing-reconcile.service" in without


# --- Ubuntu ------------------------------------------------------------------------------


def test_ubuntus_updates_join_the_night_and_the_restart_waits_its_turn(lib, stubs, tmp_path):  # noqa: F811
    paths = {name: tmp_path / name for name in ("unattended", "timer.d/50.conf", "service.d/50.conf",
                                                 "restart.service", "restart.timer")}
    result = run(lib, stubs, f"""
install_packages() {{ echo "installed $*"; }}
PACKAGE_MANAGER=apt
SERVICE_NAME=portal
NIGHT_LOCK=/var/lib/x/night.lock
UNATTENDED_UPGRADES_FILE="{paths['unattended']}"
APT_UPGRADE_TIMER_DROPIN="{paths['timer.d/50.conf']}"
APT_UPGRADE_SERVICE_DROPIN="{paths['service.d/50.conf']}"
RESTART_IF_NEEDED_SERVICE_FILE="{paths['restart.service']}"
RESTART_IF_NEEDED_TIMER_FILE="{paths['restart.timer']}"
configure_unattended_upgrades
""", MEMBERSHIP_TIMEZONE="Europe/Vienna")

    assert result.returncode == 0, result.stderr
    assert "installed unattended-upgrades" in result.stdout
    settings = paths["unattended"].read_text()
    assert 'APT::Periodic::Unattended-Upgrade "1";' in settings
    # Not unattended-upgrades' own restart, which could cut an update in half.
    assert 'Unattended-Upgrade::Automatic-Reboot "false";' in settings
    assert "OnCalendar=*-*-* 03:40 Europe/Vienna" in paths["timer.d/50.conf"].read_text()
    assert "flock --wait 3600 /var/lib/x/night.lock /usr/lib/apt/apt.systemd.daily install" in (
        paths["service.d/50.conf"].read_text())
    restart = paths["restart.service"].read_text()
    assert "flock --wait 3600 /var/lib/x/night.lock" in restart and "/run/reboot-required" in restart
    assert "OnCalendar=*-*-* 04:00 Europe/Vienna" in paths["restart.timer"].read_text()


def test_left_to_the_system_where_it_is_not_apt(lib, stubs, tmp_path):  # noqa: F811
    config = tmp_path / "52portal-unattended-upgrades"

    result = run(lib, stubs, f"""
PACKAGE_MANAGER=dnf
UNATTENDED_UPGRADES_FILE="{config}"
configure_unattended_upgrades
""")

    assert result.returncode == 0 and not config.exists()


# --- Switched on, taken away ---------------------------------------------------------------


def test_the_portals_check_runs_by_itself_and_never_makes_a_missed_night_up():
    body = _function("render_automatic_updates_timer_files")

    assert "automatic-updates run" in body and "Persistent=false" in body


def test_switched_on_for_a_new_installation_only():
    body = _function("install_or_update")
    new_installation = body[body.index("        initialize_database_schema"):]
    new_installation = new_installation[: new_installation.index("\n    fi\n")]

    # Not on an update, and not for a database moved from elsewhere: it keeps its own.
    assert "switch_on_automatic_updates" in new_installation
    assert 'MIGRATE_DB_DATA}" != "1"' in new_installation
    assert body.count("switch_on_automatic_updates") == 1


def test_uninstall_takes_it_all_away():
    body = _function("uninstall_everything")

    assert "automatic-updates.timer" in body and "restart-if-needed.timer" in body
    assert '"${UNATTENDED_UPGRADES_FILE}" "${APT_UPGRADE_TIMER_DROPIN}" "${APT_UPGRADE_SERVICE_DROPIN}"' in body
