"""Attendance with the same cards (services/attendance.py, docs/attendance-plan.md):
checking in and out at a team's readers, counted in full minutes; who may;
a forgotten check-out; corrections; what a member sees in their account.

The reader is driven as its firmware drives it: plain HTTP with its key,
reporting `check_in` and `check_out` with the card's ID.
"""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import db
from aeronautics_members.db_models import AttendanceStay, AuditLog
from aeronautics_members.services import ValidationError, attendance, cards, devices, privacy
from test_teams_flow import _led, _person, switched_on  # noqa: F401
from test_teams_foundation import _in_team

CARD = "04A1B2C3D4E5F6"
UTC = timezone.utc


def _at(day, hh, mm, ss=0):
    """A local (Vienna, summer: UTC+2) time on a day in October 2026, as UTC."""
    return datetime(2026, 10, day, hh, mm, ss, tzinfo=UTC) - timedelta(hours=2)


@pytest.fixture
def rocket(app, switched_on):  # noqa: F811
    team, lead = _led()
    team.attendance_enabled = True
    db.session.commit()
    return team, lead


@pytest.fixture
def anna(rocket):
    team, _lead = rocket
    person = _person("anna@example.com", "Anna", "Berger")
    _in_team(person, team)
    cards.link(person, CARD, name="Student card", by=person, how=cards.BY_PHONE)
    db.session.commit()
    return person


@pytest.fixture
def doors(rocket):
    """Two of the team's readers: the hangar door and the workshop."""
    team, _lead = rocket
    hangar, hangar_key = devices.add(None, name="Hangar door", type="attendance", owner=team, place="Hangar")
    workshop, workshop_key = devices.add(None, name="Workshop", type="attendance", owner=team, place="Workshop")
    db.session.commit()
    return (hangar, hangar_key), (workshop, workshop_key)


def _report(app, key, kind, card=CARD, event_id=None):
    event_id = event_id or f"{kind}-{datetime.now(UTC).timestamp()}"
    response = app.test_client().post("/api/v1/device/events", headers={"Authorization": f"Bearer {key}"},
                                      json={"event_id": event_id, "kind": kind, "data": {"card": card}})
    assert response.status_code == 200, response.get_json()
    return response.get_json()["answer"]


class TestAtTheReader:
    def test_in_at_one_reader_out_at_another_is_one_stay(self, app, anna, doors):
        (hangar, hangar_key), (workshop, workshop_key) = doors

        hello = _report(app, hangar_key, "check_in")
        bye = _report(app, workshop_key, "check_out")

        assert (hello["result"], hello["first_name"]) == ("checked_in", "Anna")
        assert hello["at"].endswith(("+02:00", "+01:00"))
        assert (bye["result"], bye["since"]) == ("checked_out", hello["at"])
        assert bye["duration_seconds"] % 60 == 0
        [stay] = db.session.query(AttendanceStay).all()
        assert (stay.in_device_id, stay.out_device_id, stay.team_id) == (hangar.id, workshop.id, hangar.team_id)

    def test_checked_in_twice_or_out_without_being_in_is_refused(self, app, anna, doors):
        (_hangar, key), _workshop = doors

        assert _report(app, key, "check_out")["reason"] == "not_checked_in"
        _report(app, key, "check_in")
        again = _report(app, key, "check_in")

        assert again["reason"] == "already_checked_in" and again["first_name"] == "Anna"
        assert again["message"].startswith("Already checked in since ") and "since" in again

    def test_only_the_teams_members_and_only_while_it_is_on(self, app, anna, doors, rocket):
        (_hangar, key), _workshop = doors
        outsider = _person("otto@example.com", "Otto", "Other")
        cards.link(outsider, "04FFEEDDCCBB11", by=outsider, how=cards.BY_PHONE)
        db.session.commit()

        refused = _report(app, key, "check_in", card="04FFEEDDCCBB11")
        assert (refused["reason"], refused["message"]) == ("not_a_member", "Only members of Rocket check in here.")

        attendance.set_enabled(None, rocket[0], False)
        db.session.commit()
        assert _report(app, key, "check_in")["reason"] == "attendance_off"
        assert db.session.query(AttendanceStay).count() == 0

    def test_an_unknown_card_gets_the_qr_code_to_link_it(self, app, anna, doors):
        (_hangar, key), _workshop = doors

        answer = _report(app, key, "check_in", card="04000000000001")

        assert answer["reason"] == "card_unknown" and "/link/" in answer["link"]["url"]


