"""Members only, and the archive read-only.

The failure this guards against does not raise and does not look like a
failure. A Discourse category with no group permission on it is public, so a
run that reports "1,529 posts" and nothing else has put thirteen years of exams
and transcripts, with students' names in them, where anybody can read them and
a crawler will. Everything here is about that one silence.
"""
import pytest

from aeronautics_members import app as app_module

from aeronautics_members.services.forum_permissions import (
    CREATE,
    EVERYONE,
    GUEST_GROUP,
    SEE,
    STAFF_GROUP,
    apply_permissions,
    current_permissions,
    describe,
    groups_wanted,
    live_tree,
    owned_roots,
    permission_plan,
)

WORKSHEET = {
    "mapping": [
        {"old_fid": "1", "old_path": "Bachelor", "decided": True,
         "target": "Bachelor 4. Semester / Angewandte Thermodynamik"},
        {"old_fid": "2", "old_path": "Bachelor", "decided": True,
         "target": "Bachelor 4. Semester / Mechanik 2"},
        {"old_fid": "3", "old_path": "Master", "decided": True,
         "target": "Master 2. Semester / CNS/ATM Systems"},
        {"old_fid": "4", "old_path": "Master", "decided": True, "target": "ARCHIVE"},
        {"old_fid": "5", "old_path": "Bachelor", "decided": False, "target": ""},
    ]
}

#: What /site.json answers with: flat, with a parent id on each row.
FORUM = [
    {"id": 1, "name": "Uncategorized", "parent_category_id": None},
    {"id": 2, "name": "Site Feedback", "parent_category_id": None},
    {"id": 10, "name": "Bachelor 4. Semester", "parent_category_id": None},
    {"id": 11, "name": "Angewandte Thermodynamik", "parent_category_id": 10},
    {"id": 12, "name": "Mechanik 2", "parent_category_id": 10},
    {"id": 20, "name": "Master 2. Semester", "parent_category_id": None},
    {"id": 21, "name": "CNS/ATM Systems", "parent_category_id": 20},
    {"id": 30, "name": "Archiv", "parent_category_id": None},
    {"id": 31, "name": "Bachelor", "parent_category_id": 30},
    {"id": 32, "name": "Master", "parent_category_id": 30},
]


class FakeForum:
    """Enough Discourse to answer what a category's permissions are."""

    def __init__(self, categories=FORUM, permissions=None):
        self.rows = {row["id"]: dict(row) for row in categories}
        for row in self.rows.values():
            row.setdefault("color", "0088CC")
            row["group_permissions"] = [
                {"group_name": name, "permission_type": level}
                for name, level in (permissions or {}).get(row["id"], {}).items()
            ]
        self.written = []
        self.refuse = set()

    def categories(self):
        return [
            {"id": row["id"], "name": row["name"],
             "parent_category_id": row["parent_category_id"]}
            for row in self.rows.values()
        ]

    def category(self, category_id):
        return dict(self.rows[int(category_id)])

    def set_category_permissions(self, category_id, permissions):
        if int(category_id) in self.refuse:
            raise RuntimeError("You are not permitted to view the requested resource")
        self.written.append((int(category_id), dict(permissions)))
        self.rows[int(category_id)]["group_permissions"] = [
            {"group_name": name, "permission_type": level}
            for name, level in permissions.items()
        ]


@pytest.fixture
def forum():
    return FakeForum()


@pytest.fixture
def plan(forum):
    made, _untouched = permission_plan(
        forum.categories(), owned_roots(WORKSHEET), member_group="members"
    )
    return made


class TestWhichCategoriesAreOurs:
    def test_the_roots_come_from_the_worksheet_not_the_forum(self):
        """The forum cannot tell what we made from what Discourse shipped."""
        assert owned_roots(WORKSHEET) == {
            "Archiv", "Bachelor 4. Semester", "Master 2. Semester",
        }

    def test_a_lecture_name_with_a_slash_does_not_become_a_root(self):
        """"CNS/ATM Systems" is one lecture, not a semester called CNS."""
        assert "CNS" not in owned_roots(WORKSHEET)

    def test_the_archive_is_always_ours_even_with_nothing_archived(self):
        assert "Archiv" in owned_roots({"mapping": [
            {"old_fid": "1", "decided": True, "target": "Bachelor 1. Semester / X"},
        ]})

    def test_discourses_own_categories_are_left_alone(self, forum):
        _made, untouched = permission_plan(
            forum.categories(), owned_roots(WORKSHEET), member_group="members"
        )
        assert sorted(name for _id, name in untouched) == [
            "Site Feedback", "Uncategorized",
        ]

    def test_a_tree_that_loops_does_not_hang(self):
        looping = [
            {"id": 1, "name": "A", "parent_category_id": 2},
            {"id": 2, "name": "B", "parent_category_id": 1},
        ]
        assert set(live_tree(looping)) == {1, 2}


