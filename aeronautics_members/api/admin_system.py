"""Settings -> System health and Updates: is the installation well -- the
schema, the queues, what gave up -- with what can be done about it; and which
version runs, whether a newer one is out, installing it or going back.

Both for whoever may install updates; retrying or dismissing an undelivered
email for whoever manages notifications. The report is
services/diagnostics.py, the updates services/system_update.py, the actions
services/maintenance.py. Drawn by frontend/src/pages/admin/settings/Health.tsx
and Updates.tsx.
"""

from datetime import date
from typing import Literal

from flask_login import current_user

from ..app import limiter
from ..config import RATELIMIT_ADMIN_EMAIL
from ..db_models import db
from ..permissions import Permission
from ..services import automatic_updates, maintenance
from ..services.diagnostics import collect_system_health
from ..services.notifications import list_undelivered_emails
from ..services.clock import get_now_utc
from ..services.system_update import describe_update_state, parse_time, read_full_log
from ._core import Model, UtcDateTime, endpoint

TAG = "Admin"
SYSTEM = [Permission.SYSTEM_UPDATE]


# --- System health ----------------------------------------------------------------------


class SchemaOut(Model):
    applied: str | None
    expected: str | None
    up_to_date: bool


class MembershipOut(Model):
    members: int
    currently_covered: int
    coverage_periods: int
    revoked_periods: int
    #: Kept on purpose: their payment records must stay.
    erased_members: int


class QueuesOut(Model):
    external_work_pending: int
    #: Gave up after hours of failing; can be sent again.
    external_work_failed: int
    webhook_events_completed: int
    webhook_events_failed: int
    emails_pending: int
    #: Long past their retry time: nobody is delivering them.
    emails_overdue: int
    emails_exhausted: int


class UndeliveredOut(Model):
    id: int
    recipient: str | None
    #: The kind of email, in words ("welcome email").
    kind: str
    last_tried_at: UtcDateTime | None
    error: str | None


class PagesOut(Model):
    """What the start page arrives with, fetched through the public address."""

    address: str | None
    #: False: it could not be fetched, or there is no public address.
    checked: bool
    #: How many security policies arrived with it; one is right.
    policies: int | None
    #: All is well, or why it was not checked. A problem is among the warnings.
    note: str | None


class StuckOut(Model):
    text: str
    #: Where it is about: the pull request, the checks, the guide; None.
    link: str | None


class PortalCurrentOut(Model):
    #: The newest version, while it is not the one installed.
    newest: str | None
    #: The newest version did not pass its checks, so it is not installed.
    newest_failed: bool
    automatic: bool
    last_check_at: UtcDateTime | None


class WaitingOut(Model):
    number: int
    title: str
    url: str | None
    days: int


class UbuntuOut(Model):
    name: str
    supported_until: date | None
    #: When unattended-upgrades last ran; None: never, or not set up.
    updates_installed_at: UtcDateTime | None
    restart_pending_since: UtcDateTime | None
    #: What the restart is for ("linux-image-...").
    restart_packages: list[str]


class NodeOut(Model):
    major: str
    supported_until: date | None


class CurrentOut(Model):
    """Staying current (docs/automatic-updates-plan.md, part 4): the portal, Ubuntu, Node.js."""

    portal: PortalCurrentOut
    #: Dependabot's updates waiting for a person (open over 14 days); None: GitHub could not be asked.
    waiting: list[WaitingOut] | None
    #: None: not Ubuntu.
    ubuntu: UbuntuOut | None
    #: The Node.js that builds the front end.
    node: NodeOut | None
    #: What needs a person; each is emailed once to whoever may install updates.
    stuck: list[StuckOut]
    #: The step-by-step guide to the next Ubuntu release.
    guide_url: str | None


class HealthOut(Model):
    healthy: bool
    problems: list[str]
    warnings: list[str]
    #: The database structure: the revision applied, and the one this version expects.
    database_schema: SchemaOut
    membership: MembershipOut
    queues: QueuesOut
    #: The newest emails that gave up, up to 25.
    undelivered: list[UndeliveredOut]
    #: None when the pages were not fetched (the test suite).
    pages: PagesOut | None = None
    current: CurrentOut


def _current(current):
    site, system, build = current["portal"], current["ubuntu"], current["node"]
    return CurrentOut(
        portal=PortalCurrentOut(newest=site["newest"] if site["newer_version"] else None,
                                newest_failed=site["newest_failed"], automatic=site["automatic"],
                                last_check_at=site["last_check_at"]),
        waiting=[WaitingOut(**pull) for pull in current["waiting"]] if current["waiting"] is not None else None,
        ubuntu=UbuntuOut(**{key: system[key] for key in UbuntuOut.model_fields}) if system else None,
        node=NodeOut(**build) if build else None,
        stuck=[StuckOut(text=item["text"], link=item["link"]) for item in current["stuck"]],
        guide_url=current["guide_url"],
    )


