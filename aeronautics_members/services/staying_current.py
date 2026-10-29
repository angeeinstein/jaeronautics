"""Staying current, and telling a person only when something is stuck
(docs/automatic-updates-plan.md, part 4).

What System health shows about it -- the portal's version and its automatic
updates, Ubuntu's own updates and support, the Node.js that builds the front
end -- and what counts as stuck:

- an update Dependabot proposed, open for more than 14 days: a major version
  or one CI failed, which waits for a person (minor and patch versions merge
  within hours);
- the newest version did not pass its checks, so it is not installed;
- automatic updates on, and no check for more than a day: the night does not run;
- Ubuntu's security updates not installed for more than 3 days;
- a restart an update needs, pending for more than 3 days;
- Ubuntu's or Node.js's support ending within a year.

Each of those is emailed once -- to whoever may install updates, never a
daily reminder --, forgotten when it resolves, and emailed again if it comes
back. The portal reads what it needs itself, without root: Ubuntu keeps it in
files anybody may read.
"""

import csv
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


from ..config import REPO_ROOT
from ..db_models import Role, Setting, User, db
from ..permissions import Permission, roles_with
from .clock import get_now_utc

WAITING = timedelta(days=14)
UBUNTU_UPDATES_LATE = timedelta(days=3)
RESTART_LATE = timedelta(days=3)
SUPPORT_AHEAD = timedelta(days=365)
CHECK_MISSING = timedelta(hours=26)
# Asked at most this often: GitHub answers 60 questions an hour without an account.
TELL_EVERY = timedelta(hours=1)
WAITING_CACHE = timedelta(hours=1)

# When each Node.js long-term release stops getting fixes (nodejs.org's schedule).
# The one the front end is built with is NODE_VERSION in .github/workflows/ci.yml.
NODE_SUPPORT_ENDS = {
    "20": date(2026, 4, 30),
    "22": date(2027, 4, 30),
    "24": date(2028, 4, 30),
    "26": date(2029, 4, 30),
}

# Ubuntu's own, readable by anybody.
OS_RELEASE = Path("/etc/os-release")
DISTRO_INFO = Path("/usr/share/distro-info/ubuntu.csv")
# Touched by unattended-upgrades each time it has run.
UPGRADE_STAMP = Path("/var/lib/apt/periodic/upgrade-stamp")
REBOOT_REQUIRED = Path("/run/reboot-required")
REBOOT_PACKAGES = Path("/run/reboot-required.pkgs")
CI_FILE = REPO_ROOT / ".github" / "workflows" / "ci.yml"
GUIDE = "docs/new-release.md"

TOLD_PREFIX = "staying_current.told."
TOLD_AT_KEY = "staying_current.checked_at"
EMAIL_KIND = "updates_need_a_person"

_waiting_cache = {"at": None, "value": None}


