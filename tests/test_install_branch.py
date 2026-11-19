"""The installer's branch on an update (install.sh, ``fall_back_from_gone_branch``): a
remembered branch that was merged and deleted is not followed -- main instead -- while one
given with --branch is kept, and one that cannot be checked is kept too.

install.sh's own functions, run in bash against a repository on disk (test_install_frontend.py).
"""
import subprocess

import pytest

from test_install_frontend import lib, run, stubs  # noqa: F401 -- fixtures


@pytest.fixture
def repo(tmp_path):
    """A repository with main and claude/kept."""
    work, bare = tmp_path / "work", tmp_path / "origin.git"
    for command in (["git", "init", "-q", "-b", "main", str(work)],
                    ["git", "-C", str(work), "-c", "user.name=t", "-c", "user.email=t@example.org",
                     "commit", "-q", "--allow-empty", "-m", "first"],
                    ["git", "-C", str(work), "branch", "claude/kept"],
                    ["git", "clone", "-q", "--bare", str(work), str(bare)]):
        subprocess.run(command, check=True, capture_output=True)
    return str(bare)


def _branch(lib, stubs, repo, branch, given="0"):  # noqa: F811
    result = run(lib, stubs, f'REPO_URL="{repo}"; BRANCH="{branch}"; BRANCH_GIVEN={given}\n'
                             'fall_back_from_gone_branch; echo "branch=${BRANCH}"')
    return result.stdout.strip().splitlines()[-1], result.stdout + result.stderr


def test_a_remembered_branch_that_is_gone_falls_back_to_main(lib, stubs, repo):  # noqa: F811
    branch, said = _branch(lib, stubs, repo, "claude/merged-and-deleted")

    assert branch == "branch=main" and "no longer exists" in said


def test_a_remembered_branch_still_there_is_followed(lib, stubs, repo):  # noqa: F811
    assert _branch(lib, stubs, repo, "claude/kept")[0] == "branch=claude/kept"


def test_a_branch_given_is_kept_even_if_gone(lib, stubs, repo):  # noqa: F811
    # It then fails, as before: a typo is not quietly turned into main.
    assert _branch(lib, stubs, repo, "claude/typo", given="1")[0] == "branch=claude/typo"


def test_a_branch_that_cannot_be_checked_is_kept(lib, stubs, tmp_path):  # noqa: F811
    assert _branch(lib, stubs, str(tmp_path / "nowhere.git"), "claude/kept")[0] == "branch=claude/kept"


def test_the_rerun_takes_the_branch_the_first_run_chose():
    from test_install_frontend import INSTALLER

    text = INSTALLER.read_text(encoding="utf-8")
    main = text[text.index("\nmain() {"):]
    assert main.index('BRANCH="${BOOTSTRAP_BRANCH}"') < main.index('parse_args "$@"')
    assert 'BOOTSTRAP_BRANCH="${BRANCH}"' in text
