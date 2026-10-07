"""The installer's front end: CI's build downloaded, or one made here.

install.sh's own functions, run in bash against stand-ins: ``curl`` answers
from files instead of GitHub, ``systemctl`` and ``sleep`` only say they were
called. Nothing here needs a network, root or a server.
"""
import json
import os
import subprocess
import tarfile
import textwrap
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parent.parent / "install.sh"
SLUG = "angeeinstein/jaeronautics"
SHA = "a" * 40


@pytest.fixture
def lib(tmp_path):
    """install.sh without its last line (main "$@"), to source."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert '\nmain "$@"\n' in text
    path = tmp_path / "installer-lib.sh"
    path.write_text(text.replace('\nmain "$@"\n', "\n"), encoding="utf-8")
    return path


@pytest.fixture
def stubs(tmp_path):
    """A bin folder with curl, systemctl and sleep stand-ins. ``answers`` is
    where curl reads: api.json for the CI question, the archive for a download."""
    bin_dir = tmp_path / "bin"
    answers = tmp_path / "answers"
    bin_dir.mkdir()
    answers.mkdir()
    (bin_dir / "curl").write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        out=""
        url=""
        while (($#)); do
            case "$1" in
                -o) out="$2"; shift 2 ;;
                -H|--max-time) shift 2 ;;
                -*) shift ;;
                *) url="$1"; shift ;;
            esac
        done
        echo "$url" >> "{answers}/asked"
        case "$url" in
            https://api.github.com/*)
                [[ -f "{answers}/api.json" ]] || exit 22
                cat "{answers}/api.json" ;;
            *releases/download/*)
                file="{answers}/$(basename "$url")"
                [[ -f "$file" ]] || exit 22
                cp "$file" "$out" ;;
            *) exit 22 ;;
        esac
        """))
    (bin_dir / "systemctl").write_text(f'#!/usr/bin/env bash\necho "$*" >> "{answers}/systemctl"\n')
    (bin_dir / "sleep").write_text(f'#!/usr/bin/env bash\necho "$*" >> "{answers}/slept"\n')
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)
    return bin_dir, answers


def run(lib, stubs, script, **env):
    bin_dir, _answers = stubs
    full = f"""
source "{lib}"
trap - ERR
# Run as whoever runs the tests: no runuser, no other user.
run_as_app_user() {{ "$@"; }}
{script}
"""
    return subprocess.run(
        ["bash", "-c", full], capture_output=True, text=True, timeout=60,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", **env},
    )


def runs(*runs_):
    return json.dumps({"workflow_runs": [
        {"path": path, "status": status, "conclusion": conclusion, "run_number": number, "run_attempt": 1}
        for path, status, conclusion, number in runs_
    ]})


CI = ".github/workflows/ci.yml"


class TestWhereTheRepositoryIs:
    @pytest.mark.parametrize("url, slug", [
        ("https://github.com/angeeinstein/jaeronautics.git", SLUG),
        ("https://github.com/angeeinstein/jaeronautics", SLUG),
        ("git@github.com:angeeinstein/jaeronautics.git", SLUG),
        ("https://gitlab.com/someone/elsewhere.git", ""),
        ("https://github.com/not/a/repository", ""),
    ])
    def test_on_github_or_not(self, lib, stubs, url, slug):
        result = run(lib, stubs, "github_repo_slug", REPO_URL=url, BOOTSTRAP_REPO_URL=url)
        assert result.stdout.strip() == slug


class TestWhatCISays:
    @pytest.mark.parametrize("answer, state", [
        (runs((CI, "completed", "success", 3)), "success"),
        (runs((CI, "in_progress", None, 3)), "running"),
        (runs((CI, "completed", "failure", 3)), "failure:failure"),
        (runs((CI, "completed", "cancelled", 3)), "failure:cancelled"),
        # The newest run counts: a failed one run again and passed.
        (runs((CI, "completed", "failure", 3), (CI, "completed", "success", 4)), "success"),
        # Another workflow's run says nothing about the tests.
        (runs((".github/workflows/other.yml", "completed", "success", 9)), "none"),
        (runs(), "none"),
        ("not json", "unknown"),
    ])
    def test_the_answer(self, lib, stubs, answer, state):
        (stubs[1] / "api.json").write_text(answer)
        result = run(lib, stubs, f"ci_state {SLUG} {SHA}")
        assert result.stdout.strip() == state

    def test_github_out_of_reach(self, lib, stubs):
        assert run(lib, stubs, f"ci_state {SLUG} {SHA}").stdout.strip() == "unknown"


