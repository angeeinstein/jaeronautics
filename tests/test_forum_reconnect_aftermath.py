"""What happens around a returning student reconnecting their old forum account.

The claim itself is covered in test_forum_account_claim.py. These are the
things on either side of it that went wrong: how somebody could get to claim
an account that was not theirs, what stopped a real owner from claiming, and
what the claim left broken for the person it had just helped.
"""
from datetime import datetime, timezone

from conftest import db, make_member

from aeronautics_members.db_models import (
    ExternalWorkItem,
    ForumAccount,
    ForumAvatarSubmission,
    ImportedForumProfile,
    Member,
    User,
)
from aeronautics_members.services.forum_import import (
    claim_archived_account,
    import_forum_people,
)
from aeronautics_members.services.identity import (
    build_email_verification_claims,
    generate_token,
)


def _now():
    return datetime.now(timezone.utc)


def _sign_in(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)


def _archive(uid, username, address):
    import_forum_people([{
        "source_user_id": uid, "source_username": username,
        "source_email": address, "year_group": "LAV22", "post_count": 3,
    }])
    db.session.commit()
    return db.session.execute(
        db.select(ImportedForumProfile).filter_by(source_username=username)
    ).scalar_one()


def _returning(uid, username, address, private):
    """A new member whose university address an archived account was registered under."""
    _archive(uid, username, address)
    return make_member(
        email=private, member_category="student", year_group="LAV22", email_work=address,
    )


def _profile_form(private, work):
    return {
        "profile-street": "Main", "profile-house_number": "1",
        "profile-postal_code": "8010", "profile-city": "Graz",
        "profile-country": "Austria", "profile-phone_private": "+43123",
        "profile-email_private": private,
        "profile-email_work": work,
    }


class TestChangingTheUniversityAddress:
    """A confirmation belongs to the address that was confirmed."""

    def test_a_new_address_has_to_be_confirmed_again(self, app, client):
        member = make_member(
            email="someone@example.com", member_category="student",
            year_group="LAV25", email_work="someone@edu.fh-joanneum.at",
        )
        member.email_work_verified_at = _now()
        member.user.email_verified_at = _now()
        db.session.commit()
        _sign_in(client, member.user.id)

        client.post("/account/profile", data=_profile_form(
            "someone@example.com", "somebody.else@edu.fh-joanneum.at",
        ))

        db.session.expire_all()
        member = db.session.get(Member, member.id)
        assert member.email_work == "somebody.else@edu.fh-joanneum.at"
        assert member.email_work_verified_at is None

    def test_saving_other_details_keeps_the_confirmation(self, app, client):
        member = make_member(
            email="keeper@example.com", member_category="student",
            year_group="LAV25", email_work="keeper@edu.fh-joanneum.at",
        )
        member.email_work_verified_at = _now()
        member.user.email_verified_at = _now()
        db.session.commit()
        _sign_in(client, member.user.id)

        # Only the capitalisation differs, which is the same mailbox.
        client.post("/account/profile", data=_profile_form(
            "keeper@example.com", "Keeper@edu.fh-joanneum.at",
        ))

        db.session.expire_all()
        assert db.session.get(Member, member.id).email_work_verified_at is not None

    def test_it_is_no_longer_a_way_into_somebody_elses_old_account(self, app, client):
        """Typing someone's address over a confirmed one used to keep the tick,
        and the next sign-in handed over their archived account and posts."""
        _archive("99", "VictimV_L20", "victim@edu.fh-joanneum.at")
        member = make_member(
            email="intruder@example.com", member_category="student",
            year_group="LAV25", email_work="intruder@edu.fh-joanneum.at",
        )
        member.email_work_verified_at = _now()
        member.user.email_verified_at = _now()
        db.session.commit()
        _sign_in(client, member.user.id)
        client.post("/account/profile", data=_profile_form(
            "intruder@example.com", "victim@edu.fh-joanneum.at",
        ))
        client.post("/logout")

        client.post("/login", data={
            "email": "intruder@example.com", "password": "initial-password",
        })

        db.session.expire_all()
        profile = db.session.execute(
            db.select(ImportedForumProfile).filter_by(source_username="VictimV_L20")
        ).scalar_one()
        assert profile.claimed_at is None


