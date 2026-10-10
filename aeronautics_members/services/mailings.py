"""Emails to many members (docs/messages-plan.md): the association's
announcements (Admin › Announcements) and a team's mailing to its members
(the team's Messages page).

**Kinds.**
- *News* can be switched off: every news email ends with a link to do so,
  and carries the one-click unsubscribe header mail programs show
  (``List-Unsubscribe``). Subscribing again is on that page and in My
  Account.
- *Notices* -- the general assembly's invitation and the like -- cannot.
- *Team* mailings go to the team's active members whatever their news
  setting: "a team lead should be able to contact the team members".

**Who.** Only active, paying members of the association -- for a team, its
active members, never applicants. Chosen as all, some kinds of member, some
teams, or all team leads.

**Sending.** A mailing becomes one row per recipient, sent by the
notification timer every two minutes, as many at a time as the provider
allows (Settings › Mailings: the mail account, and how many per hour and
per day). A failed send is tried again twice. The mailing keeps its counts;
the recipient rows are forgotten a year later.

**The general assembly.** Statutes § 10 (3): every member is invited at
least two weeks ahead, by email to the address they gave, with the agenda.
An invitation sent later is refused unless it is confirmed as late.
"""

from datetime import timedelta, timezone

from flask import current_app
from markdown_it import MarkdownIt
from markupsafe import Markup

from ..db_models import Mailing, MailingRecipient, Member, Team, TeamMembership, User, db
from ..mail_utils import load_mail_accounts_config, send_mail
from ..member_categories import CATEGORY_LABELS, CATEGORY_ORDER
from ..security_utils import build_public_url
from . import NotFoundError, ValidationError
from .audit import log_audit_event
from .clock import get_now_utc
from .identity import generate_token, read_token
from .membership import member_has_active_access
from .settings import get_settings_map

NEWS = "news"
NOTICE = "notice"
TEAM = "team"
ANNOUNCEMENT_KINDS = (NEWS, NOTICE)

SCOPE_ALL = "all"
SCOPE_KINDS = "kinds"
SCOPE_TEAMS = "teams"
SCOPE_LEADS = "leads"
SCOPES = (SCOPE_ALL, SCOPE_KINDS, SCOPE_TEAMS, SCOPE_LEADS)

SENDING = "sending"
SENT = "sent"
STOPPED = "stopped"

PENDING = "pending"
FAILED = "failed"

SETTING_SENDER = "mailings_sender"
SETTING_PER_HOUR = "mailings_per_hour"
SETTING_PER_DAY = "mailings_per_day"
SETTING_KEYS = (SETTING_SENDER, SETTING_PER_HOUR, SETTING_PER_DAY)
DEFAULT_PER_HOUR = 100
#: Brevo's free plan: 300 a day.
DEFAULT_PER_DAY = 300
MOST_PER_HOUR = 5000
MOST_PER_DAY = 50000
#: At most this many in one run of the timer, whatever the limits allow.
PER_RUN = 40
#: Tries per recipient before it counts as failed.
MOST_ATTEMPTS = 3

SUBJECT_MAX = 150
BODY_MAX = 20000
ASSEMBLY_NOTICE = timedelta(days=14)
#: Recipient rows are forgotten this long after the mailing.
RECIPIENTS_KEPT = timedelta(days=365)

UNSUBSCRIBE_PURPOSE = "news-unsubscribe"

# Links as [text](https://...) or <https://...>: bare addresses are not turned
# into links, which would need another package.
_markdown = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False}).enable(
    ["table", "strikethrough"]
)


def _aware(moment):
    return moment if moment is None or moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _fail(field, message, code="validation_error"):
    raise ValidationError(message, code=code, details={"fields": {field: message}})


# --- Settings ---------------------------------------------------------------------------


def settings():
    stored = get_settings_map(SETTING_KEYS)

    def number(key, default):
        try:
            return max(1, int(stored.get(key) or default))
        except (TypeError, ValueError):
            return default

    return {
        "sender": (stored.get(SETTING_SENDER) or "").strip(),
        "per_hour": number(SETTING_PER_HOUR, DEFAULT_PER_HOUR),
        "per_day": number(SETTING_PER_DAY, DEFAULT_PER_DAY),
    }


