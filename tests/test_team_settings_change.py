"""Changing how a team charges while it has members.

Set up once and seldom touched, but a change is handled rather than left to
go wrong: free from now on stops the subscriptions at the end of what is paid
and keeps the members, free; charging from now on asks the members to pay by
the next period start; the period dates are locked while subscriptions run on
them. Archiving or switching teams off is refused while anybody still pays,
and archiving asks for the team's name.
"""
from datetime import date, timedelta

import pytest

from api_helpers import send
from conftest import db
from aeronautics_members.db_models import NotificationEvent
from aeronautics_members.services import ConflictError, ValidationError, team_payments, teams
from aeronautics_members.services.clock import start_of_day_unix
from test_team_payments import (  # noqa: F401
    PRICE_ID, _approved, _charging, _paid_member, _subscription_event, fake_stripe,
)
from test_emails import outbox  # noqa: F401
from test_teams_flow import _led, _login, _person, switched_on  # noqa: F401
from test_webhook import post_event


@pytest.fixture
def stripe_calls(fake_stripe):  # noqa: F811 -- the fixture of test_team_payments
    return fake_stripe


def _save(team, **fields):
    values = {"payment_mode": "subscription", "stripe_price_id": PRICE_ID, "period_starts": "01.10, 01.04"}
    values.update(fields)
    return team_payments.update_payment_settings(None, team, **values)


def _emails(event_type, recipient=None):
    query = db.session.query(NotificationEvent).filter_by(event_type=event_type)
    if recipient:
        query = query.filter_by(recipient_email=recipient)
    return query.all()


@pytest.mark.usefixtures("switched_on")
class TestFreeFromNowOn:
    def test_subscriptions_stop_at_the_end_of_what_is_paid_and_the_members_stay(
            self, app, client, monkeypatch, stripe_calls):
        team, lead = _led()
        _charging(team)
        anna, membership = _paid_member(client, monkeypatch, team, lead)

        outcome = _save(team, payment_mode="none")

        assert outcome["stopping"] == 1
        assert stripe_calls.named("subscription.modify")[-1][2] == {"cancel_at_period_end": True}
        assert (membership.status, membership.payment_mode, membership.ends_on) == (teams.ACTIVE, "none", None)
        assert _emails("team_now_free", anna.email)

    def test_the_old_subscription_running_out_is_not_leaving(self, app, client, monkeypatch, stripe_calls):
        team, lead = _led()
        _charging(team)
        _anna, membership = _paid_member(client, monkeypatch, team, lead)
        _save(team, payment_mode="none")
        db.session.commit()

        post_event(client, monkeypatch, _subscription_event(
            membership, "customer.subscription.updated", "evt_stop", cancel_at_period_end=True,
            items={"data": [{"current_period_end": start_of_day_unix(date(2027, 4, 1))}]}))
        post_event(client, monkeypatch, _subscription_event(
            membership, "customer.subscription.deleted", "evt_gone", status="canceled",
            cancellation_details={"reason": "cancellation_requested"}))

        db.session.refresh(membership)
        assert (membership.status, membership.ends_on, membership.stripe_subscription_id) == (teams.ACTIVE, None, None)
        assert not _emails("team_member_leaving") and not _emails("team_member_left", lead.email)

    def test_somebody_approved_and_not_yet_paying_is_in_at_once(self, app, stripe_calls):
        team, lead = _led()
        _charging(team)
        anna, membership = _approved(team, lead)

        _save(team, payment_mode="none")

        assert membership.status == teams.ACTIVE
        assert _emails("team_approved", anna.email)


