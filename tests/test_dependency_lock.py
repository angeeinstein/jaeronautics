"""The dependency lock files, and the things that can quietly invalidate them.

``requirements.txt`` states what the application asks for; the lock files state
exactly what gets installed, transitive dependencies included, with a hash for
every download. That only holds as long as the three files agree, and nothing
about editing ``requirements.txt`` forces anyone to regenerate the locks -- so
the checks that matter here are the ones that catch a lock that has silently
stopped describing the install.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_INPUT = REPO_ROOT / "requirements.txt"
DEV_INPUT = REPO_ROOT / "requirements-dev.txt"
RUNTIME_LOCK = REPO_ROOT / "requirements.lock"
DEV_LOCK = REPO_ROOT / "requirements-dev.lock"

# "flask-babel==4.0.0 \" at the start of a line; everything else in the file is
# a continuation (--hash=...) or a comment.
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)")


def normalise(name):
    """Compare names the way an index does: case- and separator-insensitive."""
    return re.sub(r"[-_.]+", "-", name).lower()


def read_pins(path):
    """Map package name to version for every top-level pin in a file."""
    pins = {}
    for line in path.read_text().splitlines():
        if line.startswith((" ", "\t", "#")):
            continue
        match = PIN.match(line.strip())
        if match:
            pins[normalise(match.group(1))] = match.group(2)
    return pins


def read_lock_entries(path):
    """Map package name to (version, [hashes]) for a lock file."""
    entries = {}
    current = None
    for raw in path.read_text().splitlines():
        # Entries wrap over several lines, each continued with a trailing "\".
        line = raw.strip().rstrip("\\").strip()
        if not line or line.startswith("#"):
            continue
        match = PIN.match(line)
        if match and not raw.startswith((" ", "\t")):
            current = normalise(match.group(1))
            entries[current] = (match.group(2), [])
        elif line.startswith("--hash=") and current:
            entries[current][1].append(line.split("=", 1)[1])
    return entries


@pytest.fixture(scope="module", params=[RUNTIME_LOCK, DEV_LOCK], ids=["runtime", "dev"])
def lock(request):
    return request.param


class TestLocksExist:
    def test_both_lock_files_are_committed(self, lock):
        assert lock.exists(), f"{lock.name} is missing; regenerate it with pip-compile"

    def test_lock_explains_how_to_regenerate_it(self, lock):
        # A lock file nobody knows how to rebuild becomes a lock file nobody
        # rebuilds, and then an install that no longer matches the source.
        assert "pip-compile" in lock.read_text()


class TestHashesArePresent:
    def test_every_pin_carries_at_least_one_hash(self, lock):
        """Without hashes the pin is a version number, not a guarantee.

        ``pip install --require-hashes`` refuses the whole file if a single
        entry is missing one, so an unhashed pin does not weaken the install
        quietly -- it breaks it. Catching it here is cheaper than catching it
        during a deploy.
        """
        unhashed = [name for name, (_, hashes) in read_lock_entries(lock).items() if not hashes]
        assert not unhashed, f"no hash recorded for: {sorted(unhashed)}"

    def test_hashes_are_sha256(self, lock):
        for name, (_, hashes) in read_lock_entries(lock).items():
            for digest in hashes:
                assert digest.startswith("sha256:"), f"{name} uses a non-sha256 hash: {digest}"
                assert len(digest) == len("sha256:") + 64, f"{name} has a truncated hash"


class TestLocksMatchTheirInputs:
    """The failure this guards against: edit requirements.txt, forget the lock."""

    def test_runtime_lock_covers_every_declared_requirement(self):
        declared = read_pins(RUNTIME_INPUT)
        locked = read_lock_entries(RUNTIME_LOCK)

        missing = sorted(set(declared) - set(locked))
        assert not missing, (
            f"requirements.txt names packages the lock does not: {missing}. "
            "Regenerate requirements.lock and requirements-dev.lock."
        )

    def test_runtime_lock_pins_the_declared_versions(self):
        declared = read_pins(RUNTIME_INPUT)
        locked = read_lock_entries(RUNTIME_LOCK)

        disagreements = {
            name: (version, locked[name][0])
            for name, version in declared.items()
            if name in locked and locked[name][0] != version
        }
        assert not disagreements, (
            f"requirements.txt and requirements.lock disagree (declared, locked): "
            f"{disagreements}. Regenerate the lock files."
        )

    def test_dev_lock_covers_the_test_and_lint_tools(self):
        declared = read_pins(DEV_INPUT)
        locked = read_lock_entries(DEV_LOCK)

        for name, version in declared.items():
            assert name in locked, f"{name} is not in requirements-dev.lock"
            assert locked[name][0] == version, f"{name} is pinned differently in the dev lock"

    def test_dev_lock_is_a_superset_of_the_runtime_lock(self):
        """CI must test the versions that production runs.

        The two locks are resolved separately, so nothing stops them drifting
        to different versions of a shared dependency -- at which point a green
        test run says less than it appears to.
        """
        runtime = read_lock_entries(RUNTIME_LOCK)
        dev = read_lock_entries(DEV_LOCK)

        for name, (version, _) in runtime.items():
            assert name in dev, f"{name} is in the runtime lock but not the dev lock"
            assert dev[name][0] == version, (
                f"{name} is {version} in production but {dev[name][0]} in CI; "
                "regenerate both lock files together"
            )


class TestTheInstallerUsesTheLock:
    """A lock file the deploy ignores is documentation, not a control."""

    INSTALLER = (REPO_ROOT / "install.sh").read_text()

    def test_installer_installs_from_the_lock(self):
        assert "requirements.lock" in self.INSTALLER

    def test_installer_verifies_the_hashes(self):
        assert "--require-hashes" in self.INSTALLER

    def test_ci_installs_from_the_dev_lock(self):
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
        assert "requirements-dev.lock" in workflow
        assert "--require-hashes" in workflow
