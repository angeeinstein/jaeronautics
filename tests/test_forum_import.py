"""Importing the old forum's people.

Six hundred rows from a decade of MyBB, and the import will be run more than
once — the first export always turns out to be missing something. So the
properties worth pinning are not "it creates rows" but what happens on the
second run, what happens to the dead email addresses, and what happens when
one of six hundred entries is malformed.
"""
import json
from datetime import date

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


def test_the_placeholder_address_is_unique_per_person(app):
    first = imported_email_for("mybb", "1")
    second = imported_email_for("mybb", "2")

    assert first != second
    assert first.endswith(f"@{IMPORTED_EMAIL_DOMAIN}")
