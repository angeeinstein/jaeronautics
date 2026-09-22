"""Importing the old forum's people.

Six hundred rows from a decade of MyBB, and the import will be run more than
once — the first export always turns out to be missing something. So the
properties worth pinning are not "it creates rows" but what happens on the
second run, what happens to the dead email addresses, and what happens when
one of six hundred entries is malformed.
"""
import json
from datetime import date
from pathlib import Path

import pytest

from conftest import db, make_member
from aeronautics_members.db_models import ImportedForumProfile, User
from aeronautics_members.services import ValidationError
from aeronautics_members.services.forum_import import (
    IMPORTED_EMAIL_DOMAIN,
    derive_year_group,
    import_forum_people,
    imported_email_for,
    load_people,
)


def _person(uid="142", username="PopovicA_L23", **overrides):
    person = {
        "source_user_id": uid,
        "source_username": username,
        "source_email": "a.popovic@edu.fh-joanneum.at",
        "year_group": "LAV23",
        "post_count": 2,
        "joined_on": "2023-11-23",
        "last_posted_on": "2024-06-02",
    }
    person.update(overrides)
    return person


def _profiles():
    return db.session.execute(db.select(ImportedForumProfile)).scalars().all()


class TestTheYearGroupFallback:
    """The export may not carry Jahrgang; the username encodes it."""

    @pytest.mark.parametrize(
        "username,expected",
        [
            ("PopovicA_L23", "LAV23"),
            ("EinsteinA_M25", "MAV25"),
            ("SomeoneX_L07", "LAV07"),
        ],
    )
    def test_it_reads_the_suffix(self, username, expected):
        assert derive_year_group(username) == expected

    @pytest.mark.parametrize(
        "username,expected",
        [
            ("PopovicA_LAV23", "LAV23"),
            ("EinsteinA_MAV25", "MAV25"),
            # ATM is a third programme. It appears in the old forum's Jahrgang
            # field but never as a single letter, so this is the only way it
            # is ever recovered from a username.
            ("SomeoneX_ATM18", "ATM18"),
        ],
    )
    def test_it_also_reads_a_programme_spelled_out(self, username, expected):
        """Sixteen of the old forum's people wrote it the long way."""
        assert derive_year_group(username) == expected

    @pytest.mark.parametrize(
        "username",
        [
            "NoSuffix", "Weird_X23", "Trailing_", "TooLong_L2345", "Nondigit_LAB",
            "Unknown_XYZ23",   # right shape, not a programme we run
            "Digits_12345",
            "Mixed_LAV2X",
        ],
    )
    def test_anything_else_is_left_unknown(self, username):
        """A wrong year group is worse than a missing one."""
        assert derive_year_group(username) is None


