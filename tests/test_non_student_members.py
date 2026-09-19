"""Membership without being a student.

The statutes have always allowed it; the software did not, because every
member so far happened to be a student and ``year_group`` was NOT NULL. A
non-student is a *full* member -- same rights, same fee -- who simply has no
year group to give.

The design under test: **null year group means "not a student"**, and that is
the only place the fact is stored. There is deliberately no ``is_student``
column, because two columns can disagree and nothing would reconcile them.
So the properties worth pinning are that the null survives every round trip,
that a student still cannot slip through without one, and that nothing
downstream renders the absence as "None" or "HuberA_".
"""
import pytest
from werkzeug.datastructures import MultiDict

from conftest import db, make_member
from aeronautics_members.db_models import Member, MemberProfileChangeRequest
from aeronautics_members.forms import IdentityChangeRequestForm, MembershipForm
from aeronautics_members.services.forum import build_forum_username_base
from aeronautics_members.services.members import normalize_optional_member_value


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
        "member_kind": "student",
        "year_group": "LAV25",
        "password": "a-long-enough-password",
        "confirm_password": "a-long-enough-password",
        "terms_accepted": "y",
    }
    data.update(overrides)
    # A real POST body: what the browser sends, so an omitted radio is genuinely
    # omitted rather than a Python None the form layer treats differently.
    return MultiDict(
        (key, value) for key, value in data.items() if value is not None
    )


class TestTheForm:
    def test_a_student_still_has_to_give_a_year_group(self, app):
        """The requirement did not go away, it became conditional."""
        form = MembershipForm(formdata=_signup_data(member_kind="student", year_group=""),
                              meta={"csrf": False})

        assert form.validate() is False
        assert "year_group" in form.errors

    def test_a_student_still_has_to_give_a_valid_one(self, app):
        form = MembershipForm(formdata=_signup_data(year_group="lav25"), meta={"csrf": False})

        assert form.validate() is False
        assert "year_group" in form.errors

    def test_a_non_student_needs_none(self, app):
        form = MembershipForm(formdata=_signup_data(member_kind="non_student", year_group=""),
                              meta={"csrf": False})

        assert form.validate() is True, form.errors
        assert form.year_group.data is None

    def test_a_leftover_year_group_is_dropped_rather_than_refused(self, app):
        """Switching the choice leaves the old text in a box nobody can see.

        Refusing the form over a value the member cannot even look at would be
        a dead end, so the radio wins and the stale text is discarded.
        """
        form = MembershipForm(formdata=_signup_data(member_kind="non_student", year_group="LAV25"),
                              meta={"csrf": False})

        assert form.validate() is True, form.errors
        assert form.year_group.data is None

    def test_whitespace_does_not_count_as_a_year_group(self, app):
        form = MembershipForm(formdata=_signup_data(year_group="   "), meta={"csrf": False})

        assert form.validate() is False
        assert "year_group" in form.errors

    def test_student_is_the_default_so_an_old_client_behaves_as_before(self, app):
        """A post that predates the radio must not silently create a non-student."""
        data = _signup_data()
        data.pop("member_kind")
        form = MembershipForm(formdata=data, meta={"csrf": False})

        assert form.member_kind.data == "student"
        assert form.validate() is True, form.errors
        assert form.year_group.data == "LAV25"

    def test_an_old_client_omitting_both_is_refused(self, app):
        """The dangerous version of the above: no radio and no year group."""
        data = _signup_data(year_group="")
        data.pop("member_kind")
        form = MembershipForm(formdata=data, meta={"csrf": False})

        assert form.validate() is False
        assert "year_group" in form.errors

    def test_an_unrecognised_kind_is_refused_not_read_as_non_student(self, app):
        """Anything but "student" takes the clear-and-skip branch of the validator.

        So a typo or a hand-edited request must fail on member_kind rather than
        quietly producing a member with no year group.
        """
        form = MembershipForm(formdata=_signup_data(member_kind="staff"), meta={"csrf": False})

        assert form.validate() is False
        assert "member_kind" in form.errors


