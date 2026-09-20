"""The university or company address, and what confirming it is worth.

The association collects two addresses on purpose. The private one is the
login and the way to reach somebody forever -- including after they graduate,
when the other one stops working. The institutional one is evidence: it says
this person studies or works here *now*.

It was collected and never used for anything until the forum claim needed it,
and a claim decided on an unconfirmed address would be decided on nothing.
So these tests are mostly about what confirmation means and when it stops
meaning it.
"""
from datetime import datetime

import pytest
from werkzeug.datastructures import MultiDict

from conftest import db, make_member
from aeronautics_members.db_models import Member, Setting
from aeronautics_members.forms import MembershipForm
from aeronautics_members.member_categories import MemberCategory
from aeronautics_members.services.identity import (
    build_work_email_verification_claims,
    generate_token,
    mark_work_email_verified_from_token,
)
from aeronautics_members.services.institutional_email import (
    SETTING_KEY,
    get_institutional_domains,
    is_institutional_email,
    parse_domains,
)
from aeronautics_members.services.members import apply_member_profile

UNI = "a.popovic@edu.fh-joanneum.at"


def _signup(**overrides):
    data = {
        "salutation": "Mr", "first_name": "Anna", "last_name": "Popovic",
        "street": "Main Street", "house_number": "1", "postal_code": "8010",
        "city": "Graz", "country": "Austria", "phone_private": "+43000000000",
        "email_private": "anna.private@gmail.com",
        "email_work": UNI,
        "member_category": MemberCategory.STUDENT, "year_group": "LAV23",
        "password": "a-long-enough-password",
        "confirm_password": "a-long-enough-password",
        "terms_accepted": "y",
    }
    data.update(overrides)
    return MultiDict((k, v) for k, v in data.items() if v is not None)


class TestTheDomainList:
    @pytest.mark.parametrize("raw,expected", [
        ("edu.fh-joanneum.at", ("edu.fh-joanneum.at",)),
        ("a.at, b.at", ("a.at", "b.at")),
        ("a.at\nb.at", ("a.at", "b.at")),
        ("a.at; b.at", ("a.at", "b.at")),
        ("@a.at", ("a.at",)),
        ("A.AT", ("a.at",)),
        ("a.at, a.at", ("a.at",)),
        ("", ()),
        (None, ()),
    ])
    def test_it_reads_however_somebody_typed_the_list(self, raw, expected):
        """It is a text box an admin fills in, not a data format."""
        assert parse_domains(raw) == expected

    def test_an_admin_can_change_it_without_a_release(self, app):
        db.session.add(Setting(key=SETTING_KEY, value="partner.example"))
        db.session.commit()

        assert get_institutional_domains() == ("partner.example",)
        assert is_institutional_email("someone@partner.example") is True

    def test_clearing_it_falls_back_rather_than_locking_everyone_out(self, app):
        """An empty box must not mean "no address is ever acceptable"."""
        db.session.add(Setting(key=SETTING_KEY, value="   "))
        db.session.commit()

        assert "edu.fh-joanneum.at" in get_institutional_domains()

    @pytest.mark.parametrize("email,expected", [
        ("a@edu.fh-joanneum.at", True),
        ("a@fh-joanneum.at", True),
        ("a@campus.fh-joanneum.at", True),      # a subdomain is still theirs
        ("a@gmail.com", False),
        ("a@notfh-joanneum.at", False),         # must not pass as a suffix
        ("a@fh-joanneum.at.example.com", False),
        ("", False),
        ("no-at-sign", False),
    ])
    def test_which_addresses_count(self, app, email, expected):
        assert is_institutional_email(email) is expected


