"""Giving a returning student their old forum identity back.

In October every active student signs up on the new portal, and around 250 of
them already exist in the archive from the old forum. They should get their
posts, their username and their avatar back without doing anything.

The evidence is the verified email address. Those students are still studying,
so their university address still works, and the archived account names it --
so being able to read that inbox is proof, and it is the only proof available:
the usernames are derived from names and would only ever be a guess.

What these tests are really for is the failure modes. This is an identity
transfer, so the ways it must *not* fire matter more than the way it does.
"""
from datetime import datetime

import pytest

from conftest import db, make_member
from aeronautics_members.db_models import ImportedForumProfile, Member, User
from aeronautics_members.services.forum_import import (
    claim_archived_account,
    find_claimable_profile,
    import_forum_people,
)

OLD_EMAIL = "a.popovic@edu.fh-joanneum.at"


def _archived(email=OLD_EMAIL, username="PopovicA_L23", uid="645"):
    """One imported person, exactly as the real import creates them."""
    import_forum_people([{
        "source_user_id": uid,
        "source_username": username,
        "source_email": email,
        "year_group": "LAV23",
        "post_count": 7,
    }])
    db.session.commit()
    return db.session.execute(
        db.select(ImportedForumProfile).filter_by(source_user_id=uid)
    ).scalar_one()


def _returning(email=OLD_EMAIL, verified=True):
    """The member they create in October, signing up with the same address."""
    member = make_member(email=email, year_group="LAV23")
    member.user.forum_username = "PopovicA_L23-2"
    if verified:
        member.user.email_verified_at = datetime.utcnow()
    db.session.commit()
    return member


class TestFindingTheArchivedAccount:
    def test_it_matches_on_the_address_the_old_forum_held(self, app):
        profile = _archived()

        assert find_claimable_profile(OLD_EMAIL) is profile

    def test_the_match_ignores_case_and_spacing(self, app):
        """People type their address however they type it."""
        profile = _archived()

        assert find_claimable_profile("  A.Popovic@EDU.FH-Joanneum.AT  ") is profile

    def test_an_unknown_address_matches_nothing(self, app):
        _archived()

        assert find_claimable_profile("someone.else@edu.fh-joanneum.at") is None

    def test_an_already_claimed_account_cannot_be_claimed_twice(self, app):
        profile = _archived()
        profile.claimed_at = datetime.utcnow()
        db.session.commit()

        assert find_claimable_profile(OLD_EMAIL) is None

    def test_two_archived_accounts_on_one_address_match_neither(self, app):
        """One address in the real archive is shared by two accounts.

        Choosing between them silently would hand somebody an identity on a
        coin flip, so this is left for a person to sort out.
        """
        _archived(uid="11", username="dpilz")
        _archived(uid="156", username="LAVBoard_System")

        assert find_claimable_profile(OLD_EMAIL) is None

    def test_the_placeholder_addresses_never_match(self, app):
        """Every imported account shares that domain; matching on it would be absurd."""
        profile = _archived()

        assert find_claimable_profile(profile.user.email) is None


