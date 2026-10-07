"""Telling people there are two addresses, and letting them ask again.

There are two, they are confirmed separately, and the page said only "verify
your email". Somebody who confirmed the private one and stopped was told
nothing -- and for a returning student the university address is the *only*
evidence that can give them their old forum account, so stopping there meant
never reconnecting and never learning why.

There was also no way to ask for the university confirmation again. A lost
email was the end of the road.
"""
from datetime import date, datetime


from conftest import db, make_member
from aeronautics_members.db_models import ImportedForumProfile, User
from aeronautics_members.services.forum_import import import_forum_people

OLD_EMAIL = "a.popovic@edu.fh-joanneum.at"


def _member(work_verified=False, private_verified=True):
    member = make_member(email="private@example.com", year_group="LAV23")
    member.email_work = OLD_EMAIL
    member.payment_status = "paid"
    member.is_active = True
    member.membership_ends_on = date(date.today().year, 12, 31)
    if private_verified:
        member.user.email_verified_at = datetime.utcnow()
    if work_verified:
        member.email_work_verified_at = datetime.utcnow()
    member.user.set_password("hunter2hunter2")
    db.session.commit()
    return member


def _signed_in(client, member):
    with client.session_transaction() as session:
        session["_user_id"] = str(member.user_id)
    return client


class TestTheAccountPageShowsBoth:
    def test_both_addresses_appear_with_their_state(self, app, client):
        member = _member()
        account = _signed_in(client, member).get("/api/v1/account").get_json()

        assert account["email"] == {"address": "private@example.com", "confirmed": True}
        # The university address was never shown at all.
        assert account["member"]["work_email"] == {"address": OLD_EMAIL, "confirmed": False}

    def test_it_says_the_university_one_restores_the_old_account(self, app, client):
        """The reason to bother, at the moment it matters."""
        member = _member(work_verified=False)
        account = _signed_in(client, member).get("/api/v1/account").get_json()

        assert "old forum" in account["to_confirm"]["text"]

    def test_nothing_is_outstanding_once_both_are_confirmed(self, app, client):
        member = _member(work_verified=True)
        account = _signed_in(client, member).get("/api/v1/account").get_json()

        assert account["to_confirm"] is None


class TestAskingForTheUniversityEmailAgain:
    def test_it_can_be_requested(self, app, client, monkeypatch):
        sent = []
        monkeypatch.setattr(
            "aeronautics_members.services.account.send_work_email_verification_email",
            lambda app_, member: sent.append(member.email_work) or True,
        )
        member = _member()

        response = _signed_in(client, member).post("/api/v1/account/emails/work/confirmation")

        assert response.status_code == 200
        assert sent == [OLD_EMAIL]

    def test_it_is_not_offered_once_confirmed(self, app, client, monkeypatch):
        sent = []
        monkeypatch.setattr(
            "aeronautics_members.services.account.send_work_email_verification_email",
            lambda app_, member: sent.append(member.email_work) or True,
        )
        member = _member(work_verified=True)

        response = _signed_in(client, member).post("/api/v1/account/emails/work/confirmation")

        assert response.status_code == 409
        assert sent == [], "already proved; sending again proves nothing"

    def test_a_failure_to_send_does_not_break_the_page(self, app, client, monkeypatch):
        def explode(app_, member):
            raise RuntimeError("the mail server is down")

        monkeypatch.setattr(
            "aeronautics_members.services.account.send_work_email_verification_email",
            explode,
        )
        member = _member()

        response = _signed_in(client, member).post("/api/v1/account/emails/work/confirmation")

        # Said, not a server error; and the page still opens.
        assert response.status_code == 502
        assert "could not send" in response.get_json()["error"]["message"]
        assert client.get("/api/v1/account").status_code == 200

    def test_signing_in_is_required(self, app, client):
        response = client.post("/api/v1/account/emails/work/confirmation")

        assert response.status_code == 401


class TestReconnectingAtSignIn:
    """The claim fires when an address is confirmed -- one single instant.

    Around 250 people walk that path in one October. Anything that goes wrong
    in that instant used to mean no second chance and no explanation, which is
    exactly what happened on the real server.
    """

    def _archived(self):
        import_forum_people([{
            "source_user_id": "645",
            "source_username": "PopovicA_L23",
            "source_email": OLD_EMAIL,
            "year_group": "LAV23",
            "post_count": 7,
        }])
        db.session.commit()
        return db.session.execute(
            db.select(ImportedForumProfile).filter_by(source_user_id="645")
        ).scalar_one()

    def test_somebody_missed_at_verification_reconnects_on_sign_in(self, app, client):
        profile = self._archived()
        _member(work_verified=True)  # verified, but never claimed

        client.post("/login", data={
            "email": "private@example.com", "password": "hunter2hunter2",
        }, follow_redirects=True)

        db.session.expire_all()
        assert profile.claimed_at is not None

    def test_an_ordinary_member_signs_in_untouched(self, app, client):
        member = _member(work_verified=True)
        user_id = member.user_id

        response = client.post("/login", data={
            "email": "private@example.com", "password": "hunter2hunter2",
        }, follow_redirects=True)

        assert response.status_code == 200
        assert db.session.get(User, user_id) is not None

    def test_a_broken_claim_never_blocks_the_sign_in(self, app, client, monkeypatch):
        """Getting into your account matters more than the reconnection."""
        def explode(user):
            raise RuntimeError("something went wrong deep in the claim")

        monkeypatch.setattr(
            "aeronautics_members.blueprints.auth.claim_archived_account", explode
        )
        self._archived()
        _member(work_verified=True)

        response = client.post("/login", data={
            "email": "private@example.com", "password": "hunter2hunter2",
        }, follow_redirects=True)

        assert response.status_code == 200