class TestWaitingForCI:
    def test_a_failed_run_stops_the_update(self, lib, stubs):
        (stubs[1] / "api.json").write_text(runs((CI, "completed", "failure", 3)))

        result = run(lib, stubs, f"wait_for_ci {SLUG} {SHA}; echo went-on")

        assert result.returncode == 1 and "went-on" not in result.stdout
        assert "--build-locally" in result.stdout

    def test_one_still_running_is_waited_for_then_given_up_on(self, lib, stubs):
        (stubs[1] / "api.json").write_text(runs((CI, "in_progress", None, 3)))

        result = run(lib, stubs, f"wait_for_ci {SLUG} {SHA}; echo went-on", FRONTEND_CI_WAIT_MINUTES="2")

        assert result.returncode == 1 and "has not finished" in result.stdout
        assert (stubs[1] / "slept").read_text().split() == ["60", "60"]

    def test_passed_goes_on(self, lib, stubs):
        (stubs[1] / "api.json").write_text(runs((CI, "completed", "success", 3)))
        assert "went-on" in run(lib, stubs, f"wait_for_ci {SLUG} {SHA}; echo went-on").stdout

    def test_github_out_of_reach_goes_on(self, lib, stubs):
        result = run(lib, stubs, f"wait_for_ci {SLUG} {SHA}; echo went-on")
        assert "went-on" in result.stdout and "Could not ask GitHub" in result.stdout


@pytest.fixture
def checkout(tmp_path):
    """An installation's checkout: a git repository with a front end."""
    root = tmp_path / "install"
    (root / "frontend").mkdir(parents=True)
    (root / "frontend" / "package.json").write_text("{}")
    (root / "aeronautics_members" / "static").mkdir(parents=True)
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.org"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    tree = subprocess.run([*git, "rev-parse", "HEAD:frontend"], capture_output=True, text=True,
                          check=True).stdout.strip()
    return root, tree


def build_archive(where, tree, *, built_from=None):
    """What CI publishes: the build, with the tree it was made from."""
    app = where / "built"
    (app / "assets").mkdir(parents=True)
    (app / "index.html").write_text("<!doctype html>")
    (app / "assets" / "index-new.js").write_text("// new")
    (app / ".build-tree").write_text((built_from or tree) + "\n")
    archive = where / f"frontend-{tree}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for path in app.rglob("*"):
            tar.add(path, arcname=f"./{path.relative_to(app)}")
    return archive


def installing(checkout, tmp_path, **extra):
    root, _tree = checkout
    user = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
    group = subprocess.run(["id", "-gn"], capture_output=True, text=True).stdout.strip()
    return {
        "BOOTSTRAP_INSTALL_DIR": str(root), "BOOTSTRAP_REPO_URL": f"https://github.com/{SLUG}.git",
        "BOOTSTRAP_APP_USER": user, "BOOTSTRAP_APP_GROUP": group,
        # git_in_dir writes safe.directory to the global git config: a throwaway one.
        "HOME": str(tmp_path), **extra,
    }


