"""The admin dashboard: is anything waiting for me, and how is the membership doing.

The first is a list of tasks, absent when there are none -- each kind only
for whoever may act on it; the second is four figures, each with the one
number that explains it. Then the latest entries of the log, for whoever may
read it. Drawn by frontend/src/pages/admin/AdminDashboard.tsx.
"""

from typing import Literal

from flask_login import current_user

from ..permissions import Permission
from ..services import dashboard, reviews, team_money
from ..services.audit import get_recent_audit_logs
from ..services.diagnostics import collect_system_health
from ..services.membership import format_membership_date_display
from ._core import Model, UtcDateTime, endpoint


class WaitingCountOut(Model):
    """How many of one kind wait, and the oldest of them (for sync problems: the latest) in a few words."""

    count: int
    summary: str | None
    at: UtcDateTime | None


class Transfers(Model):
    """What the teams are owed and not yet sent, in cents."""

    teams: int
    open: int
    #: Of those teams, how many have given no account to send it to.
    without_account: int


class Attention(Model):
    """``None`` for a kind the person may not act on, so the page does not offer it."""

    name_changes: WaitingCountOut | None
    pictures: WaitingCountOut | None
    sync_problems: WaitingCountOut | None
    health_problems: list[str] | None
    transfers: Transfers | None


class Figure(Model):
    key: Literal["active", "cancelled", "forum", "archive"]
    label: str
    value: int
    note: str
    link_url: str | None


class Activity(Model):
    id: int
    category: str
    event_type: str
    target: str | None
    at: UtcDateTime


class DashboardOut(Model):
    attention: Attention
    #: Empty without the permission to see member accounts.
    figures: list[Figure]
    #: ``None`` without the permission to read the log.
    recent_activity: list[Activity] | None


def _name(member):
    return f"{member.first_name} {member.last_name}".strip() if member is not None else "Somebody"


def _attention():
    counts = reviews.waiting_counts(current_user)
    oldest = reviews.oldest_waiting(current_user)

    name_changes = pictures = sync_problems = None
    if reviews.can_review_name_changes(current_user):
        record = oldest[reviews.KIND_NAME_CHANGE]
        summary = None
        if record is not None:
            summary = (f"{_name(record.member)} → {record.requested_full_name}" if record.is_name_change
                       else f"{_name(record.member)}: {', '.join(label for _f, label, *_ in record.changes)}")
        name_changes = WaitingCountOut(count=counts[reviews.KIND_NAME_CHANGE], summary=summary,
                               at=record.created_at if record is not None else None)
    if reviews.can_review_pictures(current_user):
        picture = oldest[reviews.KIND_PICTURE]
        pictures = WaitingCountOut(count=counts[reviews.KIND_PICTURE],
                           summary=_name(picture.member) if picture is not None else None,
                           at=picture.uploaded_at if picture is not None else None)
        problem = oldest["sync_problems"]
        sync_problems = WaitingCountOut(
            count=counts["sync_problems"],
            summary=(problem.user.email if problem is not None and problem.user is not None
                     else ("An account" if problem is not None else None)),
            at=problem.updated_at if problem is not None else None,
        )
    health = None
    if current_user.can(Permission.SYSTEM_UPDATE):
        health = list(collect_system_health(check_pages=False)["problems"])
    transfers = None
    if current_user.can(Permission.TEAMS_MONEY):
        transfers = Transfers(**team_money.open_transfers())
    return Attention(name_changes=name_changes, pictures=pictures, sync_problems=sync_problems,
                     health_problems=health, transfers=transfers)


def _figures():
    # How the membership stands is for whoever looks after the members; the
    # association's treasurer sees the money and nothing else of it.
    if not current_user.can(Permission.ACCOUNTS_VIEW):
        return []
    numbers = dashboard.metrics()

    def link(query):
        return f"/admin/accounts?{query}"

    waiting = numbers["pending_checkouts"]
    figures = [Figure(
        key="active", label="Active members", value=numbers["active_memberships"],
        note=f"of {numbers['total_accounts']} accounts" + (f" · {waiting} waiting for payment" if waiting else ""),
        link_url=link("membership=active"),
    )]
    cancelled = numbers["cancel_scheduled_memberships"]
    ends = dashboard.shared_cancellation_end()
    if ends is not None:
        figures.append(Figure(key="cancelled", label=f"Ending {format_membership_date_display(ends)}",
                              value=cancelled, note="cancelled, stay active until then",
                              link_url=link("membership=ending")))
    else:
        figures.append(Figure(
            key="cancelled", label="Cancelled", value=cancelled,
            note="stay active until their paid period ends" if cancelled else "nobody has cancelled",
            link_url=link("membership=ending"),
        ))
    setting_up = numbers["forum_onboarding_accounts"]
    figures.append(Figure(
        key="forum", label="On the forum", value=numbers["forum_active_accounts"],
        note=f"{setting_up} still setting up their account" if setting_up else "nobody still setting up",
        link_url=None,
    ))
    if numbers["archived_forum_accounts"]:
        # Kept apart from the account and membership counts rather than added to
        # them: those answer who pays, who can be reached, who renews -- and an
        # archived person is none of that.
        figures.append(Figure(key="archive", label="Old forum archive", value=numbers["archived_forum_accounts"],
                              note=f"{numbers['archived_forum_claimed']} reconnected so far",
                              link_url=link("kind=archived")))
    return figures


def _activity():
    if not current_user.can(Permission.LOGS_VIEW):
        return None
    entries = []
    for entry in get_recent_audit_logs(limit=8):
        target = (entry.target_user.email if entry.target_user is not None
                  else entry.target_member.email_private if entry.target_member is not None else None)
        entries.append(Activity(id=entry.id, category=entry.category, event_type=entry.event_type,
                                target=target, at=entry.created_at))
    return entries


@endpoint("GET", "/admin/dashboard", response=DashboardOut, permissions=[Permission.ADMIN_ACCESS], tag="Admin")
def admin_dashboard():
    """What needs doing, the membership in four figures, and the latest log entries."""
    return DashboardOut(attention=_attention(), figures=_figures(), recent_activity=_activity())
