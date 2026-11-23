# ruff: noqa: F811 -- the fixtures shared with test_attendance are named as parameters.
"""Remote work in attendance (services/attendance.py, docs/attendance-plan.md, "Remote work"):
a member adds work done away from the readers -- the day, from, to, what they did -- where the
team (or the association) allows it. It counts at once; only for today and the 14 days before,
within one attendance day, never overlapping their own time wherever it counts. They change or
delete their own until a lead marks it as not counted, with a reason they see.
"""
from datetime import date, datetime, time, timedelta, timezone

import pytest

from conftest import db
from aeronautics_members.db_models import AttendanceStay, AuditLog
from aeronautics_members.services import NotFoundError, ValidationError, attendance, privacy
from test_attendance import anna, doors, rocket  # noqa: F401
from test_teams_flow import _person, switched_on  # noqa: F401

UTC = timezone.utc
# Today in these tests: Tuesday 20.10.2026, 12:00 in Vienna (summer time, UTC+2).
TODAY = date(2026, 10, 20)
NOW = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)


@pytest.fixture
def clock(monkeypatch):
    from aeronautics_members.api import attendance as attendance_api

    monkeypatch.setattr(attendance, "get_now_utc", lambda: NOW)
    monkeypatch.setattr(attendance_api, "get_now_utc", lambda: NOW)
    return NOW


@pytest.fixture
def remote_on(rocket):
    team, _lead = rocket
    team.remote_work_enabled = True
    db.session.commit()
    return team


def _add(user, owner, day=TODAY - timedelta(days=1), start=time(18, 0), end=time(20, 30), note="Wing rib CAD"):
    stay = attendance.add_remote(user, owner, day=day, start=start, end=end, note=note)
    db.session.commit()
    return stay


def _fields(error):
    return error.value.details["fields"]


class TestAdding:
    def test_it_counts_at_once_and_is_not_here_now(self, app, anna, remote_on, clock):
        stay = _add(anna, remote_on)

        assert (stay.remote, stay.note, attendance.minutes(stay), attendance.counted(stay)) == (
            True, "Wing rib CAD", 150, 150)
        assert attendance.local(stay.started_at).strftime("%d.%m. %H:%M") == "19.10. 18:00"
        [row] = [row for row in attendance.hours(remote_on, date(2026, 10, 1), TODAY) if row["user"] == anna]
        assert (row["minutes"], row["remote_minutes"]) == (150, 150)
        assert attendance.present(remote_on, now=NOW) == []
        assert db.session.query(AuditLog).filter_by(event_type="attendance_remote_added").count() == 1

    def test_only_where_it_is_switched_on(self, app, anna, rocket, clock):
        team, _lead = rocket
        with pytest.raises(ValidationError, match="not added"):
            _add(anna, team)
        team.remote_work_enabled = True
        team.attendance_enabled = False
        db.session.commit()
        assert attendance.remote_enabled(team) is False  # only while attendance is on too
        with pytest.raises(ValidationError):
            _add(anna, team)

    def test_only_its_members(self, app, remote_on, clock):
        stranger = _person("zoe@example.com", "Zoe", "Stranger")
        db.session.commit()
        with pytest.raises(ValidationError):
            _add(stranger, remote_on)

    def test_today_and_the_fourteen_days_before_and_nothing_to_come(self, app, anna, remote_on, clock):
        assert _add(anna, remote_on, day=TODAY - timedelta(days=14), note="Oldest allowed")
        with pytest.raises(ValidationError) as too_old:
            _add(anna, remote_on, day=TODAY - timedelta(days=15))
        assert "day" in _fields(too_old)
        with pytest.raises(ValidationError) as later:
            _add(anna, remote_on, day=TODAY, start=time(11, 0), end=time(13, 0))
        assert "end" in _fields(later)
        assert _add(anna, remote_on, day=TODAY, start=time(9, 0), end=time(11, 30), note="This morning")

    def test_past_midnight_up_to_three_fifteen(self, app, anna, remote_on, clock):
        late = _add(anna, remote_on, start=time(22, 0), end=time(3, 15), note="Late night")
        assert (attendance.minutes(late), attendance.day_of(late.started_at)) == (315, TODAY - timedelta(days=1))
        with pytest.raises(ValidationError) as wrong:
            _add(anna, remote_on, day=TODAY - timedelta(days=2), start=time(22, 0), end=time(4, 0))
        assert "03:15" in _fields(wrong)["end"]

    def test_what_they_did_is_required(self, app, anna, remote_on, clock):
        with pytest.raises(ValidationError) as missing:
            _add(anna, remote_on, note="   ")
        assert "note" in _fields(missing)
        with pytest.raises(ValidationError):
            _add(anna, remote_on, note="x" * 201)


