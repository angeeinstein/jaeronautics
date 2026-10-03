"""Cancelling the association membership ends the teams on the same day.

Somebody who cancels their association membership -- on Stripe's billing
page, most likely -- stays a member to the end of what they paid for. Their
team memberships are set to end on that day too, their subscriptions told to
stop then without renewing, and they and the leads are told. Taking the
cancellation back lifts it again; leaving on one's own is left alone.
"""
from datetime import date, timedelta

import pytest

from conftest import db
from aeronautics_members.db_models import MembershipPeriod, NotificationEvent
from aeronautics_members.services import ConflictError, team_payments, teams
from aeronautics_members.services.clock import get_membership_today, start_of_day_unix
from aeronautics_members.services.periods import grant_period
from test_emails import FakeSMTP, _user_status_event, outbox  # noqa: F401
from test_team_payments import _charging, _paid_member, fake_stripe  # noqa: F401
from test_teams_flow import _led, _person, switched_on  # noqa: F401
from test_webhook import post_event

YEAR_END = date(get_membership_today().year, 12, 31)


def _covered_to_year_end(user):
    grant_period(user.member, get_membership_today() - timedelta(days=30), YEAR_END, MembershipPeriod.REASON_PAID)
    db.session.commit()


def _cancel_association(user):
    user.member.cancel_at_period_end = True
    user.member.payment_status = "cancel_scheduled"
    db.session.commit()


def _keep_association(user):
    user.member.cancel_at_period_end = False
    user.member.payment_status = "paid"
    db.session.commit()


def _emails(event_type, recipient=None):
    query = db.session.query(NotificationEvent).filter_by(event_type=event_type)
    if recipient:
        query = query.filter_by(recipient_email=recipient)
    return query.all()


def _modifications(fake):
    return [kwargs for name, _args, kwargs in fake.calls if name == "subscription.modify"]


@pytest.fixture
def stripe_record(fake_stripe):  # noqa: F811 -- the fixture of test_team_payments
    return fake_stripe


@pytest.fixture
def paying(app, client, monkeypatch, stripe_record, request):
    request.getfixturevalue("switched_on")
    team, lead = _led()
    _charging(team)
    anna, membership = _paid_member(client, monkeypatch, team, lead)
    _covered_to_year_end(anna)
    return team, lead, anna, membership


