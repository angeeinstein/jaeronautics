"""A verification link must prove ownership of one specific address.

A token carrying only ``user_id`` stays valid after the account's email changes,
so an old link could mark a newly entered (unproven) address as verified. That
matters because DiscourseConnect asserts this address to the forum, which
associates forum accounts by email.
"""
from conftest import clock, db, identity, make_member, workflows
from aeronautics_members.db_models import User
from aeronautics_members.forum_service import DiscourseConnectProvider


def _issue_verification_token(user):
    token = identity.generate_token(
        "verify-email", **identity.build_email_verification_claims(user)
    )
    db.session.commit()
    return token


def test_token_does_not_verify_a_changed_address(app, client):
    member = make_member(email="old@example.com")
    user = member.user
    token = _issue_verification_token(user)

    # The account moves to a different address before the link is used.
    user.email = "attacker@example.com"
    user.email_verified_at = None
    identity.rotate_email_verification_nonce(user)
    db.session.commit()

    resp = client.get(f"/verify-email/{token}", follow_redirects=False)

    assert resp.status_code == 302
    assert db.session.get(User, user.id).email_is_verified is False


def test_token_verifies_the_address_it_was_issued_for(app, client):
    member = make_member(email="owner@example.com")
    user = member.user
    token = _issue_verification_token(user)

    resp = client.get(f"/verify-email/{token}", follow_redirects=False)

    assert resp.status_code == 302
    assert db.session.get(User, user.id).email_is_verified is True


def test_rotated_nonce_revokes_outstanding_links(app, client):
    member = make_member(email="rotate@example.com")
    user = member.user
    token = _issue_verification_token(user)

    # Same address, but the nonce was rotated (e.g. an address change that was
    # then reverted). The old link must no longer be accepted.
    identity.rotate_email_verification_nonce(user)
    db.session.commit()

    client.get(f"/verify-email/{token}", follow_redirects=False)

    assert db.session.get(User, user.id).email_is_verified is False


def test_legacy_token_without_claims_is_rejected(app, client):
    # Tokens minted before binding existed carry only user_id and cannot prove
    # which address they were sent to.
    member = make_member(email="legacy@example.com")
    user = member.user
    token = identity.generate_token("verify-email", user_id=user.id)

    client.get(f"/verify-email/{token}", follow_redirects=False)

    assert db.session.get(User, user.id).email_is_verified is False


def test_email_change_rotates_the_nonce(app):
    member = make_member(email="change@example.com")
    user = member.user
    identity.build_email_verification_claims(user)
    db.session.commit()
    original_nonce = user.email_verification_nonce

    workflows.sync_member_primary_email(member, "changed@example.com")
    db.session.commit()

    assert user.email_verification_nonce != original_nonce
    assert user.email_is_verified is False


class TestDiscourseActivation:
    """Never vouch to Discourse for an address we have not verified ourselves."""

    def _payload(self, verified):
        member = make_member(email="sso@example.com")
        user = member.user
        if verified:
            user.email_verified_at = clock.get_now_utc()
        db.session.commit()
        provider = DiscourseConnectProvider(settings={})
        return provider.build_sso_payload(user, member, desired_state="active", nonce="n1")

    def test_unverified_email_requires_discourse_activation(self, app):
        assert self._payload(verified=False)["require_activation"] == "true"

    def test_verified_email_skips_discourse_activation(self, app):
        assert self._payload(verified=True)["require_activation"] == "false"


class TestOldLinksDieWithTheOldAddress:
    """Found by the pre-deployment audit: after an account's address changed,
    a reset link or a fresh welcome link still in the old mailbox could still
    take the account -- the one a verification link could not."""

    def test_a_reset_link_sent_to_the_old_address_stops_working(self, app, client):
        member = make_member(email="before@example.com")
        token = identity.build_password_reset_token(member.user)
        db.session.commit()

        workflows.sync_member_primary_email(member, "after@example.com")
        db.session.commit()

        resp = client.put(f"/api/v1/password-reset/{token}", json={"password": "A-new-password-1"})

        assert (resp.status_code, resp.get_json()["error"]["code"]) == (400, "link_invalid")
        assert db.session.get(User, member.user.id).check_password("initial-password")

    def test_a_reset_link_still_works_while_the_address_is_unchanged(self, app, client):
        member = make_member(email="same@example.com")
        token = identity.build_password_reset_token(member.user)
        db.session.commit()

        assert client.get(f"/api/v1/password-reset/{token}").get_json() == {"valid": True}
        resp = client.put(f"/api/v1/password-reset/{token}", json={"password": "A-new-password-1"})

        assert resp.status_code == 200
        assert db.session.get(User, member.user.id).check_password("A-new-password-1")
        # Once.
        assert client.get(f"/api/v1/password-reset/{token}").status_code == 400

    def _welcome_link(self, user):
        from aeronautics_members.services.forum import build_forum_entry_url

        url = build_forum_entry_url(user, include_token=True)
        db.session.commit()
        return "/forum?" + url.split("?", 1)[1]

    def test_a_fresh_welcome_link_signs_in(self, app, client):
        member = make_member(email="welcome@example.com")
        link = self._welcome_link(member.user)

        client.get(link)

        with client.session_transaction() as session:
            assert session.get("_user_id") == str(member.user.id)

    def test_a_welcome_link_sent_to_the_old_address_does_not_sign_in(self, app, client):
        member = make_member(email="welcome-old@example.com")
        link = self._welcome_link(member.user)

        workflows.sync_member_primary_email(member, "welcome-new@example.com")
        db.session.commit()

        resp = client.get(link)

        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
        with client.session_transaction() as session:
            assert session.get("_user_id") is None