def sender_account():
    """The mail account mailings and messages go out from: the one chosen
    here, else the one the notifications use."""
    chosen = settings()["sender"]
    if chosen:
        return chosen
    from .notifications import get_default_sender_account, get_notification_service

    return get_notification_service().get_sender_account() or get_default_sender_account() or ""


def save_settings(actor, *, sender, per_hour, per_day):
    from .settings_sections import _write

    sender = (sender or "").strip()
    if sender and sender not in load_mail_accounts_config():
        _fail("sender", "No such mail account (Settings › Mail accounts).")
    if not 1 <= per_hour <= MOST_PER_HOUR:
        _fail("per_hour", f"Between 1 and {MOST_PER_HOUR}.")
    if not 1 <= per_day <= MOST_PER_DAY:
        _fail("per_day", f"Between 1 and {MOST_PER_DAY}.")
    if per_hour > per_day:
        _fail("per_hour", "Not more per hour than per day.")
    return _write(actor, "mailings", {
        SETTING_SENDER: sender or None,
        SETTING_PER_HOUR: str(per_hour) if per_hour != DEFAULT_PER_HOUR else None,
        SETTING_PER_DAY: str(per_day) if per_day != DEFAULT_PER_DAY else None,
    })


# --- Who ----------------------------------------------------------------------------------


def _reachable(user):
    return (
        user is not None and user.deleted_at is None and user.disabled_at is None
        and bool(user.email) and member_has_active_access(user.member)
    )


def clean_audience(audience):
    """The audience as chosen, checked: {"scope", "kinds", "teams"}."""
    audience = dict(audience or {})
    scope = audience.get("scope")
    if scope not in SCOPES:
        _fail("audience", "Choose who it is for.")
    kinds = [kind for kind in CATEGORY_ORDER if kind in set(audience.get("kinds") or ())]
    teams = sorted({int(team_id) for team_id in audience.get("teams") or ()})
    if scope == SCOPE_KINDS and not kinds:
        _fail("audience", "Choose at least one kind of member.")
    if scope == SCOPE_TEAMS:
        if not teams:
            _fail("audience", "Choose at least one team.")
        known = set(db.session.execute(db.select(Team.id).where(Team.id.in_(teams))).scalars())
        if known != set(teams):
            _fail("audience", "One of the teams no longer exists.")
    return {"scope": scope, "kinds": kinds if scope == SCOPE_KINDS else [],
            "teams": teams if scope == SCOPE_TEAMS else []}


def audience_label(audience, team=None):
    scope = (audience or {}).get("scope")
    if team is not None:
        return f"The members of {team.name}"
    if scope == SCOPE_KINDS:
        return ", ".join(str(CATEGORY_LABELS[kind]) for kind in audience.get("kinds") or ()) or "Some kinds"
    if scope == SCOPE_TEAMS:
        names = db.session.execute(
            db.select(Team.name).where(Team.id.in_(audience.get("teams") or ())).order_by(Team.name)
        ).scalars().all()
        return "Members of " + ", ".join(names) if names else "Some teams"
    if scope == SCOPE_LEADS:
        return "All team leads"
    return "All active members"


def _team_members(team_ids):
    from .teams import ACTIVE

    return db.session.execute(
        db.select(User).join(TeamMembership, TeamMembership.user_id == User.id)
        .where(TeamMembership.team_id.in_(team_ids), TeamMembership.status == ACTIVE)
    ).scalars().unique().all()


def people_for(audience, *, team=None):
    """Every active member the audience means, each once, by address."""
    if team is not None:
        people = _team_members([team.id])
    else:
        scope = audience["scope"]
        if scope == SCOPE_TEAMS:
            people = _team_members(audience["teams"])
        elif scope == SCOPE_LEADS:
            from .teams import ROLE_LEAD, role_counts
            from ..db_models import TeamRole

            people = [team_role.user for team_role in db.session.execute(
                db.select(TeamRole).filter_by(role=ROLE_LEAD)
            ).scalars() if role_counts(team_role) and team_role.team.status == "active"]
        else:
            query = db.select(User).join(Member, Member.user_id == User.id)
            if scope == SCOPE_KINDS:
                query = query.where(Member.member_category.in_(audience["kinds"]))
            people = db.session.execute(query).scalars().all()
    chosen = {}
    for user in people:
        if _reachable(user):
            chosen.setdefault(user.id, user)
    return sorted(chosen.values(), key=lambda user: user.email.lower())