@endpoint("GET", "/admin/settings/health", response=HealthOut, permissions=SYSTEM, tag=TAG)
def admin_system_health():
    """How the installation is: problems first, then the figures behind them."""
    health = collect_system_health()
    queues = health["queues"]
    return HealthOut(
        healthy=health["healthy"], problems=health["problems"], warnings=health["warnings"],
        database_schema=SchemaOut(**health["schema"]),
        membership=MembershipOut(**{key: health["membership"][key] for key in MembershipOut.model_fields}),
        queues=QueuesOut(**{key: queues[key] for key in QueuesOut.model_fields}),
        undelivered=[
            UndeliveredOut(id=job.id, recipient=job.recipient_email, kind=(job.email_type or "").replace("_", " "),
                           last_tried_at=job.last_attempted_at, error=job.last_error)
            for job in list_undelivered_emails()
        ],
        pages=PagesOut(**{key: health["pages"][key] for key in PagesOut.model_fields}) if health["pages"] else None,
        current=_current(health["current"]),
    )


class RetriedOut(Model):
    #: How many will be tried again within a few minutes.
    count: int


@endpoint("POST", "/admin/settings/health/forum-tasks/retry", response=RetriedOut, permissions=SYSTEM, tag=TAG)
def admin_retry_forum_tasks():
    """Send the background tasks that gave up once more -- once the forum is reachable again."""
    count = maintenance.retry_forum_tasks(current_user)
    db.session.commit()
    return RetriedOut(count=count)


class ResolvedOut(Model):
    recipient: str | None


def _resolve(job_id, action):
    recipient = maintenance.resolve_undelivered_email(current_user, job_id, action)
    db.session.commit()
    return ResolvedOut(recipient=recipient)


@endpoint("POST", "/admin/settings/health/undelivered/<int:job_id>/retry", response=ResolvedOut,
          permissions=[Permission.NOTIFICATIONS_MANAGE], tag=TAG)
def admin_undelivered_retry(job_id):
    """Try an email that gave up again -- to the address on the profile now, so a typo fixed there counts."""
    return _resolve(job_id, "retry")


@endpoint("POST", "/admin/settings/health/undelivered/<int:job_id>/dismiss", response=ResolvedOut,
          permissions=[Permission.NOTIFICATIONS_MANAGE], tag=TAG)
def admin_undelivered_dismiss(job_id):
    """Stop reporting an email that gave up. It is kept in the account's record, not sent."""
    return _resolve(job_id, "dismiss")


# --- Updates ----------------------------------------------------------------------------


class VersionOut(Model):
    short_revision: str | None
    #: None after a rollback, which leaves no branch checked out.
    branch: str | None
    rolled_back: bool
    committed_at: UtcDateTime | None
    subject: str | None


class UpdateStepOut(Model):
    #: As the installer says it: "Installing Python dependencies".
    label: str
    #: Pending: still to come, as the last successful update went.
    state: Literal["done", "running", "failed", "pending"]
    #: Its warnings, or why it failed.
    detail: str | None


class ProgressOut(Model):
    steps_done: int
    #: How many the last successful update took; None before the first.
    steps_expected: int | None
    percent: int | None
    current_step: str | None
    #: The running update's steps -- or else the last one's -- to tick off.
    steps: list[UpdateStepOut]


class RollbackPointOut(Model):
    short_revision: str
    recorded_at: UtcDateTime | None
    #: The file the installer saved the database to before the update.
    database_backup: str | None


class LastRunOut(Model):
    state: str | None
    finished_at: UtcDateTime | None
    exit_code: int | None
    revision_after: str | None
    #: The end of its log -- while one runs, of the running one.
    log_tail: str | None
    #: Why a run still marked running cannot be.
    interrupted: str | None


class LatestCheckOut(Model):
    """Whether CI passed for the newest version: it is offered once it has."""

    #: passed; running (also when its run has not started yet); failed;
    #: unknown -- GitHub could not be asked, and the version is offered as before.
    state: Literal["passed", "running", "failed", "unknown"]
    #: The run on GitHub, or its list of runs.
    url: str | None
    started_at: UtcDateTime | None
    #: Since when it runs, in whole minutes; while it runs.
    minutes_running: int | None
    #: How long CI usually takes, from its last runs; while it runs.
    typical_minutes: int | None


class AutomaticRunOut(Model):
    at: UtcDateTime | None
    #: requested (an update was asked for), up_to_date, ci_not_passed (not
    #: installed: CI has not passed for it), failed_before (not tried again: it
    #: failed, or somebody went back from it), busy (an update was running),
    #: unreachable (the git remote could not be asked), no_runner.
    outcome: str
    revision: str | None


class AutomaticUpdatesOut(Model):
    """Installing the newest version by itself, at night, once CI passed for it."""

    enabled: bool
    #: When, in the portal's time zone: "03:30".
    at: str
    #: What the last night's check came to; None before the first.
    last_run: AutomaticRunOut | None