class TestOverlap:
    def test_not_over_their_own_time_wherever_it_counts(self, app, anna, remote_on, doors, clock):
        (hangar, _key), _workshop = doors
        attendance.check_in(hangar, anna, now=datetime(2026, 10, 19, 16, 0, tzinfo=UTC))  # 18:00 local
        attendance.check_out(hangar, anna, now=datetime(2026, 10, 19, 17, 0, tzinfo=UTC))
        db.session.commit()

        with pytest.raises(ValidationError) as clash:
            _add(anna, remote_on, start=time(18, 30), end=time(19, 30))
        assert "Hangar" in _fields(clash)["start"]

        # The association's remote work counts too: a person is in one place at a time.
        attendance.set_enabled(None, None, True)
        attendance.set_remote_enabled(None, None, True)
        db.session.commit()
        _add(anna, None, start=time(20, 0), end=time(21, 0), note="Open day texts")
        with pytest.raises(ValidationError) as other:
            _add(anna, remote_on, start=time(20, 30), end=time(22, 0))
        assert "for Joanneum Aeronautics" in _fields(other)["start"]

    def test_an_entry_marked_not_counted_does_not_block(self, app, anna, remote_on, rocket, clock):
        _team, lead = rocket
        first = _add(anna, remote_on)
        attendance.mark_not_counted(lead, first, "Logged twice")
        db.session.commit()
        assert _add(anna, remote_on, note="The right one")


class TestTheirOwn:
    def test_changed_and_deleted_until_marked(self, app, anna, remote_on, rocket, clock):
        _team, lead = rocket
        stay = _add(anna, remote_on)
        assert attendance.may_change_own(anna, stay) is True

        attendance.change_remote(anna, attendance.own_remote(anna, stay.id), day=TODAY - timedelta(days=1),
                                 start=time(18, 0), end=time(21, 0), note="Wing rib CAD, revision 3")
        db.session.commit()
        assert (attendance.minutes(stay), stay.note) == (180, "Wing rib CAD, revision 3")

        attendance.mark_not_counted(lead, stay, "Not project work")
        db.session.commit()
        assert attendance.may_change_own(anna, stay) is False
        with pytest.raises(ValidationError, match="no longer"):
            attendance.own_remote(anna, stay.id)

        attendance.count_again(lead, stay)
        db.session.commit()
        attendance.delete_remote(anna, attendance.own_remote(anna, stay.id))
        db.session.commit()
        assert db.session.query(AttendanceStay).count() == 0

    def test_a_mark_committed_while_the_member_waited_is_seen(self, app, anna, remote_on, rocket, clock,
                                                              monkeypatch):
        # Codex on PR #89: the member's change passed its check before the lead's mark committed.
        # Now the entry is read and checked only once the member's lock is held.
        _team, lead = rocket
        stay = _add(anna, remote_on)
        waited = attendance._one_at_a_time

        def lead_marks_meanwhile(user):
            waited(user)
            db.session.execute(db.update(AttendanceStay).where(AttendanceStay.id == stay.id).values(
                not_counted_at=NOW.replace(tzinfo=None), not_counted_reason="Not project work"))

        monkeypatch.setattr(attendance, "_one_at_a_time", lead_marks_meanwhile)
        with pytest.raises(ValidationError, match="no longer"):
            attendance.own_remote(anna, stay.id)

    def test_a_mark_on_an_entry_deleted_meanwhile_is_refused(self, app, anna, remote_on, rocket, clock,
                                                             monkeypatch):
        _team, lead = rocket
        stay = _add(anna, remote_on)
        waited = attendance._one_at_a_time

        def member_deletes_meanwhile(user):
            waited(user)
            db.session.execute(db.delete(AttendanceStay).where(AttendanceStay.id == stay.id))

        monkeypatch.setattr(attendance, "_one_at_a_time", member_deletes_meanwhile)
        with pytest.raises(NotFoundError):
            attendance.mark_not_counted(lead, stay, "Not project work")

    def test_not_somebody_elses_and_not_a_readers(self, app, anna, remote_on, doors, clock):
        (hangar, _key), _workshop = doors
        stay = _add(anna, remote_on)
        ben = _person("ben@example.com", "Ben", "Berger")
        db.session.commit()
        with pytest.raises(NotFoundError):
            attendance.own_remote(ben, stay.id)
        _error, at_reader = attendance.check_in(hangar, anna, now=NOW)
        db.session.commit()
        with pytest.raises(NotFoundError):
            attendance.own_remote(anna, at_reader.id)

    def test_not_once_its_day_is_too_old(self, app, anna, remote_on, clock, monkeypatch):
        stay = _add(anna, remote_on, day=TODAY - timedelta(days=14))
        later = NOW + timedelta(days=1)
        monkeypatch.setattr(attendance, "get_now_utc", lambda: later)
        assert attendance.may_change_own(anna, stay) is False