class TestImporting:
    def test_a_person_becomes_an_account_and_a_profile(self, app):
        report = import_forum_people([_person()])
        db.session.commit()

        assert report["created"] == 1
        profile = _profiles()[0]
        assert profile.source_username == "PopovicA_L23"
        assert profile.year_group == "LAV23"
        assert profile.post_count == 2
        assert profile.joined_on == date(2023, 11, 23)
        assert profile.user.forum_username == "PopovicA_L23"

    def test_the_account_cannot_be_signed_into(self, app):
        import_forum_people([_person()])
        db.session.commit()

        user = _profiles()[0].user
        assert user.password_hash is None
        assert user.check_password("anything") is False
        assert user.permissions == set()

    def test_the_old_address_is_kept_as_history_not_as_identity(self, app):
        """Those addresses are dead and may be reissued to another student."""
        import_forum_people([_person()])
        db.session.commit()

        profile = _profiles()[0]
        assert profile.source_email == "a.popovic@edu.fh-joanneum.at"
        assert profile.user.email != "a.popovic@edu.fh-joanneum.at"
        assert profile.user.email.endswith(f"@{IMPORTED_EMAIL_DOMAIN}")

    def test_the_real_address_cannot_be_used_to_find_the_account(self, app):
        """The point of the placeholder: password reset looks up users.email."""
        import_forum_people([_person()])
        db.session.commit()

        found = db.session.execute(
            db.select(User).filter_by(email="a.popovic@edu.fh-joanneum.at")
        ).scalar_one_or_none()

        assert found is None

    def test_a_missing_year_group_falls_back_to_the_username(self, app):
        report = import_forum_people([_person(year_group=None)])
        db.session.commit()

        assert _profiles()[0].year_group == "LAV23"
        assert report["year_groups_derived"] == 1

    def test_the_display_name_falls_back_to_the_username(self, app):
        """MyBB profiles here carry no real name."""
        import_forum_people([_person(display_name=None)])
        db.session.commit()

        assert _profiles()[0].display_name == "PopovicA_L23"

    def test_the_exported_field_beats_the_username_and_says_so(self, app):
        """Somebody who joined as LAV21 and went on to the master's is MAV24.

        The name they registered under never changed, so the two disagree for
        nineteen people in the real export. The field is the later truth; the
        count exists so a run that suddenly disagrees about half the forum is
        not silent.
        """
        report = import_forum_people([
            _person(uid="1", username="PopovicA_L21", year_group="MAV24"),
        ])
        db.session.commit()

        assert _profiles()[0].year_group == "MAV24"
        assert report["year_groups_disagreeing"] == 1
        assert report["problems"] == [], "a progression is not a problem to fix"

    def test_agreeing_does_not_count_as_disagreeing(self, app):
        report = import_forum_people([_person()])
        db.session.commit()

        assert report["year_groups_disagreeing"] == 0

    def test_a_username_with_no_year_group_cannot_disagree(self, app):
        report = import_forum_people([
            _person(uid="1", username="NoSuffixHere", year_group="LAV23"),
        ])
        db.session.commit()

        assert report["year_groups_disagreeing"] == 0

    def test_they_are_not_members(self, app):
        import_forum_people([_person()])
        db.session.commit()

        assert _profiles()[0].user.member is None


class TestWhatTheReportSaysAboutYearGroups:
    """Six hundred rows is too many to eyeball, so the report has to summarise.

    Two questions it must answer: which year groups exist and how many people
    are in each -- enough to spot a typo -- and exactly who could not be placed
    at all, by name, because that list gets fixed by hand.
    """

    def test_it_counts_every_distinct_year_group(self, app):
        report = import_forum_people([
            _person(uid="1", username="OneA_L23"),
            _person(uid="2", username="TwoB_L23"),
            _person(uid="3", username="ThreeC_M20", year_group="MAV20"),
        ])
        db.session.commit()

        assert report["year_group_counts"] == {"LAV23": 2, "MAV20": 1}

    def test_a_derived_year_group_is_counted_like_any_other(self, app):
        """The table is about where people ended up, not how they got there."""
        report = import_forum_people([
            _person(uid="1", username="OneA_L23", year_group=None),
            _person(uid="2", username="TwoB_L23"),
        ])
        db.session.commit()

        assert report["year_group_counts"] == {"LAV23": 2}

    def test_case_and_spacing_are_counted_as_written(self, app):
        """A stray 'lav23' is a typo to show, not one to normalise away.

        Folding case here would hide exactly the mistake this table exists to
        surface -- and the value is stored as written, so the count would then
        describe something the database does not contain.
        """
        report = import_forum_people([
            _person(uid="1", username="OneA_L23", year_group="LAV23"),
            _person(uid="2", username="TwoB_L23", year_group="lav23"),
        ])
        db.session.commit()

        assert report["year_group_counts"] == {"LAV23": 1, "lav23": 1}

    def test_people_with_no_year_group_are_named_not_just_counted(self, app):
        report = import_forum_people([
            _person(uid="1", username="Placeable_L23"),
            _person(uid="2", username="NoSuffixAtAll", year_group=None,
                    joined_on="2019-04-01"),
        ])
        db.session.commit()

        assert report["unknown_year_group"] == [{
            "source_user_id": "2",
            "source_username": "NoSuffixAtAll",
            "joined_on": "2019-04-01",
        }]

    def test_the_registration_year_comes_along_to_identify_them(self, app):
        """It is usually enough to guess the cohort from."""
        report = import_forum_people([
            _person(uid="2", username="Mystery", year_group=None, joined_on="2016-10-02"),
        ])
        db.session.commit()

        assert report["unknown_year_group"][0]["joined_on"] == "2016-10-02"

    def test_a_skipped_entry_is_not_reported_as_missing_a_year_group(self, app):
        """It was never imported, so it is a problem, not a gap to fill in."""
        report = import_forum_people([{"source_username": "NoIdHere_L21"}])
        db.session.commit()

        assert report["skipped"] == 1
        assert report["unknown_year_group"] == []

    def test_the_log_line_carries_counts_but_not_names(self, app, caplog):
        """The report goes to a terminal; the log is kept, shipped and read.

        Six hundred usernames and everyone's year group in an application log
        is a copy of the membership list in a place nobody is guarding.
        """
        import logging

        with caplog.at_level(logging.INFO):
            import_forum_people([
                _person(uid="1", username="Placeable_L23"),
                _person(uid="2", username="SecretName", year_group=None),
            ])
        db.session.commit()

        logged = "\n".join(record.getMessage() for record in caplog.records)
        assert "Forum import" in logged
        assert "SecretName" not in logged
        assert "LAV23" not in logged
        assert "year_groups_unknown" in logged
        assert "year_groups_distinct" in logged


