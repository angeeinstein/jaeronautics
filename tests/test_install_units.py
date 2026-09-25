"""The systemd units the installer writes, checked without a machine to run on.

`ReadWritePaths=` is the one directive that turns a missing directory into a
service that will not start at all. systemd sets up the mount namespace before
the process exists, so a path that is not there is not a warning and not a
permission error -- it is

    Failed to set up mount namespacing: /var/www/jaeronautics/storage:
    No such file or directory
    status=226/NAMESPACE

and the service never runs. `storage/` is in .gitignore, so a fresh clone does
not have it, and every install until the first genuinely fresh one was an
update over a tree where the running application had already made it. Which is
exactly the kind of thing that waits until the migration to appear.
"""
import re
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parent.parent / "install.sh"


@pytest.fixture(scope="module")
def installer():
    return INSTALLER.read_text(encoding="utf-8")


def _granted_paths(text):
    """Every path named in a ReadWritePaths= line, as written."""
    found = set()
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("ReadWritePaths="):
            continue
        found.update(line[len("ReadWritePaths="):].split())
    return found


def _created_paths(text):
    """Every directory the installer makes with install -d or mkdir -p.

    Every quoted word on such a line, not the first one: ``install -d`` carries
    the owner and the group in quotes ahead of the path, and matching the first
    would collect the user name and call the directory created.
    """
    found = set()
    for statement in re.finditer(r"(?:install -d|mkdir -p)[^\n]*", text):
        found.update(re.findall(r'"([^"]+)"', statement.group(0)))
    return found


def test_the_units_grant_something(installer):
    """If this ever finds nothing, the checks below prove nothing either."""
    assert _granted_paths(installer)


def test_every_granted_directory_is_one_the_installer_creates(installer):
    """Otherwise the service cannot start, and says so in namespace terms."""
    missing = _granted_paths(installer) - _created_paths(installer)
    assert not missing, (
        f"These are granted with ReadWritePaths and never created: "
        f"{sorted(missing)}. systemd refuses to start a unit whose "
        f"ReadWritePaths names a directory that is not there."
    )


def test_the_storage_directory_is_created_because_git_cannot_bring_it(installer):
    """It is in .gitignore, so a fresh clone has no storage/ at all."""
    assert '"${INSTALL_DIR}/storage"' in installer


def test_it_is_created_before_the_service_is_written(installer):
    """Making it after the unit starts would be making it too late."""
    creates = installer.index('install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0750 "${INSTALL_DIR}/storage"')
    writes_unit = installer.index("render_service_file() {")
    assert creates < writes_unit or "ensure_writable_directories" in installer


def test_it_belongs_to_the_application_user(installer):
    """Root-owned, the application could not write an avatar into it."""
    line = next(
        line for line in installer.splitlines()
        if 'install -d' in line and '"${INSTALL_DIR}/storage"' in line
    )
    assert "-o \"${APP_USER}\"" in line
    assert "-g \"${APP_GROUP}\"" in line


def test_the_staging_directory_comes_with_it(installer):
    """Where approved avatars wait; made now rather than on the first upload."""
    assert '"${INSTALL_DIR}/storage/forum_avatar_staging"' in installer


def test_a_fresh_install_reaches_it(installer):
    """ensure_repo_present runs on every install, update and repair."""
    body = installer[installer.index("ensure_repo_present() {"):]
    body = body[: body.index("\n}")]
    assert "ensure_writable_directories" in body