@pytest.mark.usefixtures("switched_on")
class TestChargingFromNowOn:
    def _free_member(self):
        team, lead = _led()
        anna = _person()
        teams.join_or_apply(anna, team)
        teams.approve(lead, team, teams.ongoing_membership(anna, team).id)
        db.session.commit()
        return team, lead, anna, teams.ongoing_membership(anna, team)

    def test_members_stay_free_until_the_next_period_and_are_asked_to_pay(self, app, client, stripe_calls):
        team, _lead, anna, membership = self._free_member()

        outcome = _save(team)

        free_until = team_payments.joining_period(team)["paid_until"]
        assert outcome["asked_to_pay"] == 2  # the lead pays too
        assert (membership.payment_mode, membership.paid_until) == ("subscription", free_until)
        assert team_payments.needs_to_pay(membership)
        [email] = _emails("team_now_charges", anna.email)
        assert email.payload["fee"] == "€10.00 every 6 months"
        _login(client, anna.id)
        [card] = client.get("/api/v1/teams").get_json()["mine"]
        assert "pay_stay" in card["membership"]["actions"]

    def test_paying_early_charges_nothing_today(self, app, stripe_calls):
        team, _lead, anna, membership = self._free_member()
        _save(team)

        team_payments.start_checkout(anna, team)

        [(_name, _args, params)] = stripe_calls.named("session.create")
        assert params["line_items"] == [{"price": PRICE_ID, "quantity": 1}]
        assert params["subscription_data"]["trial_end"] == start_of_day_unix(
            membership.paid_until + timedelta(days=1))

    def test_not_paid_by_the_period_start_ends_it(self, app, stripe_calls):
        team, _lead, anna, membership = self._free_member()
        _save(team)

        assert team_payments.end_finished_team_memberships(membership.paid_until + timedelta(days=1)) == 2  # and the lead

        assert (membership.status, membership.end_reason) == (teams.ENDED, teams.END_PAYMENT_FAILED)
        assert teams.join_or_apply(anna, team).status == teams.APPROVED  # back by paying


@pytest.mark.usefixtures("switched_on")
class TestWhatIsRefusedWhileAnybodyPays:
    def test_the_period_dates(self, app, client, monkeypatch, stripe_calls):
        team, lead = _led()
        _charging(team)
        _paid_member(client, monkeypatch, team, lead)

        with pytest.raises(ValidationError) as caught:
            _save(team, period_starts="15.10, 15.04")

        assert caught.value.code == "team_period_starts_locked"

    def test_archiving(self, app, client, monkeypatch, stripe_calls):
        team, lead = _led()
        _charging(team)
        _paid_member(client, monkeypatch, team, lead)

        with pytest.raises(ConflictError):
            teams.set_team_archived(None, team, True, confirmed_name=team.name)
        assert team.status == teams.STATUS_ACTIVE

    def test_switching_teams_off(self, app, client, monkeypatch, stripe_calls):
        team, lead = _led()
        _charging(team)
        _paid_member(client, monkeypatch, team, lead)

        with pytest.raises(ConflictError):
            teams.save_team_settings(None, enabled=False, label_singular="", label_plural="")
        assert teams.teams_enabled()


@pytest.mark.usefixtures("switched_on")
class TestArchivingAsksForTheName:
    def test_without_it_nothing_happens(self, app):
        team, _lead = _led()

        with pytest.raises(ValidationError):
            teams.set_team_archived(None, team, True, confirmed_name="Rockt")
        assert team.status == teams.STATUS_ACTIVE

    def test_the_page_asks_for_it(self, app, client):
        from test_admin_reviews import _staff

        team, _lead = _led()
        _login(client, _staff("admin@example.com", "admin").id)

        response = send(client, "PUT", f"/api/v1/admin/teams/{team.slug}/archived",
                        {"archived": True, "confirm_name": "nope"})
        assert response.status_code == 400
        assert response.get_json()["error"]["message"] == "Type the team's name to archive it."
        db.session.refresh(team)
        assert team.status == teams.STATUS_ACTIVE


@pytest.mark.usefixtures("outbox")
@pytest.mark.parametrize("event_type, subject", [
    ("team_now_free", "Rocket is free from now on"),
    ("team_now_charges", "Rocket charges a fee from 01.04.2027"),
])
def test_the_emails_go_out(app, event_type, subject):
    from test_emails import FakeSMTP, _user_status_event

    from aeronautics_members.services.notifications import get_notification_service

    service = get_notification_service()
    event = _user_status_event(event_type, team_name="Rocket", team_slug="rocket",
                               until="31.03.2027", fee="€10.00 every 6 months", from_date="01.04.2027")
    with app.test_request_context():
        built_subject, template_vars = service._build_user_status_message(event)
        ok, _error = service._send_user_status_mail(event, built_subject, template_vars)

    assert ok and built_subject == subject
    assert FakeSMTP.sent