class TestWhatNoLongerStandsInTheWay:
    def test_a_photo_uploaded_before_confirming_moves_across(self, app):
        """The account page offers the upload before the address is confirmed,
        so doing things in that order must not cost somebody their history."""
        member = _returning(
            "77", "ReturningR_L22", "returning@edu.fh-joanneum.at", "returning@example.com",
        )
        db.session.add(ForumAvatarSubmission(
            user_id=member.user.id, member_id=member.id,
            status="pending", public_token="t-before-confirming",
        ))
        member.email_work_verified_at = _now()
        db.session.commit()

        profile = claim_archived_account(member.user)
        db.session.commit()

        assert profile is not None
        submission = db.session.execute(db.select(ForumAvatarSubmission)).scalar_one()
        assert submission.user_id == profile.user_id
        assert submission.member_id == member.id

    def test_a_pending_identity_request_moves_across(self, app):
        from aeronautics_members.db_models import MemberProfileChangeRequest

        member = _returning(
            "78", "RequestR_L22", "request@edu.fh-joanneum.at", "request@example.com",
        )
        db.session.add(MemberProfileChangeRequest(
            member_id=member.id, requested_by_user_id=member.user.id,
            requested_salutation="mr", requested_first_name="Test",
            requested_last_name="Member", requested_member_category="student",
            requested_year_group="LAV21",
        ))
        member.email_work_verified_at = _now()
        db.session.commit()

        profile = claim_archived_account(member.user)
        db.session.commit()

        assert profile is not None
        request = db.session.execute(db.select(MemberProfileChangeRequest)).scalar_one()
        assert request.requested_by_user_id == profile.user_id

    def test_an_admin_reconnects_and_keeps_their_roles(self, app):
        """Found on the test server: the super admin made at install was also
        on the old forum, and the claim skipped them without a word."""
        from conftest import app_module

        member = _returning(
            "79", "StaffS_L22", "staff@edu.fh-joanneum.at", "staff@example.com",
        )
        member.user.grant_role(app_module.get_role("superadmin"))
        member.user.grant_role(app_module.get_role("admin"))
        member.email_work_verified_at = _now()
        db.session.commit()

        profile = claim_archived_account(member.user)
        db.session.commit()

        assert profile is not None
        kept = db.session.get(User, profile.user_id)
        assert {role.slug for role in kept.roles} == {"superadmin", "admin"}
        assert kept.member.id == member.id


