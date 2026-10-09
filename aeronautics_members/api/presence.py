"""Who is using the portal right now (services/presence.py, docs/presence.md).

Every page in use says so (POST /presence, anybody, signed in or not); whoever
may install updates sees the counts on Settings › Updates before starting one
(frontend/src/pages/admin/settings/Presence.tsx).
"""

from datetime import datetime

from flask_login import current_user
from pydantic import Field

from ..app import limiter
from ..config import RATELIMIT_PRESENCE
from ..permissions import Permission
from ..services import presence
from ._core import Model, UtcDateTime, endpoint


class PresenceIn(Model):
    #: A random id the tab made up, kept for as long as the tab is open.
    tab: str = Field(min_length=8, max_length=36)
    #: The app's address pattern ("/teams/:slug"), or "other".
    page: str = Field(max_length=200)
    #: Typed into a field since the tab last said so.
    typed: bool = False


class PresencePageOut(Model):
    page: str
    people: int
    typing: int


class PresenceOut(Model):
    #: Used a page in the last two minutes, the asking admin's own tab left out.
    active: int
    signed_in: int
    visitors: int
    #: Of them, typed into a field in the last three minutes.
    typing: int
    pages: list[PresencePageOut]
    #: The last moment anybody was there, in the last quarter of an hour.
    last_seen_at: UtcDateTime | None


class PresenceQuery(Model):
    #: The asking tab, left out of the counts.
    tab: str | None = None


@endpoint("POST", "/presence", body=PresenceIn, public=True, tag="Session")
@limiter.limit(RATELIMIT_PRESENCE)
def presence_seen(body):
    """This tab is in use: the page, and whether somebody typed. Answers nothing."""
    presence.seen(body.tab, body.page, typed=body.typed, signed_in=current_user.is_authenticated)


@endpoint("GET", "/admin/presence", response=PresenceOut, query=PresenceQuery,
          permissions=[Permission.SYSTEM_UPDATE], tag="Admin")
def admin_presence(query):
    """Who is using the portal right now: counts and pages, never who."""
    found = presence.report(besides=query.tab)
    last: datetime | None = found["last_seen_at"]
    return PresenceOut(
        active=found["active"],
        signed_in=found["signed_in"],
        visitors=found["visitors"],
        typing=found["typing"],
        pages=[PresencePageOut(**page) for page in found["pages"]],
        last_seen_at=last,
    )
