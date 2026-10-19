"""Attendance with the same cards (docs/attendance-plan.md; the reader's side:
docs/device-protocol.md, 8.2).

A reader in its *attendance* use shows *Check in* and *Check out*; one is
pressed and the card tapped. What is stored is a **stay** -- from checking in
to checking out -- that belongs to the reader's **owner**, a team or the
association (``owner`` None here), not to the reader: checked in at one of
its readers and out at another is one stay. The exact times are kept and
counted in **full minutes**: both ends by their minute (in at 11:15:59 counts
from 11:15).

- An owner records attendance only while it is switched on (:func:`enabled`):
  a team's by its leads or an admin, the association's by admins.
- Who may check in (:func:`may_check_in`): at a team's reader its active
  members, at the association's any active member -- one rule, in one place.
- An attendance **day** runs from 03:15 to 03:15 (:data:`DAY_DIVIDE`): a stay
  belongs to the day it started on, one begun after midnight to the evening
  before, and is never split. Checked in at 22:00, out at 01:30 is one stay.
- A stay still open from a day before is closed **not checked out**
  (:func:`close_forgotten`: every night at 03:15, on every page that shows
  stays, and whenever that person checks in or out) and counts nothing until
  somebody of the team sets when it ended (:func:`change_stay`). Corrections keep who made them; each is in
  the audit log with what it was before.
"""

from datetime import datetime, time, timedelta, timezone

from ..config import MEMBERSHIP_TIMEZONE
from ..db_models import AttendanceStay, Team, TeamMembership, User, db
from . import NotFoundError, ValidationError
from . import card_links, cards, device_types
from .audit import log_audit_event
from .clock import get_now_utc
from .locking import locked
from .settings import get_settings_map, set_setting_value

SLUG = "attendance"
#: The association's switch; a team's is ``Team.attendance_enabled``.
SETTING = "attendance_enabled"

ATTENDANCE_OFF = "attendance_off"
NOT_A_MEMBER = "not_a_member"
ALREADY_IN = "already_checked_in"
NOT_IN = "not_checked_in"

# What a stay is now, as pages show it.
OPEN = "open"
DONE = "done"
NOT_CHECKED_OUT = "not_checked_out"


# --- Times ---------------------------------------------------------------------------


def _aware(moment):
    return moment if moment is None or moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _stored(moment):
    """As the database keeps it: UTC, without a zone."""
    return _aware(moment).astimezone(timezone.utc).replace(tzinfo=None)


def local(moment):
    """A stored time as people read it here, with its offset: 2026-10-14T16:02:11+02:00."""
    return _aware(moment).astimezone(MEMBERSHIP_TIMEZONE)


def _minute(moment):
    return _aware(moment).replace(second=0, microsecond=0)


#: Where one attendance day ends and the next begins, local time: late nights
#: in the workshop stay on the evening they began. The night's run is at this
#: time too (``reconcile-billing``); Vienna's clock changes at 02:00/03:00, so
#: 03:15 is there every day.
DAY_DIVIDE = time(3, 15)


def day_start(day):
    """The first moment of an attendance day -- 03:15 local -- as stored (UTC)."""
    return _stored(datetime.combine(day, DAY_DIVIDE, tzinfo=MEMBERSHIP_TIMEZONE))


def day_of(moment):
    """The attendance day a moment belongs to: before 03:15, the day before."""
    moment = local(moment)
    return moment.date() - timedelta(days=1) if moment.time() < DAY_DIVIDE else moment.date()


