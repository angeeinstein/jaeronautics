"""A server's night for the system (deploy/server-night.sh,
docs/automatic-updates-plan.md, parts 3 and 5): Ubuntu's security updates and
cloudflared at 03:40, a restart at 04:00 only when one is needed, behind one
lock -- on the portal's server, set up by install.sh, and on the forum's, by
hand with the command System health shows.

The script's own functions, run in bash against stand-ins in a folder of the
test's own (as test_install_frontend.py does for install.sh).
"""
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "server-night.sh"


@pytest.fixture
def night(tmp_path):
    """Runs a piece of bash with the script's functions, every path in ``tmp_path``."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.rstrip().endswith('main "$@"')
    lib = tmp_path / "server-night-lib.sh"
    lib.write_text(text.rstrip()[: -len('main "$@"')], encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    for name in ("systemctl", "apt-get", "apt-daily"):
        (bin_dir / name).write_text(f'#!/usr/bin/env bash\necho "{name} $*" >> "{calls}"\nexit ${{EXIT_{name.replace("-", "_").upper()}:-0}}\n')
        (bin_dir / name).chmod(0o755)
    root = tmp_path / "root"

    def run(script, **env):
        full = textwrap.dedent(f"""
            source "{lib}"
            SELF="{SCRIPT}"
            APT_CONF_DIR="{root}/etc/apt/apt.conf.d"
            SYSTEMD_DIR="{root}/etc/systemd/system"
            LIB_DIR="{root}/usr/local/lib"
            STATE_DIR="{root}/var/lib"
            REBOOT_REQUIRED="{root}/run/reboot-required"
            APT_DAILY="{bin_dir}/apt-daily"
            ensure_unattended_upgrades() {{ echo "ensured" >> "{calls}"; }}
        """) + textwrap.dedent(script)
        result = subprocess.run(["bash", "-c", full], capture_output=True, text=True, timeout=30,
                                env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", **env})
        result.calls = calls.read_text().splitlines() if calls.exists() else []
        return result

    run.root = root
    return run


def _install(night, **options):
    args = " ".join(f"--{key} {value}" for key, value in options.items())
    return night(f"""
        NAME=forum
        set -- {args}
        while (( $# )); do case "$1" in --timezone) ZONE="$2" ;; --lock) LOCK="$2" ;; --wait) WAIT_SECONDS="$2" ;; esac; shift 2; done
        paths
        install_night
    """)


class TestSettingUp:
    def test_the_night_and_its_files(self, night):
        result = _install(night, timezone="Europe/Vienna")
        root = night.root

        assert result.returncode == 0, result.stderr
        conf = (root / "etc/apt/apt.conf.d/52forum-unattended-upgrades").read_text()
        assert 'APT::Periodic::Unattended-Upgrade "1";' in conf
        # Not unattended-upgrades' own restart, which could cut an update in half.
        assert 'Unattended-Upgrade::Automatic-Reboot "false";' in conf
        # cloudflared with the security updates, its repository named by its address.
        assert '"site=pkg.cloudflare.com";' in conf
        assert "OnCalendar=*-*-* 03:40 Europe/Vienna" in (
            root / "etc/systemd/system/apt-daily-upgrade.timer.d/50-forum.conf").read_text()
        copy = root / "usr/local/lib/forum/server-night.sh"
        lock = root / "var/lib/forum/night.lock"
        assert f"ExecStart=/usr/bin/flock --wait 3600 {lock} {copy} updates" in (
            root / "etc/systemd/system/apt-daily-upgrade.service.d/50-forum.conf").read_text()
        assert f"/usr/bin/flock --wait 3600 {lock} {copy} restart-if-needed" in (
            root / "etc/systemd/system/forum-restart-if-needed.service").read_text()
        assert "OnCalendar=*-*-* 04:00 Europe/Vienna" in (
            root / "etc/systemd/system/forum-restart-if-needed.timer").read_text()
        assert copy.read_text() == SCRIPT.read_text() and os.access(copy, os.X_OK)
        assert lock.exists()
        assert "systemctl enable forum-restart-if-needed.timer" in result.calls

    def test_a_time_zone_the_system_does_not_know_falls_back_to_its_clock(self, night):
        _install(night, timezone="Mars/Olympus")

        assert "OnCalendar=*-*-* 03:40 UTC" in (
            night.root / "etc/systemd/system/apt-daily-upgrade.timer.d/50-forum.conf").read_text()

    def test_a_lock_that_is_there_is_kept(self, night, tmp_path):
        """Whoever holds or waits for it holds the file: a new one would be a new lock."""
        lock = tmp_path / "portal-night.lock"
        lock.write_text("")
        inode = lock.stat().st_ino

        _install(night, lock=lock, wait=600)

        assert lock.stat().st_ino == inode
        assert f"--wait 600 {lock}" in (
            night.root / "etc/systemd/system/forum-restart-if-needed.service").read_text()

    def test_taken_away_again(self, night, tmp_path):
        _install(night)
        own_lock = night.root / "var/lib/forum/night.lock"
        assert own_lock.exists()

        result = night("NAME=forum; paths; remove_night")

        assert result.returncode == 0, result.stderr
        left = [path for path in night.root.rglob("*") if path.is_file()]
        assert left == []
        assert "systemctl disable --now forum-restart-if-needed.timer" in result.calls

    def test_a_lock_passed_in_stays_when_taken_away(self, night, tmp_path):
        lock = tmp_path / "portal-night.lock"
        lock.write_text("")
        _install(night, lock=lock)

        night(f'NAME=forum; LOCK="{lock}"; paths; remove_night')

        assert lock.exists()


class TestTheNight:
    def test_fresh_lists_then_ubuntus_install_step(self, night):
        result = night("pgrep() { :; }; updates")

        assert result.returncode == 0, result.stderr
        assert result.calls == ["apt-get -q update", "apt-daily install"]

    def test_its_failure_is_the_steps_failure(self, night):
        result = night("pgrep() { :; }; updates", EXIT_APT_DAILY="3")

        assert result.returncode == 3

    def test_cloudflared_restarted_once_updated_and_only_then(self, night):
        result = night("""
            pgrep() { echo 11; echo 12; echo 13; }
            readlink() { case "$1" in
                /proc/11/exe|/proc/12/exe) echo "/usr/bin/cloudflared (deleted)" ;;
                *) echo "/usr/bin/cloudflared" ;; esac; }
            ps() { case "$*" in *" 13") echo " other.service" ;; *) echo " cloudflared.service" ;; esac; }
            updates
        """)

        # Two processes of one service, replaced: one restart. The one still current: none.
        assert [call for call in result.calls if call.startswith("systemctl")] == [
            "systemctl restart cloudflared.service"]
        assert "cloudflared was updated: restarting cloudflared.service." in result.stdout

    def test_a_restart_only_when_an_update_needs_one(self, night):
        assert "systemctl reboot" not in night("restart_if_needed").calls

        (night.root / "run").mkdir(parents=True)
        (night.root / "run/reboot-required").write_text("*** System restart required ***\n")
        assert "systemctl reboot" in night("restart_if_needed").calls


def test_names_it_refuses(night):
    result = night('main install --name "../etc"')

    assert result.returncode != 0
