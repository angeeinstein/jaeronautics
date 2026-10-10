"""Messages from one person: the contact form, to the site's admins, and a
message to one team's leads (docs/messages-plan.md).

A message is kept, and emailed to whoever should read it: the contact form's
to everybody who receives messages (``messages.receive``: the admins and super
admins, not the treasurer), a team's to its leads in force -- or, with none,
to the admins. The email carries the sender as Reply-To, so it is answered
from one's own mailbox, and the sender never sees a recipient's address until
somebody answers.

An email that could not be sent is tried again by the notification timer
(``deliver_waiting``); the message is in Admin › Messages, or on the team's
Messages page, either way. A message marked done is forgotten a year later.

Against spam, without a captcha service (the portal loads nothing from
elsewhere): a hidden field people do not see and bots fill in, a form sent
faster than a person types, and rate limits on the endpoint.
"""

from datetime import timedelta

from flask import current_app

from ..db_models import ContactMessage, Role, User, db
from ..mail_utils import send_mail
from ..permissions import Permission, roles_with
from ..security_utils import build_public_url
from . import NotFoundError, ValidationError
from .audit import log_audit_event
from .clock import get_now_utc
from .membership import member_has_active_access

TOPICS = {
    "membership": "Membership & payment",
    "account": "Account & forum",
    "teams": "Teams",
    "portal": "Problem with the portal",
    "other": "Something else",
}
NAME_MAX = 120
SUBJECT_MAX = 150
BODY_MAX = 5000
#: A form sent faster than this after it was opened was not typed by a person.
SOONEST_SECONDS = 3
#: An email that keeps failing is given up after this many tries; the message stays.
MOST_ATTEMPTS = 6
#: Forgotten this long after being marked done.
KEPT_AFTER_DONE = timedelta(days=365)
#: Forgotten this long after arriving, done or not.
KEPT_AT_MOST = timedelta(days=730)



def _fail(field, message):
    raise ValidationError(message, details={"fields": {field: message}})


def _clean(value, field, limit, *, required=True, what="This"):
    text = (value or "").strip()
    if required and not text:
        _fail(field, f"{what} is missing.")
    if len(text) > limit:
        _fail(field, f"At most {limit} characters.")
    return text


def _clean_email(value):
    address = _clean(value, "email", 255, what="An email address")
    if "@" not in address or " " in address or address.startswith("@") or address.endswith("@"):
        _fail("email", "This is not an email address.")
    return address


def _not_a_bot(trap, seconds):
    """A filled-in hidden field, or a form sent faster than anybody types."""
    if (trap or "").strip() or (seconds is not None and seconds < SOONEST_SECONDS):
        raise ValidationError("The message could not be sent. Wait a moment and try again.", code="refused")


def admin_recipients():
    """Everybody who receives the contact form's messages: verified, not erased."""
    slugs = roles_with(Permission.MESSAGES_RECEIVE)
    if not slugs:
        return []
    return list(db.session.execute(
        db.select(User.email).where(
            User.email_verified_at.is_not(None),
            User.deleted_at.is_(None),
            User.disabled_at.is_(None),
            User.roles.any(Role.slug.in_(slugs)),
        ).order_by(User.email)
    ).scalars())


def team_recipients(team):
    """The team's leads in force; with none, the admins."""
    from .teams import ROLE_LEAD, role_counts, role_holders

    leads = [team_role.user.email for team_role in role_holders(team, ROLE_LEAD) if role_counts(team_role)]
    return leads or admin_recipients()


def sender_of(user):
    member = user.member
    if member is not None:
        name = f"{member.first_name} {member.last_name}".strip()
    else:
        name = user.forum_username or user.email.split("@")[0]
    return name, user.email


def _context_of(user, page):
    """What helps answering: who this is, and where they were."""
    context = {"page": (page or "")[:200] or None}
    if user is None:
        return context
    member = user.member
    context.update({
        "account_id": user.id,
        "member_id": member.id if member is not None else None,
        "membership": "active" if member_has_active_access(member) else ("ended" if member else "none"),
    })
    return context


def send_contact(*, user, name=None, email=None, topic, subject, body, page=None, trap=None, seconds=None):
    """The contact form. Somebody signed in writes as their account."""
    _not_a_bot(trap, seconds)
    if topic not in TOPICS:
        _fail("topic", "Choose what it is about.")
    if user is not None:
        name, email = sender_of(user)
    else:
        name = _clean(name, "name", NAME_MAX, what="A name")
        email = _clean_email(email)
    message = ContactMessage(
        topic=topic,
        sender_user_id=user.id if user is not None else None,
        sender_name=name[:NAME_MAX],
        sender_email=email,
        subject=_clean(subject, "subject", SUBJECT_MAX, what="A subject"),
        body=_clean(body, "message", BODY_MAX, what="A message"),
        context=_context_of(user, page),
    )
    db.session.add(message)
    db.session.commit()
    deliver(message)
    return message


def send_to_team(*, user, team, subject, body, trap=None, seconds=None):
    """A message to one team's leads, from somebody signed in."""
    _not_a_bot(trap, seconds)
    from .teams import STATUS_ACTIVE

    if team is None or team.status != STATUS_ACTIVE:
        raise NotFoundError("This team takes no messages.")
    name, email = sender_of(user)
    message = ContactMessage(
        team_id=team.id,
        sender_user_id=user.id,
        sender_name=name[:NAME_MAX],
        sender_email=email,
        subject=_clean(subject, "subject", SUBJECT_MAX, what="A subject"),
        body=_clean(body, "message", BODY_MAX, what="A message"),
        context=_context_of(user, None),
    )
    db.session.add(message)
    db.session.commit()
    deliver(message)
    return message


