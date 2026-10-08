"""Who is signed in, and what the front end needs to start.

``GET /session`` is the front end's first call: whether anybody is signed in,
and the CSRF token it sends back as ``X-CSRFToken`` with every change.
``GET /me`` is the signed-in person: what to show in the top bar, which areas
and menu entries they may see, and the counts beside those entries. Seeing an
entry is only a convenience; every endpoint checks the permission again.
"""

from typing import Literal

from flask import get_flashed_messages
from flask_login import current_user
from flask_wtf.csrf import generate_csrf

from ..permissions import Permission
from ..services.background_jobs import is_paused
from ..services.membership import free_period_start_test_override
from ..services.reviews import waiting_for_review_count
from ..services.pictures import picture_url
from ..services.teams import team_labels, teams_enabled
from ._core import Model, endpoint


class SessionOut(Model):
    signed_in: bool
    csrf_token: str


@endpoint("GET", "/session", response=SessionOut, public=True, tag="Session")
def session_state():
    """Whether somebody is signed in, and the CSRF token for changes."""
    return SessionOut(signed_in=bool(current_user.is_authenticated), csrf_token=generate_csrf())


class FlashedOut(Model):
    tone: Literal["info", "success", "warning", "danger"]
    text: str


class MessagesOut(Model):
    messages: list[FlashedOut]


TONES = {"success": "success", "warning": "warning", "danger": "danger", "error": "danger"}


@endpoint("GET", "/messages", response=MessagesOut, public=True, tag="Session")
def flashed_messages():
    """What a Flask route said before sending the browser to one of the app's pages -- an
    expired link, a step to do first. Each is handed out once; the app asks when it starts."""
    return MessagesOut(messages=[
        FlashedOut(tone=TONES.get(category, "info"), text=str(text))
        for category, text in get_flashed_messages(with_categories=True)
    ])


#: Every permission there is, so the front end's checks are spelt right (a type error otherwise).
PermissionName = Literal[tuple(sorted(
    value for name, value in vars(Permission).items() if name.isupper() and isinstance(value, str)
))]


class Counts(Model):
    """Numbers shown beside menu entries; 0 when there is nothing, or nothing the person may see."""

    reviews_waiting: int


class TeamLabels(Model):
    """What teams are called here; an admin can rename them."""

    singular: str
    plural: str


class Notice(Model):
    """A banner over every page of an area, for something that is off on purpose or by accident."""

    tone: Literal["info", "warning", "danger"]
    message: str
    link_url: str | None = None
    link_label: str | None = None


class MeOut(Model):
    id: int
    email: str
    first_name: str | None
    last_name: str | None
    forum_username: str | None
    #: Their picture (approved here, else the old forum's), for the top bar; None shows their initials.
    picture_url: str | None
    roles: list[str]
    permissions: list[PermissionName]
    admin_area: bool
    #: A member's way into the forum (/forum): everybody with a membership.
    forum_area: bool
    #: /forum would open the forum itself (not the page saying what is still
    #: missing): the top bar then opens it in a new tab.
    forum_ready: bool = False
    teams_area: bool
    team_labels: TeamLabels
    counts: Counts
    admin_notices: list[Notice]


def _admin_notices():
    notices = []
    override = free_period_start_test_override()
    if override:
        notices.append(Notice(
            tone="danger",
            message=(f"Test setting active: the free period starts on {override[1]:02d}.{override[0]:02d}. "
                     "instead of 01.10. Remove TEST_FREE_PERIOD_START from .env before going live."),
        ))
    if is_paused():
        notice = Notice(
            tone="warning",
            message=("This portal was restored from a backup and its background jobs are paused: no emails, "
                     "forum sync or payment checks run on their own."),
        )
        if current_user.can(Permission.SYSTEM_BACKUP):
            notice.link_url, notice.link_label = "/admin/settings/backup", "Review and resume"
        notices.append(notice)
    return notices


def _forum_ready(member):
    """What /forum decides (blueprints/forum.py, forum_entry): it lets them in."""
    if member is None or not current_user.email_is_verified:
        return False
    from ..app import build_forum_context

    return bool(build_forum_context(member)["can_enter_forum"])


@endpoint("GET", "/me", response=MeOut, tag="Session")
def me():
    """The signed-in person, what they may see, and the counts for their menus."""
    member = current_user.member
    admin_area = current_user.can(Permission.ADMIN_ACCESS)
    singular, plural = team_labels()
    return MeOut(
        id=current_user.id,
        email=current_user.email,
        first_name=member.first_name if member is not None else None,
        last_name=member.last_name if member is not None else None,
        forum_username=current_user.forum_username,
        picture_url=picture_url(current_user),
        roles=sorted(role.slug for role in current_user.roles),
        permissions=sorted(current_user.permissions),
        admin_area=admin_area,
        forum_area=member is not None,
        forum_ready=_forum_ready(member),
        teams_area=teams_enabled(),
        team_labels=TeamLabels(singular=singular, plural=plural),
        counts=Counts(reviews_waiting=waiting_for_review_count(current_user)),
        admin_notices=_admin_notices() if admin_area else [],
    )
