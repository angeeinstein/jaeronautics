"""Attendance (docs/attendance-plan.md; services/attendance.py): a member's own
stays on My Account, and the remote work they add, change and delete there; a
team's on its management page, for whoever holds *Attendance* there -- hours
per person for a period, a person's stays, who is checked in now, corrections,
remote work marked as not counting, a CSV, the switches; the association's on
Admin › Attendance, for admins. The readers' side is the device API
(`check_in`, `check_out`; docs/device-protocol.md, 8.2).
"""

import csv
import io
from datetime import date, datetime, time
from typing import Literal

from flask import Response
from flask_login import current_user

from ..db_models import Team, User, db
from ..permissions import Permission
from ..services import NotFoundError, PermissionError_
from ..services import attendance
from ..services import teams as teams_service
from ..services.clock import get_now_utc
from ._core import Model, UtcDateTime, endpoint

TAG = "Attendance"
ADMIN = [Permission.TEAMS_MANAGE]


class StayOut(Model):
    id: int
    started_at: UtcDateTime
    ended_at: UtcDateTime | None
    #: Counted, in full minutes; none while open or not checked out.
    minutes: int | None
    state: Literal["open", "done", "not_checked_out"]
    #: Where it was tapped; none when it was entered by hand.
    in_place: str | None
    out_place: str | None
    #: Who last corrected it (or entered it), and when.
    corrected_by: str | None
    corrected_at: UtcDateTime | None
    #: Remote work, added by the member, with what they did.
    remote: bool = False
    note: str | None = None
    #: Marked as not counting: when, by whom, why. ``minutes`` is still how long it was.
    not_counted_at: UtcDateTime | None = None
    not_counted_by: str | None = None
    not_counted_reason: str | None = None
    #: On My Account: the member may still change or delete it (their own remote work).
    may_change: bool = False


def _stay_out(stay, viewer=None):
    return StayOut(
        id=stay.id, started_at=stay.started_at, ended_at=stay.ended_at, minutes=attendance.minutes(stay),
        state=attendance.state(stay), in_place=attendance.place_of(stay.in_device),
        out_place=attendance.place_of(stay.out_device),
        corrected_by=attendance.person_name(stay.corrected_by) if stay.corrected_by is not None else None,
        corrected_at=stay.corrected_at, remote=bool(stay.remote), note=stay.note,
        not_counted_at=stay.not_counted_at,
        not_counted_by=attendance.person_name(stay.not_counted_by) if stay.not_counted_by is not None else None,
        not_counted_reason=stay.not_counted_reason,
        may_change=attendance.may_change_own(viewer, stay) if viewer is not None else False,
    )


# --- In a member's account ----------------------------------------------------------------


class OwnerStaysOut(Model):
    #: "association", or the team's slug.
    owner: str
    name: str
    #: Theirs now: switched on, and they may check in there. Otherwise only kept.
    current: bool
    #: Where to ask for a correction: the team's leads, or the contact form.
    ask: Literal["team", "contact"]
    #: They may add remote work here now.
    remote: bool = False
    stays: list[StayOut]


class AccountAttendanceOut(Model):
    owners: list[OwnerStaysOut]
    #: The attendance days remote work may be added for: from ``remote_from`` to ``today``.
    remote_from: date
    today: date


class RemoteIn(Model):
    #: The attendance day; a time before 03:15 is the night after it.
    day: date
    start: time
    end: time
    #: What they did; required.
    note: str


@endpoint("GET", "/account/attendance", response=AccountAttendanceOut, tag=TAG)
def account_attendance():
    """The person's own stays, per team (and the association): where attendance
    is on for them, and wherever they have stays."""
    attendance.close_forgotten()
    db.session.commit()
    owners = []
    for owner, current in attendance.owners_for(current_user):
        owners.append(OwnerStaysOut(
            owner=owner.slug if owner is not None else "association", name=attendance.owner_name(owner),
            current=current, ask="team" if owner is not None else "contact",
            remote=current and attendance.may_add_remote(current_user, owner),
            stays=[_stay_out(stay, current_user) for stay in attendance.stays(owner, user=current_user)],
        ))
    remote_from, today = attendance.remote_days()
    return AccountAttendanceOut(owners=owners, remote_from=remote_from, today=today)


def _owner_named(name):
    """``association``, or a team's slug."""
    if name == "association":
        return None
    team = db.session.execute(db.select(Team).where(Team.slug == name)).scalar_one_or_none()
    if team is None:
        raise NotFoundError("No such team.", code="team_unknown")
    return team