class TestTheSignupForm:
    def test_a_student_must_give_one(self, app):
        form = MembershipForm(formdata=_signup(email_work=""), meta={"csrf": False})

        assert form.validate() is False
        assert "email_work" in form.errors

    def test_a_student_cannot_use_a_private_address_for_it(self, app):
        """The point of the field is that it proves something."""
        form = MembershipForm(formdata=_signup(email_work="anna@gmail.com"),
                              meta={"csrf": False})

        assert form.validate() is False
        assert "email_work" in form.errors

    def test_a_student_with_a_university_address_is_accepted(self, app):
        form = MembershipForm(formdata=_signup(), meta={"csrf": False})

        assert form.validate() is True, form.errors
        assert form.email_work.data == UNI

    def test_the_address_is_stored_lowercased(self, app):
        """Otherwise the archive lookup misses on how somebody typed it."""
        form = MembershipForm(formdata=_signup(email_work="A.Popovic@EDU.FH-Joanneum.AT"),
                              meta={"csrf": False})

        assert form.validate() is True, form.errors
        assert form.email_work.data == UNI

    @pytest.mark.parametrize("category", [
        MemberCategory.PARTNER, MemberCategory.HONORARY, MemberCategory.STAFF,
    ])
    def test_other_categories_are_not_required_to_give_one(self, app, category):
        form = MembershipForm(
            formdata=_signup(member_category=category, email_work="", year_group=""),
            meta={"csrf": False},
        )

        assert form.validate() is True, form.errors
        assert form.email_work.data is None

    def test_a_partner_may_use_their_own_company_domain(self, app):
        """No list here could anticipate every company."""
        form = MembershipForm(
            formdata=_signup(member_category=MemberCategory.PARTNER,
                             email_work="rep@some-airline.example", year_group=""),
            meta={"csrf": False},
        )

        assert form.validate() is True, form.errors
        assert form.email_work.data == "rep@some-airline.example"

    def test_an_alumnus_may_keep_a_dead_university_address(self, app):
        """Theirs has usually stopped working, which is not a reason to refuse."""
        form = MembershipForm(
            formdata=_signup(member_category=MemberCategory.ALUMNI,
                             email_work="old@wherever.example"),
            meta={"csrf": False},
        )

        assert form.validate() is True, form.errors


class TestConfirming:
    def _member(self, email_work=UNI):
        member = make_member(email="anna.private@gmail.com", year_group="LAV23")
        member.email_work = email_work
        db.session.commit()
        return member

    def test_a_fresh_membership_is_not_confirmed(self, app):
        member = self._member()

        assert member.email_work_is_verified is False

    def test_following_the_link_confirms_it(self, app):
        member = self._member()
        claims = build_work_email_verification_claims(member)
        db.session.commit()

        assert mark_work_email_verified_from_token(claims, member) is True
        assert member.email_work_is_verified is True

    def test_a_link_for_a_different_address_does_not_confirm_this_one(self, app):
        """The whole point of binding the address into the token."""
        member = self._member()
        claims = build_work_email_verification_claims(member)
        db.session.commit()

        member.email_work = "someone.else@edu.fh-joanneum.at"

        assert mark_work_email_verified_from_token(claims, member) is False
        assert member.email_work_is_verified is False

    def test_confirming_twice_changes_nothing(self, app):
        member = self._member()
        claims = build_work_email_verification_claims(member)
        db.session.commit()
        mark_work_email_verified_from_token(claims, member)

        assert mark_work_email_verified_from_token(claims, member) is False

    def test_a_token_without_a_nonce_is_refused(self, app):
        """An old link must not be replayable against a reissued address."""
        member = self._member()

        assert mark_work_email_verified_from_token(
            {"member_id": member.id, "email": UNI, "nonce": None}, member
        ) is False


class TestChangingTheAddressAfterConfirming:
    """The attack this exists to stop.

    Confirm an address you really can read, then edit the field to somebody
    else's and keep the tick. Since the forum claim is decided on exactly this
    flag, that would hand you their archived account and their posts.
    """

    def _confirmed_member(self):
        member = make_member(email="anna.private@gmail.com", year_group="LAV23")
        member.email_work = UNI
        member.email_work_verified_at = datetime.utcnow()
        member.email_work_verification_nonce = "whatever"
        db.session.commit()
        return member

    def test_editing_the_address_withdraws_the_confirmation(self, app):
        member = self._confirmed_member()

        apply_member_profile(member, {"email_work": "victim@edu.fh-joanneum.at"},
                             fields=("email_work",))
        db.session.commit()

        assert member.email_work_is_verified is False
        assert member.email_work_verification_nonce is None

    def test_the_old_link_cannot_confirm_the_new_address(self, app):
        member = self._confirmed_member()
        claims = build_work_email_verification_claims(member)
        db.session.commit()

        apply_member_profile(member, {"email_work": "victim@edu.fh-joanneum.at"},
                             fields=("email_work",))

        assert mark_work_email_verified_from_token(claims, member) is False

    def test_clearing_the_address_withdraws_it_too(self, app):
        member = self._confirmed_member()

        apply_member_profile(member, {"email_work": ""}, fields=("email_work",))
        db.session.commit()

        assert member.email_work is None
        assert member.email_work_is_verified is False

    def test_saving_the_same_address_keeps_the_confirmation(self, app):
        """Editing a phone number must not make somebody confirm again."""
        member = self._confirmed_member()

        apply_member_profile(member, {"email_work": UNI.upper()},
                             fields=("email_work",))
        db.session.commit()

        assert member.email_work_is_verified is True