class TestClaiming:
    def test_the_member_keeps_the_archived_user_id(self, app):
        """Discourse knows people by user.id, so losing it splits their history."""
        profile = _archived()
        archived_id = profile.user_id
        member = _returning()

        claimed = claim_archived_account(member.user)
        db.session.commit()

        assert claimed is profile
        assert member.user_id == archived_id

    def test_they_get_their_old_username_back(self, app):
        _archived()
        member = _returning()

        claim_archived_account(member.user)
        db.session.commit()

        assert member.user.forum_username == "PopovicA_L23"

    def test_they_can_sign_in_with_the_password_they_just_chose(self, app):
        _archived()
        member = _returning()
        member.user.set_password("the-new-password")
        db.session.commit()

        claim_archived_account(member.user)
        db.session.commit()

        assert member.user.check_password("the-new-password") is True

    def test_the_address_and_its_verification_come_across(self, app):
        _archived()
        member = _returning()

        claim_archived_account(member.user)
        db.session.commit()

        assert member.user.email == OLD_EMAIL
        assert member.user.email_is_verified is True

    def test_the_profile_is_marked_claimed(self, app):
        profile = _archived()
        member = _returning()

        claim_archived_account(member.user)
        db.session.commit()

        assert profile.claimed_at is not None

    def test_the_archived_post_count_survives(self, app):
        """The point of the whole exercise."""
        profile = _archived()
        member = _returning()

        claim_archived_account(member.user)
        db.session.commit()

        assert member.user.imported_forum_profile is profile
        assert profile.post_count == 7

    def test_only_one_account_is_left_usable(self, app):
        """The retired row is emptied, not deleted: audit entries point at it."""
        _archived()
        member = _returning()
        retired_id = member.user_id

        claim_archived_account(member.user)
        db.session.commit()

        retired = db.session.get(User, retired_id)
        assert retired.deleted_at is not None
        assert retired.forum_username is None
        assert retired.email != OLD_EMAIL
        assert retired.member is None

    def test_the_address_is_free_of_the_retired_row(self, app):
        """Otherwise the unique column would block the claim outright."""
        _archived()
        member = _returning()

        claim_archived_account(member.user)
        db.session.commit()

        found = db.session.execute(
            db.select(User).filter_by(email=OLD_EMAIL)
        ).scalars().all()
        assert len(found) == 1
        assert found[0].member is not None

    def test_there_is_still_exactly_one_membership(self, app):
        _archived()
        _returning()

        members_before = len(db.session.execute(db.select(Member)).scalars().all())
        claim_archived_account(
            db.session.execute(db.select(Member)).scalars().first().user
        )
        db.session.commit()

        assert len(db.session.execute(db.select(Member)).scalars().all()) == members_before


class TestWhenItMustNotFire:
    def test_an_unverified_address_claims_nothing(self, app):
        """The verification is the entire evidence. Without it there is none.

        Otherwise typing somebody else's university address at signup would be
        enough to take their posts.
        """
        _archived()
        member = _returning(verified=False)

        assert claim_archived_account(member.user) is None
        assert member.user.imported_forum_profile is None

    def test_a_different_address_claims_nothing(self, app):
        _archived()
        member = _returning(email="someone.else@edu.fh-joanneum.at")

        assert claim_archived_account(member.user) is None

    def test_an_imported_account_does_not_claim_another(self, app):
        _archived(uid="1", username="OneA_L20", email="one@edu.fh-joanneum.at")
        other = _archived(uid="2", username="TwoB_L21", email="two@edu.fh-joanneum.at")

        assert claim_archived_account(other.user) is None

    def test_an_erased_account_claims_nothing(self, app):
        _archived()
        member = _returning()
        member.user.deleted_at = datetime.utcnow()
        db.session.commit()

        assert claim_archived_account(member.user) is None

    def test_an_account_holding_a_role_is_left_for_a_person(self, app):
        """Retiring an admin's row would silently take their access with it."""
        from conftest import app_module

        _archived()
        member = _returning()
        member.user.grant_role(app_module.get_role("admin"))
        db.session.commit()

        assert claim_archived_account(member.user) is None
        assert member.user.is_admin is True

    def test_claiming_twice_is_a_no_op(self, app):
        _archived()
        member = _returning()

        assert claim_archived_account(member.user) is not None
        db.session.commit()

        assert claim_archived_account(member.user) is None


