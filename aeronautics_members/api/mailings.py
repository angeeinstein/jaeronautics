"""Emails to many members (services/mailings.py, docs/messages-plan.md).

- **Announcements:** Admin › Announcements (``announcements.send``).
- **Team mailings:** on a team's Messages page (``team.messages``).
- **Settings › Mailings:** the account they are sent from, and how many an
  hour and a day.
- **The news switch:** the page an email's "No more news by email" link
  opens (by its token, signed in or not), the one-click unsubscribe mail
  programs offer (List-Unsubscribe-Post), and My Account.
"""

from datetime import datetime
from typing import Literal

from flask_login import current_user
from pydantic import Field

from ..app import csrf
from ..db_models import Team, db
from ..mail_utils import load_mail_accounts_config
from ..member_categories import CATEGORY_LABELS, CATEGORY_ORDER
from ..permissions import Permission
from ..services import mailings as mailings_service
from ..services.teams import STATUS_ACTIVE, TeamPermission
from ._core import Model, UtcDateTime, endpoint
from .team_manage import _managed

TAG = "Mailings"
ANNOUNCE = [Permission.ANNOUNCEMENTS_SEND]
KIND_LABELS = {"news": "News", "notice": "Notice", "team": "Team mailing"}


class AudienceIn(Model):
    scope: Literal["all", "kinds", "teams", "leads"]
    kinds: list[str] = []
    teams: list[int] = []


class AnnouncementIn(Model):
    kind: Literal["news", "notice"]
    audience: AudienceIn
    subject: str = Field(max_length=300)
    body: str = Field(max_length=40000)
    #: The general assembly's date, for its invitation.
    assembly_at: datetime | None = None
    #: Sent although the general assembly is less than two weeks away.
    late_ok: bool = False


class CountIn(Model):
    kind: Literal["news", "notice"]
    audience: AudienceIn


class CountOut(Model):
    recipients: int
    #: Left out: they switched the news off.
    unsubscribed: int


class DraftIn(Model):
    kind: Literal["news", "notice"] = "news"
    subject: str = Field(max_length=300)
    body: str = Field(max_length=40000)


class TeamDraftIn(Model):
    subject: str = Field(max_length=300)
    body: str = Field(max_length=40000)


class TestSentOut(Model):
    #: Where the test went.
    to: str


class MailingOut(Model):
    id: int
    kind: str
    kind_label: str
    audience_label: str
    subject: str
    body: str
    assembly_at: UtcDateTime | None
    author: str | None
    created_at: UtcDateTime
    status: Literal["sending", "sent", "stopped"]
    finished_at: UtcDateTime | None
    recipient_count: int
    unsubscribed_count: int
    sent: int
    failed: int
    waiting: int
    stopped: int


class FailedOut(Model):
    email: str
    error: str | None


class MailingDetailOut(MailingOut):
    failed_addresses: list[FailedOut]


class MemberKindOut(Model):
    value: str
    label: str


class TeamChoiceOut(Model):
    id: int
    name: str


class LimitsOut(Model):
    per_hour: int
    per_day: int
    sender: str


class AnnouncementsOut(Model):
    items: list[MailingOut]
    kinds: list[MemberKindOut]
    teams: list[TeamChoiceOut]
    limits: LimitsOut


class TeamMailingsOut(Model):
    items: list[MailingOut]
    #: Whom a mailing would reach now: the team's active members.
    recipients: int


class MailingSettingsOut(Model):
    #: The chosen account; empty: the notifications' sender.
    sender: str
    #: What is used when none is chosen.
    default_sender: str
    accounts: list[str]
    per_hour: int
    per_day: int


class MailingSettingsIn(Model):
    sender: str = ""
    per_hour: int = Field(ge=1)
    per_day: int = Field(ge=1)


class MailingSettingsSavedOut(Model):
    changed: list[str]


class NewsOut(Model):
    email: str
    subscribed: bool
    unsubscribed_at: UtcDateTime | None


class NewsIn(Model):
    subscribed: bool


def _name(user):
    if user is None:
        return None
    member = user.member
    return f"{member.first_name} {member.last_name}".strip() if member is not None else user.email


