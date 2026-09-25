"""The Discourse boundary.

Forum accounts live in Discourse, not here. This module owns the handoff: it
decides what state a member's forum account *should* be in and asks Discourse to
match it, so membership remains authoritative for access while Discourse remains
authoritative for forum content.

A sync failure is reported to administrators rather than raised at the member,
because losing forum access briefly is better than failing the membership
operation that triggered the sync. The durable-outbox work is what will make
those retries automatic rather than manual.
"""

from flask import current_app
from flask_babel import _

from ..db_models import Member, User, db
from ..forum_service import (
    FORUM_SETTING_KEYS,
    FORUM_STATE_ANONYMISED,
    ForumProviderError,
    ForumService,
)
from ..security_utils import build_public_url
from .clock import get_now_utc
from ..notification_service import ADMIN_ERROR_CHANNEL
from .identity import build_email_verification_claims, generate_token
from .notifications import queue_curated_admin_notification
from .settings import get_settings_map




# What the portal builds to, and what it makes the forum accept: publishing
# profiles raises Discourse's max_username_length to at least this before it
# sends anybody, and leaves it raised.
#
# Thirty rather than Discourse's own default of twenty, because the scheme is
# surname, initial, cohort: Niedergrottenthaler needs twenty-four, and
# double-barrelled surnames such as SchachlLughofer sit on the default exactly.
# A limit that truncates a student's name is a limit that punishes them for
# their name, and Discourse truncates silently.
FORUM_USERNAME_LENGTH_LIMIT = 30


def get_forum_settings_map():
    return get_settings_map(FORUM_SETTING_KEYS)


def get_forum_service():
    return ForumService(get_forum_settings_map())


def build_forum_username_base(first_name, last_name, year_group,
                              limit=FORUM_USERNAME_LENGTH_LIMIT):
    last_name_cleaned = "".join(filter(str.isalnum, last_name or "")).capitalize()
    first_name_initial = first_name[0].upper() if first_name else ""
    study_field_initial = year_group[0].upper() if year_group else ""
    year_short = year_group[-2:] if year_group and len(year_group) > 2 else ""
    suffix = f"{study_field_initial}{year_short}"
    # A member who is not a student has no year group, and the separator exists
    # only to introduce one. Keeping it would hand them "HuberA_", which reads
    # as a name with something missing off the end.
    tail = f"{first_name_initial}_{suffix}" if suffix else first_name_initial
    # The surname gives way, not the year group: Discourse will not store a
    # username longer than its max_username_length and shortens it without
    # saying so, and what it cuts is the end -- which here is the cohort, the
    # part that tells two Hubers apart. Twenty-four characters is a real name
    # on this board, not a hypothetical one.
    if limit and len(last_name_cleaned) + len(tail) > limit:
        last_name_cleaned = last_name_cleaned[: max(limit - len(tail), 1)]
    return f"{last_name_cleaned}{tail}"


def generate_suggested_username(member):
    """Generates the base forum username using the legacy welcome-email scheme."""
    return build_forum_username_base(member.first_name, member.last_name, member.year_group)


def generate_unique_forum_username(first_name, last_name, year_group, exclude_user_id=None, preferred=None):
    base = preferred or build_forum_username_base(first_name, last_name, year_group)
    if not base:
        base = "Member"

    candidate = base
    suffix = 2
    while True:
        query = db.select(User).filter_by(forum_username=candidate)
        if exclude_user_id is not None:
            query = query.filter(User.id != exclude_user_id)
        existing_user = db.session.execute(query).scalar_one_or_none()
        if existing_user is None:
            return candidate
        candidate = f"{base}-{suffix}"
        suffix += 1


