"""What kind of member somebody is.

Membership is not only for students, and the kinds are not interchangeable:
an alumnus has a year group, a partner has none, and both are full members.
So the category is stored in its own right -- it cannot be read off the year
group, which was the first design and is why these tests exist.

The properties worth pinning are that the category and the year group stay
consistent with each other, that the rules live in one module rather than
being restated in forms, JavaScript and templates, and that the permissive
path is never the one a missing field falls into.
"""
import pytest
from werkzeug.datastructures import MultiDict

from conftest import app_module, db, make_member
from aeronautics_members import member_categories
from aeronautics_members.db_models import (
    Member,
    MemberProfileChangeRequest,
    User,
)
from aeronautics_members.forms import IdentityChangeRequestForm, MembershipForm
from aeronautics_members.member_categories import (
    CATEGORY_ORDER,
    DEFAULT_CATEGORY,
    MemberCategory,
)
from aeronautics_members.services.forum import (
    FORUM_USERNAME_LENGTH_LIMIT,
    build_forum_username_base,
)
from aeronautics_members.services.members import normalize_optional_member_value

NEEDS_A_YEAR_GROUP = [MemberCategory.STUDENT]
OFFERED_A_YEAR_GROUP = [MemberCategory.STUDENT, MemberCategory.ALUMNI]
NOT_ASKED = [MemberCategory.STAFF, MemberCategory.PARTNER, MemberCategory.HONORARY]


def _signup_data(**overrides):
    data = {
        "salutation": "Mr",
        "first_name": "Jonas",
        "last_name": "Huber",
        "street": "Main Street",
        "house_number": "1",
        "postal_code": "8010",
        "city": "Graz",
        "country": "Austria",
        "phone_private": "+43000000000",
        "email_private": "jonas@example.com",
        "email_work": "jonas.huber@edu.fh-joanneum.at",
        "member_category": MemberCategory.STUDENT,
        "year_group": "LAV25",
        "password": "a-long-enough-password",
        "confirm_password": "a-long-enough-password",
        "terms_accepted": "y",
    }
    data.update(overrides)
    # A real POST body, so an omitted radio is genuinely omitted rather than a
    # Python None the form layer would treat differently.
    return MultiDict((key, value) for key, value in data.items() if value is not None)


class TestTheRulesAreComplete:
    """Adding a category must not half-happen.

    Everything about one lives in member_categories.py precisely so that a new
    kind of member is one edit. These fail if that edit is partial.
    """

    @pytest.mark.parametrize("category", CATEGORY_ORDER)
    def test_every_category_has_a_label_and_a_description(self, category):
        assert member_categories.category_label(category)
        assert member_categories.category_description(category)

    @pytest.mark.parametrize("category", CATEGORY_ORDER)
    def test_every_category_has_a_year_group_rule(self, category):
        assert category in member_categories.YEAR_GROUP_RULES

    @pytest.mark.parametrize("category", CATEGORY_ORDER)
    def test_a_required_year_group_is_also_a_shown_one(self, category):
        """A field nobody is asked for cannot be one they must fill in."""
        if member_categories.requires_year_group(category):
            assert member_categories.shows_year_group(category)

    def test_the_form_offers_exactly_the_known_categories(self):
        assert [value for value, _label in member_categories.category_choices()] == list(
            CATEGORY_ORDER
        )

    def test_the_default_is_a_real_category(self):
        assert member_categories.is_valid(DEFAULT_CATEGORY)

    def test_an_unknown_value_is_not_valid_and_needs_no_year_group(self):
        assert member_categories.is_valid("lecturer") is False
        assert member_categories.shows_year_group("lecturer") is False
        assert member_categories.requires_year_group("lecturer") is False


