"""A person's picture, the same everywhere (services/pictures.py): approved
here, else the old forum's; nothing for an erased account. Shown in the top
bar, the team lists and the admins' Accounts list."""
from datetime import datetime, timedelta

from conftest import db, make_member
from aeronautics_members.db_models import ForumAvatarSubmission
from aeronautics_members.services.forum_import import claim_archived_account
from aeronautics_members.services.pictures import picture_url, picture_urls
from api_helpers import signed_in
from test_admin_reviews import _staff
from test_forum_account_claim import _archived, _returning


def _approved(member, token, *, days_ago=0, status="approved"):
    db.session.add(ForumAvatarSubmission(
        user_id=member.user.id, member_id=member.id, status=status, public_token=token,
        uploaded_at=datetime.utcnow() - timedelta(days=days_ago),
    ))
    db.session.commit()


def _old_picture(profile, token="old-face"):
    profile.avatar_path = "/srv/imported/face.jpg"
    profile.avatar_public_token = token
    db.session.commit()


class TestWhichPicture:
    def test_the_one_approved_here(self, app):
        member = make_member(email="anna@example.com")
        _approved(member, "older", days_ago=30, status="superseded")
        _approved(member, "now")

        with app.test_request_context():
            assert picture_url(member.user).endswith("/now")

    def test_not_one_waiting_for_review(self, app):
        member = make_member(email="anna@example.com")
        _approved(member, "waiting", status="pending")

        with app.test_request_context():
            assert picture_url(member.user) is None

    def test_a_returning_student_keeps_the_old_forums(self, app):
        profile = _archived()
        _old_picture(profile)
        claim_archived_account(_returning().user)
        db.session.commit()

        with app.test_request_context():
            assert picture_url(profile.user).endswith("/forum/avatar/imported/old-face")

    def test_one_approved_here_replaces_the_old_forums(self, app):
        profile = _archived()
        _old_picture(profile)
        claim_archived_account(_returning().user)
        db.session.commit()
        _approved(profile.user.member, "new-face")

        with app.test_request_context():
            assert picture_url(profile.user).endswith("/new-face")

    def test_nothing_for_an_erased_account(self, app):
        member = make_member(email="anna@example.com")
        _approved(member, "now")
        member.user.deleted_at = datetime.utcnow()
        db.session.commit()

        with app.test_request_context():
            assert picture_url(member.user) is None

    def test_a_list_in_two_queries(self, app):
        people = [make_member(email=f"p{i}@example.com") for i in range(5)]
        for i, member in enumerate(people):
            _approved(member, f"face-{i}")
        users = [member.user for member in people]
        assert all(user.deleted_at is None for user in users)  # loaded before counting
        statements = []

        from sqlalchemy import event

        def count(*_args, **_kwargs):
            statements.append(1)

        event.listen(db.engine, "before_cursor_execute", count)
        try:
            with app.test_request_context():
                pictures = picture_urls(users)
        finally:
            event.remove(db.engine, "before_cursor_execute", count)

        assert len(pictures) == 5
        assert len(statements) == 2


class TestWhereItShows:
    def test_the_top_bar(self, client, app):
        member = make_member(email="anna@example.com")
        _approved(member, "now")

        me = signed_in(client, member.user).get("/api/v1/me").get_json()

        assert me["picture_url"].endswith("/now")

    def test_the_accounts_list(self, client, app):
        member = make_member(email="anna@example.com")
        _approved(member, "now")
        admin = _staff("boss@example.org", "admin")

        rows = signed_in(client, admin).get("/api/v1/admin/accounts?q=anna").get_json()["items"]

        assert [row["picture_url"].endswith("/now") for row in rows] == [True]
