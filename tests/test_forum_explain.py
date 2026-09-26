"""forum-explain: one person, as the portal and the forum each see them.

Found needed on 2026-09-26: a role that did not reach the forum, a reconnected
member with a second forum account holding their address, and a hand-typed
Python snippet to tell the causes apart. This reads both sides and says.
"""
from click.testing import CliRunner

from conftest import db, make_member

from aeronautics_members import app as app_module


class FakeProvider:
    def __init__(self, others=()):
        self.others = list(others)

    def build_sso_payload(self, user, member, desired_state, nonce, **kwargs):
        return {"external_id": str(user.id), "username": user.forum_username,
                "email": user.email, "add_groups": "members"}

    def get_remote_user_by_external_id(self, external_id):
        return {"id": 645, "username": "obermuellerB_L24", "admin": False,
                "moderator": False, "groups": [{"name": "members"}]}

    def _request(self, method, path, **kwargs):
        return [{"id": 645, "username": "obermuellerB_L24"}] + self.others


class FakeService:
    def __init__(self, provider):
        self.provider = provider

    def is_ready(self):
        return True

    def get_desired_state(self, member):
        return "active"


def _run(app, monkeypatch, who, others=()):
    monkeypatch.setattr(app_module, "get_forum_service",
                        lambda: FakeService(FakeProvider(others)))
    return CliRunner().invoke(app.cli.commands["forum-explain"], [who])


def test_it_says_when_the_portal_is_not_managing_the_flags(app, monkeypatch):
    make_member(email="bianca@example.com")
    db.session.commit()

    result = _run(app, monkeypatch, "bianca@example.com")

    assert result.exit_code == 0, result.output
    assert "not sent" in result.output


def test_a_second_account_holding_the_address_is_named(app, monkeypatch):
    """The account signup made, left behind when the old one was reclaimed."""
    make_member(email="bianca@example.com")
    db.session.commit()

    result = _run(app, monkeypatch, "bianca@example.com", others=[
        {"id": 1510, "username": "ObermullerB_L24", "active": False, "post_count": 0},
    ])

    assert "ObermullerB_L24" in result.output
    assert "one account only" in result.output


def test_nobody_by_that_name_is_a_sentence(app, monkeypatch):
    result = _run(app, monkeypatch, "nobody@example.com")
    assert result.exit_code != 0
    assert "Nobody here" in result.output
