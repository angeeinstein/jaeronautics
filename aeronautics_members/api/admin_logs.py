"""The log: every change recorded, who made it and to whom, newest first,
with what it looked like before and after -- secrets left out. Searched by
address, category or event; narrowed to one category, or to everything about
one person. The rules are in services/audit.py. Drawn by
frontend/src/pages/admin/Logs.tsx.
"""

from typing import Any

from flask_login import current_user
from pydantic import Field

from ..db_models import User, db
from ..permissions import Permission
from ..services import NotFoundError
from ..services import audit
from ._core import Model, UtcDateTime, endpoint


class LogPersonOut(Model):
    #: ``None`` for a membership without an account.
    user_id: int | None
    email: str


class LogEntry(Model):
    id: int
    at: UtcDateTime
    category: str
    event_type: str
    #: ``None``: the system did it.
    actor: LogPersonOut | None
    target: LogPersonOut | None
    before: Any
    after: Any
    details: Any


class LogsOut(Model):
    items: list[LogEntry]
    page: int
    pages: int
    total: int
    #: Every category there is, for the filter.
    categories: list[str]
    #: The person the log is narrowed to, when it is.
    about: LogPersonOut | None
    #: Whether the people link to their accounts (the person looking may see accounts).
    accounts_linked: bool


class LogsQuery(Model):
    q: str = Field("", max_length=200)
    category: str | None = Field(None, max_length=80)
    #: Only what is about this account: done to it, by it, or to its membership.
    user: int | None = None
    page: int = Field(1, ge=1)


def _person(user=None, member=None):
    if user is not None:
        return LogPersonOut(user_id=user.id, email=user.email)
    if member is not None:
        return LogPersonOut(user_id=member.user.id if member.user is not None else None, email=member.email_private)
    return None


def _redacted(value):
    return audit.redact_sensitive_audit_value(value) if value else None


@endpoint("GET", "/admin/logs", response=LogsOut, query=LogsQuery, permissions=[Permission.LOGS_VIEW],
          tag="Admin")
def admin_logs(query):
    """One page of the log, newest first."""
    about = None
    if query.user is not None:
        about = db.session.get(User, query.user)
        if about is None:
            raise NotFoundError("That account does not exist.")
    found = audit.log_page(q=query.q.strip(), category=query.category or None, user=about, page=query.page)
    return LogsOut(
        items=[
            LogEntry(id=entry.id, at=entry.created_at, category=entry.category, event_type=entry.event_type,
                     actor=_person(entry.actor_user),
                     target=_person(entry.target_user, entry.target_member),
                     before=_redacted(entry.before_state), after=_redacted(entry.after_state),
                     details=_redacted(entry.event_metadata))
            for entry in found.items
        ],
        page=found.page, pages=found.pages, total=found.total,
        categories=audit.log_categories(),
        about=_person(about),
        accounts_linked=current_user.can(Permission.ACCOUNTS_VIEW),
    )