class TestTheAdminSetting:
    """The list has to be editable without a release, and visible when it is."""

    def _admin_client(self, app, client):
        from conftest import app_module
        from aeronautics_members.db_models import User

        admin = User(email="admin-domains@example.com")
        admin.set_password("x")
        admin.grant_role(app_module.get_role("admin"))
        db.session.add(admin)
        db.session.commit()
        with client.session_transaction() as session:
            session["_user_id"] = str(admin.id)
        return client

    def test_the_settings_page_shows_the_list_in_force(self, app, client):
        body = self._admin_client(app, client).get("/admin/settings").get_data(as_text=True)

        assert "institutional_email_domains" in body
        assert "edu.fh-joanneum.at" in body

    def test_saving_it_changes_which_addresses_are_accepted(self, app, client):
        admin = self._admin_client(app, client)

        response = admin.post("/admin/settings", data={
            "save_settings": "1",
            "settings_section": "general",
            "welcome_email_sender": "",
            "automatic_email_template": "",
            "institutional_email_domains": "partner.example, other.example",
        }, follow_redirects=True)

        assert response.status_code < 400
        assert get_institutional_domains() == ("partner.example", "other.example")
        assert is_institutional_email("someone@partner.example") is True
        assert is_institutional_email("someone@edu.fh-joanneum.at") is False


class TestTheLoginAddressCannotBeInstitutional:
    """The mistake the form could not otherwise catch.

    A university address in the private field is perfectly well-formed and
    completely wrong: it is the login, and it stops working the day the person
    graduates. Rejecting it is what lets the form carry no hint text -- the
    explanation reaches the one person who needs it, when they need it.
    """

    def test_a_university_address_is_refused_as_the_login(self, app):
        form = MembershipForm(formdata=_signup(email_private=UNI), meta={"csrf": False})

        assert form.validate() is False
        assert "email_private" in form.errors

    def test_the_error_says_what_to_do_instead(self, app):
        """It is the hint, shown only to somebody who needs it."""
        form = MembershipForm(formdata=_signup(email_private=UNI), meta={"csrf": False})
        form.validate()

        message = " ".join(form.errors["email_private"])
        assert "private" in message.lower()

    def test_any_of_the_allowed_domains_is_refused(self, app):
        """The rule is about who owns the address, not about one domain."""
        form = MembershipForm(formdata=_signup(email_private="someone@fh-joanneum.at"),
                              meta={"csrf": False})

        assert form.validate() is False
        assert "email_private" in form.errors

    def test_a_subdomain_is_refused_too(self, app):
        form = MembershipForm(
            formdata=_signup(email_private="someone@campus.fh-joanneum.at"),
            meta={"csrf": False},
        )

        assert form.validate() is False

    def test_a_private_address_is_accepted(self, app):
        form = MembershipForm(formdata=_signup(), meta={"csrf": False})

        assert form.validate() is True, form.errors

    def test_it_follows_the_admin_setting(self, app):
        """Whatever counts as institutional counts here too, by definition."""
        db.session.add(Setting(key=SETTING_KEY, value="partner.example"))
        db.session.commit()

        refused = MembershipForm(
            formdata=_signup(email_private="rep@partner.example",
                             email_work="rep@partner.example"),
            meta={"csrf": False},
        )
        assert refused.validate() is False
        assert "email_private" in refused.errors

        # ...and the previous list no longer applies to either field.
        allowed = MembershipForm(
            formdata=_signup(email_private="anna@edu.fh-joanneum.at",
                             email_work="anna@partner.example"),
            meta={"csrf": False},
        )
        assert allowed.validate() is True, allowed.errors

    def test_an_empty_address_is_left_to_the_required_check(self, app):
        """One error about one problem, not two."""
        form = MembershipForm(formdata=_signup(email_private=""), meta={"csrf": False})

        form.validate()
        message = " ".join(form.errors["email_private"])
        assert "university or company address" not in message

    def test_the_profile_page_cannot_be_used_to_switch_to_one(self, app):
        """Otherwise the check applies at signup and nowhere afterwards."""
        from aeronautics_members.forms import MemberProfileForm

        form = MemberProfileForm(formdata=MultiDict({
            "street": "Main", "house_number": "1", "postal_code": "8010",
            "city": "Graz", "country": "Austria", "phone_private": "+43000",
            "email_private": UNI, "email_work": UNI,
        }), meta={"csrf": False})
        form.member_category_value = MemberCategory.STUDENT

        assert form.validate() is False
        assert "email_private" in form.errors
