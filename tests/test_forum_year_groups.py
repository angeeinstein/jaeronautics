"""A new member's year group reaches the forum, as an imported person's does.

Until 2026-09-26 only the 740 people imported from the old board had "Year
group" on their forum profile and were in their cohort group (lav24, mav13,
...). A new LAV25 student showed "Year group --" and was in no cohort group, so
@lav25 never reached them.

The cohort group is kept for good, like old_forum: it says who somebody is, not
what they may see -- no category is ever granted to one.
"""
from conftest import db, make_member

from aeronautics_members.forum_service import (
    FORUM_STATE_ACTIVE,
    FORUM_STATE_INACTIVE,
    DiscourseConnectProvider,
    _ALREADY_ON_THE_FORUM,
    group_name_for_year_group,
    year_group_for,
)


def _provider(field=None):
    provider = DiscourseConnectProvider(settings={
        "forum_member_group": "members",
        "forum_inactive_group": "membership-inactive",
        "forum_category_groups": "student = students",
        "discourse_connect_secret": "s",
    })
    provider.year_group_field = field
    return provider


def _member(year_group="LAV25", email="new.student@example.com"):
    member = make_member(email=email)
    member.member_category = "student"
    member.year_group = year_group
    db.session.commit()
    return member


def _groups(payload, field):
    return set(filter(None, payload.get(field, "").split(",")))


class TestTheCohortGroup:
    def test_a_new_student_is_put_in_their_cohort(self, app):
        member = _member()
        payload = _provider().build_sso_payload(
            member.user, member, FORUM_STATE_ACTIVE, nonce="n")
        assert "lav25" in _groups(payload, "add_groups")

    def test_and_stays_in_it_when_the_membership_ends(self, app):
        """Who somebody is does not lapse."""
        member = _member()
        payload = _provider().build_sso_payload(
            member.user, member, FORUM_STATE_INACTIVE, nonce="n")
        assert "lav25" in _groups(payload, "add_groups")
        assert "lav25" not in _groups(payload, "remove_groups")
        assert "students" in _groups(payload, "remove_groups"), "access does end"

    def test_somebody_with_no_year_group_is_in_no_cohort(self, app):
        member = _member(year_group=None, email="lecturer@example.com")
        payload = _provider().build_sso_payload(
            member.user, member, FORUM_STATE_ACTIVE, nonce="n")
        assert not {g for g in _groups(payload, "add_groups") if g.startswith(("lav", "mav"))}

    def test_the_name_is_the_one_the_import_used(self, app):
        assert group_name_for_year_group("LAV24") == "lav24"
        assert group_name_for_year_group(" mav-13 ") == "mav_13"


class TestTheProfileField:
    def test_it_is_filled_in(self, app):
        member = _member()
        payload = _provider(field="user_field_1").build_sso_payload(
            member.user, member, FORUM_STATE_ACTIVE, nonce="n")
        assert payload["custom.user_field_1"] == "LAV25"

    def test_a_forum_without_the_field_is_sent_nothing_about_it(self, app):
        member = _member()
        payload = _provider(field=None).build_sso_payload(
            member.user, member, FORUM_STATE_ACTIVE, nonce="n")
        assert not any(key.startswith("custom.") for key in payload)

    def test_a_reclaimed_account_falls_back_to_the_old_forums_year_group(self, app):
        from datetime import datetime, timezone
        from types import SimpleNamespace

        member = SimpleNamespace(
            year_group=None,
            user=SimpleNamespace(imported_forum_profile=SimpleNamespace(
                year_group="lav24", claimed_at=datetime.now(timezone.utc))),
        )
        assert year_group_for(member) == "LAV24"


class TestMakingSureTheyExist:
    """add_groups drops a group the forum does not have, without a word."""

    class Recording(DiscourseConnectProvider):
        def __init__(self):
            super().__init__({"forum_base_url": "https://forum.test"})
            self.groups, self.fields = [], 0

        def ensure_group(self, name):
            self.groups.append(name)
            return {"id": 1, "name": name}, True

        def ensure_user_field(self, name, description=""):
            self.fields += 1
            return "user_field_7", False

    def test_the_cohort_group_and_field_are_made_before_the_sync(self, app):
        _ALREADY_ON_THE_FORUM.clear()
        provider = self.Recording()
        member = _member(year_group="LAV26", email="next.year@example.com")

        provider.prepare_for(member)

        assert provider.groups == ["lav26"]
        assert provider.year_group_field == "user_field_7"

    def test_once_per_process_not_once_per_sync(self, app):
        _ALREADY_ON_THE_FORUM.clear()
        member = _member(year_group="LAV26", email="twice@example.com")
        first, second = self.Recording(), self.Recording()

        first.prepare_for(member)
        second.prepare_for(member)

        assert second.groups == [] and second.fields == 0
        assert second.year_group_field == "user_field_7"
