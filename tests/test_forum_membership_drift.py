"""A membership that ends because its last day passed tells nobody.

Everything else that changes what somebody may read on the forum is an event
that somebody causes -- a payment lands, an administrator switches an account
off, a photograph is approved -- and each of those syncs the forum where it
happens. The passage of time causes nothing. Nobody clicks, nothing is written,
no code runs, and the person goes on reading thirteen years of exam papers
until somebody happens to open their record.

The portal itself is never wrong about this: access is decided by date on every
request. It is the copy on the forum that goes stale, and only a sweep that
asks the question can find it.
"""
from datetime import date, timedelta

import pytest

from conftest import db, make_member

from aeronautics_members.db_models import ForumAccount
from aeronautics_members.forum_service import (
    FORUM_STATE_ACTIVE,
    FORUM_STATE_ANONYMISED,
    FORUM_STATE_INACTIVE,
)
from aeronautics_members.services.forum import members_whose_forum_state_has_drifted

TODAY = date.today()


def _member_with_forum_account(email, state, **overrides):
    member = make_member(email=email, **overrides)
    db.session.add(ForumAccount(
        user=member.user, member=member, provider="discourse",
        external_id=str(member.user.id), state=state,
    ))
    db.session.commit()
    return member


@pytest.fixture
def lapsed_yesterday(app):
    """Paid, covered, and their last day was yesterday. Nothing has run since."""
    return _member_with_forum_account(
        "lapsed@example.com", FORUM_STATE_ACTIVE,
        payment_status="paid", is_active=True,
        membership_ends_on=TODAY - timedelta(days=1),
    )


class TestFindingWhatTheForumIsWrongAbout:
    def test_a_membership_that_ended_yesterday_is_found(self, app, lapsed_yesterday):
        drifted = members_whose_forum_state_has_drifted()
        assert [(member.id, was, should) for member, was, should in drifted] == [
            (lapsed_yesterday.id, FORUM_STATE_ACTIVE, FORUM_STATE_INACTIVE)
        ]

    def test_a_membership_still_covered_is_not(self, app):
        _member_with_forum_account(
            "current@example.com", FORUM_STATE_INACTIVE,
            payment_status="paid", is_active=True,
            membership_ends_on=TODAY + timedelta(days=30),
        )
        # It has no approved photograph, so onboarding is what it should be --
        # which is drift of a different kind, and still drift.
        drifted = members_whose_forum_state_has_drifted()
        assert [should for _member, _was, should in drifted] == ["onboarding"]

    def test_one_the_forum_already_agrees_about_costs_no_call(self, app):
        """The ordinary case, and it must stay free: 749 of 750 every night."""
        _member_with_forum_account(
            "agreed@example.com", FORUM_STATE_INACTIVE,
            payment_status="unpaid", is_active=False,
            membership_ends_on=TODAY - timedelta(days=400),
        )
        assert members_whose_forum_state_has_drifted() == []

    def test_somebody_who_has_never_had_a_forum_account_is_left_alone(self, app):
        """Not drift: there is nothing on the forum to be out of date."""
        make_member(email="never@example.com", payment_status="paid",
                    is_active=True, membership_ends_on=TODAY - timedelta(days=1))
        db.session.commit()
        assert members_whose_forum_state_has_drifted() == []

    def test_an_erased_account_is_never_put_back(self, app):
        """Anonymised is terminal. Syncing would undo the erasure."""
        _member_with_forum_account(
            "erased@example.com", FORUM_STATE_ANONYMISED,
            payment_status="paid", is_active=True,
            membership_ends_on=TODAY + timedelta(days=30),
        )
        assert members_whose_forum_state_has_drifted() == []

    def test_a_deleted_member_is_skipped(self, app):
        from aeronautics_members.services.clock import get_now_utc

        member = _member_with_forum_account(
            "gone@example.com", FORUM_STATE_ACTIVE,
            payment_status="paid", is_active=True,
            membership_ends_on=TODAY - timedelta(days=1),
        )
        member.deleted_at = get_now_utc()
        db.session.commit()
        assert members_whose_forum_state_has_drifted() == []

    def test_an_account_switched_off_while_still_paid_is_found(self, app):
        """Barring somebody here means nothing there until this notices."""
        from aeronautics_members.services.clock import get_now_utc

        member = _member_with_forum_account(
            "barred@example.com", FORUM_STATE_ACTIVE,
            payment_status="paid", is_active=True,
            membership_ends_on=TODAY + timedelta(days=30),
        )
        member.user.disabled_at = get_now_utc()
        db.session.commit()
        drifted = members_whose_forum_state_has_drifted()
        assert [should for _m, _was, should in drifted] == [FORUM_STATE_INACTIVE]


class TestTheCommand:
    def test_it_reports_having_nothing_to_do(self, app):
        result = app.test_cli_runner().invoke(
            args=["sync-forum-members", "--only-changed"]
        )
        assert result.exit_code == 0
        assert "Nothing has drifted" in result.output

    def test_it_names_what_it_brought_up_to_date(self, app, lapsed_yesterday):
        result = app.test_cli_runner().invoke(
            args=["sync-forum-members", "--only-changed"]
        )
        assert result.exit_code == 0
        assert f"member {lapsed_yesterday.id}: active -> inactive" in result.output
        assert "1 brought up to date" in result.output

    def test_and_the_forum_account_now_says_so(self, app, lapsed_yesterday):
        app.test_cli_runner().invoke(args=["sync-forum-members", "--only-changed"])
        db.session.expire_all()
        assert lapsed_yesterday.user.forum_account.state == FORUM_STATE_INACTIVE

    def test_running_it_again_finds_nothing(self, app, lapsed_yesterday):
        app.test_cli_runner().invoke(args=["sync-forum-members", "--only-changed"])
        result = app.test_cli_runner().invoke(
            args=["sync-forum-members", "--only-changed"]
        )
        assert "Nothing has drifted" in result.output
