"""The portal's idea of somebody's forum name must be the forum's idea of it.

Discourse does not have to accept the username it is handed. It caps them at
``max_username_length`` -- twenty by default -- and shortens anything longer as
it creates the account, and it appends to one that is already taken. It reports
neither.

The portal's scheme is surname, initial, year group, so a student called
Niedergrottenthaler needs twenty-four characters. Left alone the portal shows a
name the forum has never heard of, which is not merely cosmetic: the archive
import posts each old message *as* its author, by name, and four files and a
post were lost to exactly this before it was understood.
"""
from conftest import db, make_member
from aeronautics_members.forum_service import _record_the_name_the_forum_gave


def _somebody(email="student@example.com", forum_username="NiedergrottenthalerR_L25"):
    member = make_member(email=email, last_name="Niedergrottenthaler")
    member.user.forum_username = forum_username
    db.session.commit()
    return member.user


class TestFollowingTheForumsName:
    def test_a_shortened_username_is_written_down(self, app):
        user = _somebody()

        changed = _record_the_name_the_forum_gave(
            user, {"id": 7, "username": "NiedergrottenthalerR"}
        )

        assert changed is True
        assert user.forum_username == "NiedergrottenthalerR"

    def test_a_name_that_came_back_unchanged_changes_nothing(self, app):
        user = _somebody(forum_username="HuberA_L25")

        assert _record_the_name_the_forum_gave(
            user, {"id": 7, "username": "HuberA_L25"}
        ) is False

    def test_a_forum_that_said_nothing_is_not_taken_as_an_empty_name(self, app):
        """A missing field must not blank somebody's username."""
        user = _somebody(forum_username="HuberA_L25")

        assert _record_the_name_the_forum_gave(user, {"id": 7}) is False
        assert user.forum_username == "HuberA_L25"

    def test_a_name_another_account_already_holds_is_reported_not_taken(self, app):
        """Two portal accounts cannot hold one forum name.

        Which of them is wrong is a question for a person, so this says so and
        leaves both alone rather than trading one wrong record for a crash on
        the unique index.
        """
        _somebody(email="first@example.com", forum_username="NiedergrottenthalerR")
        second = _somebody(email="second@example.com")

        changed = _record_the_name_the_forum_gave(
            second, {"id": 8, "username": "NiedergrottenthalerR"}
        )

        assert changed is False
        assert second.forum_username == "NiedergrottenthalerR_L25"

    def test_whitespace_around_it_is_not_part_of_the_name(self, app):
        user = _somebody()

        _record_the_name_the_forum_gave(user, {"username": " HuberA_L25 "})

        assert user.forum_username == "HuberA_L25"