class TestThroughTheVerificationLink:
    """The real path: nothing happens until the link in the email is followed."""

    def _verify(self, client, user):
        from aeronautics_members.services.identity import (
            build_email_verification_claims,
            generate_token,
        )

        claims = build_email_verification_claims(user)
        # Committed, as it would be when the verification email was sent: the
        # nonce is what the link is checked against.
        db.session.commit()
        token = generate_token("verify-email", **claims)
        return client.get(f"/verify-email/{token}", follow_redirects=True)

    def test_following_the_link_reconnects_the_old_account(self, app, client):
        profile = _archived()
        archived_id = profile.user_id
        member = _returning(verified=False)

        response = self._verify(client, member.user)

        assert response.status_code < 400
        db.session.expire_all()
        member = db.session.execute(db.select(Member)).scalars().one()
        assert member.user_id == archived_id
        assert member.user.forum_username == "PopovicA_L23"

    def test_the_member_is_told_it_happened(self, app, client):
        _archived()
        member = _returning(verified=False)

        body = self._verify(client, member.user).get_data(as_text=True)

        assert "PopovicA_L23" in body

    def test_a_member_with_no_archive_just_gets_verified(self, app, client):
        member = _returning(email="newcomer@edu.fh-joanneum.at", verified=False)

        response = self._verify(client, member.user)

        assert response.status_code < 400
        db.session.expire_all()
        assert db.session.get(User, member.user_id).email_is_verified is True

    def test_a_failing_claim_still_verifies_the_address(self, app, client, monkeypatch):
        """A fault here must not cost somebody a working account.

        They end up verified with an unclaimed archive, which an admin can
        link by hand -- not stuck with a verification link that never works.
        """
        import aeronautics_members.blueprints.auth as auth_module

        _archived()
        member = _returning(verified=False)
        user_id = member.user_id

        def explode(user):
            raise RuntimeError("claim is broken")

        monkeypatch.setattr(auth_module, "claim_archived_account", explode)

        response = self._verify(client, member.user)

        assert response.status_code < 400
        db.session.expire_all()
        assert db.session.get(User, user_id).email_is_verified is True


class TestClaimingOnTheUniversityAddress:
    """The path that actually matters in October.

    A returning student signs up with a private address as their login -- it is
    labelled "Private Email" and it has to outlive their studies -- and gives
    their university address separately. The archive knows them by the
    university one, so that is what the claim has to match, and the claim can
    only be trusted once that address has been confirmed.
    """

    def _member_with_work_email(self, work_email=OLD_EMAIL, work_verified=False):
        member = make_member(email="anna.private@gmail.com", year_group="LAV23")
        member.user.email_verified_at = datetime.utcnow()
        member.email_work = work_email
        if work_verified:
            member.email_work_verified_at = datetime.utcnow()
        db.session.commit()
        return member

    def test_an_unconfirmed_university_address_claims_nothing(self, app):
        """Typing it is not evidence. Reading mail sent to it is."""
        _archived()
        member = self._member_with_work_email(work_verified=False)

        assert claim_archived_account(member.user) is None
        assert member.user.imported_forum_profile is None

    def test_a_confirmed_university_address_claims_the_archive(self, app):
        profile = _archived()
        archived_id = profile.user_id
        member = self._member_with_work_email(work_verified=True)

        claimed = claim_archived_account(member.user)
        db.session.commit()

        assert claimed is profile
        assert member.user_id == archived_id
        assert member.user.forum_username == "PopovicA_L23"

    def test_the_private_address_stays_the_login(self, app):
        """It is the one that still works after they graduate."""
        _archived()
        member = self._member_with_work_email(work_verified=True)

        claim_archived_account(member.user)
        db.session.commit()

        assert member.user.email == "anna.private@gmail.com"
        assert member.email_work == OLD_EMAIL

    def test_the_confirmation_travels_with_the_membership(self, app):
        """The claim moves the member onto another row; the flag must come too."""
        _archived()
        member = self._member_with_work_email(work_verified=True)

        claim_archived_account(member.user)
        db.session.commit()
        db.session.expire_all()

        moved = db.session.execute(db.select(Member)).scalars().one()
        assert moved.email_work_is_verified is True

    def test_a_confirmed_address_nobody_archived_claims_nothing(self, app):
        _archived()
        member = self._member_with_work_email(
            work_email="newcomer@edu.fh-joanneum.at", work_verified=True
        )

        assert claim_archived_account(member.user) is None

    def test_it_still_works_for_somebody_who_used_the_university_address_to_log_in(self, app):
        """A few will do that, and they should not be worse off for it."""
        profile = _archived()
        member = make_member(email=OLD_EMAIL, year_group="LAV23")
        member.user.email_verified_at = datetime.utcnow()
        db.session.commit()

        assert claim_archived_account(member.user) is profile