class TestStayingSignedIn:
    """The claim deletes the row somebody signed up with. Nothing they were
    holding for that row may stop working because of it."""

    def test_signing_in_and_reconnecting_keeps_them_signed_in(self, app, client):
        member = _returning("62", "SignS_L22", "sign@edu.fh-joanneum.at", "sign@example.com")
        member.email_work_verified_at = _now()
        member.user.email_verified_at = _now()
        db.session.commit()

        client.post("/login", data={"email": "sign@example.com", "password": "initial-password"})

        response = client.get("/account", follow_redirects=True)
        assert response.request.path == "/account"
        with client.session_transaction() as session:
            archived = db.session.execute(
                db.select(ImportedForumProfile).filter_by(source_username="SignS_L22")
            ).scalar_one().user
            assert session["_user_id"] == str(archived.id)

    def test_confirming_the_university_address_keeps_them_signed_in(self, app, client):
        from aeronautics_members.services.identity import build_work_email_verification_claims

        member = _returning("64", "WorkW_L22", "work@edu.fh-joanneum.at", "work@example.com")
        member.user.email_verified_at = _now()
        token = generate_token("verify-work-email", **build_work_email_verification_claims(member))
        db.session.commit()
        _sign_in(client, member.user.id)

        client.get(f"/verify-work-email/{token}")

        assert client.get("/account", follow_redirects=True).request.path == "/account"
        archived = db.session.execute(
            db.select(ImportedForumProfile).filter_by(source_username="WorkW_L22")
        ).scalar_one()
        assert archived.claimed_at is not None
        with client.session_transaction() as session:
            assert session["_user_id"] == str(archived.user_id)

    def test_the_first_verification_link_still_works_afterwards(self, app, client):
        member = _returning("61", "LinkL_L22", "link@edu.fh-joanneum.at", "link@example.com")
        token = generate_token("verify-email", **build_email_verification_claims(member.user))
        member.email_work_verified_at = _now()
        db.session.commit()
        assert claim_archived_account(member.user) is not None
        db.session.commit()

        body = client.get(f"/verify-email/{token}", follow_redirects=True).get_data(as_text=True)

        assert "invalid or has expired" not in body
        account = db.session.execute(
            db.select(User).filter_by(email="link@example.com")
        ).scalar_one()
        assert account.email_is_verified

    def test_a_link_is_only_followed_with_its_secret(self, app, client):
        """The address alone must never be enough to land in an account."""
        member = _returning("65", "NonceN_L22", "nonce@edu.fh-joanneum.at", "nonce@example.com")
        claims = build_email_verification_claims(member.user)
        member.email_work_verified_at = _now()
        db.session.commit()
        assert claim_archived_account(member.user) is not None
        db.session.commit()

        forged = generate_token("verify-email", **{**claims, "nonce": "guessed"})
        body = client.get(f"/verify-email/{forged}", follow_redirects=True).get_data(as_text=True)

        assert "invalid or has expired" in body
        account = db.session.execute(
            db.select(User).filter_by(email="nonce@example.com")
        ).scalar_one()
        assert not account.email_is_verified

    def test_the_forum_link_in_the_welcome_mail_still_works_afterwards(self, app, client):
        member = _returning("66", "WelcomeW_L22", "welcome@edu.fh-joanneum.at", "welcome@example.com")
        token = generate_token(
            "forum-entry", issued_at=int(_now().timestamp()),
            **build_email_verification_claims(member.user),
        )
        member.email_work_verified_at = _now()
        db.session.commit()
        assert claim_archived_account(member.user) is not None
        db.session.commit()

        client.get(f"/forum?token={token}")

        # Issued within the hour, so the link signs them in -- as the account
        # they now have, not as nobody.
        archived = db.session.execute(
            db.select(ImportedForumProfile).filter_by(source_username="WelcomeW_L22")
        ).scalar_one()
        with client.session_transaction() as session:
            assert session.get("_user_id") == str(archived.user_id)