class TestTheForm:
    @pytest.mark.parametrize("category", NEEDS_A_YEAR_GROUP)
    def test_a_category_that_needs_one_is_refused_without_it(self, app, category):
        form = MembershipForm(
            formdata=_signup_data(member_category=category, year_group=""),
            meta={"csrf": False},
        )

        assert form.validate() is False
        assert "year_group" in form.errors

    @pytest.mark.parametrize("category", OFFERED_A_YEAR_GROUP)
    def test_a_category_that_is_offered_one_still_has_to_spell_it_right(self, app, category):
        form = MembershipForm(
            formdata=_signup_data(member_category=category, year_group="lav25"),
            meta={"csrf": False},
        )

        assert form.validate() is False
        assert "year_group" in form.errors

    def test_an_alumnus_may_leave_it_blank(self, app):
        """Somebody who studied here in 2006 may genuinely not know it.

        Refusing their membership over a cohort label nobody needs would be
        absurd, so alumni are asked but not required.
        """
        form = MembershipForm(
            formdata=_signup_data(member_category=MemberCategory.ALUMNI, year_group=""),
            meta={"csrf": False},
        )

        assert form.validate() is True, form.errors
        assert form.year_group.data is None

    def test_an_alumnus_who_knows_it_keeps_it(self, app):
        """The case that killed the first design: a non-student WITH a year group."""
        form = MembershipForm(
            formdata=_signup_data(member_category=MemberCategory.ALUMNI, year_group="LAV11"),
            meta={"csrf": False},
        )

        assert form.validate() is True, form.errors
        assert form.year_group.data == "LAV11"
        assert form.member_category.data == MemberCategory.ALUMNI

    @pytest.mark.parametrize("category", NOT_ASKED)
    def test_a_category_that_is_not_asked_needs_nothing(self, app, category):
        form = MembershipForm(
            formdata=_signup_data(member_category=category, year_group=""),
            meta={"csrf": False},
        )

        assert form.validate() is True, form.errors
        assert form.year_group.data is None

    @pytest.mark.parametrize("category", NOT_ASKED)
    def test_a_leftover_year_group_is_dropped_rather_than_refused(self, app, category):
        """Switching the choice leaves old text in a box nobody can see.

        Refusing the form over a value the member cannot look at would be a
        dead end, so the category wins and the stale text is discarded.
        """
        form = MembershipForm(
            formdata=_signup_data(member_category=category, year_group="LAV25"),
            meta={"csrf": False},
        )

        assert form.validate() is True, form.errors
        assert form.year_group.data is None

    def test_whitespace_does_not_count_as_a_year_group(self, app):
        form = MembershipForm(formdata=_signup_data(year_group="   "), meta={"csrf": False})

        assert form.validate() is False
        assert "year_group" in form.errors

    def test_student_is_the_default_so_an_older_client_behaves_as_before(self, app):
        """A post that predates the radio must not land in a laxer category."""
        data = _signup_data()
        data.pop("member_category")
        form = MembershipForm(formdata=data, meta={"csrf": False})

        assert form.member_category.data == MemberCategory.STUDENT
        assert form.validate() is True, form.errors
        assert form.year_group.data == "LAV25"

    def test_an_older_client_omitting_both_is_refused(self, app):
        """The dangerous version of the above: no category and no year group."""
        data = _signup_data(year_group="")
        data.pop("member_category")
        form = MembershipForm(formdata=data, meta={"csrf": False})

        assert form.validate() is False
        assert "year_group" in form.errors

    def test_an_unrecognised_category_is_refused(self, app):
        """It would otherwise take the not-asked branch and skip the year group.

        So a typo or a hand-edited request must fail on the category rather
        than quietly producing a member in no category at all.
        """
        form = MembershipForm(formdata=_signup_data(member_category="lecturer"),
                              meta={"csrf": False})

        assert form.validate() is False
        assert "member_category" in form.errors


class TestTheStoredFact:
    def test_a_new_member_is_a_student_unless_told_otherwise(self, app):
        member = make_member(email="default@example.com")

        assert member.member_category == MemberCategory.STUDENT
        assert member.is_student is True

    def test_an_alumnus_is_not_a_student_even_with_a_year_group(self, app):
        """The whole reason the category is stored rather than derived."""
        member = make_member(
            email="alum@example.com",
            member_category=MemberCategory.ALUMNI,
            year_group="LAV11",
        )

        assert member.is_student is False
        assert member.year_group == "LAV11"

    @pytest.mark.parametrize("category", NOT_ASKED)
    def test_the_categories_with_no_year_group(self, app, category):
        member = make_member(
            email=f"{category}@example.com", member_category=category, year_group=None
        )

        assert member.is_student is False
        assert member.year_group is None

    def test_the_label_is_readable(self, app):
        member = make_member(email="partner@example.com",
                             member_category=MemberCategory.PARTNER)

        assert str(member.category_label) == "Company or partner"

    def test_an_empty_year_group_is_stored_as_null(self, app):
        """One spelling of "no year group" in the column, not two."""
        assert normalize_optional_member_value("year_group", "") is None
        assert normalize_optional_member_value("year_group", "   ") is None
        assert normalize_optional_member_value("year_group", "LAV25") == "LAV25"

    def test_both_facts_survive_a_round_trip(self, app):
        make_member(
            email="alum@example.com",
            member_category=MemberCategory.ALUMNI,
            year_group="LAV11",
        )
        db.session.expire_all()

        loaded = db.session.execute(
            db.select(Member).filter_by(email_private="alum@example.com")
        ).scalar_one()

        assert loaded.member_category == MemberCategory.ALUMNI
        assert loaded.year_group == "LAV11"
        assert loaded.is_student is False