def count_for(audience, kind, *, team=None):
    """How many it would reach, and how many are left out for having switched the news off."""
    people = people_for(audience, team=team)
    unsubscribed = sum(1 for user in people if kind == NEWS and user.news_unsubscribed_at is not None)
    return {"recipients": len(people) - unsubscribed, "unsubscribed": unsubscribed}


# --- Writing --------------------------------------------------------------------------------


def render_body(text):
    """The Markdown as HTML for the email: formatting and links, never raw HTML."""
    return Markup(_markdown.render(text or ""))


def _clean_text(subject, body):
    subject = (subject or "").strip()
    body = (body or "").strip()
    if not subject:
        _fail("subject", "A subject is missing.")
    if len(subject) > SUBJECT_MAX:
        _fail("subject", f"At most {SUBJECT_MAX} characters.")
    if not body:
        _fail("body", "The text is missing.")
    if len(body) > BODY_MAX:
        _fail("body", f"At most {BODY_MAX} characters.")
    return subject, body


def _check_assembly(assembly_at, late_ok, now):
    if assembly_at is None:
        return None
    assembly_at = _aware(assembly_at)
    if assembly_at <= now:
        _fail("assembly_at", "The general assembly's date has passed.")
    if assembly_at - now < ASSEMBLY_NOTICE and not late_ok:
        _fail("assembly_at", "Less than two weeks before the general assembly: the statutes ask for two weeks' "
                             "notice (§ 10 (3)). Send anyway only if that is intended.", code="too_late")
    return assembly_at


def _start(mailing, people):
    unsubscribed = 0
    for user in people:
        if mailing.kind == NEWS and user.news_unsubscribed_at is not None:
            unsubscribed += 1
            continue
        mailing.recipients.append(MailingRecipient(user_id=user.id, email=user.email, status=PENDING))
    mailing.recipient_count = len(mailing.recipients)
    mailing.unsubscribed_count = unsubscribed
    if not mailing.recipients:
        _fail("audience", "Nobody to send it to.")
    db.session.add(mailing)
    db.session.flush()
    return mailing


def announce(actor, *, kind, audience, subject, body, assembly_at=None, late_ok=False, now=None):
    """An announcement from the association, queued for sending."""
    now = now or get_now_utc()
    if kind not in ANNOUNCEMENT_KINDS:
        _fail("kind", "News or a notice.")
    audience = clean_audience(audience)
    subject, body = _clean_text(subject, body)
    assembly_at = _check_assembly(assembly_at, late_ok, now)
    if assembly_at is not None:
        if kind != NOTICE or audience["scope"] != SCOPE_ALL:
            _fail("assembly_at", "The general assembly's invitation is a notice to all members.")
    mailing = _start(Mailing(
        kind=kind, audience=audience, subject=subject, body=body, assembly_at=assembly_at,
        author_user_id=actor.id, reply_to=None, status=SENDING, created_at=now,
    ), people_for(audience))
    log_audit_event(category="mailings", event_type="announcement_sent", actor_user=actor,
                    metadata={"mailing_id": mailing.id, "kind": kind, "audience": audience,
                              "recipients": mailing.recipient_count})
    db.session.commit()
    return mailing


def write_to_team(actor, team, *, subject, body, now=None):
    """A team's mailing to its active members; answers go to whoever wrote it."""
    from .teams import STATUS_ACTIVE

    if team.status != STATUS_ACTIVE:
        raise ValidationError("An archived team sends nothing.")
    subject, body = _clean_text(subject, body)
    mailing = _start(Mailing(
        team_id=team.id, kind=TEAM, audience={"scope": "team", "kinds": [], "teams": [team.id]},
        subject=subject, body=body, author_user_id=actor.id, reply_to=actor.email, status=SENDING,
        created_at=now or get_now_utc(),
    ), people_for(None, team=team))
    log_audit_event(category="mailings", event_type="team_mailing_sent", actor_user=actor,
                    metadata={"mailing_id": mailing.id, "team_id": team.id, "recipients": mailing.recipient_count})
    db.session.commit()
    return mailing