class TestThroughTheUniversityLink:
    def _verify_work(self, client, member):
        from aeronautics_members.services.identity import (
            build_work_email_verification_claims,
            generate_token,
        )

        claims = build_work_email_verification_claims(member)
        db.session.commit()
        token = generate_token("verify-work-email", **claims)
        return client.get(f"/verify-work-email/{token}", follow_redirects=True)

    def test_following_it_confirms_the_address_and_reconnects_the_account(self, app, client):
        profile = _archived()
        archived_id = profile.user_id
        member = make_member(email="anna.private@gmail.com", year_group="LAV23")
        member.user.email_verified_at = datetime.utcnow()
        member.email_work = OLD_EMAIL
        db.session.commit()

        response = self._verify_work(client, member)

        assert response.status_code < 400
        body = response.get_data(as_text=True)
        assert "PopovicA_L23" in body
        db.session.expire_all()
        moved = db.session.execute(db.select(Member)).scalars().one()
        assert moved.email_work_is_verified is True
        assert moved.user_id == archived_id

    def test_a_tampered_link_confirms_nothing(self, app, client):
        member = make_member(email="anna.private@gmail.com", year_group="LAV23")
        member.email_work = OLD_EMAIL
        db.session.commit()

        response = client.get("/verify-work-email/not-a-real-token", follow_redirects=True)

        assert response.status_code < 400
        assert member.email_work_is_verified is False


class TestSeeingTheArchiveInTheAdmin:
    """Former members live in the same directory as everybody else.

    A former member is a member the association still has a record of, and
    reconnecting one is the same action as anything else done from an account
    page -- so they belong in the one list, narrowed by a filter rather than
    hidden behind a second page.

    What they must not be is unreadable. Their account row carries a
    placeholder address and no name, so without help an archived person reads
    as a broken account rather than somebody who used to post here.
    """

    @pytest.fixture
    def admin_client(self, app, client):
        from conftest import app_module

        admin = User(email="admin-archive@example.com")
        admin.set_password("x")
        admin.grant_role(app_module.get_role("admin"))
        db.session.add(admin)
        db.session.commit()
        with client.session_transaction() as session:
            session["_user_id"] = str(admin.id)
        return client

    def _rows(self, response):
        return response.get_data(as_text=True).count('class="btn btn-secondary btn-sm"')

    def test_they_appear_in_the_ordinary_account_list(self, app, admin_client):
        _archived()

        body = admin_client.get("/admin/accounts").get_data(as_text=True)

        assert "PopovicA_L23" in body

    def test_a_row_shows_who_it_was_not_a_placeholder_address(self, app, admin_client):
        """forum-mybb-645@imported.invalid tells an admin nothing."""
        _archived()

        body = admin_client.get("/admin/accounts").get_data(as_text=True)

        assert OLD_EMAIL in body
        assert "imported.invalid" not in body
        assert ">Former forum member</span>" in body

    def test_the_filter_narrows_to_them(self, app, admin_client):
        _archived()
        make_member(email="current@example.com")
        db.session.commit()

        # The admin doing the looking is an account too.
        assert self._rows(admin_client.get("/admin/accounts")) == 3
        assert self._rows(admin_client.get("/admin/accounts?kind=archived")) == 1
        assert self._rows(admin_client.get("/admin/accounts?kind=portal")) == 2

    def test_asking_for_active_members_leaves_them_out(self, app, admin_client):
        """The filter that was already there, which is why this fits the page."""
        _archived()

        assert self._rows(admin_client.get("/admin/accounts?active=active")) == 0

    def test_they_can_be_searched_by_their_forum_name(self, app, admin_client):
        _archived()

        assert self._rows(admin_client.get("/admin/accounts?q=PopovicA")) == 1

    def test_they_can_be_searched_by_the_address_the_forum_held(self, app, admin_client):
        """An admin who is asked "is my old account in there" has an address."""
        _archived()

        assert self._rows(admin_client.get("/admin/accounts?q=a.popovic")) == 1

    def test_the_account_page_shows_what_the_archive_recorded(self, app, admin_client):
        profile = _archived()

        body = admin_client.get(f"/admin/accounts/{profile.user_id}").get_data(as_text=True)

        assert "Old Forum Account" in body
        assert "PopovicA_L23" in body
        assert "LAV23" in body
        assert "Unclaimed" in body

    def test_a_reconnected_person_reads_as_a_member_not_an_archive(self, app, admin_client):
        """Claiming makes them a member. The row should say so.

        The archive row and the membership are the same account afterwards, so
        showing the old placeholder treatment would file a current member under
        "former forum member" for ever.
        """
        profile = _archived()
        member = _returning()
        claim_archived_account(member.user)
        db.session.commit()

        listing = admin_client.get("/admin/accounts?kind=archived").get_data(as_text=True)
        # Scoped to the badge: the filter dropdown names the category too.
        assert ">Former forum member</span>" not in listing
        assert "Reconnected" in listing, "but it is still visible that they came back"

        detail = admin_client.get(f"/admin/accounts/{profile.user_id}").get_data(as_text=True)
        assert "Claimed" in detail

    def test_an_ordinary_account_grows_no_archive_panel(self, app, admin_client):
        member = make_member(email="current@example.com")

        body = admin_client.get(f"/admin/accounts/{member.user_id}").get_data(as_text=True)

        assert "Old Forum Account" not in body

    def test_the_dashboard_counts_them_apart_from_real_accounts(self, app, admin_client):
        """760 accounts when the association has twenty would be a lie."""
        from aeronautics_members.app import get_admin_dashboard_metrics

        _archived()
        make_member(email="current@example.com")
        db.session.commit()

        metrics = get_admin_dashboard_metrics()

        assert metrics["total_accounts"] == 2, "the member and the admin, not the archive"
        assert metrics["archived_forum_accounts"] == 1
        assert metrics["archived_forum_claimed"] == 0