def minutes(stay):
    """What a stay counts, in full minutes; None while open or not checked out."""
    if stay.ended_at is None:
        return None
    return max(0, int((_minute(stay.ended_at) - _minute(stay.started_at)).total_seconds() // 60))


def state(stay):
    if stay.ended_at is not None:
        return DONE
    return NOT_CHECKED_OUT if stay.closed_at is not None else OPEN


# --- Owners and the switch -------------------------------------------------------------


def owner_name(owner):
    from .devices import ASSOCIATION

    return owner.name if owner is not None else ASSOCIATION


def enabled(owner):
    if owner is not None:
        return bool(owner.attendance_enabled)
    return get_settings_map([SETTING]).get(SETTING) == "True"


def set_enabled(actor, owner, on):
    """Switch an owner's attendance on or off. Its stays stay either way."""
    if enabled(owner) == bool(on):
        return
    if owner is not None:
        owner.attendance_enabled = bool(on)
    else:
        set_setting_value(SETTING, "True" if on else None)
    log_audit_event("attendance", "attendance_switched", actor_user=actor,
                    metadata={"team_id": owner.id if owner is not None else None, "enabled": bool(on)})


def may_check_in(user, owner):
    """Whether ``user`` may check in at ``owner``'s readers: a team's active
    members at a team's, any active member at the association's. The one rule;
    another (anyone at a team's reader, say) is a change here only."""
    from .teams import active_team_membership, is_active_association_member

    if not is_active_association_member(user):
        return False
    return owner is None or active_team_membership(user, owner) is not None


def _owner_filter(query, owner):
    return query.where(AttendanceStay.team_id.is_(None) if owner is None else AttendanceStay.team_id == owner.id)


# --- Checking in and out -----------------------------------------------------------------


def close_forgotten(now=None, user=None):
    """Stays still open from an attendance day before closed as not checked
    out: at 03:15 the stays begun before it. Every night at 03:15, on pages
    that show stays, and for ``user`` when they check in or out. Returns how
    many."""
    now = now or get_now_utc()
    query = db.select(AttendanceStay).where(
        AttendanceStay.ended_at.is_(None), AttendanceStay.closed_at.is_(None),
        AttendanceStay.started_at < day_start(day_of(now)),
    )
    if user is not None:
        query = query.where(AttendanceStay.user_id == user.id)
    found = db.session.execute(query).scalars().all()
    for stay in found:
        stay.closed_at = _stored(now)
    return len(found)


def _one_at_a_time(user):
    """A person's check-ins, check-outs and corrections, one after another:
    two readers tapped in the same instant both found no open stay, and both
    opened one. Their account row is the lock, held until commit."""
    db.session.execute(locked(db.select(User.id).where(User.id == user.id)))


def open_stay(user, owner):
    query = db.select(AttendanceStay).where(
        AttendanceStay.user_id == user.id, AttendanceStay.ended_at.is_(None), AttendanceStay.closed_at.is_(None))
    return db.session.execute(_owner_filter(query, owner).order_by(AttendanceStay.id.desc())).scalars().first()


def check_in(device, user, now=None):
    """``user`` checked in at ``device``: the new stay, or ALREADY_IN with the open one."""
    now = now or get_now_utc()
    owner = device.team
    _one_at_a_time(user)
    close_forgotten(now, user=user)
    current = open_stay(user, owner)
    if current is not None:
        return ALREADY_IN, current
    stay = AttendanceStay(user_id=user.id, team_id=owner.id if owner is not None else None,
                          started_at=_stored(now), in_device_id=device.id)
    db.session.add(stay)
    db.session.flush()
    return None, stay


def check_out(device, user, now=None):
    """``user`` checked out at ``device``: the stay it ended, or NOT_IN."""
    now = now or get_now_utc()
    _one_at_a_time(user)
    close_forgotten(now, user=user)
    stay = open_stay(user, device.team)
    if stay is None:
        return NOT_IN, None
    stay.ended_at, stay.out_device_id = _stored(now), device.id
    return None, stay


# --- Corrections, by whoever runs attendance for the owner --------------------------------


def _snapshot(stay):
    return {"started_at": stay.started_at, "ended_at": stay.ended_at, "closed_at": stay.closed_at}


def _checked_times(stay_user, owner, started, ended, *, now, other_than=None):
    if started is None or ended is None:
        raise ValidationError("Give when it started and when it ended.", code="attendance_times_missing")
    started, ended = _aware(started), _aware(ended)
    if ended <= started:
        message = "It ends after it starts."
        raise ValidationError(message, code="attendance_times_order", details={"fields": {"ended_at": message}})
    if ended > _aware(now) + timedelta(minutes=1):
        message = "Not in the future."
        raise ValidationError(message, code="attendance_times_future", details={"fields": {"ended_at": message}})
    query = db.select(AttendanceStay).where(
        AttendanceStay.user_id == stay_user.id,
        AttendanceStay.started_at < _stored(ended),
        db.or_(AttendanceStay.ended_at.is_(None), AttendanceStay.ended_at > _stored(started)),
        db.or_(AttendanceStay.ended_at.is_not(None), AttendanceStay.closed_at.is_(None)),
    )
    if other_than is not None:
        query = query.where(AttendanceStay.id != other_than.id)
    clash = db.session.execute(_owner_filter(query, owner)).scalars().first()
    if clash is not None:
        message = f"It overlaps the stay from {local(clash.started_at):%d.%m.%Y %H:%M}."
        raise ValidationError(message, code="attendance_overlap", details={"fields": {"started_at": message}})
    return _stored(started), _stored(ended)


def add_stay(actor, owner, user, *, started_at, ended_at, now=None):
    """A stay entered by hand: one forgotten altogether."""
    now = now or get_now_utc()
    _one_at_a_time(user)
    started, ended = _checked_times(user, owner, started_at, ended_at, now=now)
    stay = AttendanceStay(user_id=user.id, team_id=owner.id if owner is not None else None,
                          started_at=started, ended_at=ended, corrected_by_user_id=actor.id if actor else None,
                          corrected_at=_stored(now))
    db.session.add(stay)
    db.session.flush()
    log_audit_event("attendance", "attendance_stay_added", actor_user=actor, target_user=user,
                    after=_snapshot(stay), metadata={"stay_id": stay.id, "team_id": stay.team_id})
    return stay


def change_stay(actor, stay, *, started_at, ended_at, now=None):
    """When a stay started and ended, set by hand -- a check-out forgotten, a
    wrong tap. A stay closed as not checked out then counts again."""
    now = now or get_now_utc()
    _one_at_a_time(stay.user)
    before = _snapshot(stay)
    stay.started_at, stay.ended_at = _checked_times(stay.user, stay.team, started_at, ended_at, now=now,
                                                    other_than=stay)
    stay.closed_at = None
    stay.corrected_by_user_id, stay.corrected_at = (actor.id if actor else None), _stored(now)
    log_audit_event("attendance", "attendance_stay_changed", actor_user=actor, target_user=stay.user,
                    before=before, after=_snapshot(stay), metadata={"stay_id": stay.id, "team_id": stay.team_id})
    return stay


def delete_stay(actor, stay):
    _one_at_a_time(stay.user)
    log_audit_event("attendance", "attendance_stay_deleted", actor_user=actor, target_user=stay.user,
                    before=_snapshot(stay), metadata={"stay_id": stay.id, "team_id": stay.team_id})
    db.session.delete(stay)


def stay_of(owner, stay_id):
    stay = db.session.get(AttendanceStay, stay_id)
    if stay is None or stay.team_id != (owner.id if owner is not None else None):
        raise NotFoundError("No such stay.", code="attendance_stay_unknown")
    return stay


# --- What is shown -------------------------------------------------------------------------


def stays(owner, *, user=None, start=None, end=None):
    """An owner's stays, newest first: of one person, and starting between the
    attendance days ``start`` and ``end`` (both included), if given."""
    query = _owner_filter(db.select(AttendanceStay), owner)
    if user is not None:
        query = query.where(AttendanceStay.user_id == user.id)
    if start is not None:
        query = query.where(AttendanceStay.started_at >= day_start(start))
    if end is not None:
        query = query.where(AttendanceStay.started_at < day_start(end + timedelta(days=1)))
    return db.session.execute(query.order_by(AttendanceStay.started_at.desc(), AttendanceStay.id.desc())).scalars().all()


def members_of(owner):
    """Who may check in now: a team's active members; for the association, nobody
    is listed by default (anyone with a stay is)."""
    if owner is None:
        return []
    from .teams import ACTIVE

    return db.session.execute(
        db.select(User).join(TeamMembership, TeamMembership.user_id == User.id)
        .where(TeamMembership.team_id == owner.id, TeamMembership.status == ACTIVE)
    ).scalars().all()


def eligible(owner):
    """Who a stay may be added for by hand: whoever may check in at the
    owner's readers -- a team's active members; for the association, every
    active member, not only those with a stay already. Sorted by name."""
    from .teams import is_active_association_member

    if owner is not None:
        people = members_of(owner)
    else:
        people = [user for user in db.session.execute(
            db.select(User).where(User.deleted_at.is_(None), User.disabled_at.is_(None))).scalars()
            if is_active_association_member(user)]
    return sorted(people, key=lambda user: person_name(user).lower())


def hours(owner, start, end):
    """Per person between two attendance days: minutes counted, stays, how many
    not checked out -- every member of a team, with a stay or not, and anyone
    else with a stay in that time (a former member). Sorted by name."""
    rows = {user.id: {"user": user, "minutes": 0, "stays": 0, "not_checked_out": 0} for user in members_of(owner)}
    for stay in stays(owner, start=start, end=end):
        row = rows.setdefault(stay.user_id, {"user": stay.user, "minutes": 0, "stays": 0, "not_checked_out": 0})
        row["stays"] += 1
        row["minutes"] += minutes(stay) or 0
        row["not_checked_out"] += state(stay) == NOT_CHECKED_OUT
    return sorted(rows.values(), key=lambda row: person_name(row["user"]).lower())


def present(owner, now=None):
    """Who is checked in right now, earliest first."""
    close_forgotten(now)
    query = _owner_filter(db.select(AttendanceStay), owner).where(
        AttendanceStay.ended_at.is_(None), AttendanceStay.closed_at.is_(None))
    return db.session.execute(query.order_by(AttendanceStay.started_at)).scalars().all()


def person_name(user):
    member = getattr(user, "member", None)
    if member is not None and (member.first_name or member.last_name):
        return " ".join(part for part in (member.first_name, member.last_name) if part)
    return user.email


def first_name(user):
    member = getattr(user, "member", None)
    return member.first_name if member is not None and member.first_name else None


def place_of(device):
    if device is None:
        return None
    from .devices import label

    return device.place or label(device)


# --- In a person's account -------------------------------------------------------------------


def owners_for(user):
    """The owners whose attendance a person sees on My Account, each with
    whether it is theirs now -- switched on, and they may check in there --
    or only kept because they have stays there. Teams by name, the
    association first."""
    found = {}
    for team_id in db.session.execute(
        db.select(AttendanceStay.team_id).where(AttendanceStay.user_id == user.id).distinct()
    ).scalars():
        found[team_id] = False
    if enabled(None) and may_check_in(user, None):
        found[None] = True
    from .teams import memberships_of

    for membership in memberships_of(user):
        team = membership.team
        if team is not None and team.attendance_enabled and may_check_in(user, team):
            found[team.id] = True
    owners = []
    for team_id, current in found.items():
        owner = db.session.get(Team, team_id) if team_id is not None else None
        if team_id is not None and owner is None:
            continue
        owners.append((owner, current))
    return sorted(owners, key=lambda pair: (pair[0] is not None, owner_name(pair[0]).lower()))


def area(user):
    """Where attendance shows on My Account: "menu" while it is theirs now
    somewhere, "data" while only stays are kept, None otherwise."""
    if user is None or not getattr(user, "is_authenticated", True):
        return None
    owners = owners_for(user)
    if any(current for _owner, current in owners):
        return "menu"
    return "data" if owners else None


def export_for(user):
    """A person's stays, for their data export."""
    rows = db.session.execute(
        db.select(AttendanceStay).where(AttendanceStay.user_id == user.id).order_by(AttendanceStay.started_at)
    ).scalars()
    return [
        {"for": owner_name(stay.team), "checked_in": stay.started_at, "checked_out": stay.ended_at,
         "minutes": minutes(stay), "not_checked_out": state(stay) == NOT_CHECKED_OUT,
         "corrected_at": stay.corrected_at}
        for stay in rows
    ]


def forget_for_erasure(user):
    """An erased account's stays deleted; corrections they made keep no name. Returns how many."""
    db.session.execute(
        db.update(AttendanceStay).where(AttendanceStay.corrected_by_user_id == user.id)
        .values(corrected_by_user_id=None)
    )
    return db.session.query(AttendanceStay).filter(AttendanceStay.user_id == user.id).delete(
        synchronize_session=False)


# --- The reader ------------------------------------------------------------------------------

MESSAGES = {
    **cards.REFUSALS,
    ATTENDANCE_OFF: "Attendance is switched off here.",
    NOT_A_MEMBER: "Only members of {owner} check in here.",
    ALREADY_IN: "Already checked in since {since}: check out first.",
    NOT_IN: "Not checked in here: check in first.",
}


def _refused(reason, **extra):
    message = MESSAGES[reason].format(**{key: value for key, value in extra.pop("_words", {}).items()})
    return {"result": "refused", "reason": reason, "message": message, **extra}


def _iso(moment):
    return local(moment).isoformat(timespec="seconds")


def _tapped(device, data, event_id):
    """The card checked for either report: (user, None), or (None, the refusal)."""
    card_id = data.get("card")
    if not isinstance(card_id, str) or not card_id.strip():
        raise ValidationError("Send the card's ID as \"card\".", code="attendance_card_missing")
    from .sales_points import _one_at_a_time

    earlier = _one_at_a_time(device, event_id)
    if earlier is not None:
        return None, earlier
    tap = cards.tapped(device, card_id)
    if tap.refused == cards.CARD_UNKNOWN:
        return None, _refused(tap.refused, link=card_links.for_device(card_links.offer(device, card_id)))
    if tap.refused is not None:
        return None, _refused(tap.refused)
    user = tap.card.user
    if not enabled(device.team):
        return None, _refused(ATTENDANCE_OFF, first_name=first_name(user))
    if not may_check_in(user, device.team):
        return None, _refused(NOT_A_MEMBER, first_name=first_name(user), _words={"owner": owner_name(device.team)})
    return user, None


def _check_in(device, data, event_id=None):
    user, refusal = _tapped(device, data, event_id)
    if refusal is not None:
        return refusal
    reason, stay = check_in(device, user)
    if reason == ALREADY_IN:
        return _refused(ALREADY_IN, first_name=first_name(user), since=_iso(stay.started_at),
                        _words={"since": f"{local(stay.started_at):%H:%M}"})
    return {"result": "checked_in", "first_name": first_name(user), "at": _iso(stay.started_at)}


def _check_out(device, data, event_id=None):
    user, refusal = _tapped(device, data, event_id)
    if refusal is not None:
        return refusal
    reason, stay = check_out(device, user)
    if reason == NOT_IN:
        return _refused(NOT_IN, first_name=first_name(user))
    return {"result": "checked_out", "first_name": first_name(user), "at": _iso(stay.ended_at),
            "since": _iso(stay.started_at), "duration_seconds": minutes(stay) * 60}


def _keep_card(data):
    from .sales_points import _keep_card as keep

    return keep(data)


def _summary(word):
    def summary(kept, answer):
        card = kept.get("card") or "A card"
        if answer.get("result") in ("checked_in", "checked_out"):
            return f"{card} {word}, {local(datetime.fromisoformat(answer['at'])):%H:%M}"
        return f"{card} refused: {answer.get('message') or answer.get('reason')}"

    return summary


device_types.register(device_types.DeviceType(
    slug=SLUG,
    label="Attendance",
    description="A card reader where members check in and out: who was there, and how long, for its owner.",
    reports={
        "check_in": device_types.Report(_check_in, description="Check in pressed, then a card tapped.",
                                        keep=_keep_card, summary=_summary("checked in")),
        "check_out": device_types.Report(_check_out, description="Check out pressed, then a card tapped.",
                                         keep=_keep_card, summary=_summary("checked out")),
    },
    reads_cards=True,
    firmware_notes="""\
**Check in or out:** two buttons, *Check in* and *Check out*; one is pressed, then the card tapped. Report
`check_in` or `check_out` with the card's ID in hex, `"data": {"card": "04A1B2C3D4E5F6"}`. The answer:

- `{"result": "checked_in", "first_name": "Alex", "at": "2026-10-14T16:02:11+02:00"}` -- "Welcome Alex, 16:02";
- `{"result": "checked_out", "first_name": "Alex", "at": "…", "since": "…", "duration_seconds": 9240}` --
  "Goodbye Alex, 18:36, 2 h 34 min" (whole minutes, as the portal counts them);
- `{"result": "refused", "reason": "...", "message": "..."}` -- `reason` one of `already_checked_in` (with
  `since`), `not_checked_in`, `not_a_member` (only the owner's members check in here), `attendance_off`, and
  the card refusals: `card_unknown` (with the QR code's `link`, as at a sales point), `card_disabled`,
  `card_blocked`, `account_off`. `first_name` comes with the refusals once the card is known.

Show `message` for a reason you do not know. Times are local, with their offset.""",
))
