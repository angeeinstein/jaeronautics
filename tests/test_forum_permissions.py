"""Members only, and the archive read-only.

The failure this guards against does not raise and does not look like a
failure. A Discourse category with no group permission on it is public, so a
run that reports "1,529 posts" and nothing else has put thirteen years of exams
and transcripts, with students' names in them, where anybody can read them and
a crawler will. Everything here is about that one silence.
"""
import pytest

from aeronautics_members import app as app_module
from aeronautics_members.forum_service import member_category_groups
from aeronautics_members.member_categories import CATEGORY_ORDER

from aeronautics_members.services.forum_permissions import (
    CREATE,
    EVERYONE,
    SEE,
    STAFF_GROUP,
    access_groups,
    apply_permissions,
    current_permissions,
    describe,
    groups_wanted,
    let_authors_post,
    live_tree,
    owned_roots,
    permission_plan,
    unknown_member_kinds,
    what_is_not_set_up,
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
        forum.categories(), owned_roots(WORKSHEET), lecture_groups=["students"]
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
            forum.categories(), owned_roots(WORKSHEET), lecture_groups=["students"]
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
    def test_a_live_lecture_lets_students_start_topics(self, plan):
        """Students keep adding exams; a read-only lecture is a museum."""
        assert plan[11] == {"students": CREATE, STAFF_GROUP: CREATE}

    def test_the_semester_above_it_is_the_same(self, plan):
        assert plan[10]["students"] == CREATE

    def test_the_archive_is_readable_and_nothing_more(self, plan):
        assert plan[30]["students"] == SEE
        assert plan[31]["students"] == SEE
        assert plan[32]["students"] == SEE

    def test_the_staff_group_may_post_in_the_archive_too(self, plan):
        """Somebody has to be able to re-file a thread that landed wrong."""
        assert plan[31][STAFF_GROUP] == CREATE

    def test_everyone_is_never_granted_anywhere(self, plan):
        assert not any(EVERYONE in grants for grants in plan.values())

    def test_nobody_else_is_granted_anything(self, plan):
        for grants in plan.values():
            assert set(grants) == {"students", STAFF_GROUP}

    def test_several_groups_may_read_it(self, forum):
        """Students and alumni, say -- Discourse checks a union of groups."""
        made, _ = permission_plan(
            forum.categories(), owned_roots(WORKSHEET),
            lecture_groups=["students", "alumni"],
        )
        assert made[11] == {"students": CREATE, "alumni": CREATE,
                            STAFF_GROUP: CREATE}

    def test_the_archive_can_be_read_by_somebody_the_lectures_are_not(self, forum):
        made, _ = permission_plan(
            forum.categories(), owned_roots(WORKSHEET),
            lecture_groups=["students"], archive_groups=["students", "alumni"],
        )
        assert set(made[31]) == {"students", "alumni", STAFF_GROUP}
        assert "alumni" not in made[11]

    def test_granting_nobody_is_refused_rather_than_done(self, forum):
        """A plan that grants nothing reads as "members only" and is not."""
        with pytest.raises(ValueError, match="nobody to grant"):
            permission_plan(forum.categories(), owned_roots(WORKSHEET),
                            lecture_groups=[])

    def test_the_member_group_is_deliberately_not_the_answer(self, plan):
        """The mistake this whole arrangement exists to stop.

        A lecturer and a company representative are full members who pay the
        same fee. Granting the member group would show every exam paper to the
        people who set them.
        """
        for grants in plan.values():
            assert "members" not in grants


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

    def test_discourses_own_staff_group_is_there_too(self):
        """Granted everywhere, so it has to be named; it always exists."""
        assert STAFF_GROUP in groups_wanted({"forum_member_group": "members"})

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
        forum = FakeForum(permissions={11: {"students": CREATE, EVERYONE: CREATE}})
        made, _ = permission_plan(
            forum.categories(), owned_roots(WORKSHEET), lecture_groups=["students"]
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
        assert describe(plan[31]) == "staff: create, students: see"


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
            lecture_groups=["students"], staff_groups=(STAFF_GROUP, "committee"),
        )
        assert made[11]["committee"] == CREATE
        assert made[31]["committee"] == CREATE

    def test_it_is_made_before_anybody_is_put_in_it(self, app):
        """add_groups drops a name Discourse does not already have, silently."""
        assert "committee" in groups_wanted({
            "forum_member_group": "members", "forum_staff_group": "committee",
        })


class TestSortingPeopleByWhatKindOfMemberTheyAre:
    """Two axes, not one.

    What somebody has paid for and what kind of person they are are different
    questions. A lecturer is not a student whose membership is in a different
    state, and a category that wants only one of the two should be able to say
    so without dragging in the other. So membership drives one group, the
    member category drives another, and Discourse grants whichever it means.
    """

    SETTING = "student = students\nstaff = lecturers\npartner = companies\n"

    def _payload(self, app, category, mapping=None):
        from conftest import db, make_member

        from aeronautics_members.forum_service import DiscourseConnectProvider

        member = make_member(email=f"{category}@example.com")
        member.member_category = category
        db.session.commit()
        provider = DiscourseConnectProvider(settings={
            "forum_member_group": "members",
            "forum_category_groups": self.SETTING if mapping is None else mapping,
            "discourse_connect_secret": "s",
        })
        return provider.build_sso_payload(
            member.user, member, desired_state="active", nonce="n1"
        )

    def _groups(self, payload, field):
        return set(filter(None, payload.get(field, "").split(",")))

    def test_the_mapping_is_read_from_the_setting(self):
        assert member_category_groups({"forum_category_groups": self.SETTING}) == {
            "student": "students", "staff": "lecturers", "partner": "companies",
        }

    def test_blank_lines_comments_and_nonsense_are_skipped(self):
        assert member_category_groups({"forum_category_groups": (
            "\n# who gets what\nstudent = students\nnot a line\nalumni =\n"
        )}) == {"student": "students"}

    def test_a_kind_of_member_this_portal_does_not_have_is_not_invented(self):
        """A typo in the setting should not make a group nobody is ever in."""
        assert member_category_groups({
            "forum_category_groups": "studnet = students"
        }) == {}

    def test_a_student_is_put_in_the_student_group(self, app):
        payload = self._payload(app, "student")
        assert "students" in self._groups(payload, "add_groups")

    def test_and_taken_out_of_the_others(self, app):
        """A student who graduates moves groups at the next sync, by itself."""
        payload = self._payload(app, "student")
        assert self._groups(payload, "remove_groups") >= {"lecturers", "companies"}

    def test_a_lecturer_is_sorted_by_kind_and_not_by_what_they_paid(self, app):
        payload = self._payload(app, "staff")
        assert "lecturers" in self._groups(payload, "add_groups")
        assert "members" in self._groups(payload, "add_groups")

    def test_a_kind_with_no_line_is_in_none_of_them(self, app):
        """"We have not decided about honorary members yet" is a real answer."""
        payload = self._payload(app, "honorary")
        assert self._groups(payload, "add_groups") == {"members"}
        assert self._groups(payload, "remove_groups") >= {
            "students", "lecturers", "companies",
        }

    def test_an_empty_setting_says_nothing_about_kinds_at_all(self, app):
        payload = self._payload(app, "student", mapping="")
        assert self._groups(payload, "add_groups") == {"members"}
        assert "students" not in self._groups(payload, "remove_groups")

    def test_the_groups_are_made_before_anybody_is_put_in_them(self):
        wanted = groups_wanted(
            {"forum_member_group": "members"},
            extra=["students", "lecturers", "companies"],
        )
        assert {"students", "lecturers", "companies"} <= set(wanted)

    def test_they_are_granted_nothing_until_somebody_says_so(self, app):
        """What a kind of member may see is decided on the forum, per category.

        Access to the lecture material is "has paid", which is the members
        group. Restricting something to lecturers is a grant somebody makes in
        Discourse, on the one category they mean -- not something this command
        guesses at across a hundred and seven of them.
        """
        made, _ = permission_plan(
            FakeForum().categories(), owned_roots(WORKSHEET),
            lecture_groups=["students"],
        )
        for grants in made.values():
            assert not {"lecturers", "companies"} & set(grants)


class TestNotUndoingWhatSomebodyDecidedOnTheForum:
    """The standing job is narrow: nothing is ever left open.

    Who may see what is decided on the forum, over months, one category at a
    time -- a job board that companies may post in, a lounge that only the
    committee reads. A command that reimposed its own idea of the answer every
    time it ran would quietly undo all of that, and the undoing would look like
    nothing at all.
    """

    def test_a_category_somebody_gave_permissions_to_is_left_alone(self):
        forum = FakeForum(permissions={11: {"lecturers": CREATE}})
        made, _ = permission_plan(
            forum.categories(), owned_roots(WORKSHEET), lecture_groups=["students"]
        )
        report = apply_permissions(forum, made)
        assert report["decided_elsewhere"] == 1
        assert 11 not in dict(forum.written)

    def test_but_one_that_is_still_open_is_closed(self):
        forum = FakeForum(permissions={11: {"lecturers": CREATE}})
        made, _ = permission_plan(
            forum.categories(), owned_roots(WORKSHEET), lecture_groups=["students"]
        )
        apply_permissions(forum, made)
        assert 12 in dict(forum.written)

    def test_one_that_still_grants_everyone_is_not_a_decision(self):
        """Public plus a group is still public, whoever set it."""
        forum = FakeForum(permissions={11: {"lecturers": CREATE, EVERYONE: SEE}})
        made, _ = permission_plan(
            forum.categories(), owned_roots(WORKSHEET), lecture_groups=["students"]
        )
        apply_permissions(forum, made)
        assert EVERYONE not in dict(forum.written)[11]

    def test_enforce_puts_it_back_to_the_plan_when_that_is_what_is_wanted(self):
        forum = FakeForum(permissions={11: {"lecturers": CREATE}})
        made, _ = permission_plan(
            forum.categories(), owned_roots(WORKSHEET), lecture_groups=["students"]
        )
        report = apply_permissions(forum, made, enforce=True)
        assert report["decided_elsewhere"] == 0
        assert dict(forum.written)[11] == {"students": CREATE, STAFF_GROUP: CREATE}


class TestReadingTheAccessSettings:
    def test_commas_and_newlines_are_both_how_people_type_a_list(self):
        assert access_groups(
            {"forum_lecture_groups": "students, alumni\nhonorary"},
            "forum_lecture_groups",
        ) == ["students", "alumni", "honorary"]

    def test_an_empty_setting_is_empty_rather_than_a_group_called_nothing(self):
        assert access_groups({"forum_lecture_groups": " , "},
                             "forum_lecture_groups") == []

    def test_the_archive_falls_back_to_whoever_may_read_the_lectures(self):
        assert access_groups({}, "forum_archive_groups", ["students"]) == ["students"]


class TestAGroupMeansHasPaidAndIsThatKindOfPerson:
    """Discourse checks a union of groups, never an intersection.

    The lecture categories are granted to the kind-of-member groups, so being
    in one has to mean both things at once. The conjunction cannot be written
    on the forum side, so it is made here: a student whose membership lapses
    leaves the students group, not only the members group.
    """

    def _payload(self, state):
        from conftest import db, make_member

        from aeronautics_members.forum_service import DiscourseConnectProvider

        member = make_member(email=f"{state}-student@example.com")
        member.member_category = "student"
        db.session.commit()
        provider = DiscourseConnectProvider(settings={
            "forum_member_group": "members",
            "forum_category_groups": "student = students\nstaff = lecturers",
            "discourse_connect_secret": "s",
        })
        return provider.build_sso_payload(
            member.user, member, desired_state=state, nonce="n1"
        )

    def _groups(self, payload, field):
        return set(filter(None, payload.get(field, "").split(",")))

    def test_an_active_student_is_in_the_students_group(self, app):
        assert "students" in self._groups(self._payload("active"), "add_groups")

    def test_a_lapsed_student_is_taken_out_of_it(self, app):
        """Otherwise a membership that ran out still reads every exam paper."""
        payload = self._payload("inactive")
        assert "students" in self._groups(payload, "remove_groups")
        assert "students" not in self._groups(payload, "add_groups")

    def test_somebody_still_onboarding_is_not_in_it_yet(self, app):
        payload = self._payload("onboarding")
        assert "students" in self._groups(payload, "remove_groups")


class TestSayingWhatHasNotBeenDecidedYet:
    """Each of these settings is empty by default, and empty is a silence.

    A run with them unset does something defensible and not what anybody meant:
    groups nobody is in, material nobody may read, people put nowhere. Said
    before the run, because deducing it from the results afterwards means
    deducing it from a forum that is already wrong.
    """

    FULL = {
        "forum_lecture_groups": "students, alumni",
        "forum_category_groups": "student = students",
        "forum_staff_group": "committee",
        "forum_inactive_group": "membership-inactive",
    }

    def _names(self, settings):
        return [name for name, _why in what_is_not_set_up(settings)]

    def test_a_forum_with_nothing_set_names_all_four(self):
        assert len(self._names({})) == 4

    def test_a_forum_with_everything_set_says_nothing(self):
        assert self._names(self.FULL) == []

    def test_the_one_that_stops_the_run_is_named(self):
        assert "forum_lecture_groups" in self._names(
            dict(self.FULL, forum_lecture_groups="")
        )

    def test_groups_nobody_will_ever_be_in_are_named(self):
        """Lecture groups set and no kind mapping is a forum nobody can read."""
        assert "forum_category_groups" in self._names(
            dict(self.FULL, forum_category_groups="")
        )

    def test_people_who_would_be_put_nowhere_are_named(self):
        assert "forum_inactive_group" in self._names(
            dict(self.FULL, forum_inactive_group="")
        )

    def test_each_one_says_what_goes_wrong_rather_than_only_its_name(self):
        for _name, why in what_is_not_set_up({}):
            assert len(why) > 40


class TestALineThatNamesNothing:
    """The left-hand side is fixed and a typo in it is silent.

    It is read, matched against nothing and dropped -- so a group is never
    made, people are never sorted, and every report says the run succeeded.
    """

    def test_a_misspelled_kind_is_named(self):
        assert unknown_member_kinds({
            "forum_category_groups": "studnet = students\nalumni = alumni",
        }) == ["studnet = students"]

    def test_a_line_with_no_group_on_the_right_is_named(self):
        assert unknown_member_kinds({
            "forum_category_groups": "student =\n",
        }) == ["student ="]

    def test_every_real_kind_passes(self):
        lines = "\n".join(f"{kind} = g-{kind}" for kind in CATEGORY_ORDER)
        assert unknown_member_kinds({"forum_category_groups": lines}) == []

    def test_comments_and_blank_lines_are_not_mistakes(self):
        assert unknown_member_kinds({
            "forum_category_groups": "\n# who gets what\nstudent = students\n",
        }) == []

    def test_the_setup_report_says_which_lines_and_what_is_allowed(self):
        problems = dict(what_is_not_set_up({
            "forum_lecture_groups": "students",
            "forum_category_groups": "studnet = students",
            "forum_staff_group": "committee",
            "forum_inactive_group": "membership-inactive",
        }))
        assert "studnet = students" in problems["forum_category_groups"]
        assert "honorary" in problems["forum_category_groups"]


class TestTheBoxesOnTheSettingsPage:
    """One box per kind, because the left-hand side is not a thing to type.

    It is fixed -- five values this portal stores on a member -- so it belongs
    in a label beside the box rather than in something that has to be spelled
    right, silently does nothing when it is not, and has to be looked up
    somewhere to be spelled at all.
    """

    @pytest.fixture
    def admin_client(self, app, client):
        from conftest import app_module, db

        from aeronautics_members.db_models import User

        admin = User(email="admin-forum-groups@example.com")
        admin.set_password("x")
        admin.grant_role(app_module.get_role("superadmin"))
        db.session.add(admin)
        db.session.commit()
        with client.session_transaction() as session:
            session["_user_id"] = str(admin.id)
        return client

    def _save(self, client, **groups):
        from conftest import db

        form = {
            "save_settings": "1",
            "settings_section": "forum",
            "forum_integration_enabled": "y",
            "forum_provider": "discourse",
            "forum_auth_strategy": "discourse_connect",
            "forum_base_url": "https://forum.example.org",
            "discourse_api_username": "system",
            "forum_member_group": "members",
            "forum_onboarding_group": "members-onboarding",
            "forum_inactive_group": "membership-inactive",
            "forum_lecture_groups": "students",
            "forum_onboarding_path": "/",
            "forum_avatar_max_bytes": "5242880",
            "forum_avatar_allowed_types": "jpg,png",
        }
        form.update({f"forum_group_{kind}": name for kind, name in groups.items()})
        response = client.post("/admin/settings", data=form, follow_redirects=True)
        assert response.status_code == 200
        db.session.expire_all()
        return response

    def test_a_box_per_kind_becomes_the_mapping_the_rest_of_it_reads(
        self, admin_client
    ):
        self._save(admin_client, student="students", staff="institute")
        from aeronautics_members.services.forum import get_forum_settings_map

        assert member_category_groups(get_forum_settings_map()) == {
            "student": "students", "staff": "institute",
        }

    def test_a_kind_left_empty_is_in_no_group(self, admin_client):
        self._save(admin_client, student="students", partner="")
        from aeronautics_members.services.forum import get_forum_settings_map

        assert "partner" not in member_category_groups(get_forum_settings_map())

    def test_what_is_saved_can_never_name_a_kind_that_does_not_exist(
        self, admin_client
    ):
        """The failure the text area allowed: a typo that does nothing."""
        self._save(admin_client, student="students")
        from aeronautics_members.services.forum import get_forum_settings_map

        assert unknown_member_kinds(get_forum_settings_map()) == []

    def test_every_kind_gets_a_box(self, admin_client):
        page = admin_client.get("/admin/settings").get_data(as_text=True)
        for kind in CATEGORY_ORDER:
            assert f'name="forum_group_{kind}"' in page


class TestTheOrderDiscourseInsistsOn:
    """Discourse refuses to restrict a parent while a child still admits more.

        Any group that is allowed to access a subcategory must also be allowed
        to access the parent category. The following groups have access to one
        of the subcategories, but no access to parent category: everyone.

    Every category starts public, so restricting a semester before its lectures
    means restricting a parent while eleven children still admit everyone. The
    first run against a real forum set 97 categories and was refused all ten of
    the top-level ones for exactly this.
    """

    class StrictForum(FakeForum):
        """A fake that enforces the rule, so the order is actually tested."""

        def set_category_permissions(self, category_id, permissions):
            children = [
                row for row in self.rows.values()
                if row["parent_category_id"] == int(category_id)
            ]
            for child in children:
                admitted = {
                    entry["group_name"] for entry in child["group_permissions"]
                }
                if admitted - set(permissions):
                    raise RuntimeError(
                        "(422): Any group that is allowed to access a "
                        "subcategory must also be allowed to access the parent "
                        "category."
                    )
            super().set_category_permissions(category_id, permissions)

    def _plan(self, forum):
        made, _ = permission_plan(
            forum.categories(), owned_roots(WORKSHEET), lecture_groups=["students"]
        )
        return made

    def test_the_plan_puts_the_deepest_categories_first(self, forum):
        plan = self._plan(forum)
        tree = live_tree(forum.categories())
        depths = [len(tree[category_id]) for category_id in plan]
        assert depths == sorted(depths, reverse=True)

    def test_a_forum_that_enforces_it_still_ends_up_fully_restricted(self):
        forum = self.StrictForum()
        report = apply_permissions(forum, self._plan(forum))
        assert report["problems"] == []
        assert report["set"] == len(self._plan(forum))

    def test_no_category_is_left_admitting_everyone(self):
        forum = self.StrictForum()
        forum.rows[11]["group_permissions"] = [
            {"group_name": EVERYONE, "permission_type": CREATE}
        ]
        apply_permissions(forum, self._plan(forum))
        for row in forum.rows.values():
            admitted = {e["group_name"] for e in row["group_permissions"]}
            assert EVERYONE not in admitted or row["id"] in (1, 2)

    def test_one_that_cannot_be_settled_is_still_reported_once(self):
        forum = self.StrictForum()
        forum.refuse = {11}
        report = apply_permissions(forum, self._plan(forum))
        assert len(report["problems"]) == 1
        assert "Angewandte Thermodynamik" in report["problems"][0]


class TestTheAuthorsMayPostWhileTheImportRuns:
    """Found for real: with the categories restricted first, every post refused.

    The archive is posted as its authors, and Discourse checks each of them
    against the category like anybody typing. They are the old forum's people
    -- in ``old_forum``, not in ``students`` -- so they may post only for as
    long as the import runs.
    """

    class BothWays(FakeForum):
        """Discourse's rule, checked from the child's side as well as the parent's."""

        def set_category_permissions(self, category_id, permissions):
            row = self.rows[int(category_id)]
            parent = self.rows.get(row["parent_category_id"])
            if parent is not None:
                parents = {e["group_name"] for e in parent["group_permissions"]}
                if parents and set(permissions) - parents:
                    raise RuntimeError("(422): must also be allowed on the parent")
            for child in self.rows.values():
                if child["parent_category_id"] == int(category_id):
                    admitted = {e["group_name"] for e in child["group_permissions"]}
                    if admitted - set(permissions):
                        raise RuntimeError("(422): a subcategory admits more")
            super().set_category_permissions(category_id, permissions)

    def _restricted(self):
        forum = self.BothWays()
        plan, _ = permission_plan(
            forum.categories(), owned_roots(WORKSHEET),
            lecture_groups=["students"], staff_groups=["committee"],
        )
        apply_permissions(forum, plan)
        return forum, plan

    def _grants(self, forum, category_id):
        return current_permissions(forum.category(category_id))

    def test_they_may_post_everywhere_the_import_writes(self):
        forum, plan = self._restricted()

        report = let_authors_post(forum, list(plan), "old_forum", allow=True)

        assert report["problems"] == []
        for category_id in plan:
            assert self._grants(forum, category_id)["old_forum"] == CREATE

    def test_and_afterwards_exactly_what_was_there_before(self):
        forum, plan = self._restricted()
        before = {category_id: self._grants(forum, category_id) for category_id in plan}

        let_authors_post(forum, list(plan), "old_forum", allow=True)
        report = let_authors_post(forum, list(plan), "old_forum", allow=False)

        assert report["problems"] == []
        after = {category_id: self._grants(forum, category_id) for category_id in plan}
        assert after == before

    def test_something_decided_on_the_forum_survives_the_round_trip(self):
        """Added to and taken from what is there -- never replaced by the plan."""
        forum, plan = self._restricted()
        # Somebody on the forum let students add to this part of the archive.
        chosen = {"students": CREATE, "committee": CREATE}
        forum.set_category_permissions(31, chosen)

        let_authors_post(forum, list(plan), "old_forum", allow=True)
        let_authors_post(forum, list(plan), "old_forum", allow=False)

        assert self._grants(forum, 31) == chosen

    def test_a_category_still_public_is_not_restricted_to_the_authors(self):
        """Adding a group to an empty set would make it that group's alone."""
        forum = self.BothWays()

        report = let_authors_post(forum, [11, 10], "old_forum", allow=True)

        assert forum.written == []
        assert report["public"] == 2

    def test_a_child_that_fails_once_does_not_strand_its_parent(self):
        """Found for real: the retry took the parents first and all ten failed.

        A forum under load refused some subcategories with a 500. Retrying the
        parents before those children meant each parent was refused because a
        child still admitted old_forum -- and the parents kept it.
        """
        forum, plan = self._restricted()
        let_authors_post(forum, list(plan), "old_forum", allow=True)
        flaky = {11, 21}
        setting = forum.set_category_permissions

        def once_refused(category_id, permissions):
            if int(category_id) in flaky:
                flaky.discard(int(category_id))
                raise RuntimeError("(500): Internal Server Error")
            return setting(category_id, permissions)

        forum.set_category_permissions = once_refused

        report = let_authors_post(forum, list(plan), "old_forum", allow=False)

        assert report["problems"] == []
        for category_id in plan:
            assert "old_forum" not in self._grants(forum, category_id)

    def test_one_that_never_goes_through_is_reported_once(self):
        forum, plan = self._restricted()
        let_authors_post(forum, list(plan), "old_forum", allow=True)
        forum.refuse = {11}

        report = let_authors_post(forum, list(plan), "old_forum", allow=False)

        assert len(report["problems"]) == 2, "the child, and its parent after it"
        assert any("Angewandte Thermodynamik" in problem for problem in report["problems"])

    def test_categories_outside_the_import_are_never_touched(self):
        forum, plan = self._restricted()
        forum.written.clear()

        let_authors_post(forum, list(plan), "old_forum", allow=True)

        assert {category_id for category_id, _ in forum.written} <= set(plan)