class TestTheArchivedAvatar:
    @pytest.fixture
    def admin_client(self, app, client):
        from conftest import app_module

        admin = User(email="admin-avatar@example.com")
        admin.set_password("x")
        admin.grant_role(app_module.get_role("admin"))
        db.session.add(admin)
        db.session.commit()
        with client.session_transaction() as session:
            session["_user_id"] = str(admin.id)
        return client

    def _with_avatar(self, app, tmp_path):
        from PIL import Image

        source = tmp_path / "avatars"
        source.mkdir()
        Image.new("RGB", (48, 48), (90, 120, 200)).save(source / "avatar_645.jpg")
        import_forum_people(
            [{"source_user_id": "645", "source_username": "PopovicA_L23",
              "source_email": OLD_EMAIL, "avatar_file": "avatar_645.jpg"}],
            avatar_dir=str(source),
        )
        db.session.commit()
        return db.session.execute(db.select(ImportedForumProfile)).scalar_one()

    def test_the_face_is_actually_served(self, app, admin_client, tmp_path):
        """It was stored by the import and read by nothing at all."""
        profile = self._with_avatar(app, tmp_path)

        response = admin_client.get(f"/admin/accounts/{profile.user_id}/archived-avatar")

        assert response.status_code == 200
        assert response.get_data()[:3] == b"\xff\xd8\xff", "a JPEG, not an error page"

    def test_the_account_page_shows_it(self, app, admin_client, tmp_path):
        profile = self._with_avatar(app, tmp_path)

        body = admin_client.get(f"/admin/accounts/{profile.user_id}").get_data(as_text=True)

        assert f"/admin/accounts/{profile.user_id}/archived-avatar" in body

    def test_somebody_without_one_is_not_a_broken_image(self, app, admin_client):
        profile = _archived()

        assert admin_client.get(
            f"/admin/accounts/{profile.user_id}/archived-avatar"
        ).status_code == 404
        body = admin_client.get(f"/admin/accounts/{profile.user_id}").get_data(as_text=True)
        assert "archived-avatar" not in body

    def test_it_is_not_public(self, app, client, tmp_path):
        """The staging directory also holds avatars awaiting review."""
        profile = self._with_avatar(app, tmp_path)

        response = client.get(f"/admin/accounts/{profile.user_id}/archived-avatar")

        assert response.status_code in (302, 401, 403)