@endpoint("POST", "/account/attendance/<owner>/remote", response=StayOut, body=RemoteIn, status=201, tag=TAG)
def account_remote_add(owner, body):
    """Remote work added for a team (its slug) or the association (``association``): it counts at once."""
    stay = attendance.add_remote(current_user, _owner_named(owner), day=body.day, start=body.start, end=body.end,
                                 note=body.note)
    db.session.commit()
    return _stay_out(stay, current_user)


@endpoint("PUT", "/account/attendance/remote/<int:stay_id>", response=StayOut, body=RemoteIn, tag=TAG)
def account_remote_change(stay_id, body):
    """One's own remote work changed, while it may be: in the last days, not marked."""
    stay = attendance.change_remote(current_user, attendance.own_remote(current_user, stay_id), day=body.day,
                                    start=body.start, end=body.end, note=body.note)
    db.session.commit()
    return _stay_out(stay, current_user)


@endpoint("DELETE", "/account/attendance/remote/<int:stay_id>", tag=TAG)
def account_remote_delete(stay_id):
    """One's own remote work deleted, while it may be."""
    attendance.delete_remote(current_user, attendance.own_remote(current_user, stay_id))
    db.session.commit()


# --- An owner's: a team's, the association's ---------------------------------------------------


class PeriodQuery(Model):
    #: Attendance days (03:15 to 03:15), both included; default: this month up to today.
    start: date | None = None
    end: date | None = None


class HoursRowOut(Model):
    user_id: int
    name: str
    #: Counted, all of it; of that, the remote work's.
    minutes: int
    remote_minutes: int = 0
    stays: int
    not_checked_out: int
    #: Remote entries marked as not counting.
    not_counted: int = 0


class PresentOut(Model):
    stay_id: int
    user_id: int
    name: str
    since: UtcDateTime
    place: str | None


class AttendancePersonOut(Model):
    user_id: int
    name: str


class AttendanceOut(Model):
    enabled: bool
    #: Members add remote work (only while attendance is on).
    remote_enabled: bool = False
    start: date
    end: date
    rows: list[HoursRowOut]
    present: list[PresentOut]
    #: Whom a stay may be added for: a team's active members; for the association, every active member.
    people: list[AttendancePersonOut]


class PersonStaysOut(Model):
    user_id: int
    name: str
    stays: list[StayOut]


class StayIn(Model):
    user_id: int
    started_at: datetime
    ended_at: datetime


class StayChangeIn(Model):
    started_at: datetime
    ended_at: datetime


class SwitchIn(Model):
    enabled: bool


class SwitchOut(Model):
    enabled: bool


class NotCountedIn(Model):
    #: Why it does not count; the member sees it.
    reason: str


def _period(query):
    today = attendance.day_of(get_now_utc())
    start = query.start or today.replace(day=1)
    end = query.end or today
    if end < start:
        start, end = end, start
    return start, end


def _overview(owner, query):
    attendance.close_forgotten()
    db.session.commit()
    start, end = _period(query)
    return AttendanceOut(
        enabled=attendance.enabled(owner), remote_enabled=attendance.remote_enabled(owner), start=start, end=end,
        rows=[HoursRowOut(user_id=row["user"].id, name=attendance.person_name(row["user"]), minutes=row["minutes"],
                          remote_minutes=row["remote_minutes"], stays=row["stays"],
                          not_checked_out=row["not_checked_out"], not_counted=row["not_counted"])
              for row in attendance.hours(owner, start, end)],
        present=[PresentOut(stay_id=stay.id, user_id=stay.user_id, name=attendance.person_name(stay.user),
                            since=stay.started_at, place=attendance.place_of(stay.in_device))
                 for stay in attendance.present(owner)],
        people=[AttendancePersonOut(user_id=user.id, name=attendance.person_name(user))
                for user in attendance.eligible(owner)],
    )


def _person(owner, user_id):
    """Somebody whose stays this owner may see: a member now, or anyone with a stay there."""
    user = db.session.get(User, user_id)
    if user is None or not (attendance.may_check_in(user, owner) or attendance.stays(owner, user=user)):
        raise NotFoundError("No such person here.", code="attendance_person_unknown")
    return user


def _person_out(owner, user, query):
    start, end = (query.start, query.end) if query is not None else (None, None)
    return PersonStaysOut(user_id=user.id, name=attendance.person_name(user),
                          stays=[_stay_out(stay) for stay in attendance.stays(owner, user=user, start=start, end=end)])