class UpdatesOut(Model):
    installed: VersionOut
    #: None when it could not be checked.
    latest: str | None
    latest_check_failed: bool
    #: The newest version is not the one installed (offered or not).
    newer_version: bool
    #: Its CI, while there is a newer version.
    latest_check: LatestCheckOut | None
    #: A newer version whose CI passed -- or that GitHub could not say about.
    update_available: bool
    #: Without the runner on the server nothing can be started from here.
    runner_installed: bool
    in_progress: bool
    #: A request the runner never picked up.
    request_never_picked_up: bool
    progress: ProgressOut
    rollback_point: RollbackPointOut | None
    last_run: LastRunOut
    automatic: AutomaticUpdatesOut


_CI_STATES = {"success": "passed", "running": "running", "none": "running", "failure": "failed"}


def _latest_check(ci):
    if ci is None:
        return None
    state = _CI_STATES.get(ci["state"], "unknown")
    started = parse_time(ci.get("started_at"))
    running = (max(0, int((get_now_utc() - started).total_seconds() // 60))
               if state == "running" and started else None)
    return LatestCheckOut(state=state, url=ci.get("url"), started_at=started, minutes_running=running,
                          typical_minutes=ci.get("typical_minutes"))


def _automatic():
    last = automatic_updates.last_run()
    return AutomaticUpdatesOut(
        enabled=automatic_updates.is_enabled(), at=automatic_updates.AT,
        last_run=AutomaticRunOut(at=parse_time(last.get("at")), outcome=last.get("outcome") or "",
                                 revision=(last.get("revision") or "")[:8] or None) if last else None,
    )


def _updates(force_remote_check=False):
    state = describe_update_state(force_remote_check=force_remote_check)
    local, run, point = state["local"], state["last_run"], state["rollback_point"]
    detached = local.get("branch") == "HEAD"
    return UpdatesOut(
        installed=VersionOut(short_revision=local.get("short_revision"),
                             branch=None if detached else local.get("branch"), rolled_back=detached,
                             committed_at=parse_time(local.get("committed_at")), subject=local.get("subject")),
        latest=state["remote_short_revision"], latest_check_failed=state["remote_check_failed"],
        newer_version=state["newer_version"], latest_check=_latest_check(state["remote_ci"]),
        update_available=state["update_available"], runner_installed=state["runner_installed"],
        in_progress=state["in_progress"], request_never_picked_up=state["request_never_picked_up"],
        progress=ProgressOut(**state["progress"]),
        rollback_point=RollbackPointOut(short_revision=point["short_revision"],
                                        recorded_at=parse_time(point.get("recorded_at")),
                                        database_backup=point.get("database_backup")) if point else None,
        last_run=LastRunOut(state=run.get("state"), finished_at=parse_time(run.get("finished_at")),
                            exit_code=run.get("exit_code"),
                            revision_after=(run.get("revision_after") or "")[:8] or None,
                            log_tail=run.get("log_tail"), interrupted=run.get("interrupted")),
        automatic=_automatic(),
    )


class UpdatesQuery(Model):
    #: Ask the git remote now rather than reuse the answer of the last ten minutes.
    refresh: bool = False


@endpoint("GET", "/admin/settings/updates", response=UpdatesOut, query=UpdatesQuery, permissions=SYSTEM, tag=TAG)
def admin_updates(query):
    """The version running and the newest; while an update runs, how far it is. The page asks again and again."""
    return _updates(force_remote_check=query.refresh)


class UpdateLogOut(Model):
    #: The latest update's whole output, as in a terminal; None before the first.
    text: str | None
    #: Only its end, the output being very long.
    cut: bool


@endpoint("GET", "/admin/settings/updates/log", response=UpdateLogOut, permissions=SYSTEM, tag=TAG)
def admin_update_log():
    """Everything the latest update printed. The page asks again while one runs."""
    text, cut = read_full_log()
    return UpdateLogOut(text=text, cut=cut)


class UpdateIn(Model):
    #: Install the newest version, or go back to the one before the last update.
    action: Literal["update", "rollback"]


@endpoint("POST", "/admin/settings/updates", response=UpdatesOut, body=UpdateIn, permissions=SYSTEM,
          status=202, tag=TAG)
@limiter.limit(RATELIMIT_ADMIN_EMAIL)
def admin_update_start(body):
    """Ask the server to install the update (or roll back). It starts within moments and restarts the site."""
    maintenance.request_version_change(current_user, body.action)
    db.session.commit()
    return _updates()


class AutomaticUpdatesIn(Model):
    enabled: bool


@endpoint("PUT", "/admin/settings/updates/automatic", response=UpdatesOut, body=AutomaticUpdatesIn,
          permissions=SYSTEM, tag=TAG)
def admin_automatic_updates(body):
    """Switch installing updates by themselves at night on or off."""
    automatic_updates.set_enabled(body.enabled, actor=current_user)
    db.session.commit()
    return _updates()