class TestWhatEachGroupMay:
    def test_a_live_lecture_lets_members_start_topics(self, plan):
        """Students keep adding exams; a read-only lecture is a museum."""
        assert plan[11] == {"members": CREATE, STAFF_GROUP: CREATE}

    def test_the_semester_above_it_is_the_same(self, plan):
        assert plan[10]["members"] == CREATE

    def test_the_archive_is_readable_and_nothing_more(self, plan):
        assert plan[30]["members"] == SEE
        assert plan[31]["members"] == SEE
        assert plan[32]["members"] == SEE

    def test_the_staff_group_may_post_in_the_archive_too(self, plan):
        """Somebody has to be able to re-file a thread that landed wrong."""
        assert plan[31][STAFF_GROUP] == CREATE

    def test_everyone_is_never_granted_anywhere(self, plan):
        assert not any(EVERYONE in grants for grants in plan.values())

    def test_nobody_else_is_granted_anything(self, plan):
        for grants in plan.values():
            assert set(grants) == {"members", STAFF_GROUP}

    def test_the_member_group_name_is_whatever_the_portal_calls_it(self, forum):
        made, _ = permission_plan(
            forum.categories(), owned_roots(WORKSHEET), member_group="mitglieder"
        )
        assert made[11]["mitglieder"] == CREATE


class TestTheGroupsThatHaveToExistFirst:
    """add_groups silently drops names Discourse does not already have."""

    def test_every_group_the_portal_drives_is_in_the_list(self):
        wanted = groups_wanted({
            "forum_member_group": "members",
            "forum_onboarding_group": "member-onboarding",
            "forum_inactive_group": "lapsed",
        })
        assert wanted[:3] == ["members", "member-onboarding", "lapsed"]

    def test_a_group_the_portal_does_not_use_is_not_invented(self):
        wanted = groups_wanted({
            "forum_member_group": "members", "forum_inactive_group": "",
        })
        assert "" not in wanted

    def test_the_staff_and_guest_groups_are_there_too(self):
        wanted = groups_wanted({"forum_member_group": "members"})
        assert STAFF_GROUP in wanted and GUEST_GROUP in wanted

    def test_a_name_used_twice_is_listed_once(self):
        wanted = groups_wanted({
            "forum_member_group": "members", "forum_onboarding_group": "members",
        })
        assert wanted.count("members") == 1


class TestReadingWhatTheForumHasNow:
    def test_the_shape_discourse_sends(self):
        assert current_permissions({"group_permissions": [
            {"group_name": "members", "permission_type": 1},
        ]}) == {"members": 1}

    def test_the_other_shape_discourse_sends(self):
        assert current_permissions({"permissions": {"members": 3}}) == {"members": 3}

    def test_a_category_with_none_reads_as_none_rather_than_raising(self):
        assert current_permissions({"name": "Uncategorized"}) == {}

    def test_an_unreadable_row_does_not_take_the_others_with_it(self):
        assert current_permissions({"group_permissions": [
            "nonsense", {"group_name": "members", "permission_type": 3},
        ]}) == {"members": 3}