class TestTheLeads:
    def test_marked_not_counted_with_a_reason_and_taken_back(self, app, anna, remote_on, rocket, clock):
        _team, lead = rocket
        stay = _add(anna, remote_on)
        with pytest.raises(ValidationError) as no_reason:
            attendance.mark_not_counted(lead, stay, " ")
        assert "reason" in _fields(no_reason)

        attendance.mark_not_counted(lead, stay, "Not project work")
        db.session.commit()
        [row] = [row for row in attendance.hours(remote_on, date(2026, 10, 1), TODAY) if row["user"] == anna]
        assert (row["minutes"], row["remote_minutes"], row["not_counted"]) == (0, 0, 1)
        assert (stay.not_counted_by, stay.not_counted_reason) == (lead, "Not project work")

        attendance.count_again(lead, stay)
        db.session.commit()
        assert attendance.counted(stay) == 150
        assert db.session.query(AuditLog).filter(AuditLog.event_type.in_(
            ["attendance_marked_not_counted", "attendance_counted_again"])).count() == 2

    def test_not_counted_again_over_the_entry_that_replaced_it(self, app, anna, remote_on, rocket, clock):
        # Codex on PR #89: marked, then entered again by the member; Count again on the first
        # would have counted the same time twice.
        _team, lead = rocket
        first = _add(anna, remote_on)
        attendance.mark_not_counted(lead, first, "Wrong times, please add it again")
        db.session.commit()
        replacement = _add(anna, remote_on, note="Wing rib CAD, the right times")

        with pytest.raises(ValidationError, match="counted twice"):
            attendance.count_again(lead, first)
        assert first.not_counted_at is not None

        attendance.delete_remote(anna, attendance.own_remote(anna, replacement.id))
        db.session.commit()
        attendance.count_again(lead, first)
        assert first.not_counted_at is None

    def test_only_remote_work_is_marked(self, app, anna, rocket, doors, clock):
        (hangar, _key), _workshop = doors
        _team, lead = rocket
        _error, stay = attendance.check_in(hangar, anna, now=NOW)
        with pytest.raises(ValidationError, match="Only remote work"):
            attendance.mark_not_counted(lead, stay, "No")