class TestTellingTheForum:
    def test_the_claim_queues_a_sync_of_the_account_they_kept(self, app):
        member = _returning("63", "SyncS_L22", "sync@edu.fh-joanneum.at", "sync@example.com")
        db.session.add(ForumAccount(
            user_id=member.user.id, member_id=member.id, provider="discourse",
            external_id=str(member.user.id), remote_user_id=901, state="onboarding",
        ))
        member.email_work_verified_at = _now()
        db.session.commit()

        profile = claim_archived_account(member.user)
        db.session.commit()

        queued = {
            item.kind: item for item in db.session.execute(db.select(ExternalWorkItem)).scalars()
        }
        assert set(queued) == {
            ExternalWorkItem.KIND_FORUM_DISCARD_REPLACED, ExternalWorkItem.KIND_FORUM_SYNC,
        }
        assert queued[ExternalWorkItem.KIND_FORUM_SYNC].member_id == member.id
        assert queued[ExternalWorkItem.KIND_FORUM_SYNC].user_id == profile.user_id

    def _discard(self, monkeypatch, remote_user, *, known_as=None):
        """Run the discard against a forum holding ``remote_user``; returns what it was asked."""
        from aeronautics_members.services.workflows import _handle_forum_discard_replaced_work

        asked = {"released": [], "deleted": []}

        class FakeProvider:
            def get_remote_user_by_external_id(self, external_id):
                return known_as or {}

            def release_address(self, remote_user_id, external_id, placeholder):
                asked["released"].append((remote_user_id, external_id, placeholder))
                return True

            def delete_remote_user(self, remote_user_id):
                if remote_user.get("admin"):
                    return False, "the account is an admin or moderator on the forum and was left alone"
                asked["deleted"].append(remote_user_id)
                return True, None

        class FakeService:
            provider = FakeProvider()

            def is_ready(self):
                return True

        monkeypatch.setattr(
            "aeronautics_members.services.workflows.get_forum_service", lambda: FakeService(),
        )
        member = make_member(email="kept2@example.com")
        item = ExternalWorkItem(
            kind=ExternalWorkItem.KIND_FORUM_DISCARD_REPLACED, user=member.user,
            payload={"remote_user_id": 903, "external_id": "7"},
            status=ExternalWorkItem.STATUS_PENDING,
        )
        db.session.add(item)
        db.session.commit()
        _handle_forum_discard_replaced_work(item)
        db.session.commit()
        asked["syncs"] = [
            sync.member_id for sync in db.session.execute(
                db.select(ExternalWorkItem).filter_by(kind=ExternalWorkItem.KIND_FORUM_SYNC)
            ).scalars()
        ]
        asked["member_id"] = member.id
        return asked

    def test_the_address_comes_off_the_leftover_first(self, app, monkeypatch):
        asked = self._discard(monkeypatch, {"id": 903})

        assert asked["released"] == [(903, "7", "forum-replaced-7@imported.invalid")]
        assert asked["deleted"] == [903]
        assert asked["syncs"] == [asked["member_id"]]

    def test_an_admin_leftover_is_kept_but_no_longer_in_the_way(self, app, monkeypatch):
        """Discourse will not delete an admin; the address is what matters."""
        from aeronautics_members.db_models import NotificationEvent

        asked = self._discard(monkeypatch, {"id": 903, "admin": True})

        assert asked["deleted"] == []
        assert asked["released"]
        assert asked["syncs"] == [asked["member_id"]]
        assert db.session.execute(
            db.select(NotificationEvent).filter_by(event_type="forum_replaced_account_kept")
        ).scalars().first() is not None

    def test_removing_the_leftover_account_syncs_the_kept_one_again(self, app, monkeypatch):
        """The leftover holds the address, so a sync that ran first was refused."""
        from aeronautics_members.services.workflows import _handle_forum_discard_replaced_work

        class FakeProvider:
            def delete_remote_user(self, remote_user_id):
                return True, None

        class FakeService:
            provider = FakeProvider()

            def is_ready(self):
                return True

        monkeypatch.setattr(
            "aeronautics_members.services.workflows.get_forum_service", lambda: FakeService(),
        )
        member = make_member(email="kept@example.com")
        item = ExternalWorkItem(
            kind=ExternalWorkItem.KIND_FORUM_DISCARD_REPLACED, user=member.user,
            payload={"remote_user_id": 902}, status=ExternalWorkItem.STATUS_PENDING,
        )
        db.session.add(item)
        db.session.commit()

        _handle_forum_discard_replaced_work(item)

        syncs = db.session.execute(
            db.select(ExternalWorkItem).filter_by(kind=ExternalWorkItem.KIND_FORUM_SYNC)
        ).scalars().all()
        assert [sync.member_id for sync in syncs] == [member.id]