class TestOnePersonAtATime:
    """Two readers tapped by one person in the same instant both found no open
    stay and both opened one. SQLite has no row locks: what is tested is that
    each way of changing someone's stays takes the lock on them."""

    @pytest.fixture
    def locks(self, monkeypatch):
        taken = []
        real = attendance.locked

        def spy(select):
            taken.append(str(select))
            return real(select)

        monkeypatch.setattr(attendance, "locked", spy)
        return taken

    def test_checking_in_and_out_and_every_correction_lock_the_person(self, app, anna, doors, locks):
        (hangar, _key), _workshop = doors

        _reason, stay = attendance.check_in(hangar, anna, now=_at(14, 10, 0))
        attendance.check_out(hangar, anna, now=_at(14, 11, 0))
        attendance.change_stay(None, stay, started_at=_at(14, 9, 0), ended_at=_at(14, 11, 0), now=_at(14, 12, 0))
        added = attendance.add_stay(None, hangar.team, anna, started_at=_at(14, 13, 0), ended_at=_at(14, 14, 0),
                                    now=_at(14, 15, 0))
        attendance.delete_stay(None, added)

        assert len(locks) == 5 and all("users" in statement for statement in locks)

    def test_the_lock_really_is_a_lock_on_the_real_database(self):
        from sqlalchemy.dialects import mysql
        from aeronautics_members.db_models import User

        statement = attendance.locked(db.select(User.id).where(User.id == 1))

        assert "FOR UPDATE" in str(statement.compile(dialect=mysql.dialect()))


class TestCounting:
    def test_full_minutes_from_the_minute_in_to_the_minute_out(self, app, anna, doors):
        (hangar, _key), _workshop = doors

        _reason, stay = attendance.check_in(hangar, anna, now=_at(14, 11, 15, 59))
        attendance.check_out(hangar, anna, now=_at(14, 13, 40, 2))

        assert attendance.minutes(stay) == 145
        assert stay.started_at.second == 59  # the exact time is kept

    def test_a_stay_left_open_overnight_is_not_checked_out_and_counts_nothing(self, app, anna, doors):
        (hangar, _key), _workshop = doors
        _reason, forgotten = attendance.check_in(hangar, anna, now=_at(14, 16, 0))

        # The same evening nothing happens; at 03:15 the next day it is closed.
        assert attendance.close_forgotten(now=_at(14, 23, 50)) == 0
        assert attendance.close_forgotten(now=_at(15, 3, 15)) == 1
        assert attendance.state(forgotten) == attendance.NOT_CHECKED_OUT and attendance.minutes(forgotten) is None

        # Checking in the next day starts a new stay -- and would close it too, had the night's step not run.
        reason, today = attendance.check_in(hangar, anna, now=_at(15, 9, 0))
        assert reason is None and today.id != forgotten.id

    def test_a_late_night_is_one_stay_on_the_evening_it_began(self, app, anna, doors, rocket):
        """The day runs from 03:15 to 03:15 (found in review, PR #39: at
        midnight a stay begun at 22:00 was closed and the check-out refused)."""
        (hangar, _key), _workshop = doors
        team, _lead = rocket
        _reason, stay = attendance.check_in(hangar, anna, now=_at(14, 22, 0))

        assert attendance.close_forgotten(now=_at(15, 0, 30)) == 0
        reason, out = attendance.check_out(hangar, anna, now=_at(15, 1, 30))

        assert reason is None and out.id == stay.id and attendance.minutes(stay) == 210
        # Begun after midnight, it is the evening before's too.
        attendance.check_in(hangar, anna, now=_at(15, 2, 0))
        attendance.check_out(hangar, anna, now=_at(15, 3, 0))
        day = _at(14, 12, 0).date()
        assert len(attendance.stays(team, user=anna, start=day, end=day)) == 2
        rows = {row["user"].id: row["minutes"] for row in attendance.hours(team, day, day)}
        assert rows[anna.id] == 270

    def test_open_at_03_15_it_is_closed(self, app, anna, doors):
        (hangar, _key), _workshop = doors
        attendance.check_in(hangar, anna, now=_at(15, 1, 0))

        assert attendance.close_forgotten(now=_at(15, 3, 14)) == 0
        assert attendance.close_forgotten(now=_at(15, 3, 15)) == 1

    def test_the_attendance_day_of_a_moment(self, app):
        assert attendance.day_of(_at(15, 3, 14)) == _at(14, 12, 0).date()
        assert attendance.day_of(_at(15, 3, 15)) == _at(15, 12, 0).date()
        assert attendance.day_start(_at(15, 12, 0).date()) == _at(15, 3, 15).replace(tzinfo=None)

    def test_checking_in_closes_yesterdays_open_stay_itself(self, app, anna, doors):
        (hangar, _key), _workshop = doors
        attendance.check_in(hangar, anna, now=_at(14, 16, 0))

        reason, _stay = attendance.check_in(hangar, anna, now=_at(15, 9, 0))

        assert reason is None
        assert [attendance.state(stay) for stay in db.session.query(AttendanceStay).order_by(AttendanceStay.id)] == [
            attendance.NOT_CHECKED_OUT, attendance.OPEN]

    def test_hours_per_person_members_with_none_too(self, app, anna, doors, rocket):
        (hangar, _key), _workshop = doors
        team, lead = rocket
        attendance.check_in(hangar, anna, now=_at(14, 10, 0))
        attendance.check_out(hangar, anna, now=_at(14, 12, 30))
        attendance.check_in(hangar, anna, now=_at(15, 10, 0))
        attendance.close_forgotten(now=_at(16, 3, 15))
        db.session.commit()

        rows = {row["user"].id: row for row in attendance.hours(team, _at(1, 12, 0).date(), _at(31, 12, 0).date())}

        assert (rows[anna.id]["minutes"], rows[anna.id]["stays"], rows[anna.id]["not_checked_out"]) == (150, 2, 1)
        assert rows[lead.id]["minutes"] == 0