def stop(actor, mailing):
    """What has not gone out yet does not."""
    if mailing.status != SENDING:
        return mailing
    for recipient in mailing.recipients:
        if recipient.status == PENDING:
            recipient.status = STOPPED
    mailing.status = STOPPED
    mailing.finished_at = get_now_utc()
    log_audit_event(category="mailings", event_type="mailing_stopped", actor_user=actor,
                    metadata={"mailing_id": mailing.id})
    db.session.commit()
    return mailing


# --- The email ------------------------------------------------------------------------------


def unsubscribe_token(user):
    return generate_token(UNSUBSCRIBE_PURPOSE, user_id=user.id)


def user_for_token(token):
    try:
        data = read_token(token, UNSUBSCRIBE_PURPOSE, None)
        user = db.session.get(User, int(data.get("user_id")))
    except Exception:  # noqa: BLE001 -- any bad token is the same answer
        user = None
    if user is None or user.deleted_at is not None:
        raise NotFoundError("This link is not valid.")
    return user


def set_news(user, subscribed, *, how):
    """Switch the association's news on or off for ``user``."""
    if subscribed and user.news_unsubscribed_at is not None:
        user.news_unsubscribed_at = None
    elif not subscribed and user.news_unsubscribed_at is None:
        user.news_unsubscribed_at = get_now_utc()
    else:
        return user
    log_audit_event(category="mailings", event_type="news_subscribed" if subscribed else "news_unsubscribed",
                    actor_user=user, target_user=user, metadata={"how": how})
    db.session.commit()
    return user


def _from_name(mailing):
    return f"{mailing.team.name} via Joanneum Aeronautics" if mailing.team_id else None


def _why(mailing, user):
    if mailing.kind == TEAM:
        return f"You receive this as a member of {mailing.team.name} at Joanneum Aeronautics.", None
    if mailing.kind == NOTICE:
        return ("A notice to the members of Joanneum Aeronautics. It reaches every member, whatever their "
                "news setting."), None
    url = build_public_url("public.unsubscribe", token=unsubscribe_token(user))
    return "You receive this as a member of Joanneum Aeronautics.", url


def _send(mailing, user, to_email, *, subject_prefix=""):
    why, unsubscribe_url = _why(mailing, user)
    headers = {}
    if unsubscribe_url:
        one_click = build_public_url("api.news_unsubscribe_one_click", token=unsubscribe_token(user))
        headers = {"List-Unsubscribe": f"<{one_click}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    return send_mail(
        from_account=sender_account(),
        to_email=to_email,
        subject=f"{subject_prefix}{mailing.subject}",
        template_name="mailing.html",
        heading=mailing.subject,
        body_html=render_body(mailing.body),
        team_badge_name=mailing.team.name if mailing.team_id else None,
        why=why,
        unsubscribe_url=unsubscribe_url,
        reply_to=mailing.reply_to,
        from_name=_from_name(mailing),
        headers=headers,
        return_error=True,
    )


def send_test(actor, *, kind, subject, body, team=None):
    """The email as it would arrive, to whoever is writing it."""
    subject, body = _clean_text(subject, body)
    draft = Mailing(kind=TEAM if team is not None else kind, subject=subject, body=body,
                    team=team, team_id=team.id if team is not None else None,
                    reply_to=actor.email if team is not None else None)
    sent, error = _send(draft, actor, actor.email, subject_prefix="[Test] ")
    db.session.expunge(draft) if draft in db.session else None
    if not sent:
        raise ValidationError(f"The test could not be sent: {error}", code="not_sent")
    return actor.email


# --- The queue -----------------------------------------------------------------------------------