def _aware(moment):
    return moment if moment is None or moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _modified(path):
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _days(delta):
    return max(0, int(delta.total_seconds() // 86400))


# --- What there is ------------------------------------------------------------------------


def waiting_updates(now=None):
    """Dependabot's pull requests open for more than WAITING; None when GitHub cannot be asked."""
    from . import system_update

    now = now or get_now_utc()
    if _waiting_cache["at"] and now - _waiting_cache["at"] < WAITING_CACHE:
        return _waiting_cache["value"]
    slug = system_update.github_slug() if system_update._asks_github() else None
    answer = system_update._github_json(f"/repos/{slug}/pulls?state=open&per_page=100") if slug else None
    value = None
    if isinstance(answer, list):
        value = []
        for pull in answer:
            if (pull.get("user") or {}).get("login") != "dependabot[bot]":
                continue
            opened = system_update.parse_time(pull.get("created_at"))
            if opened and now - opened > WAITING:
                value.append({"number": pull.get("number"), "title": pull.get("title") or "",
                              "url": pull.get("html_url"), "days": _days(now - opened)})
    _waiting_cache.update(at=now, value=value)
    return value


def ubuntu(now=None):
    """The release, its support, its security updates and a pending restart; None if not Ubuntu."""
    try:
        fields = dict(line.split("=", 1) for line in OS_RELEASE.read_text().splitlines() if "=" in line)
    except OSError:
        return None
    fields = {key: value.strip().strip('"') for key, value in fields.items()}
    if fields.get("ID") != "ubuntu":
        return None
    codename = fields.get("VERSION_CODENAME")
    supported_until = None
    try:
        with DISTRO_INFO.open(newline="") as rows:
            for row in csv.DictReader(rows):
                if row.get("series") == codename and row.get("eol"):
                    supported_until = date.fromisoformat(row["eol"])
    except (OSError, ValueError):
        pass
    restart_since = _modified(REBOOT_REQUIRED)
    try:
        packages = sorted(set(REBOOT_PACKAGES.read_text().split())) if restart_since else []
    except OSError:
        packages = []
    return {
        "name": fields.get("PRETTY_NAME") or "Ubuntu",
        "codename": codename,
        "supported_until": supported_until,
        "updates_installed_at": _modified(UPGRADE_STAMP),
        "restart_pending_since": restart_since,
        "restart_packages": packages,
    }


def node():
    """The Node.js major version the front end is built with, and how long it gets fixes."""
    try:
        text = CI_FILE.read_text()
    except OSError:
        return None
    for line in text.splitlines():
        if line.strip().startswith("NODE_VERSION:"):
            major = line.split(":", 1)[1].strip().strip('"').strip("'")
            return {"major": major, "supported_until": NODE_SUPPORT_ENDS.get(major)}
    return None


def portal(now=None):
    """The newest version's checks and the automatic updates, as the night sees them."""
    from . import automatic_updates, system_update

    local = system_update.get_local_version().get("revision")
    remote = system_update.get_remote_version()
    newer = bool(local and remote and local != remote)
    ci = system_update.ci_state(remote) if newer else None
    last = automatic_updates.last_run() or {}
    return {
        "newer_version": newer,
        "newest": (remote or "")[:8] or None,
        "newest_failed": bool(ci and ci["state"] == "failure"),
        "newest_checks_url": (ci or {}).get("url"),
        "automatic": automatic_updates.is_enabled(),
        "automatic_since": automatic_updates.enabled_since(),
        "last_check_at": system_update.parse_time(last.get("at")),
    }


def guide_url():
    from . import system_update

    slug = system_update.github_slug()
    return f"https://github.com/{slug}/blob/main/{GUIDE}" if slug else None


# --- What is stuck ------------------------------------------------------------------------


def _date(day):
    return day.strftime("%d.%m.%Y")


def stuck(now=None, *, parts=None):
    """Each thing stuck: ``{"key", "text", "link"}`` -- the key says which, to tell it once."""
    now = now or get_now_utc()
    parts = parts or {"portal": portal(now), "ubuntu": ubuntu(now), "node": node(),
                      "waiting": waiting_updates(now)}
    found = []
    for pull in parts["waiting"] or []:
        found.append({"key": f"waiting:{pull['number']}", "link": pull["url"],
                      "text": f"An update has waited {pull['days']} days for a person: {pull['title']}."})
    site = parts["portal"]
    if site and site["newest_failed"]:
        found.append({"key": f"newest_failed:{site['newest']}", "link": site["newest_checks_url"],
                      "text": f"The newest version, {site['newest']}, did not pass its checks: it is not installed."})
    if site and site["automatic"]:
        since = _aware(site["last_check_at"] or site["automatic_since"])
        if since and now - since > CHECK_MISSING:
            found.append({"key": "check_missing", "link": None,
                          "text": "Automatic updates are on, but the nightly check has not run for "
                                  f"{_days(now - since) or 1} day(s)."})
    system = parts["ubuntu"]
    if system:
        installed = system["updates_installed_at"]
        if installed and now - installed > UBUNTU_UPDATES_LATE:
            found.append({"key": "ubuntu_updates", "link": None,
                          "text": f"Ubuntu's security updates were last installed {_days(now - installed)} days ago."})
        pending = system["restart_pending_since"]
        if pending and now - pending > RESTART_LATE:
            found.append({"key": "restart", "link": None,
                          "text": f"An update has waited {_days(now - pending)} days for the server to restart."})
        ends = system["supported_until"]
        if ends and ends - now.date() < SUPPORT_AHEAD:
            found.append({"key": f"support:ubuntu:{system['codename']}", "link": guide_url(),
                          "text": f"{system['name']} gets security updates until {_date(ends)}: "
                                  "move to the next release before then."})
    build = parts["node"]
    if build and build["supported_until"] and build["supported_until"] - now.date() < SUPPORT_AHEAD:
        found.append({"key": f"support:node:{build['major']}", "link": None,
                      "text": f"Node.js {build['major']}, which builds the front end, gets fixes until "
                              f"{_date(build['supported_until'])}: move to the next long-term release before then."})
    return found


def report(now=None):
    """What System health shows: each part, and what is stuck."""
    now = now or get_now_utc()
    parts = {"portal": portal(now), "ubuntu": ubuntu(now), "node": node(), "waiting": waiting_updates(now)}
    return {**parts, "stuck": stuck(now, parts=parts), "guide_url": guide_url()}


# --- Telling a person, once ---------------------------------------------------------------


def _recipients():
    """Whoever may install updates: the only ones who see System health."""
    slugs = roles_with(Permission.SYSTEM_UPDATE)
    if not slugs:
        return []
    return db.session.execute(
        db.select(User.email).where(User.email_verified_at.is_not(None), User.deleted_at.is_(None),
                                    User.roles.any(Role.slug.in_(slugs)))
        .order_by(User.email.asc())
    ).scalars().all()


def tell(now=None):
    """Email what is newly stuck, once; forget what resolved. At most once an hour. Returns how many were new."""
    from .notifications import queue_user_status_notification

    now = now or get_now_utc()
    checked = db.session.get(Setting, TOLD_AT_KEY)
    try:
        last = _aware(datetime.fromisoformat(checked.value)) if checked else None
    except ValueError:
        last = None
    if last and now - last < TELL_EVERY:
        return 0
    if checked is None:
        db.session.add(Setting(key=TOLD_AT_KEY, value=now.isoformat()))
    else:
        checked.value = now.isoformat()

    items = stuck(now)
    told = {row.key[len(TOLD_PREFIX):]: row for row in db.session.execute(
        db.select(Setting).where(Setting.key.like(f"{TOLD_PREFIX}%"))).scalars()}
    current = {item["key"] for item in items}
    for key, row in told.items():
        if key not in current:
            db.session.delete(row)  # resolved: told again if it comes back
    new = [item for item in items if item["key"] not in told]
    for item in new:
        db.session.add(Setting(key=f"{TOLD_PREFIX}{item['key']}"[:50], value=now.isoformat()[:255]))
    if new:
        payload = {"items": [item["text"] for item in new], "links": [item["link"] for item in new]}
        for email in _recipients():
            queue_user_status_notification(EMAIL_KIND, "Updates need a person", email,
                                           payload=json.loads(json.dumps(payload)))
    return len(new)
