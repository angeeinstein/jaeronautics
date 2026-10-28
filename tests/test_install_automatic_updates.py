"""The installer's part of updates without a person (docs/automatic-updates-plan.md):
the portal's nightly job at 04:30, switched on for a new installation only,
and Ubuntu's security updates with a restart at 04:00 when one needs it.

install.sh's own functions, run in bash against stand-ins (test_install_frontend.py).
"""
import re

from test_install_frontend import INSTALLER, lib, run, stubs  # noqa: F401 -- fixtures


def _function(name):
    text = INSTALLER.read_text(encoding="utf-8")
    start = text.index(f"\n{name}() {{") + 1
    following = re.search(r"\n[a-z_]+\(\) \{", text[start + 1:])
    return text[start: start + 1 + following.start()] if following else text[start:]


def test_the_system_updates_itself_and_restarts_at_four(lib, stubs, tmp_path):  # noqa: F811
    config = tmp_path / "52portal-unattended-upgrades"

    result = run(lib, stubs, f"""
install_packages() {{ echo "installed $*"; }}
PACKAGE_MANAGER=apt
UNATTENDED_UPGRADES_FILE="{config}"
configure_unattended_upgrades
""")

    assert result.returncode == 0, result.stderr
    assert "installed unattended-upgrades" in result.stdout
    text = config.read_text()
    assert 'APT::Periodic::Unattended-Upgrade "1";' in text
    assert 'Unattended-Upgrade::Automatic-Reboot "true";' in text
    assert 'Unattended-Upgrade::Automatic-Reboot-Time "04:00";' in text
    assert "enable --now unattended-upgrades" in (stubs[1] / "systemctl").read_text()


def test_left_to_the_system_where_it_is_not_apt(lib, stubs, tmp_path):  # noqa: F811
    config = tmp_path / "52portal-unattended-upgrades"

    result = run(lib, stubs, f"""
PACKAGE_MANAGER=dnf
UNATTENDED_UPGRADES_FILE="{config}"
configure_unattended_upgrades
""")

    assert result.returncode == 0 and not config.exists()


def test_the_portals_job_runs_at_half_past_four_and_never_makes_a_missed_night_up():
    body = _function("render_automatic_updates_timer_files")

    assert "automatic-updates run" in body
    assert "OnCalendar=*-*-* 04:30:00" in body and "Persistent=false" in body


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

    assert "automatic-updates.timer" in body and '"${UNATTENDED_UPGRADES_FILE}"' in body