def _sender_account():
    from .mailings import sender_account

    return sender_account()


def _open_url(message):
    if message.team_id:
        return build_public_url("teams.team_messages", slug=message.team.slug)
    return build_public_url("admin.admin_messages")


def deliver(message):
    """Email the message to whoever reads it. False when it has to wait."""
    recipients = team_recipients(message.team) if message.team_id else admin_recipients()
    message.delivery_attempts += 1
    if not recipients:
        message.delivery_error = "Nobody receives these messages."
        db.session.commit()
        return False
    where = message.team.name if message.team_id else TOPICS.get(message.topic, "Contact")
    lines = [
        f"From: {message.sender_name} <{message.sender_email}>",
        f"{'Team' if message.team_id else 'Topic'}: {where}",
    ]
    context = message.context or {}
    if context.get("account_id"):
        lines.append(f"Account no. {context['account_id']}, membership {context.get('membership')}")
    if context.get("page"):
        lines.append(f"Written on {context['page']}")
    failures = []
    for address in recipients:
        sent, error = send_mail(
            from_account=_sender_account(),
            to_email=address,
            subject=f"{'[' + message.team.name + '] ' if message.team_id else ''}{message.subject}",
            template_name="contact_message.html",
            heading=message.subject,
            details=lines,
            body_text=message.body,
            action_url=_open_url(message),
            action_label="Open in the portal",
            reply_to=message.sender_email,
            return_error=True,
        )
        if not sent:
            failures.append(error or "not sent")
    if failures and len(failures) == len(recipients):
        message.delivery_error = failures[0][:500]
        db.session.commit()
        return False
    message.delivered_at = get_now_utc()
    message.delivery_error = None
    db.session.commit()
    return True


def deliver_waiting(limit=20):
    """Messages whose email could not be sent yet, tried again (the notification timer)."""
    waiting = db.session.execute(
        db.select(ContactMessage)
        .where(ContactMessage.delivered_at.is_(None), ContactMessage.delivery_attempts < MOST_ATTEMPTS)
        .order_by(ContactMessage.created_at)
        .limit(limit)
    ).scalars().all()
    sent = 0
    for message in waiting:
        try:
            sent += bool(deliver(message))
        except Exception:  # noqa: BLE001 -- the next one is still tried
            db.session.rollback()
            current_app.logger.exception("Could not deliver contact message %s.", message.id)
    return sent


def messages(*, team=None, state="open"):
    """The admins' messages (no team) or one team's, newest first; "open", "done" or "all"."""
    query = db.select(ContactMessage).where(
        ContactMessage.team_id == team.id if team is not None else ContactMessage.team_id.is_(None)
    )
    if state == "open":
        query = query.where(ContactMessage.done_at.is_(None))
    elif state == "done":
        query = query.where(ContactMessage.done_at.is_not(None))
    return db.session.execute(query.order_by(ContactMessage.created_at.desc()).limit(200)).scalars().all()


def open_count(team=None):
    query = db.select(db.func.count()).select_from(ContactMessage).where(ContactMessage.done_at.is_(None))
    query = query.where(ContactMessage.team_id == team.id if team is not None else ContactMessage.team_id.is_(None))
    return db.session.scalar(query) or 0


def message_of(message_id, team=None):
    message = db.session.get(ContactMessage, message_id)
    if message is None or message.team_id != (team.id if team is not None else None):
        raise NotFoundError("No such message.")
    return message


def mark_done(actor, message, done=True):
    if done and message.done_at is None:
        message.done_at = get_now_utc()
        message.done_by_user_id = actor.id
    elif not done:
        message.done_at = None
        message.done_by_user_id = None
    log_audit_event(
        category="messages",
        event_type="message_done" if done else "message_reopened",
        actor_user=actor,
        metadata={"message_id": message.id, "team_id": message.team_id},
    )
    db.session.commit()
    return message


def forget_old(now=None):
    """Messages done a year ago, and any two years old, deleted (nightly)."""
    now = now or get_now_utc()
    old = db.session.execute(
        db.select(ContactMessage).where(db.or_(
            ContactMessage.done_at < now - KEPT_AFTER_DONE,
            ContactMessage.created_at < now - KEPT_AT_MOST,
        ))
    ).scalars().all()
    for message in old:
        db.session.delete(message)
    return len(old)


def sent_by(user):
    """What somebody wrote, for their data export."""
    return [
        {"at": message.created_at, "to": message.team.name if message.team_id else "The association",
         "topic": TOPICS.get(message.topic), "subject": message.subject, "message": message.body}
        for message in db.session.execute(
            db.select(ContactMessage).where(ContactMessage.sender_user_id == user.id)
            .order_by(ContactMessage.created_at)
        ).scalars()
    ]


def forget_sender(user):
    """At erasure: what somebody wrote goes with them."""
    for message in db.session.execute(
        db.select(ContactMessage).where(ContactMessage.sender_user_id == user.id)
    ).scalars().all():
        db.session.delete(message)
    db.session.execute(
        db.update(ContactMessage).where(ContactMessage.done_by_user_id == user.id).values(done_by_user_id=None)
    )
