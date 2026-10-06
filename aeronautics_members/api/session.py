"""Who is signed in, and what the front end needs to start.

``GET /session`` is the front end's first call: whether anybody is signed in,
and the CSRF token it sends back as ``X-CSRFToken`` with every change.
``GET /me`` is the signed-in person: what to show in the top bar, which areas
and menu entries they may see, and the counts beside those entries. Seeing an
entry is only a convenience; every endpoint checks the permission again.
"""

from flask_login import current_user
from flask_wtf.csrf import generate_csrf

from ..permissions import Permission
from ..services.reviews import waiting_for_review_count
from ._core import Model, endpoint


class SessionOut(Model):
    signed_in: bool
    csrf_token: str


@endpoint("GET", "/session", response=SessionOut, public=True, tag="Session")
def session_state():
    """Whether somebody is signed in, and the CSRF token for changes."""
    return SessionOut(signed_in=bool(current_user.is_authenticated), csrf_token=generate_csrf())


class Counts(Model):
    """Numbers shown beside menu entries; 0 when there is nothing, or nothing the person may see."""

    reviews_waiting: int


class MeOut(Model):
    id: int
    email: str
    first_name: str | None
    last_name: str | None
    forum_username: str | None
    roles: list[str]
    permissions: list[str]
    admin_area: bool
    counts: Counts


@endpoint("GET", "/me", response=MeOut, tag="Session")
def me():
    """The signed-in person, what they may see, and the counts for their menus."""
    member = current_user.member
    return MeOut(
        id=current_user.id,
        email=current_user.email,
        first_name=member.first_name if member is not None else None,
        last_name=member.last_name if member is not None else None,
        forum_username=current_user.forum_username,
        roles=sorted(role.slug for role in current_user.roles),
        permissions=sorted(current_user.permissions),
        admin_area=current_user.can(Permission.ADMIN_ACCESS),
        counts=Counts(reviews_waiting=waiting_for_review_count(current_user)),
    )