def _add(owner, body):
    user = _person(owner, body.user_id)
    stay = attendance.add_stay(current_user, owner, user, started_at=body.started_at, ended_at=body.ended_at)
    db.session.commit()
    return _stay_out(stay)


def _change(owner, stay_id, body):
    stay = attendance.change_stay(current_user, attendance.stay_of(owner, stay_id), started_at=body.started_at,
                                  ended_at=body.ended_at)
    db.session.commit()
    return _stay_out(stay)


def _delete(owner, stay_id):
    attendance.delete_stay(current_user, attendance.stay_of(owner, stay_id))
    db.session.commit()


def _switch(owner, body):
    attendance.set_enabled(current_user, owner, body.enabled)
    db.session.commit()
    return SwitchOut(enabled=attendance.enabled(owner))


def _remote_switch(owner, body):
    attendance.set_remote_enabled(current_user, owner, body.enabled)
    db.session.commit()
    return SwitchOut(enabled=attendance.remote_enabled(owner))


def _not_counted(owner, stay_id, body):
    stay = attendance.mark_not_counted(current_user, attendance.stay_of(owner, stay_id), body.reason)
    db.session.commit()
    return _stay_out(stay)


def _count_again(owner, stay_id):
    stay = attendance.count_again(current_user, attendance.stay_of(owner, stay_id))
    db.session.commit()
    return _stay_out(stay)