class TestCorrections:
    def test_a_check_out_set_by_hand_counts_and_is_kept_in_the_log(self, app, anna, doors, rocket):
        (hangar, _key), _workshop = doors
        _team, lead = rocket
        _reason, stay = attendance.check_in(hangar, anna, now=_at(14, 16, 0))
        attendance.close_forgotten(now=_at(15, 3, 15))

        attendance.change_stay(lead, stay, started_at=_at(14, 16, 0), ended_at=_at(14, 18, 30), now=_at(15, 9, 0))
        db.session.commit()

        assert attendance.minutes(stay) == 150 and attendance.state(stay) == attendance.DONE
        assert stay.corrected_by_user_id == lead.id
        logged = db.session.query(AuditLog).filter_by(event_type="attendance_stay_changed").one()
        assert logged.before_state["ended_at"] is None and logged.target_user_id == anna.id

    @pytest.mark.parametrize("started, ended, code", [
        ((14, 18, 0), (14, 17, 0), "attendance_times_order"),
        ((14, 10, 0), (14, 11, 0), "attendance_overlap"),
        ((14, 9, 0), (31, 9, 0), "attendance_times_future"),
    ])
    def test_what_cannot_be_a_stay_is_refused(self, app, anna, doors, rocket, started, ended, code):
        (hangar, _key), _workshop = doors
        attendance.check_in(hangar, anna, now=_at(14, 10, 30))
        attendance.check_out(hangar, anna, now=_at(14, 12, 0))

        with pytest.raises(ValidationError) as refused:
            attendance.add_stay(rocket[1], rocket[0], anna, started_at=_at(*started), ended_at=_at(*ended),
                                now=_at(15, 9, 0))

        assert refused.value.code == code

    def test_a_stay_forgotten_altogether_is_added_and_one_deleted(self, app, anna, rocket):
        team, lead = rocket

        stay = attendance.add_stay(lead, team, anna, started_at=_at(13, 9, 0), ended_at=_at(13, 12, 0),
                                   now=_at(15, 9, 0))
        assert attendance.minutes(stay) == 180 and stay.in_device_id is None

        attendance.delete_stay(lead, stay)
        db.session.commit()
        assert db.session.query(AttendanceStay).count() == 0
        assert db.session.query(AuditLog).filter_by(event_type="attendance_stay_deleted").count() == 1


    def test_a_stay_can_be_added_for_anybody_who_may_check_in(self, app, anna, rocket):
        """Found in review (PR #39): for the association only people with a
        stay already were offered, so somebody's first stay could not be added."""
        team, lead = rocket
        assert anna in attendance.eligible(team)
        everyone = attendance.eligible(None)
        assert anna in everyone and lead in everyone
        assert attendance.hours(None, _at(1, 0, 0).date(), _at(31, 0, 0).date()) == []