def _mailing_out(mailing, model=MailingOut, **extra):
    counts = mailings_service.progress(mailing)
    return model(
        id=mailing.id, kind=mailing.kind, kind_label=KIND_LABELS.get(mailing.kind, mailing.kind),
        audience_label=mailings_service.audience_label(mailing.audience, mailing.team),
        subject=mailing.subject, body=mailing.body, assembly_at=mailing.assembly_at,
        author=_name(mailing.author), created_at=mailing.created_at, status=mailing.status,
        finished_at=mailing.finished_at, recipient_count=mailing.recipient_count,
        unsubscribed_count=mailing.unsubscribed_count,
        sent=counts.get("sent", 0), failed=counts.get("failed", 0), waiting=counts.get("waiting", 0),
        stopped=counts.get("stopped", 0), **extra,
    )


def _detail(mailing):
    return _mailing_out(mailing, MailingDetailOut, failed_addresses=[
        FailedOut(email=email, error=error) for email, error in mailings_service.failed_addresses(mailing)
    ])


def _news_out(user):
    return NewsOut(email=user.email, subscribed=user.news_unsubscribed_at is None,
                   unsubscribed_at=user.news_unsubscribed_at)


# --- Announcements ---------------------------------------------------------------------------


@endpoint("GET", "/admin/announcements", response=AnnouncementsOut, permissions=ANNOUNCE, tag=TAG)
def admin_announcements():
    """What was sent, newest first, and what can be chosen for a new one."""
    limits = mailings_service.settings()
    teams = db.session.execute(
        db.select(Team).where(Team.status == STATUS_ACTIVE).order_by(Team.name)
    ).scalars().all()
    return AnnouncementsOut(
        items=[_mailing_out(mailing) for mailing in mailings_service.mailings()],
        kinds=[MemberKindOut(value=kind, label=str(CATEGORY_LABELS[kind])) for kind in CATEGORY_ORDER],
        teams=[TeamChoiceOut(id=team.id, name=team.name) for team in teams],
        limits=LimitsOut(per_hour=limits["per_hour"], per_day=limits["per_day"],
                         sender=mailings_service.sender_account()),
    )


@endpoint("POST", "/admin/announcements/count", response=CountOut, body=CountIn, permissions=ANNOUNCE, tag=TAG)
def admin_announcement_count(body):
    """How many an announcement to this audience would reach."""
    audience = mailings_service.clean_audience(body.audience.model_dump())
    return CountOut(**mailings_service.count_for(audience, body.kind))


@endpoint("POST", "/admin/announcements/test", response=TestSentOut, body=DraftIn, permissions=ANNOUNCE, tag=TAG)
def admin_announcement_test(body):
    """The announcement as it would arrive, sent to oneself only."""
    return TestSentOut(to=mailings_service.send_test(current_user, kind=body.kind, subject=body.subject,
                                                     body=body.body))


@endpoint("POST", "/admin/announcements", response=MailingOut, body=AnnouncementIn, permissions=ANNOUNCE,
          status=201, tag=TAG)
def admin_announcement_send(body):
    """Send an announcement: queued, and sent within the hour's and the day's limits."""
    mailing = mailings_service.announce(
        current_user, kind=body.kind, audience=body.audience.model_dump(), subject=body.subject, body=body.body,
        assembly_at=body.assembly_at, late_ok=body.late_ok,
    )
    return _mailing_out(mailing)


@endpoint("GET", "/admin/announcements/<int:mailing_id>", response=MailingDetailOut, permissions=ANNOUNCE, tag=TAG)
def admin_announcement(mailing_id):
    """One announcement: its text, and how far it is."""
    return _detail(mailings_service.mailing_of(mailing_id))


@endpoint("POST", "/admin/announcements/<int:mailing_id>/stop", response=MailingDetailOut, permissions=ANNOUNCE,
          tag=TAG)
def admin_announcement_stop(mailing_id):
    """Stop an announcement: what has not gone out yet does not."""
    return _detail(mailings_service.stop(current_user, mailings_service.mailing_of(mailing_id)))


# --- Team mailings -------------------------------------------------------------------------------


@endpoint("GET", "/teams/<slug>/manage/mailings", response=TeamMailingsOut, tag=TAG)
def team_mailings(slug):
    """What the team sent its members, and how many a mailing would reach now."""
    team = _managed(slug, TeamPermission.MESSAGES)
    return TeamMailingsOut(
        items=[_mailing_out(mailing) for mailing in mailings_service.mailings(team=team)],
        recipients=len(mailings_service.people_for(None, team=team)),
    )