class TestThroughThePages:
    def test_the_member_adds_sees_and_changes_theirs(self, app, client, anna, remote_on, clock):
        from api_helpers import send, signed_in

        signed_in(client, anna)
        mine = client.get("/api/v1/account/attendance").get_json()
        [owner] = mine["owners"]
        assert (owner["remote"], mine["remote_from"], mine["today"]) == (True, "2026-10-06", "2026-10-20")

        added = send(client, "POST", f"/api/v1/account/attendance/{remote_on.slug}/remote",
                     {"day": "2026-10-19", "start": "18:00", "end": "20:30", "note": "Wing rib CAD"})
        assert added.status_code == 201, added.get_json()
        entry = added.get_json()
        assert (entry["remote"], entry["note"], entry["minutes"], entry["may_change"]) == (
            True, "Wing rib CAD", 150, True)

        refused = send(client, "POST", f"/api/v1/account/attendance/{remote_on.slug}/remote",
                       {"day": "2026-10-19", "start": "19:00", "end": "19:30", "note": ""})
        assert refused.status_code == 400 and "note" in refused.get_json()["error"]["fields"]

        changed = send(client, "PUT", f"/api/v1/account/attendance/remote/{entry['id']}",
                       {"day": "2026-10-19", "start": "18:00", "end": "21:00", "note": "Wing rib CAD, rev 3"})
        assert changed.get_json()["minutes"] == 180
        assert send(client, "DELETE", f"/api/v1/account/attendance/remote/{entry['id']}").status_code == 204

    def test_the_lead_switches_marks_and_exports(self, app, client, anna, rocket, clock):
        from api_helpers import send, signed_in

        team, lead = rocket
        signed_in(client, lead)
        url = f"/api/v1/teams/{team.slug}/manage/attendance"
        assert send(client, "PUT", f"{url}/remote-switch", {"enabled": True}).get_json() == {"enabled": True}
        stay = _add(anna, team)

        overview = client.get(url, query_string={"start": "2026-10-01", "end": "2026-10-20"}).get_json()
        assert overview["remote_enabled"] is True
        [row] = [row for row in overview["rows"] if row["name"] == "Anna Berger"]
        assert (row["minutes"], row["remote_minutes"]) == (150, 150)

        assert send(client, "PUT", f"{url}/stays/{stay.id}/not-counted", {"reason": ""}).status_code == 400
        marked = send(client, "PUT", f"{url}/stays/{stay.id}/not-counted", {"reason": "Logged twice"}).get_json()
        assert (marked["not_counted_by"], marked["not_counted_reason"]) == ("Lena Lead", "Logged twice")

        csv_text = client.get(f"{url}.csv", query_string={"start": "2026-10-01", "end": "2026-10-20"}).get_data(
            as_text=True)
        assert "Remote,Wing rib CAD,Not counted: Logged twice" in csv_text

        again = send(client, "DELETE", f"{url}/stays/{stay.id}/not-counted").get_json()
        assert again["not_counted_at"] is None

        # The member sees who marked it and why; then may not change it.
        send(client, "PUT", f"{url}/stays/{stay.id}/not-counted", {"reason": "Not project work"})
        from flask import g

        g.pop("_login_user", None)  # the test app keeps whoever signed in last
        member = signed_in(app.test_client(), anna)
        owners = member.get("/api/v1/account/attendance").get_json()["owners"]
        assert [owner["name"] for owner in owners] == [team.name], owners
        [shown] = owners[0]["stays"]
        assert (shown["not_counted_by"], shown["not_counted_reason"], shown["may_change"]) == (
            "Lena Lead", "Not project work", False)

    def test_the_association_too(self, app, client, anna, clock):
        from api_helpers import send, signed_in

        attendance.set_enabled(None, None, True)
        attendance.set_remote_enabled(None, None, True)
        db.session.commit()
        signed_in(client, anna)
        added = send(client, "POST", "/api/v1/account/attendance/association/remote",
                     {"day": "2026-10-19", "start": "10:00", "end": "11:00", "note": "Open day texts"})
        assert added.status_code == 201, added.get_json()
        assert db.session.get(AttendanceStay, added.get_json()["id"]).team_id is None


class TestKeptAsStaysAre:
    def test_in_the_export_and_gone_with_an_erasure(self, app, anna, remote_on, rocket, clock):
        _team, lead = rocket
        stay = _add(anna, remote_on)
        attendance.mark_not_counted(lead, stay, "Logged twice")
        db.session.commit()

        [exported] = attendance.export_for(anna)
        assert (exported["remote"], exported["what"], exported["not_counted_reason"]) == (
            True, "Wing rib CAD", "Logged twice")
        assert privacy.export_account_data(anna)["attendance"][0]["what"] == "Wing rib CAD"

        attendance.forget_for_erasure(lead)
        db.session.commit()
        assert stay.not_counted_by_user_id is None and stay.not_counted_reason == "Logged twice"