class TestInTheAccount:
    def test_shown_while_it_is_on_kept_once_there_is_data(self, app, anna, doors, rocket):
        (hangar, _key), _workshop = doors
        team, _lead = rocket
        outsider = _person("otto@example.com", "Otto", "Other")

        assert attendance.area(anna) == "menu" and attendance.area(outsider) is None
        attendance.set_enabled(None, team, False)
        db.session.commit()
        assert attendance.area(anna) is None

        attendance.set_enabled(None, team, True)
        attendance.check_in(hangar, anna, now=_at(14, 10, 0))
        attendance.set_enabled(None, team, False)
        db.session.commit()
        assert attendance.area(anna) == "data"
        assert [(owner.name, current) for owner, current in attendance.owners_for(anna)] == [("Rocket", False)]

    def test_in_the_data_export_and_gone_with_an_erasure(self, app, anna, doors):
        (hangar, _key), _workshop = doors
        attendance.check_in(hangar, anna, now=_at(14, 10, 0))
        attendance.check_out(hangar, anna, now=_at(14, 11, 0))
        db.session.commit()

        assert [row["minutes"] for row in privacy.export_account_data(anna)["attendance"]] == [60]
        privacy.erase_account(anna)
        db.session.commit()
        assert db.session.query(AttendanceStay).count() == 0


@pytest.fixture
def midday(monkeypatch):
    """Today at 12:00 in Vienna, wherever the clock really is: a check-in an
    hour ago must not fall on yesterday, which the page closes as forgotten."""
    from aeronautics_members.api import attendance as attendance_api

    today = attendance.local(datetime.now(UTC)).date()
    noon = datetime.combine(today, datetime.min.time().replace(hour=12), tzinfo=attendance.MEMBERSHIP_TIMEZONE)
    now = noon.astimezone(UTC)
    monkeypatch.setattr(attendance, "get_now_utc", lambda: now)
    monkeypatch.setattr(attendance_api, "get_now_utc", lambda: now)
    return now


class TestPages:
    def test_a_lead_sees_hours_and_who_is_here_and_corrects(self, app, client, anna, doors, rocket, midday):
        from api_helpers import send, signed_in

        (hangar, _key), _workshop = doors
        team, lead = rocket
        attendance.check_in(hangar, anna, now=midday - timedelta(hours=1))
        db.session.commit()
        signed_in(client, lead)
        url = f"/api/v1/teams/{team.slug}/manage/attendance"

        overview = client.get(url).get_json()
        assert overview["enabled"] is True
        # The side menu's Attendance follows the switch.
        page = client.get(f"/api/v1/teams/{team.slug}").get_json()
        assert page["attendance_enabled"] is True and "team.attendance" in page["permissions"]
        assert [row["name"] for row in overview["rows"]] == ["Anna Berger", "Lena Lead"]
        assert [(here["name"], here["place"]) for here in overview["present"]] == [("Anna Berger", "Hangar")]

        stay_id = overview["present"][0]["stay_id"]
        started = db.session.get(AttendanceStay, stay_id).started_at
        changed = send(client, "PUT", f"{url}/stays/{stay_id}", {
            "started_at": started.replace(tzinfo=UTC).isoformat(),
            "ended_at": (started + timedelta(minutes=30)).replace(tzinfo=UTC).isoformat()})
        assert changed.status_code == 200, changed.get_json()
        assert (changed.get_json()["minutes"], changed.get_json()["corrected_by"]) == (30, "Lena Lead")

        person = client.get(f"{url}/people/{anna.id}").get_json()
        assert [stay["state"] for stay in person["stays"]] == ["done"]
        csv_text = client.get(f"{url}.csv").get_data(as_text=True)
        assert csv_text.splitlines()[0] == "Name,Date,Checked in,Checked out,Minutes,Note"
        assert "Anna Berger" in csv_text and "Corrected" in csv_text

        assert send(client, "PUT", f"{url}/switch", {"enabled": False}).get_json() == {"enabled": False}
        assert team.attendance_enabled is False

    def test_a_member_sees_their_own_and_not_the_teams(self, app, client, anna, doors, rocket):
        from api_helpers import signed_in

        (hangar, _key), _workshop = doors
        team, _lead = rocket
        attendance.check_in(hangar, anna)
        db.session.commit()
        signed_in(client, anna)

        assert client.get("/api/v1/me").get_json()["attendance_area"] == "menu"
        mine = client.get("/api/v1/account/attendance").get_json()["owners"]
        assert [(owner["name"], owner["current"], owner["ask"]) for owner in mine] == [("Rocket", True, "team")]
        assert [stay["state"] for stay in mine[0]["stays"]] == ["open"]
        assert client.get(f"/api/v1/teams/{team.slug}/manage/attendance").status_code == 403