class TestOpeningTheForumStraightAway:
    """Found on the test server: reconnected, clicked through to the forum at
    once, and Discourse answered "the change you wanted was rejected" -- the
    leftover account still held the address. Two minutes later it worked."""

    def _pending_discard(self, user, remote_user_id):
        item = ExternalWorkItem(
            kind=ExternalWorkItem.KIND_FORUM_DISCARD_REPLACED, user=user,
            payload={"remote_user_id": remote_user_id, "external_id": "1"},
            status=ExternalWorkItem.STATUS_PENDING,
        )
        db.session.add(item)
        db.session.commit()
        return item

    def _handler(self, monkeypatch, handled, fail=False):
        from aeronautics_members.services import outbox

        def handler(item):
            if fail:
                raise RuntimeError("forum unreachable")
            handled.append(item.payload["remote_user_id"])

        monkeypatch.setitem(outbox._HANDLERS, ExternalWorkItem.KIND_FORUM_DISCARD_REPLACED, handler)

    def test_their_own_cleanup_is_done_first_and_nobody_elses(self, app, monkeypatch):
        from aeronautics_members.services.workflows import finish_forum_cleanup_for

        handled = []
        self._handler(monkeypatch, handled)
        mine = make_member(email="mine@example.com")
        theirs = make_member(email="theirs@example.com")
        self._pending_discard(mine.user, 11)
        self._pending_discard(theirs.user, 22)

        assert finish_forum_cleanup_for(mine.user) is True
        assert handled == [11]

    def test_while_it_fails_they_are_not_sent_on(self, app, monkeypatch):
        from aeronautics_members.services.workflows import finish_forum_cleanup_for

        self._handler(monkeypatch, [], fail=True)
        member = make_member(email="waiting@example.com")
        self._pending_discard(member.user, 33)

        assert finish_forum_cleanup_for(member.user) is False

    def test_the_forum_sign_in_waits_rather_than_showing_the_rejection(self, app, client, monkeypatch):
        from datetime import date

        self._handler(monkeypatch, [], fail=True)
        member = make_member(
            email="clicker@example.com", payment_status="paid", is_active=True,
            membership_starts_on=date(2026, 1, 1), membership_ends_on=date(2099, 12, 31),
        )
        member.user.email_verified_at = _now()
        db.session.commit()
        self._pending_discard(member.user, 44)
        _sign_in(client, member.user.id)

        response = client.get("/forum/discourse/connect?sso=x&sig=y")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/account")
        with client.session_transaction() as session:
            messages = [text for _category, text in session.get("_flashes", [])]
        assert any("still being set up" in text for text in messages)


class TestTheWelcomeMail:
    """Sent at payment, usually before the university address is confirmed --
    so before the reconnect that decides which username they will have."""

    def _welcome(self, app, monkeypatch, member):
        from flask import render_template

        from aeronautics_members.db_models import Setting
        from aeronautics_members.services import workflows

        for key, value in {
            "automatic_emails_enabled": "True",
            "welcome_email_sender": "office",
            "automatic_email_template": "welcome_email.html",
        }.items():
            db.session.add(Setting(key=key, value=value))
        db.session.commit()
        sent = {}

        def fake_send_mail(**kwargs):
            sent.update(kwargs)
            return True, None

        monkeypatch.setattr(workflows, "send_mail", fake_send_mail)
        with app.test_request_context():
            workflows.send_member_welcome_email(app, member)
            template_vars = {
                k: v for k, v in sent.items()
                if k not in {"from_account", "to_email", "subject", "template_name",
                             "attachments", "return_error"}
            }
            return render_template("emails/welcome_email.html", **template_vars)

    def test_somebody_with_an_old_account_is_told_it_comes_back_not_what_it_is(self, app, monkeypatch):
        member = _returning("81", "MailM_L22", "mail@edu.fh-joanneum.at", "mail@example.com")
        member.user.forum_username = "NewN_L22-2"
        db.session.commit()

        body = self._welcome(app, monkeypatch, member)

        assert "will be reconnected" in body
        assert "NewN_L22-2" not in body   # a username they will never use
        assert "MailM_L22" not in body    # nor whose account that address was

    def test_everybody_else_is_given_their_username(self, app, monkeypatch):
        member = make_member(email="plain@example.com", email_work="plain@edu.fh-joanneum.at")
        member.user.forum_username = "PlainP_L25"
        db.session.commit()

        body = self._welcome(app, monkeypatch, member)

        assert "PlainP_L25" in body
        assert "will be reconnected" not in body
