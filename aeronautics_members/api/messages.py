"""Messages from one person (services/messages.py, docs/messages-plan.md):
the contact form, a message to a team's leads, and reading them -- the admins
on Admin › Messages, a team's leads on its Messages page.

Drawn by frontend/src/pages/public/Contact.tsx, the team page's "Message the
leads", frontend/src/pages/admin/Messages.tsx and
frontend/src/pages/teams/manage/Messages.tsx.
"""

from typing import Literal

from flask_login import current_user
from pydantic import Field

from ..app import limiter, rate_limit_network
from ..config import RATELIMIT_CONTACT, RATELIMIT_CONTACT_PER_IP
from ..permissions import Permission
from ..services import messages as messages_service
from ..services.teams import TeamPermission
from ._core import Model, UtcDateTime, endpoint
from .team_manage import _managed
from .teams import _team


class TopicOut(Model):
    value: str
    label: str


class ContactFormOut(Model):
    topics: list[TopicOut]
    #: Signed in: the message is sent as the account; name and address are shown, not asked.
    signed_in: bool
    name: str | None
    email: str | None


class ContactMessageIn(Model):
    topic: str
    #: Asked only of somebody not signed in.
    name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=300)
    subject: str = Field(max_length=300)
    message: str = Field(max_length=10000)
    #: The page they came from.
    page: str | None = Field(default=None, max_length=300)
    #: A field nobody sees; filled in, the message was written by a bot.
    website: str | None = Field(default=None, max_length=300)
    #: Seconds between opening the form and sending it.
    seconds: float | None = None


class TeamMessageIn(Model):
    subject: str = Field(max_length=300)
    message: str = Field(max_length=10000)
    website: str | None = Field(default=None, max_length=300)
    seconds: float | None = None


class MessageSentOut(Model):
    message: str


class ContactMessageOut(Model):
    id: int
    topic: str | None
    topic_label: str | None
    sender_name: str
    sender_email: str
    #: The sender's account, for somebody who was signed in.
    account_id: int | None
    membership: str | None
    page: str | None
    subject: str
    body: str
    created_at: UtcDateTime
    #: Whether the email to the recipients went out.
    delivered: bool
    done_at: UtcDateTime | None
    done_by: str | None


class ContactMessagesOut(Model):
    items: list[ContactMessageOut]
    open_count: int


class MessagesQuery(Model):
    state: Literal["open", "done", "all"] = "open"


class MessageDoneIn(Model):
    done: bool


def _name(user):
    if user is None:
        return None
    member = user.member
    return f"{member.first_name} {member.last_name}".strip() if member is not None else user.email


def message_out(message):
    context = message.context or {}
    return ContactMessageOut(
        id=message.id,
        topic=message.topic,
        topic_label=messages_service.TOPICS.get(message.topic) if message.topic else None,
        sender_name=message.sender_name,
        sender_email=message.sender_email,
        account_id=context.get("account_id"),
        membership=context.get("membership"),
        page=context.get("page"),
        subject=message.subject,
        body=message.body,
        created_at=message.created_at,
        delivered=message.delivered_at is not None,
        done_at=message.done_at,
        done_by=_name(message.done_by),
    )


def _list(team, state):
    return ContactMessagesOut(
        items=[message_out(message) for message in messages_service.messages(team=team, state=state)],
        open_count=messages_service.open_count(team),
    )


@endpoint("GET", "/contact", response=ContactFormOut, public=True, tag="Messages")
def contact_form():
    """The contact form's topics, and who is writing when somebody is signed in."""
    signed_in = current_user.is_authenticated
    name = email = None
    if signed_in:
        name, email = messages_service.sender_of(current_user)
    return ContactFormOut(
        topics=[TopicOut(value=value, label=label) for value, label in messages_service.TOPICS.items()],
        signed_in=signed_in, name=name, email=email,
    )


@endpoint("POST", "/contact", response=MessageSentOut, body=ContactMessageIn, public=True, tag="Messages")
@limiter.limit(RATELIMIT_CONTACT)
@limiter.limit(RATELIMIT_CONTACT_PER_IP, key_func=rate_limit_network)
def contact_send(body):
    """Send a message to the association's admins."""
    messages_service.send_contact(
        user=current_user if current_user.is_authenticated else None,
        name=body.name, email=body.email, topic=body.topic, subject=body.subject, body=body.message,
        page=body.page, trap=body.website, seconds=body.seconds,
    )
    return MessageSentOut(message="Sent. We answer by email.")


@endpoint("POST", "/teams/<slug>/message", response=MessageSentOut, body=TeamMessageIn, tag="Messages")
@limiter.limit(RATELIMIT_CONTACT)
def team_message_send(slug, body):
    """Send a message to a team's leads."""
    team = _team(slug)
    messages_service.send_to_team(user=current_user, team=team, subject=body.subject, body=body.message,
                                  trap=body.website, seconds=body.seconds)
    return MessageSentOut(message="Sent to the team's leads. They answer by email.")


@endpoint("GET", "/admin/messages", response=ContactMessagesOut, query=MessagesQuery,
          permissions=[Permission.MESSAGES_RECEIVE], tag="Messages")
def admin_messages(query):
    """The contact form's messages, newest first."""
    return _list(None, query.state)


@endpoint("PUT", "/admin/messages/<int:message_id>", response=ContactMessageOut, body=MessageDoneIn,
          permissions=[Permission.MESSAGES_RECEIVE], tag="Messages")
def admin_message_done(message_id, body):
    """Mark a message done (answered), or open again."""
    message = messages_service.message_of(message_id)
    return message_out(messages_service.mark_done(current_user, message, body.done))


@endpoint("GET", "/teams/<slug>/manage/messages", response=ContactMessagesOut, query=MessagesQuery, tag="Messages")
def team_messages(slug, query):
    """The messages written to the team's leads, newest first."""
    return _list(_managed(slug, TeamPermission.MESSAGES), query.state)


@endpoint("PUT", "/teams/<slug>/manage/messages/<int:message_id>", response=ContactMessageOut, body=MessageDoneIn,
          tag="Messages")
def team_message_done(slug, message_id, body):
    """Mark a message to the team done, or open again."""
    team = _managed(slug, TeamPermission.MESSAGES)
    message = messages_service.message_of(message_id, team)
    return message_out(messages_service.mark_done(current_user, message, body.done))
