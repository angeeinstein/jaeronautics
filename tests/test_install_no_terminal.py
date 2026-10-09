"""An update started from the admin page has no terminal: it must never stop
to ask anything.

Seen live (2026-10-09): the old first admin account was erased, the only one
left had the super admin role, and the installer -- counting only "admin" --
offered to create an initial admin account. Its prompt went to /dev/tty, which
passes a permission check but cannot be opened without a controlling
terminal, and the update ended after the migration, with the old code still
running.
"""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "install.sh"
RUNNER = ROOT / "deploy" / "update-runner.sh"


@pytest.fixture
def lib(tmp_path):
    """install.sh without its last line (main "$@"), to source."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert '\nmain "$@"\n' in text
    path = tmp_path / "installer-lib.sh"
    path.write_text(text.replace('\nmain "$@"\n', "\n"), encoding="utf-8")
    return path


def _bash(lib, script, **env):
    """Run ``script`` after sourcing the installer, in a session of its own: no
    controlling terminal, as under the update runner."""
    return subprocess.run(
        ["bash", "-c", f'source "{lib}"\n{script}'],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
        env={**os.environ, **env}, start_new_session=True,
    )


def test_no_terminal_is_no_terminal(lib):
    result = _bash(lib, "has_tty && echo yes || echo no")
    assert result.stdout.strip() == "no"


def test_a_question_without_a_terminal_takes_its_default(lib):
    result = _bash(lib, 'prompt_yes_no choice "Go on?" 1; echo "choice=${choice}"')
    assert result.returncode == 0, result.stderr
    assert "choice=1" in result.stdout


def test_an_update_never_offers_to_create_an_admin(lib):
    script = """
        MODE=update NONINTERACTIVE=0 ADMIN_EMAIL="" SKIP_ADMIN_ACCOUNT=0
        count_admin_accounts() { echo 0; }
        prompt_yes_no() { echo "ASKED"; }
        prompt_value() { echo "ASKED"; }
        ensure_admin_account
        echo done
    """
    result = _bash(lib, script)
    assert result.returncode == 0, result.stderr
    assert "ASKED" not in result.stdout and "done" in result.stdout
    assert "No admin account exists yet" in result.stderr + result.stdout


def test_a_super_admin_counts_as_an_admin():
    assert "Role.slug.in_(('admin', 'superadmin'))" in INSTALLER.read_text(encoding="utf-8")


def test_the_runner_says_nobody_is_there_to_answer(tmp_path):
    state_dir = tmp_path / "updates"
    state_dir.mkdir()
    command = tmp_path / "fake-update"
    command.write_text('#!/usr/bin/env bash\necho "ARGS: $*"\n')
    command.chmod(0o755)
    for action, expected in (("update", "ARGS: --yes"), ("rollback", "ARGS: --rollback --yes")):
        (state_dir / "request.json").write_text(f'{{"requested_at": "x", "action": "{action}"}}')
        subprocess.run(
            ["bash", str(RUNNER)], timeout=60, start_new_session=True,
            env={**os.environ, "UPDATE_STATE_DIR": str(state_dir), "UPDATE_COMMAND": str(command),
                 "INSTALL_DIR": str(state_dir), "UPDATE_TIMEOUT": "120"},
        )
        assert expected in (state_dir / "last-run.log").read_text()
