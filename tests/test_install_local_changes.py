"""The update's warning of local changes (install.sh, warn_local_repo_changes):
only where the update discards something -- the portal's own files, which it
resets --, not for files git does not track, which it leaves where they are.
"""
import subprocess

from test_install_frontend import lib, run, stubs  # noqa: F401 -- fixtures


def _checkout(tmp_path):
    folder = tmp_path / "portal"
    folder.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-C", str(folder)]
    subprocess.run([*git, "init", "-q"], check=True)
    (folder / "app.py").write_text("print('portal')\n")
    subprocess.run([*git, "add", "app.py"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "first"], check=True)
    return folder


def _warning(lib, stubs, tmp_path, folder):  # noqa: F811
    result = run(lib, stubs, f'cleanup_legacy_repo_backups() {{ :; }}; warn_local_repo_changes "{folder}" 2>&1',
                 HOME=str(tmp_path))
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_files_git_does_not_track_are_nothing_to_warn_of(lib, stubs, tmp_path):  # noqa: F811
    folder = _checkout(tmp_path)
    # What showed on the live server: less's history, npm's cache.
    (folder / ".lesshst").write_text("x")
    (folder / ".npm").mkdir()
    (folder / ".npm" / "index").write_text("x")

    assert _warning(lib, stubs, tmp_path, folder) == ""


def test_a_changed_file_of_the_portal_is(lib, stubs, tmp_path):  # noqa: F811
    folder = _checkout(tmp_path)
    (folder / "app.py").write_text("print('changed by hand')\n")

    assert "The portal's own files were changed" in _warning(lib, stubs, tmp_path, folder)