def sync_member_forum_state(member, raise_on_error=False):
    service = get_forum_service()
    if member is None or member.user is None:
        return None, service

    result = service.sync_member(member)
    if result and result.changed:
        db.session.flush()

    if result and result.error:
        current_app.logger.warning(
            "Forum sync reported an issue for member_id=%s user_id=%s desired_state=%s: %s",
            member.id,
            member.user_id,
            result.desired_state,
            result.error,
        )
        queue_curated_admin_notification(
            ADMIN_ERROR_CHANNEL,
            "forum_sync_failed",
            _("A forum synchronization attempt failed for %(email)s.", email=member.email_private),
            payload={
                "member_email": member.email_private,
                "forum_username": member.user.forum_username,
                "desired_state": result.desired_state,
                "error": result.error,
            },
            target_user=member.user,
            target_member=member,
            object_type="forum_account",
            object_id=result.forum_account.id if result and result.forum_account is not None else None,
        )
        if raise_on_error:
            raise ForumProviderError(result.error)

    return result, service


def log_out_forum_session_if_possible(user):
    if user is None or getattr(user, "forum_account", None) is None:
        return False, None

    service = get_forum_service()
    did_log_out, error = service.log_out_user(user)
    if error:
        current_app.logger.warning("Forum logout sync failed for user_id=%s: %s", user.id, error)
    return did_log_out, error


def anonymise_forum_account(user, queue_retry=True):
    """Anonymise the member's Discourse identity and clear the local link.

    Returns ``(anonymised, deferred)``. Erasure calls this with the local data
    about to disappear, so a Discourse outage must not abort it -- the work is
    queued on the outbox instead, and the local side is cleared either way.
    That is the safe order: the copy we control goes now, and the copy we do not
    control is retried until it goes too.
    """
    if user is None or getattr(user, "forum_account", None) is None:
        return False, False

    service = get_forum_service()
    anonymised, error = service.anonymize_user(user)

    deferred = False
    if error and queue_retry:
        from .outbox import enqueue_forum_anonymise

        current_app.logger.warning(
            "Discourse anonymisation failed for user_id=%s; queued for retry: %s", user.id, error
        )
        enqueue_forum_anonymise(user, reason="account_erasure")
        deferred = True

    forum_account = user.forum_account
    forum_account.state = FORUM_STATE_ANONYMISED
    forum_account.last_synced_email = None
    forum_account.last_synced_username = None
    forum_account.last_synced_at = get_now_utc()
    if not error:
        forum_account.last_error = None

    return anonymised, deferred


def build_forum_entry_url(user, include_token=False):
    route_values = {}
    if include_token and user is not None:
        route_values["token"] = generate_token(
            "forum-entry",
            issued_at=int(get_now_utc().timestamp()),
            **build_email_verification_claims(user),
        )
    return build_public_url("forum.forum_entry", **route_values)




def members_whose_forum_state_has_drifted():
    """Members the forum still believes something out of date about.

    Everything else that changes a membership is an event somebody causes --
    a payment, an admin switching an account off, a photograph approved -- and
    each of those syncs the forum there and then. One thing is not an event at
    all: a membership ending because its last day has passed. Nobody does
    anything, nothing is saved, and so nothing tells the forum. The person goes
    on reading the archive until somebody happens to touch their record.

    So this asks the question the passage of time never asks: for each linked
    member, what should their forum state be, and what does the forum account
    say it is? The comparison is local -- no call leaves the machine for the
    749 people whose answer has not changed -- and only the ones that differ
    are worth a sync.

    Returns ``[(member, state now, state it should be)]``.
    """
    service = get_forum_service()
    members = db.session.execute(
        db.select(Member).where(Member.user_id.is_not(None))
    ).scalars().all()

    drifted = []
    for member in members:
        if member.deleted_at is not None:
            continue
        account = member.user.forum_account if member.user is not None else None
        if account is None:
            # Never synced at all. Not drift -- there is nothing on the forum
            # to be out of date -- and syncing everybody who has never had a
            # forum account would be a different job with a different risk.
            continue
        if account.state == FORUM_STATE_ANONYMISED:
            # Terminal. The account was erased and the remote identity
            # anonymised; putting them back into groups would undo it.
            continue
        should_be = service.get_desired_state(member)
        if account.state != should_be:
            drifted.append((member, account.state, should_be))
    return drifted
