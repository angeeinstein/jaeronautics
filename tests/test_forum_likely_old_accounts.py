"""Old forum accounts under a mistyped address: pointed out, never taken.

The old forum never confirmed addresses. One returning student had typed
"firstnamelastname@edu..." there and signs up with "firstname.lastname@edu...":
the address the old account names does not exist, mail to it comes back, so it
can never be confirmed and the account never reconnects by itself. Matching it
automatically would be guessing whose posts to hand over -- so a near match is
shown to the admins, who reconnect by hand if they recognise the person.
"""
from datetime import datetime

from conftest import db, make_member
from aeronautics_members.db_models import ImportedForumProfile, NotificationEvent
from aeronautics_members.services.forum_import import (
    LIKELY_BY_ADDRESS,
    LIKELY_BY_NAME,
    claim_archived_account,
    import_forum_people,
    likely_old_accounts,
    report_likely_old_accounts,
)
from test_admin_reviews import _login, _staff


def _archived(email, username="BergerA_L22", uid="801", display_name=None):
    entry = {"source_user_id": uid, "source_username": username, "source_email": email, "year_group": "LAV22"}
    if display_name:
        entry["display_name"] = display_name
    import_forum_people([entry])
    db.session.commit()
    return db.session.execute(db.select(ImportedForumProfile).filter_by(source_user_id=uid)).scalar_one()


def _returning(work_email="anna.berger@edu.fh-joanneum.at", confirmed=True, first="Anna", last="Berger"):
    member = make_member(email="anna.private@example.com", first_name=first, last_name=last, year_group="LAV22")
    member.user.email_verified_at = datetime.utcnow()
    member.email_work = work_email
    if confirmed:
        member.email_work_verified_at = datetime.utcnow()
    db.session.commit()
    return member


class TestWhatCountsAsLikely:
    def test_the_address_without_its_dot(self, app):
        profile = _archived("annaberger@edu.fh-joanneum.at")
        member = _returning()

        assert claim_archived_account(member.user) is None  # no exact match: not reconnected
        assert likely_old_accounts(member.user) == [(profile, LIKELY_BY_ADDRESS)]

    def test_umlauts_spelled_out_case_and_the_other_university_domain(self, app):
        profile = _archived("Anna.Mueller@FH-Joanneum.at")
        member = _returning("anna.müller@edu.fh-joanneum.at", first="Anna", last="Müller")

        assert likely_old_accounts(member.user) == [(profile, LIKELY_BY_ADDRESS)]

    def test_only_from_a_confirmed_address(self, app):
        _archived("annaberger@edu.fh-joanneum.at")
        member = _returning(confirmed=False, first="Someone", last="Else")

        assert likely_old_accounts(member.user) == []

    def test_the_same_name(self, app):
        profile = _archived("a.b@gmx.at", display_name="Berger Anna")
        member = _returning("anna.berger@edu.fh-joanneum.at")

        assert likely_old_accounts(member.user) == [(profile, LIKELY_BY_NAME)]

    def test_not_somebody_else(self, app):
        _archived("annabergerx@edu.fh-joanneum.at", display_name="Anna Bergerx")
        member = _returning()

        assert likely_old_accounts(member.user) == []

    def test_not_the_exact_match_which_reconnects_by_itself(self, app):
        _archived("anna.berger@edu.fh-joanneum.at", display_name="Anna Berger")
        member = _returning()

        assert likely_old_accounts(member.user) == []


class TestTellingTheAdmins:
    def _events(self):
        return db.session.query(NotificationEvent).filter_by(event_type="forum_old_account_likely").all()

    def test_once_when_the_university_address_is_confirmed(self, app, client):
        from aeronautics_members.services.identity import build_work_email_verification_claims, generate_token

        _archived("annaberger@edu.fh-joanneum.at")
        member = _returning(confirmed=False)
        token = generate_token("verify-work-email", **build_work_email_verification_claims(member))
        db.session.commit()

        client.get(f"/verify-work-email/{token}")

        [event] = self._events()
        assert "BergerA_L22" in event.summary and event.target_user_id == member.user_id
        assert report_likely_old_accounts(member.user) is False  # not twice
        assert len(self._events()) == 1


class TestTheAccountPage:
    def test_shows_what_is_probably_theirs_with_a_reconnect_button(self, app, client):
        _archived("annaberger@edu.fh-joanneum.at")
        member = _returning()
        _login(client, _staff("boss@example.org", "admin").id)

        body = client.get(f"/admin/accounts/{member.user_id}").get_data(as_text=True)

        assert "Probably theirs" in body and "BergerA_L22" in body
        assert "Differs only in dots or spelling" in body and "Reconnect" in body


def test_the_command_lists_them(app):
    _archived("annaberger@edu.fh-joanneum.at")
    _returning()

    result = app.test_cli_runner().invoke(args=["forum-likely-old-accounts"])

    assert result.exit_code == 0, result.output
    assert "BergerA_L22" in result.output and "1 likely old account(s)." in result.output