class TestCancellingTheAssociation:
    def test_the_team_ends_on_the_same_day_and_stops_renewing(self, paying, stripe_record):
        _team, lead, anna, membership = paying
        _cancel_association(anna)

        assert team_payments.follow_association_end(anna.member) == 1

        assert (membership.status, membership.ends_on, membership.ends_with_association) == (
            teams.ACTIVE, YEAR_END, True)
        assert _modifications(stripe_record)[-1] == {
            "cancel_at": start_of_day_unix(YEAR_END + timedelta(days=1)), "proration_behavior": "none"}
        assert _emails("team_ends_with_association", anna.email)
        assert _emails("team_member_leaving", lead.email)

    def test_once_is_enough(self, paying, stripe_record):
        _team, _lead, anna, _membership = paying
        _cancel_association(anna)
        team_payments.follow_association_end(anna.member)
        calls = len(_modifications(stripe_record))

        assert team_payments.follow_association_end(anna.member) == 0
        assert len(_modifications(stripe_record)) == calls
        assert len(_emails("team_ends_with_association", anna.email)) == 1

    def test_taking_it_back_lifts_it(self, paying, stripe_record):
        _team, lead, anna, membership = paying
        _cancel_association(anna)
        team_payments.follow_association_end(anna.member)
        _keep_association(anna)

        assert team_payments.follow_association_end(anna.member) == 1

        assert (membership.ends_on, membership.ends_with_association) == (None, False)
        assert _modifications(stripe_record)[-1] == {"cancel_at": "", "proration_behavior": "none"}
        assert _emails("team_member_staying", lead.email)

    def test_leaving_on_ones_own_is_left_alone(self, paying, stripe_record):
        team, _lead, anna, membership = paying
        teams.leave(anna, team)
        own = membership.ends_on
        _cancel_association(anna)

        assert team_payments.follow_association_end(anna.member) == 0
        assert (membership.ends_on, membership.ends_with_association) == (own, False)

    def test_staying_in_the_team_alone_is_not_offered(self, paying, client):
        from test_teams_flow import _login

        team, _lead, anna, _membership = paying
        _cancel_association(anna)
        team_payments.follow_association_end(anna.member)
        db.session.commit()

        with pytest.raises(ConflictError):
            teams.stay(anna, team)
        _login(client, anna.id)
        body = client.get("/teams").get_data(as_text=True)
        assert "Ends with your association membership on" in body and "Stay after all" not in body

    def test_when_the_day_comes_it_ends_as_the_associations_end(self, paying, client, monkeypatch, stripe_record):
        from test_team_payments import _subscription_event

        _team, lead, anna, membership = paying
        _cancel_association(anna)
        team_payments.follow_association_end(anna.member)
        db.session.commit()

        post_event(client, monkeypatch, _subscription_event(
            membership, "customer.subscription.deleted", "evt_end", status="canceled",
            cancellation_details={"reason": "cancellation_requested"}))

        db.session.refresh(membership)
        assert (membership.status, membership.end_reason) == (teams.ENDED, teams.END_MEMBERSHIP_ENDED)

    def test_the_night_catches_up_on_a_missed_webhook(self, paying, stripe_record):
        _team, _lead, anna, membership = paying
        _cancel_association(anna)

        assert team_payments.follow_association_ends() == 1
        assert membership.ends_with_association

    @pytest.mark.usefixtures("switched_on")
    def test_a_free_team_ends_too_without_asking_stripe(self, app, stripe_record):
        team, _lead = _led("Glider")
        anna = _person("glide@example.com")
        teams.join_or_apply(anna, team)
        teams.approve(None, team, teams.ongoing_membership(anna, team).id)
        _covered_to_year_end(anna)
        _cancel_association(anna)

        assert team_payments.follow_association_end(anna.member) == 1
        assert teams.ongoing_membership(anna, team).ends_on == YEAR_END
        assert not _modifications(stripe_record)


def test_cancelled_on_stripes_billing_page_reaches_the_teams(paying, client, monkeypatch, stripe_record):
    """The membership's own webhook, end to end: its cancellation arrives, the team follows."""
    _team, _lead, anna, membership = paying
    member = anna.member
    member.stripe_subscription_id = "sub_membership"
    member.stripe_customer_id = "cus_anna"
    db.session.commit()

    post_event(client, monkeypatch, {"id": "evt_cancel", "type": "customer.subscription.updated", "data": {"object": {
        "id": "sub_membership", "object": "subscription", "customer": "cus_anna", "status": "active",
        "metadata": {"purpose": "membership", "member_id": str(member.id)},
        "cancel_at_period_end": True, "cancel_at": None, "collection_method": "charge_automatically",
        "items": {"data": [{"current_period_end": start_of_day_unix(YEAR_END + timedelta(days=1))}]},
    }}})

    db.session.refresh(membership)
    assert member.cancel_at_period_end
    assert (membership.ends_on, membership.ends_with_association) == (YEAR_END, True)


@pytest.mark.usefixtures("outbox")
@pytest.mark.parametrize("event_type, subject", [
    ("team_ends_with_association", "Your membership in Rocket ends with your association membership"),
    ("team_member_staying", "A member stays in Rocket"),
])
def test_the_emails_go_out(app, event_type, subject):
    from aeronautics_members.services.notifications import get_notification_service

    service = get_notification_service()
    event = _user_status_event(event_type, team_name="Rocket", team_slug="rocket", ends_on="31.12.2026")
    with app.test_request_context():
        built_subject, template_vars = service._build_user_status_message(event)
        ok, _error = service._send_user_status_mail(event, built_subject, template_vars)

    assert ok and built_subject == subject
    assert FakeSMTP.sent