class TestRunningItTwice:
    """Imports get re-run. One that cannot be is one nobody dares fix."""

    def test_the_second_run_updates_rather_than_duplicates(self, app):
        import_forum_people([_person()])
        db.session.commit()
        first_user_id = _profiles()[0].user_id

        report = import_forum_people([_person(post_count=97)])
        db.session.commit()

        assert report["created"] == 0
        assert report["updated"] == 1
        assert len(_profiles()) == 1
        assert _profiles()[0].post_count == 97
        assert _profiles()[0].user_id == first_user_id, "the id Discourse knows must not move"

    def test_a_renamed_person_is_still_the_same_person(self, app):
        """Matched on the old forum's key, not on the name."""
        import_forum_people([_person()])
        db.session.commit()

        import_forum_people([_person(username="PopovicAnna_L23")])
        db.session.commit()

        assert len(_profiles()) == 1
        assert _profiles()[0].source_username == "PopovicAnna_L23"


class TestRefusals:
    def test_a_username_already_in_use_is_reported_not_suffixed(self, app):
        """Almost certainly the same person returning -- a human decides."""
        member = make_member(email="returned@example.com")
        member.user.forum_username = "PopovicA_L23"
        db.session.commit()

        report = import_forum_people([_person()])
        db.session.commit()

        assert report["created"] == 0
        assert report["skipped"] == 1
        assert _profiles() == []
        assert "already belongs to account" in report["problems"][0]

    def test_an_entry_without_a_key_is_skipped_and_the_rest_imported(self, app):
        """One malformed row out of six hundred must not stop the other 599."""
        report = import_forum_people([
            _person(uid="1", username="OneA_L20"),
            {"source_username": "NoIdHere_L21"},
            _person(uid="3", username="ThreeC_L22"),
        ])
        db.session.commit()

        assert report["created"] == 2
        assert report["skipped"] == 1
        assert len(report["problems"]) == 1

    def test_a_dry_run_writes_nothing(self, app):
        report = import_forum_people([_person()], dry_run=True)

        assert report["created"] == 1, "the report still says what would happen"
        assert _profiles() == []
        assert db.session.execute(db.select(User)).scalars().all() == []