class TestApplyingThem:
    def test_every_category_in_the_plan_is_written(self, forum, plan):
        report = apply_permissions(forum, plan)
        assert report["set"] == len(plan)
        assert {written for written, _ in forum.written} == set(plan)

    def test_a_public_category_is_counted_as_one(self, forum, plan):
        """The number that says whether this run mattered at all."""
        assert apply_permissions(forum, plan)["opened"] == len(plan)

    def test_running_it_again_changes_nothing(self, forum, plan):
        apply_permissions(forum, plan)
        forum.written.clear()
        report = apply_permissions(forum, plan)
        assert report["set"] == 0
        assert report["already"] == len(plan)
        assert forum.written == []

    def test_a_category_that_already_grants_everyone_is_still_fixed(self):
        """Granting a group does not remove anybody: this is the whole point."""
        forum = FakeForum(permissions={11: {"members": CREATE, EVERYONE: CREATE}})
        made, _ = permission_plan(
            forum.categories(), owned_roots(WORKSHEET), member_group="members"
        )
        apply_permissions(forum, made)
        written = dict(forum.written)[11]
        assert EVERYONE not in written

    def test_a_dry_run_writes_nothing_and_still_reports(self, forum, plan):
        report = apply_permissions(forum, plan, dry_run=True)
        assert forum.written == []
        assert report["set"] == len(plan)

    def test_one_category_the_key_cannot_touch_does_not_stop_the_rest(
        self, forum, plan
    ):
        forum.refuse = {11}
        report = apply_permissions(forum, plan)
        assert report["set"] == len(plan) - 1
        assert len(report["problems"]) == 1
        assert "Angewandte Thermodynamik" in report["problems"][0]

    def test_it_says_what_it_did_in_words_a_person_can_check(self, plan):
        assert describe(plan[31]) == "members: see, staff: create"


class TestTheGroupsFollowThePortalForever:
    """Not an import step. The forum is a copy of the portal, continuously.

    Category permissions are set once and stay set, because they are a property
    of the category. Who is in which group is the opposite: it changes whenever
    a membership lapses or somebody joins the committee, and it has to change
    on the forum too without anybody going there to do it.
    """

    def _payload(self, app, *roles, staff_group="committee", state="active"):
        from conftest import db, make_member

        from aeronautics_members.forum_service import DiscourseConnectProvider

        member = make_member(email=f"{'-'.join(roles) or 'plain'}@example.com")
        for slug in roles:
            member.user.grant_role(app_module.get_role(slug))
        db.session.commit()
        provider = DiscourseConnectProvider(settings={
            "forum_member_group": "members",
            "forum_onboarding_group": "member-onboarding",
            "forum_inactive_group": "lapsed",
            "forum_staff_group": staff_group,
            "discourse_connect_secret": "s",
        })
        return provider.build_sso_payload(
            member.user, member, desired_state=state, nonce="n1"
        )

    def _groups(self, payload, field):
        return set(filter(None, payload.get(field, "").split(",")))

    def test_an_active_membership_is_in_the_member_group(self, app):
        payload = self._payload(app)
        assert "members" in self._groups(payload, "add_groups")

    def test_a_membership_that_lapsed_is_taken_out_of_it_again(self, app):
        """Access only ever granted is access nobody ever loses."""
        payload = self._payload(app, state="inactive")
        assert "members" in self._groups(payload, "remove_groups")
        assert "lapsed" in self._groups(payload, "add_groups")

    def test_somebody_who_runs_the_place_is_put_in_the_staff_group(self, app):
        payload = self._payload(app, "admin")
        assert "committee" in self._groups(payload, "add_groups")

    def test_an_ordinary_member_is_taken_out_of_it(self, app):
        """The sync says both halves every time, so a role that was taken away
        is taken away on the forum at the next sync rather than never."""
        payload = self._payload(app)
        assert "committee" in self._groups(payload, "remove_groups")
        assert "committee" not in self._groups(payload, "add_groups")

    def test_the_two_are_independent(self, app):
        """Somebody can run the forum while their own membership has lapsed."""
        payload = self._payload(app, "admin", state="inactive")
        assert "committee" in self._groups(payload, "add_groups")
        assert "members" in self._groups(payload, "remove_groups")

    def test_no_staff_group_configured_means_nothing_is_said_about_one(self, app):
        payload = self._payload(app, "admin", staff_group="")
        assert "committee" not in self._groups(payload, "add_groups")
        assert "committee" not in self._groups(payload, "remove_groups")

    def test_the_group_it_drives_is_one_the_categories_grant(self, app):
        """Driving a group the categories do not grant would change nothing."""
        made, _ = permission_plan(
            FakeForum().categories(), owned_roots(WORKSHEET),
            member_group="members", portal_staff_group="committee",
        )
        assert made[11]["committee"] == CREATE
        assert made[31]["committee"] == CREATE

    def test_it_is_made_before_anybody_is_put_in_it(self, app):
        """add_groups drops a name Discourse does not already have, silently."""
        assert "committee" in groups_wanted({
            "forum_member_group": "members", "forum_staff_group": "committee",
        })
