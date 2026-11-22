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
- **Remote work** (docs/attendance-plan.md, "Remote work"): where an owner allows it
  (:func:`remote_enabled`), a member adds work done away from the readers themselves -- the day, from, to,
  and what they did -- as a stay marked ``remote`` (:func:`add_remote`). It counts at once. Only for today
  and the :data:`REMOTE_DAYS_BACK` days before, within one attendance day, never overlapping any of their
  own time, whoever it is for. They change or delete their own while it is in those days and not marked;
  whoever runs the owner's attendance marks one as not counting, with a reason (:func:`mark_not_counted`),
  or deletes it.
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
#: The association's switch for remote work; a team's is ``Team.remote_work_enabled``.
REMOTE_SETTING = "attendance_remote_enabled"
#: Remote work is added for today or one of the days before it, at most this many.
REMOTE_DAYS_BACK = 14
NOTE_MAX = 200

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
    """How long a stay was, in full minutes; None while open or not checked out."""
    if stay.ended_at is None:
        return None
    return max(0, int((_minute(stay.ended_at) - _minute(stay.started_at)).total_seconds() // 60))


def counted(stay):
    """What a stay counts: its minutes, or none once marked as not counting."""
    if stay.not_counted_at is not None:
        return 0
    return minutes(stay) or 0


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


def remote_enabled(owner):
    """Whether members add remote work for ``owner``: only while its attendance is on too."""
    if not enabled(owner):
        return False
    if owner is not None:
        return bool(owner.remote_work_enabled)
    return get_settings_map([REMOTE_SETTING]).get(REMOTE_SETTING) == "True"


def set_remote_enabled(actor, owner, on):
    """Remote work on or off for an owner. Entries already added stay either way."""
    current = bool(owner.remote_work_enabled) if owner is not None else (
        get_settings_map([REMOTE_SETTING]).get(REMOTE_SETTING) == "True")
    if current == bool(on):
        return
    if owner is not None:
        owner.remote_work_enabled = bool(on)
    else:
        set_setting_value(REMOTE_SETTING, "True" if on else None)
    log_audit_event("attendance", "attendance_remote_switched", actor_user=actor,
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
        # The person is locked (``_one_at_a_time``): read what is committed now.
        query = locked(query.where(AttendanceStay.user_id == user.id))
    found = db.session.execute(query).scalars().all()
    for stay in found:
        stay.closed_at = _stored(now)
    return len(found)


def _one_at_a_time(user):
    """A person's check-ins, check-outs and corrections, one after another:
    two readers tapped in the same instant both found no open stay, and both
    opened one. Their account row is the lock, held until commit.

    The reads after it that decide -- the open stay, the forgotten ones, an
    overlap -- are locking reads too: on MariaDB a plain read keeps seeing
    the transaction's first snapshot, from before the other request
    committed its stay, while a locking read sees what is committed."""
    db.session.execute(locked(db.select(User.id).where(User.id == user.id)))


def open_stay(user, owner, *, current=False):
    """The person's open stay with ``owner``; ``current`` (after
    ``_one_at_a_time``): read and locked as committed now."""
    query = _owner_filter(db.select(AttendanceStay).where(
        AttendanceStay.user_id == user.id, AttendanceStay.ended_at.is_(None), AttendanceStay.closed_at.is_(None)),
        owner).order_by(AttendanceStay.id.desc())
    return db.session.execute(locked(query) if current else query).scalars().first()


def check_in(device, user, now=None):
    """``user`` checked in at ``device``: the new stay, or ALREADY_IN with the open one."""
    now = now or get_now_utc()
    owner = device.team
    _one_at_a_time(user)
    close_forgotten(now, user=user)
    current = open_stay(user, owner, current=True)
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
    stay = open_stay(user, device.team, current=True)
    if stay is None:
        return NOT_IN, None
    stay.ended_at, stay.out_device_id = _stored(now), device.id
    return None, stay


# --- Corrections, by whoever runs attendance for the owner --------------------------------


def _snapshot(stay):
    snapshot = {"started_at": stay.started_at, "ended_at": stay.ended_at, "closed_at": stay.closed_at}
    if stay.remote:
        snapshot.update(remote=True, note=stay.note, not_counted_reason=stay.not_counted_reason)
    return snapshot


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
    clash = db.session.execute(locked(_owner_filter(query, owner))).scalars().first()
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


def mark_not_counted(actor, stay, reason, now=None):
    """A remote entry marked as not counting, with why: the member sees it, and who marked it."""
    if not stay.remote:
        raise ValidationError("Only remote work is marked; correct or delete a stay at a reader.",
                              code="attendance_not_remote")
    reason = (reason or "").strip()
    if not reason:
        message = "Say why it does not count."
        raise ValidationError(message, code="attendance_reason_missing", details={"fields": {"reason": message}})
    if len(reason) > NOTE_MAX:
        message = f"At most {NOTE_MAX} characters."
        raise ValidationError(message, code="attendance_reason_long", details={"fields": {"reason": message}})
    before = _snapshot(stay)
    stay.not_counted_at = _stored(now or get_now_utc())
    stay.not_counted_by_user_id = actor.id if actor is not None else None
    stay.not_counted_reason = reason
    log_audit_event("attendance", "attendance_marked_not_counted", actor_user=actor, target_user=stay.user,
                    before=before, after=_snapshot(stay), metadata={"stay_id": stay.id, "team_id": stay.team_id})
    return stay


def count_again(actor, stay):
    """A mark taken back: the entry counts again."""
    if stay.not_counted_at is None:
        return stay
    before = _snapshot(stay)
    stay.not_counted_at = stay.not_counted_by_user_id = stay.not_counted_reason = None
    log_audit_event("attendance", "attendance_counted_again", actor_user=actor, target_user=stay.user,
                    before=before, after=_snapshot(stay), metadata={"stay_id": stay.id, "team_id": stay.team_id})
    return stay


# --- Remote work, added by the member ------------------------------------------------------------


def remote_days(now=None):
    """The attendance days remote work may be added for: (the first, today)."""
    today = day_of(now or get_now_utc())
    return today - timedelta(days=REMOTE_DAYS_BACK), today


def may_add_remote(user, owner):
    return remote_enabled(owner) and may_check_in(user, owner)


def _field_error(field, message, code):
    raise ValidationError(message, code=code, details={"fields": {field: message}})


def _remote_times(day, start, end):
    """From and to on an attendance day, as stored: a time before 03:15 is the night after it,
    and 03:15 itself may end the day."""
    if day is None or start is None or end is None:
        raise ValidationError("Give the day, from and to.", code="attendance_times_missing")
    started = datetime.combine(day + timedelta(days=1) if start < DAY_DIVIDE else day, start,
                               tzinfo=MEMBERSHIP_TIMEZONE)
    ended = datetime.combine(day + timedelta(days=1) if end <= DAY_DIVIDE else day, end,
                             tzinfo=MEMBERSHIP_TIMEZONE)
    if ended <= started:
        _field_error("end", "It ends before it starts. Work past midnight can end up to 03:15.",
                     "attendance_times_order")
    return started, ended


def _checked_remote(user, owner, *, day, start, end, note, now, other_than=None):
    first, today = remote_days(now)
    if day is None or not first <= day <= today:
        _field_error("day", f"Today or one of the {REMOTE_DAYS_BACK} days before.", "attendance_remote_day")
    started, ended = _remote_times(day, start, end)
    if ended > _aware(now) + timedelta(minutes=1):
        _field_error("end", "That is still to come. Add it once it is done.", "attendance_times_future")
    note = (note or "").strip()
    if not note:
        _field_error("note", "Add a few words on what you did.", "attendance_note_missing")
    if len(note) > NOTE_MAX:
        _field_error("note", f"At most {NOTE_MAX} characters.", "attendance_note_long")
    # One person is in one place at a time: all their time counts, whoever it is for -- not a stay
    # closed as not checked out, nor an entry marked as not counting.
    query = db.select(AttendanceStay).where(
        AttendanceStay.user_id == user.id,
        AttendanceStay.not_counted_at.is_(None),
        AttendanceStay.started_at < _stored(ended),
        db.or_(AttendanceStay.ended_at.is_(None), AttendanceStay.ended_at > _stored(started)),
        db.or_(AttendanceStay.ended_at.is_not(None), AttendanceStay.closed_at.is_(None)),
    )
    if other_than is not None:
        query = query.where(AttendanceStay.id != other_than.id)
    clash = db.session.execute(locked(query.order_by(AttendanceStay.started_at))).scalars().first()
    if clash is not None:
        what = "your remote work" if clash.remote else (
            f"your time at {place_of(clash.in_device)}" if clash.in_device is not None else "your stay")
        whose = "" if clash.team_id == (owner.id if owner is not None else None) else f" for {owner_name(clash.team)}"
        until = f"{local(clash.ended_at):%H:%M}" if clash.ended_at is not None else "now"
        _field_error("start", f"It overlaps {what}{whose}, {local(clash.started_at):%H:%M} to {until}.",
                     "attendance_overlap")
    return _stored(started), _stored(ended), note


def add_remote(user, owner, *, day, start, end, note, now=None):
    """Remote work added by the member for ``owner``: it counts at once."""
    now = now or get_now_utc()
    if not may_add_remote(user, owner):
        raise ValidationError(f"Remote work is not added for {owner_name(owner)}.", code="attendance_remote_off")
    _one_at_a_time(user)
    started, ended, note = _checked_remote(user, owner, day=day, start=start, end=end, note=note, now=now)
    stay = AttendanceStay(user_id=user.id, team_id=owner.id if owner is not None else None, remote=True,
                          started_at=started, ended_at=ended, note=note)
    db.session.add(stay)
    db.session.flush()
    log_audit_event("attendance", "attendance_remote_added", actor_user=user, target_user=user,
                    after=_snapshot(stay), metadata={"stay_id": stay.id, "team_id": stay.team_id})
    return stay


def may_change_own(user, stay, now=None):
    """Whether the member may still change or delete their own remote entry: theirs, remote, not
    marked, its day still among the days remote work is added for, and remote work still on there."""
    if user is None or stay.user_id != user.id or not stay.remote or stay.not_counted_at is not None:
        return False
    first, _today = remote_days(now)
    return day_of(stay.started_at) >= first and may_add_remote(user, stay.team)


def own_remote(user, stay_id, now=None):
    """The member's own remote entry, while they may still change it."""
    stay = db.session.get(AttendanceStay, stay_id)
    if stay is None or stay.user_id != user.id or not stay.remote:
        raise NotFoundError("No such entry.", code="attendance_stay_unknown")
    if not may_change_own(user, stay, now):
        raise ValidationError("This entry can no longer be changed.", code="attendance_remote_locked")
    return stay


def change_remote(user, stay, *, day, start, end, note, now=None):
    now = now or get_now_utc()
    _one_at_a_time(user)
    before = _snapshot(stay)
    stay.started_at, stay.ended_at, stay.note = _checked_remote(
        user, stay.team, day=day, start=start, end=end, note=note, now=now, other_than=stay)
    log_audit_event("attendance", "attendance_remote_changed", actor_user=user, target_user=user,
                    before=before, after=_snapshot(stay), metadata={"stay_id": stay.id, "team_id": stay.team_id})
    return stay


def delete_remote(user, stay):
    _one_at_a_time(user)
    log_audit_event("attendance", "attendance_remote_deleted", actor_user=user, target_user=user,
                    before=_snapshot(stay), metadata={"stay_id": stay.id, "team_id": stay.team_id})
    db.session.delete(stay)


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
    """Per person between two attendance days: minutes counted -- all of them, and of those the
    remote work's -- stays, how many not checked out, how many remote entries marked as not
    counting: every member of a team, with a stay or not, and anyone else with a stay in that time
    (a former member). Sorted by name."""
    def blank(user):
        return {"user": user, "minutes": 0, "remote_minutes": 0, "stays": 0, "not_checked_out": 0,
                "not_counted": 0}

    rows = {user.id: blank(user) for user in members_of(owner)}
    for stay in stays(owner, start=start, end=end):
        row = rows.setdefault(stay.user_id, blank(stay.user))
        row["stays"] += 1
        row["minutes"] += counted(stay)
        if stay.remote:
            row["remote_minutes"] += counted(stay)
        row["not_checked_out"] += state(stay) == NOT_CHECKED_OUT
        row["not_counted"] += stay.not_counted_at is not None
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
         "corrected_at": stay.corrected_at,
         **({"remote": True, "what": stay.note, "not_counted_at": stay.not_counted_at,
             "not_counted_reason": stay.not_counted_reason} if stay.remote else {})}
        for stay in rows
    ]


def forget_for_erasure(user):
    """An erased account's stays deleted; corrections and marks they made keep no name. Returns how many."""
    db.session.execute(
        db.update(AttendanceStay).where(AttendanceStay.corrected_by_user_id == user.id)
        .values(corrected_by_user_id=None)
    )
    db.session.execute(
        db.update(AttendanceStay).where(AttendanceStay.not_counted_by_user_id == user.id)
        .values(not_counted_by_user_id=None)
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