@endpoint("POST", "/teams/<slug>/manage/mailings/test", response=TestSentOut, body=TeamDraftIn, tag=TAG)
def team_mailing_test(slug, body):
    """The team's mailing as it would arrive, sent to oneself only."""
    team = _managed(slug, TeamPermission.MESSAGES)
    return TestSentOut(to=mailings_service.send_test(current_user, kind="team", subject=body.subject,
                                                     body=body.body, team=team))


@endpoint("POST", "/teams/<slug>/manage/mailings", response=MailingOut, body=TeamDraftIn, status=201, tag=TAG)
def team_mailing_send(slug, body):
    """Write to the team's active members; answers come back to whoever wrote it."""
    team = _managed(slug, TeamPermission.MESSAGES)
    return _mailing_out(mailings_service.write_to_team(current_user, team, subject=body.subject, body=body.body))


@endpoint("GET", "/teams/<slug>/manage/mailings/<int:mailing_id>", response=MailingDetailOut, tag=TAG)
def team_mailing(slug, mailing_id):
    """One of the team's mailings: its text, and how far it is."""
    team = _managed(slug, TeamPermission.MESSAGES)
    return _detail(mailings_service.mailing_of(mailing_id, team))


@endpoint("POST", "/teams/<slug>/manage/mailings/<int:mailing_id>/stop", response=MailingDetailOut, tag=TAG)
def team_mailing_stop(slug, mailing_id):
    """Stop one of the team's mailings."""
    team = _managed(slug, TeamPermission.MESSAGES)
    return _detail(mailings_service.stop(current_user, mailings_service.mailing_of(mailing_id, team)))


# --- Settings › Mailings ---------------------------------------------------------------------------


@endpoint("GET", "/admin/settings/mailings", response=MailingSettingsOut,
          permissions=[Permission.SETTINGS_GENERAL], tag="Admin")
def admin_settings_mailings():
    """The account mailings and messages are sent from, and how many an hour and a day."""
    current = mailings_service.settings()
    chosen = current["sender"]
    default = "" if chosen else mailings_service.sender_account()
    if chosen:
        from ..services.notifications import get_default_sender_account, get_notification_service

        default = get_notification_service().get_sender_account() or get_default_sender_account() or ""
    return MailingSettingsOut(sender=chosen, default_sender=default, accounts=sorted(load_mail_accounts_config()),
                              per_hour=current["per_hour"], per_day=current["per_day"])


@endpoint("PUT", "/admin/settings/mailings", response=MailingSettingsSavedOut, body=MailingSettingsIn,
          permissions=[Permission.SETTINGS_GENERAL], tag="Admin")
def admin_settings_mailings_save(body):
    """Choose the account mailings are sent from, and the provider's limits."""
    changed = mailings_service.save_settings(current_user, sender=body.sender, per_hour=body.per_hour,
                                             per_day=body.per_day)
    db.session.commit()
    return MailingSettingsSavedOut(changed=sorted(changed or []))


# --- The news switch ---------------------------------------------------------------------------------


@endpoint("GET", "/news/<token>", response=NewsOut, public=True, tag=TAG)
def news_by_token(token):
    """Whether the news reaches the address an email's link was for."""
    return _news_out(mailings_service.user_for_token(token))


@endpoint("PUT", "/news/<token>", response=NewsOut, body=NewsIn, public=True, tag=TAG)
def news_by_token_set(token, body):
    """Switch the news off, or on again, from an email's link."""
    user = mailings_service.user_for_token(token)
    return _news_out(mailings_service.set_news(user, body.subscribed, how="link"))


@endpoint("POST", "/news/unsubscribe/<token>", public=True, tag=TAG)
def news_unsubscribe_one_click(token):
    """The one-click unsubscribe a mail program sends (List-Unsubscribe-Post). Answers nothing."""
    mailings_service.set_news(mailings_service.user_for_token(token), False, how="one-click")


# The mail program posts this from its own servers, with no session and no
# form token; the token in the address is the authority.
csrf.exempt(news_unsubscribe_one_click)


@endpoint("GET", "/account/news", response=NewsOut, tag="Account")
def account_news():
    """Whether the association's news reaches me by email."""
    return _news_out(current_user)


@endpoint("PUT", "/account/news", response=NewsOut, body=NewsIn, tag="Account")
def account_news_set(body):
    """Switch the association's news by email on or off."""
    return _news_out(mailings_service.set_news(current_user, body.subscribed, how="account"))