def _csv(owner, query, name):
    """Every stay of the period, a row each: for passing the hours on."""
    start, end = _period(query)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Name", "Date", "From", "To", "Minutes counted", "Kind", "What", "Note"])
    for stay in reversed(attendance.stays(owner, start=start, end=end)):
        state = attendance.state(stay)
        if stay.not_counted_at is not None:
            note = f"Not counted: {stay.not_counted_reason or ''}".strip()
        elif state == attendance.NOT_CHECKED_OUT:
            note = "Not checked out"
        elif state == attendance.OPEN:
            note = "Checked in now"
        else:
            note = "Corrected" if stay.corrected_at else ""
        writer.writerow([
            teams_service.csv_cell(attendance.person_name(stay.user)), f"{attendance.local(stay.started_at):%d.%m.%Y}",
            f"{attendance.local(stay.started_at):%H:%M}",
            f"{attendance.local(stay.ended_at):%H:%M}" if stay.ended_at else "",
            attendance.counted(stay) if stay.ended_at is not None else "",
            "Remote" if stay.remote else "At the readers",
            teams_service.csv_cell(stay.note or ""), teams_service.csv_cell(note),
        ])
    response = Response(buffer.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f'attachment; filename="attendance-{name}-{start:%Y%m%d}-{end:%Y%m%d}.csv"'
    return response


def _team_owner(slug):
    from .teams import _team

    team = _team(slug)
    if not teams_service.can_in_team(current_user, team, teams_service.TeamPermission.ATTENDANCE):
        raise PermissionError_("You may not do that in this team.", code="team_permission")
    return team


@endpoint("GET", "/teams/<slug>/manage/attendance", response=AttendanceOut, query=PeriodQuery, tag=TAG)
def team_attendance(slug, query):
    """Hours per person for a period, and who is checked in now."""
    return _overview(_team_owner(slug), query)


@endpoint("GET", "/teams/<slug>/manage/attendance/people/<int:user_id>", response=PersonStaysOut,
          query=PeriodQuery, tag=TAG)
def team_attendance_person(slug, user_id, query):
    """One person's stays here, newest first: for a period, or all of them."""
    owner = _team_owner(slug)
    return _person_out(owner, _person(owner, user_id), query)


@endpoint("POST", "/teams/<slug>/manage/attendance/stays", response=StayOut, body=StayIn, status=201, tag=TAG)
def team_attendance_add(slug, body):
    """A stay forgotten altogether, entered by hand."""
    return _add(_team_owner(slug), body)


@endpoint("PUT", "/teams/<slug>/manage/attendance/stays/<int:stay_id>", response=StayOut, body=StayChangeIn,
          tag=TAG)
def team_attendance_change(slug, stay_id, body):
    """When a stay started and ended, set by hand."""
    return _change(_team_owner(slug), stay_id, body)


@endpoint("DELETE", "/teams/<slug>/manage/attendance/stays/<int:stay_id>", tag=TAG)
def team_attendance_delete(slug, stay_id):
    """A stay deleted."""
    _delete(_team_owner(slug), stay_id)


@endpoint("PUT", "/teams/<slug>/manage/attendance/switch", response=SwitchOut, body=SwitchIn, tag=TAG)
def team_attendance_switch(slug, body):
    """Attendance on or off for the team: off, its readers refuse check-ins; its stays stay."""
    return _switch(_team_owner(slug), body)


@endpoint("PUT", "/teams/<slug>/manage/attendance/remote-switch", response=SwitchOut, body=SwitchIn, tag=TAG)
def team_attendance_remote_switch(slug, body):
    """Remote work on or off for the team's members; what they added stays."""
    return _remote_switch(_team_owner(slug), body)


@endpoint("PUT", "/teams/<slug>/manage/attendance/stays/<int:stay_id>/not-counted", response=StayOut,
          body=NotCountedIn, tag=TAG)
def team_attendance_not_counted(slug, stay_id, body):
    """A remote entry marked as not counting, with why."""
    return _not_counted(_team_owner(slug), stay_id, body)


@endpoint("DELETE", "/teams/<slug>/manage/attendance/stays/<int:stay_id>/not-counted", response=StayOut, tag=TAG)
def team_attendance_count_again(slug, stay_id):
    """The mark taken back: it counts again."""
    return _count_again(_team_owner(slug), stay_id)


@endpoint("GET", "/teams/<slug>/manage/attendance.csv", query=PeriodQuery, tag=TAG, produces="text/csv")
def team_attendance_csv(slug, query):
    """Every stay of a period, as a CSV file."""
    team = _team_owner(slug)
    return _csv(team, query, team.slug)


@endpoint("GET", "/admin/attendance", response=AttendanceOut, query=PeriodQuery, permissions=ADMIN, tag=TAG)
def association_attendance(query):
    """The association's readers: hours per person for a period, and who is checked in now."""
    return _overview(None, query)


@endpoint("GET", "/admin/attendance/people/<int:user_id>", response=PersonStaysOut, query=PeriodQuery,
          permissions=ADMIN, tag=TAG)
def association_attendance_person(user_id, query):
    """One person's stays at the association's readers."""
    return _person_out(None, _person(None, user_id), query)


@endpoint("POST", "/admin/attendance/stays", response=StayOut, body=StayIn, status=201, permissions=ADMIN, tag=TAG)
def association_attendance_add(body):
    """A stay forgotten altogether, entered by hand."""
    return _add(None, body)


@endpoint("PUT", "/admin/attendance/stays/<int:stay_id>", response=StayOut, body=StayChangeIn, permissions=ADMIN,
          tag=TAG)
def association_attendance_change(stay_id, body):
    """When a stay started and ended, set by hand."""
    return _change(None, stay_id, body)


@endpoint("DELETE", "/admin/attendance/stays/<int:stay_id>", permissions=ADMIN, tag=TAG)
def association_attendance_delete(stay_id):
    """A stay deleted."""
    _delete(None, stay_id)


@endpoint("PUT", "/admin/attendance/switch", response=SwitchOut, body=SwitchIn, permissions=ADMIN, tag=TAG)
def association_attendance_switch(body):
    """Attendance on or off at the association's readers."""
    return _switch(None, body)


@endpoint("PUT", "/admin/attendance/remote-switch", response=SwitchOut, body=SwitchIn, permissions=ADMIN, tag=TAG)
def association_attendance_remote_switch(body):
    """Remote work on or off for the association."""
    return _remote_switch(None, body)


@endpoint("PUT", "/admin/attendance/stays/<int:stay_id>/not-counted", response=StayOut, body=NotCountedIn,
          permissions=ADMIN, tag=TAG)
def association_attendance_not_counted(stay_id, body):
    """A remote entry marked as not counting, with why."""
    return _not_counted(None, stay_id, body)


@endpoint("DELETE", "/admin/attendance/stays/<int:stay_id>/not-counted", response=StayOut, permissions=ADMIN,
          tag=TAG)
def association_attendance_count_again(stay_id):
    """The mark taken back: it counts again."""
    return _count_again(None, stay_id)


@endpoint("GET", "/admin/attendance.csv", query=PeriodQuery, permissions=ADMIN, tag=TAG, produces="text/csv")
def association_attendance_csv(query):
    """Every stay of a period at the association's readers, as a CSV file."""
    return _csv(None, query, "association")