class TestTheForumUsername:
    def test_a_student_is_unchanged(self, app):
        assert build_forum_username_base("Anna", "Huber", "LAV25") == "HuberA_L25"

    @pytest.mark.parametrize("year_group", [None, ""])
    def test_without_a_year_group_there_is_no_dangling_separator(self, app, year_group):
        """"HuberA_" reads as a name with something missing off the end."""
        assert build_forum_username_base("Anna", "Huber", year_group) == "HuberA"

    def test_a_long_surname_still_fits_what_the_forum_will_store(self, app):
        """Discourse caps a username and shortens the rest without saying so.

        The old board has NiedergrottenthalerR_L12, twenty-four characters, so
        this is not hypothetical: on the forum that person exists under a name
        Discourse chose, and everything addressing them by the name the portal
        holds fails against somebody who is not there.
        """
        name = build_forum_username_base("Robert", "Niedergrottenthaler", "LAV25")

        assert len(name) <= FORUM_USERNAME_LENGTH_LIMIT

    def test_what_gives_way_is_the_surname_not_the_cohort(self, app):
        """Cutting the end would take the year group, which tells Hubers apart."""
        name = build_forum_username_base("Robert", "Niedergrottenthaler", "LAV25")

        assert name.endswith("R_L25")
        assert name.startswith("Niederg")

    def test_a_name_that_fits_is_left_exactly_as_it_was(self, app):
        assert build_forum_username_base("Anna", "Huber", "LAV25") == "HuberA_L25"


class TestWhatTheScreensShow:
    @pytest.fixture
    def admin_client(self, app, client):
        admin = User(email="admin-categories@example.com")
        admin.set_password("x")
        admin.grant_role(app_module.get_role("admin"))
        db.session.add(admin)
        db.session.commit()
        with client.session_transaction() as session:
            session["_user_id"] = str(admin.id)
        return client

    def test_the_admin_page_names_the_category(self, app, admin_client):
        member = make_member(
            email="partner@example.com",
            member_category=MemberCategory.PARTNER,
            year_group=None,
        )

        body = admin_client.get(f"/admin/accounts/{member.user_id}").get_data(as_text=True)

        assert "Company or partner" in body

    def test_an_alumnus_shows_both_facts(self, app, admin_client):
        member = make_member(
            email="alum@example.com",
            member_category=MemberCategory.ALUMNI,
            year_group="LAV11",
        )

        body = admin_client.get(f"/admin/accounts/{member.user_id}").get_data(as_text=True)

        assert "Alumni" in body
        assert "LAV11" in body

    def test_the_signup_page_offers_every_category(self, app, client):
        body = client.get("/").get_data(as_text=True)

        assert 'name="member_category"' in body
        for category in CATEGORY_ORDER:
            assert f'value="{category}"' in body

    def test_the_page_carries_the_rules_for_the_browser(self, app, client):
        """So the show/hide rule is not written down a second time in JavaScript."""
        body = client.get("/").get_data(as_text=True)

        assert 'data-year-group-categories="student alumni"' in body
        assert 'data-year-group-required="student"' in body
        assert "member-kind-toggle.js" in body


class TestChangingCategory:
    """A member graduates and stays in the association. That is the whole point."""

    def _form(self, **overrides):
        data = {
            "salutation": "Mr",
            "first_name": "Jonas",
            "last_name": "Huber",
            "member_category": MemberCategory.ALUMNI,
            "year_group": "LAV21",
            "member_note": "I have graduated.",
        }
        data.update(overrides)
        return IdentityChangeRequestForm(formdata=MultiDict(data), meta={"csrf": False})

    def test_a_student_can_ask_to_become_alumni_and_keep_the_cohort(self, app):
        form = self._form()

        assert form.validate() is True, form.errors
        assert form.member_category.data == MemberCategory.ALUMNI
        assert form.year_group.data == "LAV21"

    def test_a_student_can_ask_to_become_staff(self, app):
        form = self._form(member_category=MemberCategory.STAFF, year_group="LAV21")

        assert form.validate() is True, form.errors
        assert form.year_group.data is None, "staff are not asked, so the old value goes"

    def test_approving_applies_both_facts(self, app):
        """The approval path assigns straight across, so both have to be right."""
        member = make_member(email="grad@example.com", year_group="LAV21")
        request_record = MemberProfileChangeRequest(
            member=member,
            requested_by=member.user,
            requested_salutation=member.salutation,
            requested_first_name=member.first_name,
            requested_last_name=member.last_name,
            requested_member_category=MemberCategory.ALUMNI,
            requested_year_group="LAV21",
            status="pending",
        )
        db.session.add(request_record)
        db.session.commit()

        member.member_category = request_record.requested_member_category
        member.year_group = request_record.requested_year_group
        db.session.commit()

        assert member.is_student is False
        assert member.year_group == "LAV21"

    def test_the_category_counts_as_an_identity_change(self, app):
        """Otherwise a category switch would be applied without review."""
        from aeronautics_members.services.members import IDENTITY_MEMBER_FIELDS

        assert "member_category" in IDENTITY_MEMBER_FIELDS