class TestPuttingItInPlace:
    SCRIPT = 'FRONTEND_CACHE_DIR="$CACHE"; REPO_URL="$BOOTSTRAP_REPO_URL"; install_frontend'

    def test_cis_build_is_downloaded_and_swapped_in(self, lib, stubs, checkout, tmp_path):
        root, tree = checkout
        build_archive(stubs[1], tree)
        (stubs[1] / "api.json").write_text(runs((CI, "completed", "success", 3)))
        live = root / "aeronautics_members" / "static" / "app"
        (live / "assets").mkdir(parents=True)
        (live / "assets" / "index-old.js").write_text("// old")
        (live / ".build-files").write_text("index-old.js\n")

        result = run(lib, stubs, self.SCRIPT, CACHE=str(tmp_path / "cache"), **installing(checkout, tmp_path))

        assert result.returncode == 0, result.stdout + result.stderr
        assert (live / "index.html").exists() and (live / "assets" / "index-new.js").exists()
        # A page opened before the update still finds its scripts.
        assert (live / "assets" / "index-old.js").exists()
        assert not (root / "aeronautics_members" / "static" / "app.next").exists()
        assert (tmp_path / "cache" / f"frontend-{tree}.tar.gz").exists()  # kept for a rollback
        assert "npm" not in result.stdout

    def test_one_from_other_sources_is_refused(self, lib, stubs, checkout, tmp_path):
        root, tree = checkout
        build_archive(stubs[1], tree, built_from="b" * 40)
        (stubs[1] / "api.json").write_text(runs((CI, "completed", "success", 3)))

        result = run(lib, stubs, self.SCRIPT, CACHE=str(tmp_path / "cache"), **installing(checkout, tmp_path))

        assert result.returncode == 1 and "other sources" in result.stdout
        assert not (root / "aeronautics_members" / "static" / "app").exists()

    def test_a_failed_ci_run_installs_nothing(self, lib, stubs, checkout, tmp_path):
        root, tree = checkout
        build_archive(stubs[1], tree)
        (stubs[1] / "api.json").write_text(runs((CI, "completed", "failure", 3)))

        result = run(lib, stubs, self.SCRIPT, CACHE=str(tmp_path / "cache"), **installing(checkout, tmp_path))

        assert result.returncode == 1
        assert not (root / "aeronautics_members" / "static" / "app").exists()

    def test_nothing_to_download_builds_here(self, lib, stubs, checkout, tmp_path):
        (stubs[1] / "api.json").write_text(runs((CI, "completed", "success", 3)))
        script = self.SCRIPT.replace(
            "install_frontend", 'build_frontend_here() { echo "BUILT HERE"; mkdir -p "$1/assets"; }; install_frontend')

        result = run(lib, stubs, script, CACHE=str(tmp_path / "cache"), **installing(checkout, tmp_path))

        assert result.returncode == 0, result.stdout + result.stderr
        assert "could not be downloaded" in result.stdout and "BUILT HERE" in result.stdout

    def test_asked_to_it_builds_here_without_asking_github(self, lib, stubs, checkout, tmp_path):
        script = self.SCRIPT.replace(
            "install_frontend", 'build_frontend_here() { echo "BUILT HERE"; mkdir -p "$1/assets"; }; install_frontend')

        result = run(lib, stubs, script, CACHE=str(tmp_path / "cache"), BUILD_LOCALLY="1",
                     **installing(checkout, tmp_path))

        assert "BUILT HERE" in result.stdout
        assert not (stubs[1] / "asked").exists()


class TestTheBuildHere:
    def test_enough_memory_adds_no_swap(self, lib, stubs):
        result = run(lib, stubs, "BUILD_MEMORY_NEEDED_MB=1; add_build_swap; echo active=$BUILD_SWAP_ACTIVE")
        assert "enough for the build" in result.stdout and "active=0" in result.stdout

    def test_it_gives_way_to_the_portal(self, lib, stubs):
        result = run(lib, stubs, 'INSTALL_DIR=/tmp; run_build_step bash -c "cat /proc/self/oom_score_adj; nice"')
        assert result.stdout.split()[:2] == ["1000", "19"]


def test_a_stopped_update_starts_the_background_jobs_again(lib, stubs):
    """die() exits without the error handler; the exit handler covers it."""
    result = run(lib, stubs, 'trap on_exit EXIT; SERVICE_NAME=portal; BACKGROUND_JOBS_PAUSED=1; die "stopped"')

    assert result.returncode == 1
    assert "start portal-notifications.timer" in (stubs[1] / "systemctl").read_text()