def _sent_since(moment):
    return db.session.scalar(
        db.select(db.func.count()).select_from(MailingRecipient).where(MailingRecipient.sent_at >= moment)
    ) or 0


def allowance(now=None):
    """How many may go out now, within the hour's and the day's limits."""
    now = now or get_now_utc()
    limits = settings()
    return max(0, min(
        PER_RUN,
        limits["per_hour"] - _sent_since(now - timedelta(hours=1)),
        limits["per_day"] - _sent_since(now - timedelta(days=1)),
    ))


def process(now=None):
    """Send what is due (the notification timer). Returns how many went out."""
    now = now or get_now_utc()
    room = allowance(now)
    sent = 0
    if room:
        waiting = db.session.execute(
            db.select(MailingRecipient).join(Mailing)
            .where(MailingRecipient.status == PENDING, Mailing.status == SENDING)
            .order_by(Mailing.created_at, MailingRecipient.id)
            .limit(room)
        ).scalars().all()
        failures_in_a_row = 0
        for recipient in waiting:
            recipient.attempts += 1
            ok, error = _send(recipient.mailing, recipient.user, recipient.email)
            if ok:
                recipient.status = SENT
                recipient.sent_at = get_now_utc()
                recipient.error = None
                sent += 1
                failures_in_a_row = 0
            else:
                recipient.error = (error or "not sent")[:500]
                if recipient.attempts >= MOST_ATTEMPTS:
                    recipient.status = FAILED
                failures_in_a_row += 1
            db.session.commit()
            if failures_in_a_row >= 3:
                # The mail server is refusing everything: next run.
                current_app.logger.warning("Mailings: three sends failed in a row; trying again later.")
                break
    _finish(now)
    return sent


def _finish(now):
    for mailing in db.session.execute(db.select(Mailing).where(Mailing.status == SENDING)).scalars():
        waiting = db.session.scalar(
            db.select(db.func.count()).select_from(MailingRecipient)
            .where(MailingRecipient.mailing_id == mailing.id, MailingRecipient.status == PENDING)
        )
        if not waiting:
            mailing.status = SENT
            mailing.finished_at = now
    db.session.commit()


def progress(mailing):
    final = (mailing.audience or {}).get("final")
    if final and not mailing.recipients:
        return final
    counts = dict(db.session.execute(
        db.select(MailingRecipient.status, db.func.count()).where(MailingRecipient.mailing_id == mailing.id)
        .group_by(MailingRecipient.status)
    ).all())
    return {"sent": counts.get(SENT, 0), "failed": counts.get(FAILED, 0), "waiting": counts.get(PENDING, 0),
            "stopped": counts.get(STOPPED, 0)}


def failed_addresses(mailing):
    return [(recipient.email, recipient.error) for recipient in mailing.recipients
            if recipient.status == FAILED and recipient.email]


def mailings(*, team=None, limit=100):
    query = db.select(Mailing).where(
        Mailing.team_id == team.id if team is not None else Mailing.team_id.is_(None)
    )
    return db.session.execute(query.order_by(Mailing.created_at.desc()).limit(limit)).scalars().all()


def mailing_of(mailing_id, team=None):
    mailing = db.session.get(Mailing, mailing_id)
    if mailing is None or mailing.team_id != (team.id if team is not None else None):
        raise NotFoundError("No such mailing.")
    return mailing


# --- Keeping ---------------------------------------------------------------------------------


def forget_old(now=None):
    """Recipient rows of mailings over a year old deleted (nightly); the mailing keeps its counts."""
    now = now or get_now_utc()
    old = db.session.execute(
        db.select(Mailing).where(Mailing.created_at < now - RECIPIENTS_KEPT, Mailing.recipients.any())
    ).scalars().all()
    forgotten = 0
    for mailing in old:
        counts = progress(mailing)
        mailing.audience = {**(mailing.audience or {}), "final": counts}
        forgotten += len(mailing.recipients)
        mailing.recipients.clear()
    return forgotten


def forget_user(user):
    """At erasure: the person's address leaves the recipient rows."""
    db.session.execute(
        db.update(MailingRecipient).where(MailingRecipient.user_id == user.id).values(email=None)
    )
