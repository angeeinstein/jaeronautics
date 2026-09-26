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
from aeronautics_members.db_models import (
    AuditLog,
    EmailDeliveryJob,
    ImportedForumProfile,
    Member,
    User,
)
from aeronautics_members.services.clock import get_now_utc
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


def _make_paid(member):
    """Past the membership gate, so the avatar question is what is being tested."""
    from datetime import date

    member.payment_status = "paid"
    member.is_active = True
    member.membership_ends_on = date(date.today().year, 12, 31)
    db.session.commit()
    return member


def _queued_verification_mail(user_id):
    """The job signup leaves behind, pointing at the account it just made."""
    return EmailDeliveryJob(
        email_type="verify_email",
        recipient_email=OLD_EMAIL,
        target_user_id=user_id,
        status="pending",
        retry_count=0,
        next_attempt_at=get_now_utc(),
    )


@pytest.fixture
def enforce_foreign_keys(app):
    """Make SQLite behave like the MariaDB this actually runs on.

    Off by default in SQLite, always on in InnoDB -- so without this a claim
    that strands a row passes here and fails there.
    """
    from sqlalchemy import event, text

    def _turn_on(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    event.listen(db.engine, "connect", _turn_on)
    db.session.execute(text("PRAGMA foreign_keys=ON"))  # the connection already open
    yield
    event.remove(db.engine, "connect", _turn_on)


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

    def test_the_signup_row_is_gone_entirely(self, app):
        """Not emptied and kept -- gone. One person, one row.

        A tombstone would follow every returning student around for years:
        counted or not counted, filtered in or out, forever needing a special
        case. The signup row has served its purpose once its evidence has
        moved across.
        """
        _archived()
        member = _returning()
        retired_id = member.user_id

        claim_archived_account(member.user)
        db.session.commit()

        assert db.session.get(User, retired_id) is None

    def test_the_membership_stays_attached_to_the_account(self, app):
        """Deleting the signup row must not take the membership with it.

        SQLAlchemy de-associates children of a deleted parent, so moving only
        the foreign key would set member.user_id back to NULL on flush and
        leave every reconnected member without an account.
        """
        profile = _archived()
        member = _returning()

        claim_archived_account(member.user)
        db.session.commit()

        assert member.user_id == profile.user_id
        assert member.user is not None
        assert member.user.member is member

    def test_the_audit_trail_survives_the_claim(self, app):
        """Signup wrote entries against the row that is about to go."""
        profile = _archived()
        member = _returning()
        db.session.add(
            AuditLog(
                actor_user_id=member.user_id,
                target_user_id=member.user_id,
                category="account",
                event_type="signed_up",
            )
        )
        db.session.flush()

        claim_archived_account(member.user)
        db.session.commit()

        entry = db.session.execute(
            db.select(AuditLog).filter_by(event_type="signed_up")
        ).scalars().one()
        assert entry.actor_user_id == profile.user_id
        assert entry.target_user_id == profile.user_id

    def test_the_queued_verification_mail_moves_too(self, app):
        """The mail that led here points at the row the claim deletes.

        Signup queues the verification email against the new account, the
        student clicks the link in it, and that is what runs the claim -- so
        this row is not an edge case, every returning student has one.
        """
        profile = _archived()
        member = _returning()
        db.session.add(_queued_verification_mail(member.user_id))
        db.session.flush()

        claim_archived_account(member.user)
        db.session.commit()

        job = db.session.execute(db.select(EmailDeliveryJob)).scalars().one()
        assert job.target_user_id == profile.user_id

    def test_nothing_is_left_pointing_at_the_deleted_row(self, app, enforce_foreign_keys):
        """With foreign keys enforced, a missed table refuses the delete.

        This is the test that matters, because the suite runs on SQLite, which
        does not check foreign keys unless asked, while production runs on
        MariaDB, which always does. Without the fixture this passes whatever
        the claim leaves behind -- and the failure lands in October, on the
        real students, not here.
        """
        _archived()
        member = _returning()
        db.session.add(_queued_verification_mail(member.user_id))
        db.session.commit()

        claim_archived_account(member.user)
        db.session.commit()  # the delete: raises IntegrityError if anything dangles

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
        assert ">Old forum</span>" in body

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

        # "Old forum" is what is left of a person who never came back, so the
        # filter must not offer them up as one any more.
        archives = admin_client.get("/admin/accounts?kind=archived").get_data(as_text=True)
        assert ">Old forum</span>" not in archives  # scoped: the dropdown names it too
        assert "PopovicA_L23" not in archives

        # They read as an ordinary member, with the history still on show.
        listing = admin_client.get("/admin/accounts").get_data(as_text=True)
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

    def test_reconnecting_counts_as_an_account_from_then_on(self, app, admin_client):
        """The archive stops being one of the unclaimed and becomes a member.

        Before the claim they are history; after it they are someone the
        association can write to and take money from, so the count that says
        how many accounts are administered here has to move with them.
        """
        from aeronautics_members.app import get_admin_dashboard_metrics

        _archived()
        member = _returning()
        db.session.commit()

        before = get_admin_dashboard_metrics()
        assert before["total_accounts"] == 2, "the admin and the person who just signed up"

        claim_archived_account(member.user)
        db.session.commit()

        after = get_admin_dashboard_metrics()
        assert after["total_accounts"] == 2, "the two rows became one account, not none"
        assert after["archived_forum_accounts"] == 1
        assert after["archived_forum_claimed"] == 1


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


class TestWhatTheOldForumSaidAboutThem:
    """The group they were in, kept as history rather than acted on.

    "Banned" on that board was not misconduct. The ban log reads "non active
    student", "Not active student/exchange semester", "Is now a Lecturer" --
    it was how somebody who stopped studying was deactivated, and it is the
    only surviving record of who left and why. The converter exported it and
    the importer dropped it, so it reached people.json and went no further.
    """

    def _imported(self, **fields):
        person = {
            "source_user_id": "645",
            "source_username": "PopovicA_L23",
            "source_email": OLD_EMAIL,
        }
        person.update(fields)
        import_forum_people([person])
        db.session.commit()
        return db.session.execute(db.select(ImportedForumProfile)).scalar_one()

    def test_the_group_is_kept(self, app):
        profile = self._imported(source_group="Banned",
                                 source_group_reason="non active student")

        assert profile.source_group == "Banned"
        assert profile.source_group_reason == "non active student"

    def test_a_missing_reason_is_none_rather_than_empty(self, app):
        """Most of the 230 have no reason written down."""
        profile = self._imported(source_group="Banned", source_group_reason="")

        assert profile.source_group == "Banned"
        assert profile.source_group_reason is None

    def test_an_export_without_the_field_still_imports(self, app):
        """An older people.json predates the converter carrying it."""
        profile = self._imported()

        assert profile.source_group is None
        assert profile.source_username == "PopovicA_L23"

    def test_it_does_not_disable_the_account(self, app):
        """The decisive point.

        users.disabled_at means an administrator here decided something, with
        a person and a date attached. A MyBB group is a fact about a system
        being switched off. Writing one into the other would invent an admin
        action that never happened -- and a returning student claiming such an
        account would inherit a dead one.
        """
        profile = self._imported(source_group="Banned",
                                 source_group_reason="non active student")

        assert profile.user.is_disabled is False
        assert profile.user.disabled_at is None
        assert profile.user.account_status == "archived"

    def test_a_banned_person_can_still_come_back(self, app):
        """Only two of the 258 likely returners were banned, but not zero."""
        profile = self._imported(source_group="Banned",
                                 source_group_reason="non active student")
        archived_id = profile.user_id
        member = _returning()

        claimed = claim_archived_account(member.user)
        db.session.commit()

        assert claimed is profile
        assert member.user_id == archived_id
        assert member.user.is_disabled is False, "their old status is not a punishment here"
        assert member.user.can_sign_in is True

    def test_the_history_survives_the_claim(self, app):
        """Still worth knowing they had left, even once they are back."""
        profile = self._imported(source_group="Banned",
                                 source_group_reason="Is now a Lecturer")
        member = _returning()

        claim_archived_account(member.user)
        db.session.commit()

        assert profile.source_group == "Banned"
        assert profile.source_group_reason == "Is now a Lecturer"

    def test_a_second_run_updates_it(self, app):
        """The status may have changed on the forum between exports."""
        self._imported(source_group="Registered")

        import_forum_people([{
            "source_user_id": "645", "source_username": "PopovicA_L23",
            "source_email": OLD_EMAIL, "source_group": "Banned",
            "source_group_reason": "non active student",
        }])
        db.session.commit()

        profile = db.session.execute(db.select(ImportedForumProfile)).scalar_one()
        assert profile.source_group == "Banned"

    def test_the_account_page_shows_it(self, app, client):
        from conftest import app_module

        admin = User(email="admin-group@example.com")
        admin.set_password("x")
        admin.grant_role(app_module.get_role("admin"))
        db.session.add(admin)
        db.session.commit()
        profile = self._imported(source_group="Banned",
                                 source_group_reason="non active student")
        with client.session_transaction() as session:
            session["_user_id"] = str(admin.id)

        body = client.get(f"/admin/accounts/{profile.user_id}").get_data(as_text=True)

        assert "Group on the old forum" in body
        assert "Banned" in body
        assert "non active student" in body


class TestWhenTheySignedUpTheNormalWay:
    """A real signup already has a forum account by the time it is verified.

    Which the claim used to refuse. Every unit test above builds a member with
    nothing attached, so all of them passed while the only path that matters --
    somebody signing up through the site -- could never reconnect. Found on the
    real server: a matching address, a verified university email, and the
    archive still reading "Unclaimed".
    """

    def _returning_with_forum_account(self):
        from aeronautics_members.db_models import ForumAccount

        member = _returning()
        db.session.add(ForumAccount(
            user=member.user,
            member=member,
            provider="discourse",
            external_id=str(member.user_id),
            state="inactive",
        ))
        db.session.commit()
        return member

    def test_the_claim_still_happens(self, app):
        profile = _archived()
        member = self._returning_with_forum_account()

        assert claim_archived_account(member.user) is profile
        db.session.commit()

        assert profile.claimed_at is not None

    def test_the_forum_account_moves_across(self, app):
        """Deleting the signup row would otherwise take it with them."""
        profile = _archived()
        member = self._returning_with_forum_account()

        claim_archived_account(member.user)
        db.session.commit()

        assert profile.user.forum_account is not None
        assert profile.user.forum_account.member is member

    def test_it_points_at_the_account_that_holds_the_old_posts(self, app):
        """external_id is how Discourse knows people, and it has just changed."""
        profile = _archived()
        member = self._returning_with_forum_account()

        claim_archived_account(member.user)
        db.session.commit()

        assert profile.user.forum_account.external_id == str(profile.user_id)

    def test_the_forum_profile_is_looked_up_again(self, app):
        """remote_user_id names the profile made for the row being retired.

        Left alone, the returning student would be synced onto the throwaway
        account rather than the one carrying their avatar, their cohort groups
        and, later, their posts.
        """
        from aeronautics_members.db_models import ForumAccount

        profile = _archived()
        member = self._returning_with_forum_account()
        account = db.session.execute(db.select(ForumAccount)).scalar_one()
        account.remote_user_id = 4242
        db.session.commit()

        claim_archived_account(member.user)
        db.session.commit()

        assert profile.user.forum_account.remote_user_id is None


class TestTheAccountTheyLeaveBehindOnTheForum:
    """Signing up makes a Discourse account before they have reconnected.

    Reclaiming moves them onto the archived one, and that first account is then
    an orphan: no posts, but still holding their real email address -- which
    Discourse will not then give to the account they actually use, because an
    address belongs to one account. Found on the real server: enabling
    auth_overrides_email made every login fail with "The change you wanted was
    rejected", because a ghost held the address.
    """

    def _returning_on_the_forum(self, remote_user_id=8801):
        from aeronautics_members.db_models import ForumAccount

        member = _returning()
        db.session.add(ForumAccount(
            user=member.user,
            member=member,
            provider="discourse",
            external_id=str(member.user_id),
            remote_user_id=remote_user_id,
            state="onboarding",
        ))
        db.session.commit()
        return member

    def _queued(self):
        from aeronautics_members.db_models import ExternalWorkItem

        return db.session.execute(
            db.select(ExternalWorkItem).filter_by(
                kind=ExternalWorkItem.KIND_FORUM_DISCARD_REPLACED
            )
        ).scalars().all()

    def test_its_removal_is_queued(self, app):
        _archived()
        member = self._returning_on_the_forum(remote_user_id=8801)

        claim_archived_account(member.user)
        db.session.commit()

        queued = self._queued()
        assert len(queued) == 1
        assert queued[0].payload["remote_user_id"] == 8801

    def test_the_remote_id_is_carried_in_the_payload(self, app):
        """The row that knew it is deleted by the time the worker runs."""
        _archived()
        member = self._returning_on_the_forum(remote_user_id=8801)
        retired_id = member.user_id

        claim_archived_account(member.user)
        db.session.commit()

        assert db.session.get(User, retired_id) is None, "the row is gone"
        assert self._queued()[0].payload["remote_user_id"] == 8801, "the id is not"

    def test_nothing_is_queued_when_they_were_never_on_the_forum(self, app):
        """Most people verify before they ever reach it. No orphan, no work."""
        _archived()
        member = self._returning_on_the_forum(remote_user_id=None)

        claim_archived_account(member.user)
        db.session.commit()

        assert self._queued() == []

    def test_the_claim_does_not_talk_to_the_forum_itself(self, app):
        """This runs while a student is clicking a link in an email.

        A slow or unreachable forum must not be able to fail their
        reconnection, so the call is queued rather than made here.
        """
        profile = _archived()
        member = self._returning_on_the_forum()

        def explode(*args, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("the claim called the forum synchronously")

        from aeronautics_members.forum_service import DiscourseConnectProvider
        original = DiscourseConnectProvider._request
        DiscourseConnectProvider._request = explode
        try:
            assert claim_archived_account(member.user) is profile
            db.session.commit()
        finally:
            DiscourseConnectProvider._request = original

    def test_running_a_claim_twice_queues_one_deletion(self, app):
        """The dedupe key is the remote id, so a retry cannot double up."""
        _archived()
        member = self._returning_on_the_forum(remote_user_id=8801)

        claim_archived_account(member.user)
        db.session.commit()
        # A second claim finds nothing to do, but must not queue again either.
        claim_archived_account(member.user)
        db.session.commit()

        assert len(self._queued()) == 1


class TestWhatAReconnectedMemberIsAskedToDo:
    """They already have a profile picture. Do not ask for another one.

    The old forum's avatar is imported, published to Discourse and visible on
    their profile before they ever sign in -- but it is not a
    ForumAvatarSubmission, so the onboarding gate could not see it and told
    them to upload a picture as the very first thing after reconnecting.
    """

    def _reconnected_with_an_avatar(self, tmp_path, with_avatar=True):
        profile = _archived()
        if with_avatar:
            profile.avatar_path = str(tmp_path / "face.jpg")
        member = _returning()
        _make_paid(member)
        claim_archived_account(member.user)
        db.session.commit()
        return member

    def _context(self, member):
        from flask import current_app
        from aeronautics_members.app import build_forum_context
        from aeronautics_members.db_models import Setting

        # With the integration off, "the forum is not set up" is the honest
        # answer and comes first, which is not what these are about.
        for key, value in (
            ("forum_integration_enabled", "True"),
            ("forum_base_url", "https://forum.example"),
            ("discourse_connect_secret", "x" * 16),
        ):
            db.session.merge(Setting(key=key, value=value))
        db.session.commit()

        # A request context: the builder makes URLs for the forum links.
        with current_app.test_request_context("/account"):
            return build_forum_context(member)

    def test_the_old_avatar_counts(self, app, tmp_path):
        member = self._reconnected_with_an_avatar(tmp_path)

        assert self._context(member)["status_key"] == "active"

    def test_they_may_still_replace_it(self, app, tmp_path):
        """Keeping a decade-old photograph should be a choice, not a sentence."""
        member = self._reconnected_with_an_avatar(tmp_path)

        assert self._context(member)["can_upload_avatar"] is True

    def test_somebody_reconnecting_without_one_is_still_asked(self, app, tmp_path):
        """63 of the imported people have no picture at all."""
        member = self._reconnected_with_an_avatar(tmp_path, with_avatar=False)

        assert self._context(member)["status_key"] == "needs_avatar"

    def test_an_ordinary_new_member_is_still_asked(self, app):
        member = _make_paid(make_member(email="brand.new@edu.fh-joanneum.at"))

        assert self._context(member)["status_key"] == "needs_avatar"

    def test_an_unclaimed_archive_does_not_count(self, app, tmp_path):
        """The avatar only becomes theirs when the account does."""
        profile = _archived()
        profile.avatar_path = str(tmp_path / "face.jpg")
        member = _make_paid(_returning())

        assert self._context(member)["status_key"] == "needs_avatar"

    def _forum_state(self, member):
        from aeronautics_members.services.forum import get_forum_service

        return get_forum_service().get_desired_state(member)

    def test_and_the_forum_agrees_with_the_page(self, app, tmp_path):
        """Found for real: told "your forum access is ready", kept in onboarding.

        The page counted the old picture and the forum's groups did not, so a
        returning student who had paid was in members-onboarding with nothing
        to read, and nothing in the approvals queue to release them.
        """
        from aeronautics_members.forum_service import FORUM_STATE_ACTIVE

        member = self._reconnected_with_an_avatar(tmp_path)

        assert self._context(member)["status_key"] == "active"
        assert self._forum_state(member) == FORUM_STATE_ACTIVE

    def test_without_a_picture_the_forum_still_waits_for_one(self, app, tmp_path):
        from aeronautics_members.forum_service import FORUM_STATE_ONBOARDING

        member = self._reconnected_with_an_avatar(tmp_path, with_avatar=False)

        assert self._forum_state(member) == FORUM_STATE_ONBOARDING

    def test_an_unclaimed_archive_does_not_let_anybody_in(self, app, tmp_path):
        from aeronautics_members.forum_service import FORUM_STATE_ONBOARDING

        profile = _archived()
        profile.avatar_path = str(tmp_path / "face.jpg")
        member = _make_paid(_returning())

        assert self._forum_state(member) == FORUM_STATE_ONBOARDING

    def test_the_picture_does_not_outlast_the_membership(self, app, tmp_path):
        """Counting as approved is about the photograph, not about paying."""
        from aeronautics_members.forum_service import FORUM_STATE_INACTIVE

        from datetime import datetime, timezone

        member = self._reconnected_with_an_avatar(tmp_path)
        member.user.disabled_at = datetime.now(timezone.utc)

        assert self._forum_state(member) == FORUM_STATE_INACTIVE
