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


def _timer_blocks(text):
    """The body of every [Timer] section the installer writes."""
    blocks = []
    for chunk in text.split("[Timer]")[1:]:
        blocks.append(chunk.split("[Install]", 1)[0])
    return blocks


def test_every_interval_timer_also_runs_after_it_is_started(installer):
    """Found for real: a day of forum syncs and admin emails that never ran.

    OnUnitActiveSec counts from the service's last run, and OnBootSec from
    boot. On a machine that was already up when the installer started the
    timer, the first has no run to count from and the second is long past, so
    the timer shows "elapsed" with no next trigger and never fires. OnActiveSec
    counts from the timer starting, which is what an install does.
    """
    assert _timer_blocks(installer), "the installer writes timers"
    for block in _timer_blocks(installer):
        if "OnUnitActiveSec" in block:
            assert "OnActiveSec" in block or "OnCalendar" in block, block


def test_the_timers_are_restarted_not_only_enabled(installer):
    """enable --now leaves a timer that is stuck "elapsed" exactly as it is."""
    body = installer[installer.index("reload_services() {"):]
    body = body[: body.index("\n}")]
    assert 'systemctl restart "${SERVICE_NAME}-${timer}.timer"' in body
    for timer in ("billing-reconcile", "notifications", "cleanup-logs",
                  "forum-drift", "external-work", "update-runner"):
        assert timer in body


# --- An update while people use the portal -------------------------------------

JOB_TIMERS = ("billing-reconcile", "notifications", "cleanup-logs", "forum-drift", "external-work")


def _function(installer, name):
    """A shell function's text: up to the next function, since a heredoc inside may hold a "}" line."""
    start = installer.index(f"\n{name}() {{") + 1
    following = re.search(r"\n[a-z_]+\(\) \{", installer[start + 1:])
    return installer[start: start + 1 + following.start()] if following else installer[start:]


def test_an_update_pauses_the_jobs_before_the_new_code_arrives(installer):
    """A job started between the code arriving and the restart would run it
    against packages and a database not updated yet."""
    body = _function(installer, "install_or_update")
    calls = [line.strip() for line in body.splitlines()]
    assert calls.index("record_rollback_point") < calls.index("pause_background_jobs") < calls.index("ensure_repo_present")
    jobs = re.search(r"BACKGROUND_JOBS=\(([^)]*)\)", installer).group(1).split()
    assert tuple(jobs) == JOB_TIMERS
    # and every one of them is started again at the end
    assert all(timer in _function(installer, "reload_services") for timer in JOB_TIMERS)


def test_a_failed_update_starts_them_again(installer):
    assert "resume_background_jobs_after_failure" in _function(installer, "on_error")


def test_pausing_waits_for_a_running_job_and_a_failure_resumes(tmp_path):
    """The functions themselves, with a systemctl that reports one job still running."""
    import subprocess

    log = tmp_path / "systemctl.log"
    fake = tmp_path / "bin" / "systemctl"
    fake.parent.mkdir()
    fake.write_text(
        "#!/bin/bash\n"
        f'echo "$*" >> "{log}"\n'
        'if [[ "$1" == show ]]; then\n'
        f'  n=$(grep -c "show.*external-work.service" "{log}")\n'
        '  [[ "${@: -1}" == *external-work.service && $n -lt 3 ]] && echo activating || echo inactive\n'
        "fi\n"
    )
    fake.chmod(0o755)
    text = INSTALLER.read_text(encoding="utf-8")
    functions = text[text.index("# The jobs the timers run"):text.index("usage() {")]
    script = (
        "set -Eeuo pipefail\nstep(){ :; }; info(){ :; }; warn(){ :; }\nSERVICE_NAME=portal\n"
        + functions
        + "\npause_background_jobs\necho paused=$BACKGROUND_JOBS_PAUSED\n"
        + "resume_background_jobs_after_failure\necho paused=$BACKGROUND_JOBS_PAUSED\n"
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60,
                            env={"PATH": f"{fake.parent}:/usr/bin:/bin"})

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["paused=1", "paused=0"]
    calls = log.read_text().splitlines()
    assert [c for c in calls if c.startswith("stop")] == [f"stop portal-{t}.timer" for t in JOB_TIMERS]
    assert len([c for c in calls if "external-work.service" in c]) >= 3  # it waited
    assert [c for c in calls if c.startswith("start")] == [f"start portal-{t}.timer" for t in JOB_TIMERS]


# --- What the legal PDFs need from the system -----------------------------------


def test_the_pdf_libraries_are_installed_with_the_rest(installer):
    """Pango lays the PDFs out; HarfBuzz-Subset trims the fonts in them (WeasyPrint's
    fallback for it, fontTools, is going away)."""
    apt = _function(installer, "base_packages").split("dnf|yum)")[0]

    assert all(pkg in apt for pkg in ("libpango-1.0-0", "libpangoft2-1.0-0", "libharfbuzz-subset0"))


STATIC = Path(__file__).resolve().parent.parent / "aeronautics_members" / "static"


def test_nginx_shows_a_page_of_its_own_while_the_portal_does_not_answer(installer):
    """As 503, so Stripe sends its webhook again and browsers come back."""
    body = _function(installer, "render_nginx_config")
    assert body.count("error_page 502 503 504 =503 /maintenance.html;") == 2  # with and without TLS
    assert body.count("location = /maintenance.html {") == 2 and body.count("internal;") == 2
    deployed = (INSTALLER.parent / "deploy" / "nginx" / "aeronautics.conf").read_text(encoding="utf-8")
    assert "error_page 502 503 504 =503 /maintenance.html;" in deployed


def test_the_page_needs_nothing_from_the_portal():
    page = (STATIC / "maintenance.html").read_text(encoding="utf-8")
    assert '<meta http-equiv="refresh"' in page
    assert "<script" not in page and "style=" not in page and "<style" not in page  # the CSP forbids both
    for asset in re.findall(r'(?:href|src)="(/static/[^"]+)"', page):
        assert (STATIC / asset[len("/static/"):]).is_file(), asset