class TestTheStoredFact:
    def test_a_member_with_no_year_group_is_not_a_student(self, app):
        member = make_member(email="nonstudent@example.com", year_group=None)

        assert member.is_student is False
        assert member.year_group is None

    def test_a_member_with_one_is(self, app):
        member = make_member(email="student@example.com", year_group="LAV25")

        assert member.is_student is True

    def test_an_empty_string_is_not_a_student_either(self, app):
        """Belt and braces: two spellings of absent would have to agree."""
        member = make_member(email="blank@example.com", year_group="")

        assert member.is_student is False

    def test_an_empty_year_group_is_stored_as_null(self, app):
        """One spelling of "not a student" in the column, not two."""
        assert normalize_optional_member_value("year_group", "") is None
        assert normalize_optional_member_value("year_group", "   ") is None
        assert normalize_optional_member_value("year_group", "LAV25") == "LAV25"

    def test_the_null_survives_a_round_trip(self, app):
        make_member(email="nonstudent@example.com", year_group=None)
        db.session.expire_all()

        loaded = db.session.execute(
            db.select(Member).filter_by(email_private="nonstudent@example.com")
        ).scalar_one()

        assert loaded.year_group is None
        assert loaded.is_student is False


class TestTheForumUsername:
    def test_a_student_is_unchanged(self, app):
        assert build_forum_username_base("Anna", "Huber", "LAV25") == "HuberA_L25"

    @pytest.mark.parametrize("year_group", [None, ""])
    def test_a_non_student_gets_no_dangling_separator(self, app, year_group):
        """"HuberA_" reads as a name with something missing off the end."""
        assert build_forum_username_base("Anna", "Huber", year_group) == "HuberA"


class TestWhatTheScreensShow:
    """A blank year group must read as a fact about the person, not a bug.

    Jinja renders None as the literal string "None", so every place that
    printed the year group needed a fallback. These are the ones that exist.
    """

    @pytest.fixture
    def admin_client(self, app, client):
        from conftest import app_module
        from aeronautics_members.db_models import User

        admin = User(email="admin-nonstudent@example.com")
        admin.set_password("x")
        admin.grant_role(app_module.get_role("admin"))
        db.session.add(admin)
        db.session.commit()
        with client.session_transaction() as session:
            session["_user_id"] = str(admin.id)
        return client

    def test_the_admin_account_page_says_not_a_student(self, app, admin_client):
        member = make_member(email="nonstudent@example.com", year_group=None)

        response = admin_client.get(f"/admin/accounts/{member.user_id}")
        body = response.get_data(as_text=True)

        assert response.status_code < 500
        # Scoped to the year group's own cell: the page has an unrelated,
        # deliberate "None" elsewhere for an empty list.
        assert '<dd class="col-sm-8">Not a student</dd>' in body
        assert '<dd class="col-sm-8">None</dd>' not in body

    def test_a_students_page_still_shows_the_year_group(self, app, admin_client):
        member = make_member(email="student@example.com", year_group="LAV25")

        body = admin_client.get(f"/admin/accounts/{member.user_id}").get_data(as_text=True)

        assert "LAV25" in body
        assert "Not a student" not in body

    def test_the_signup_page_offers_the_choice(self, app, client):
        body = client.get("/").get_data(as_text=True)

        assert 'name="member_kind"' in body
        assert "data-year-group-field" in body
        assert "member-kind-toggle.js" in body


class TestChangingBetweenThem:
    """A member graduates and stays in the association. That is the whole point."""

    def _form(self, **overrides):
        data = {
            "salutation": "Mr",
            "first_name": "Jonas",
            "last_name": "Huber",
            "member_kind": "non_student",
            "year_group": "",
            "member_note": "I have graduated.",
        }
        data.update(overrides)
        return IdentityChangeRequestForm(formdata=MultiDict(data), meta={"csrf": False})

    def test_a_student_can_ask_to_become_a_non_student(self, app):
        form = self._form()

        assert form.validate() is True, form.errors
        assert form.year_group.data is None

    def test_approving_that_request_clears_the_year_group(self, app):
        """The approval path assigns straight across, so null has to mean null."""
        member = make_member(email="grad@example.com", year_group="LAV21")
        request_record = MemberProfileChangeRequest(
            member=member,
            requested_by=member.user,
            requested_salutation=member.salutation,
            requested_first_name=member.first_name,
            requested_last_name=member.last_name,
            requested_year_group=None,
            status="pending",
        )
        db.session.add(request_record)
        db.session.commit()

        member.year_group = request_record.requested_year_group
        db.session.commit()

        assert member.year_group is None
        assert member.is_student is False

    def test_a_non_student_can_become_a_student(self, app):
        form = self._form(member_kind="student", year_group="MAV26")

        assert form.validate() is True, form.errors
        assert form.year_group.data == "MAV26"