class TestReadingTheExport:
    def test_a_missing_file_is_refused_clearly(self, app, tmp_path):
        with pytest.raises(ValidationError):
            load_people(tmp_path / "nope.json")

    def test_broken_json_is_refused_rather_than_half_imported(self, app, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("[{\"source_user_id\": 1,")

        with pytest.raises(ValidationError):
            load_people(path)

    def test_an_object_instead_of_an_array_is_refused(self, app, tmp_path):
        path = tmp_path / "object.json"
        path.write_text(json.dumps({"source_user_id": "1"}))

        with pytest.raises(ValidationError):
            load_people(path)


class TestTheCommand:
    def _export(self, tmp_path, people):
        path = tmp_path / "people.json"
        path.write_text(json.dumps(people))
        return str(path)

    def test_it_imports_and_reports(self, app, tmp_path):
        path = self._export(tmp_path, [_person(), _person(uid="143", username="MeierB_L24")])

        result = app.test_cli_runner().invoke(args=["import-forum-people", path])

        assert result.exit_code == 0
        assert "created=2" in result.output
        assert len(_profiles()) == 2

    def test_the_dry_run_leaves_the_database_alone(self, app, tmp_path):
        path = self._export(tmp_path, [_person()])

        result = app.test_cli_runner().invoke(args=["import-forum-people", path, "--dry-run"])

        assert "Dry run" in result.output
        assert _profiles() == []

    def test_year_groups_lists_the_table_and_the_gaps(self, app, tmp_path):
        path = self._export(tmp_path, [
            _person(uid="1", username="OneA_L23"),
            _person(uid="2", username="TwoB_L23"),
            _person(uid="3", username="ThreeC_M20", year_group="MAV20"),
            _person(uid="4", username="Mystery", year_group=None, joined_on="2016-10-02"),
        ])

        result = app.test_cli_runner().invoke(
            args=["import-forum-people", path, "--dry-run", "--year-groups"]
        )

        assert result.exit_code == 0
        assert "LAV23" in result.output
        assert "MAV20" in result.output
        # The one nobody could place is named, with the year to identify it by.
        assert "Mystery" in result.output
        assert "2016" in result.output
        assert "uid     4" in result.output

    def test_without_the_flag_it_says_how_to_see_them(self, app, tmp_path):
        """A four-line report is useless if nobody knows the detail exists."""
        path = self._export(tmp_path, [
            _person(uid="1", username="OneA_L23"),
            _person(uid="4", username="Mystery", year_group=None),
        ])

        result = app.test_cli_runner().invoke(args=["import-forum-people", path, "--dry-run"])

        assert "1 distinct year groups" in result.output
        assert "1 people without one" in result.output
        assert "--year-groups" in result.output
        assert "Mystery" not in result.output, "the name only appears when asked for"

    def test_it_says_so_when_there_is_nothing_to_fix(self, app, tmp_path):
        path = self._export(tmp_path, [_person(uid="1", username="OneA_L23")])

        result = app.test_cli_runner().invoke(
            args=["import-forum-people", path, "--dry-run", "--year-groups"]
        )

        assert "Everyone has a year group." in result.output


def test_the_placeholder_address_is_unique_per_person(app):
    first = imported_email_for("mybb", "1")
    second = imported_email_for("mybb", "2")

    assert first != second
    assert first.endswith(f"@{IMPORTED_EMAIL_DOMAIN}")


class TestADryRunWritesNothingAtAll:
    """Not even the avatars.

    The rollback at the end of a dry run undoes the database and nothing else.
    Storing an avatar writes a file, so a dry run pointed at the real export
    would leave six hundred normalised images in the staging directory with
    every row that referenced them discarded -- orphans nothing will ever
    clean up, created by the command whose whole promise is that it changes
    nothing.
    """

    def _avatar(self, tmp_path):
        from PIL import Image

        source = tmp_path / "avatars"
        source.mkdir()
        Image.new("RGB", (64, 64), (10, 20, 30)).save(source / "avatar_142.jpg")
        return source

    @pytest.fixture
    def staged(self, app):
        """How many imported avatars appeared while the test ran.

        Counted as a delta: the staging directory is a real one shared by the
        whole suite, so asserting it is empty would only pass depending on
        which tests ran first.
        """
        from aeronautics_members.forum_service import get_forum_storage_dir

        storage = get_forum_storage_dir()

        def existing():
            return set(storage.glob("imported-*")) if storage.exists() else set()

        before = existing()
        yield lambda: sorted(existing() - before)

    def test_no_avatar_file_is_left_behind(self, app, tmp_path, staged):
        report = import_forum_people(
            [_person(avatar_file="avatar_142.jpg")],
            avatar_dir=str(self._avatar(tmp_path)),
            dry_run=True,
        )

        assert report["avatars_stored"] == 1, "the report still says what would happen"
        assert staged() == [], "a dry run must not write an avatar"

    def test_a_real_run_does_write_one(self, app, tmp_path, staged):
        """The other half of the pair: the guard must not disable the feature."""
        report = import_forum_people(
            [_person(avatar_file="avatar_142.jpg")],
            avatar_dir=str(self._avatar(tmp_path)),
        )
        db.session.commit()

        assert report["avatars_stored"] == 1
        assert len(staged()) == 1
        assert _profiles()[0].avatar_path

    def test_a_missing_avatar_is_still_reported_on_a_dry_run(self, app, tmp_path):
        """The point of the rehearsal is finding this before the real run."""
        report = import_forum_people(
            [_person(avatar_file="not-here.jpg")],
            avatar_dir=str(self._avatar(tmp_path)),
            dry_run=True,
        )

        assert report["avatars_stored"] == 0
        assert "avatar file not found" in report["problems"][0]

    def test_an_unreadable_image_is_found_on_a_dry_run_too(self, app, tmp_path, staged):
        """Decoding happens either way; only the write is skipped."""
        source = self._avatar(tmp_path)
        (source / "broken.jpg").write_bytes(b"this is not an image")

        report = import_forum_people(
            [_person(avatar_file="broken.jpg")],
            avatar_dir=str(source),
            dry_run=True,
        )

        assert report["avatars_stored"] == 0
        assert "could not be read" in report["problems"][0]
        assert staged() == []


class TestPointingAtTheAvatars:
    """The four ways --avatar-dir goes wrong need four different fixes.

    Click's own check cannot tell them apart: it stats the path as the calling
    user, so a directory inside an unreadable parent is reported as "does not
    exist" about a directory that plainly does. That sends somebody looking
    for a missing folder when the answer is to move a readable one -- which is
    the actual shape of this job, where the avatars get unpacked as root and
    the import runs as the application user.
    """

    def _run(self, app, tmp_path, avatar_dir):
        export = tmp_path / "people.json"
        export.write_text(json.dumps([_person(avatar_file="avatar_142.jpg")]))
        return app.test_cli_runner().invoke(args=[
            "import-forum-people", str(export), "--dry-run",
            "--avatar-dir", str(avatar_dir),
        ])

    def test_a_genuinely_missing_directory_says_so(self, app, tmp_path):
        result = self._run(app, tmp_path, tmp_path / "not-here")

        assert result.exit_code != 0
        assert "No such directory" in result.output

    def test_an_unreadable_parent_is_named_rather_than_called_missing(self, app, tmp_path, monkeypatch):
        """The /root case, seen the way the application user sees it.

        To that user the folder inside is simply not there, and the parent
        cannot be entered to find out why. The inaccessibility is simulated
        rather than made real with chmod, because the suite may run as root --
        and root bypasses the permission bits, so a genuinely 0o000 directory
        would still be readable here and this branch would never be taken.
        """
        import os as os_module

        locked = tmp_path / "locked"
        locked.mkdir()
        target = locked / "avatars"  # deliberately not created

        real_access = os_module.access
        monkeypatch.setattr(
            os_module,
            "access",
            lambda path, mode, **kw: False if Path(path) == locked else real_access(path, mode, **kw),
        )

        result = self._run(app, tmp_path, target)

        assert result.exit_code != 0
        assert "cannot be reached" in result.output
        assert str(locked) in result.output, "name the directory to fix"
        assert "No such directory" not in result.output

    def test_an_empty_directory_is_refused_rather_than_importing_nobody(self, app, tmp_path):
        """Otherwise it reports 677 missing files, which reads as data loss."""
        empty = tmp_path / "empty"
        empty.mkdir()

        result = self._run(app, tmp_path, empty)

        assert result.exit_code != 0
        assert "is empty" in result.output

    def test_a_file_is_not_a_directory(self, app, tmp_path):
        not_a_dir = tmp_path / "avatars.zip"
        not_a_dir.write_bytes(b"pk")

        result = self._run(app, tmp_path, not_a_dir)

        assert result.exit_code != 0

    def test_a_good_directory_is_accepted(self, app, tmp_path):
        """The guard must not refuse the case it exists to protect."""
        from PIL import Image

        good = tmp_path / "avatars"
        good.mkdir()
        Image.new("RGB", (32, 32), (1, 2, 3)).save(good / "avatar_142.jpg")

        result = self._run(app, tmp_path, good)

        assert result.exit_code == 0, result.output
        assert "avatars=1" in result.output


class TestTheReviewReport:
    """One row per person, so the rehearsal can be read rather than trusted.

    The counts say the import ran. They do not say it did the right thing to
    any particular person, and 740 rows is far too many to check by eye -- so
    the report carries enough per person to spot a rule misfiring from a
    handful of them.
    """

    def test_every_person_gets_a_row(self, app):
        report = import_forum_people(
            [_person(uid="1", username="A_L23"), _person(uid="2", username="B_L24")],
            dry_run=True,
        )

        assert len(report["people"]) == 2
        assert {row["source_username"] for row in report["people"]} == {"A_L23", "B_L24"}

    def test_a_row_says_what_would_happen_to_them(self, app):
        first = import_forum_people([_person()], dry_run=False)
        db.session.commit()
        second = import_forum_people([_person()], dry_run=True)

        assert first["people"][0]["action"] == "create"
        assert second["people"][0]["action"] == "update", "the same person, seen again"

    def test_it_says_where_the_year_group_came_from(self, app):
        """A derived one is a guess from a name; an exported one is recorded fact."""
        report = import_forum_people(
            [
                _person(uid="1", username="A_L23", year_group="LAV23"),
                _person(uid="2", username="B_L19", year_group=None),
            ],
            dry_run=True,
        )
        rows = {row["source_username"]: row for row in report["people"]}

        assert rows["A_L23"]["year_group_from"] == "export"
        assert rows["B_L19"]["year_group_from"] == "username"
        assert rows["B_L19"]["year_group"] == "LAV19"


class TestWhoCanGetTheirAccountBack:
    """Knowing in advance who the automatic path cannot help.

    Everyone it cannot help is somebody who writes in during the first week of
    term and has to be linked up by hand. That is a number worth seeing while
    there is still time to do something about it, not in October.
    """

    def test_an_ordinary_person_can(self, app):
        report = import_forum_people([_person()], dry_run=True)

        assert report["people"][0]["can_reclaim"] == "yes"
        assert report["people"][0]["note"] == ""

    def test_two_accounts_on_one_address_cannot(self, app):
        """Claiming refuses a shared address rather than guessing between them."""
        shared = "shared@edu.fh-joanneum.at"
        report = import_forum_people(
            [
                _person(uid="1", username="A_L23", source_email=shared),
                _person(uid="2", username="B_L19", source_email=shared),
            ],
            dry_run=True,
        )

        assert [row["can_reclaim"] for row in report["people"]] == ["no", "no"]
        assert "2 accounts share this address" in report["people"][0]["note"]

    def test_somebody_with_no_address_cannot(self, app):
        report = import_forum_people(
            [_person(source_email="")], dry_run=True
        )

        assert report["people"][0]["can_reclaim"] == "no"
        assert "no address" in report["people"][0]["note"]

    def test_the_outlook_is_worked_out_before_anything_is_written(self, app):
        """A dry run has to report exactly what the real run will do."""
        shared = "shared@edu.fh-joanneum.at"
        people = [
            _person(uid="1", username="A_L23", source_email=shared),
            _person(uid="2", username="B_L19", source_email=shared),
        ]

        rehearsal = import_forum_people(people, dry_run=True)
        real = import_forum_people(people, dry_run=False)
        db.session.commit()

        assert [row["can_reclaim"] for row in rehearsal["people"]] \
            == [row["can_reclaim"] for row in real["people"]]
