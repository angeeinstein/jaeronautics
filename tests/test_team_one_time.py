"""A team paid once per period, and switching between the two ways of paying.

One product in Stripe can hold both a recurring and a one-time price; the
team's settings say which is used. Paid once per period, joining pays the
period under way, nothing renews by itself, a reminder goes out two weeks
before the period ends, paying in advance keeps the membership without a gap,
and whoever does not pay leaves when it ends -- the leads told of everybody at
once -- and may come back by paying.
"""
from datetime import date, timedelta

import pytest

from conftest import db
from aeronautics_members.db_models import AuditLog, NotificationEvent, Payment
from aeronautics_members.services import ValidationError, team_payments, teams
from aeronautics_members.services.clock import get_membership_today, start_of_day_unix
from test_team_payments import (  # noqa: F401
    PRICE_ID, _approved, _charging, _paid_member, _subscription_event, fake_stripe,
)
from test_emails import outbox  # noqa: F401
from test_teams_flow import _led, _login, _person, switched_on  # noqa: F401
from test_webhook import post_event

ONE_TIME_PRICE = "price_once25"


@pytest.fixture
def stripe_fake(fake_stripe):  # noqa: F811 -- the fixture of test_team_payments
    fake_stripe.price = {"id": ONE_TIME_PRICE, "active": True, "currency": "eur", "unit_amount": 2500,
                         "product": "prod_rocket", "recurring": None}
    return fake_stripe


def _once_per_period(team, starts="01.10, 01.03"):
    team_payments.update_payment_settings(None, team, payment_mode="one_time",
                                          stripe_price_id=ONE_TIME_PRICE, period_starts=starts)
    db.session.commit()
    return team


def _session_event(event_id, event_type, membership, *, covers_until, payment_status="paid",
                   payment_intent="pi_1", amount=2500):
    return {"id": event_id, "type": event_type, "data": {"object": {
        "id": f"cs_{event_id}", "object": "checkout.session", "mode": "payment",
        "payment_status": payment_status, "payment_intent": payment_intent, "invoice": None,
        "customer": "cus_anna", "amount_total": amount, "currency": "eur",
        "metadata": {"purpose": "team", "team": membership.team.slug, "team_membership_id": str(membership.id),
                     "user_id": str(membership.user_id), "covers_until": covers_until.isoformat()},
    }}}


def _emails(event_type, recipient=None):
    query = db.session.query(NotificationEvent).filter_by(event_type=event_type)
    if recipient:
        query = query.filter_by(recipient_email=recipient)
    return query.all()


def _member_paid_once(client, monkeypatch, stripe_fake, *, pi="pi_1"):
    team, lead = _led()
    _once_per_period(team)
    anna, membership = _approved(team, lead)
    until = team_payments.joining_period(team)["paid_until"]
    post_event(client, monkeypatch, _session_event("evt_join", "checkout.session.completed", membership,
                                                   covers_until=until, payment_intent=pi))
    db.session.refresh(membership)
    return team, lead, anna, membership


@pytest.mark.usefixtures("switched_on")
class TestTheSettings:
    def test_a_one_time_price_and_semesters_of_different_lengths(self, app, stripe_fake):
        team, _lead = _led()

        _once_per_period(team, starts="01.10, 01.03")

        assert (team.payment_mode, team.period_starts, team.fee_display) == (
            "one_time", "01.03, 01.10", "€25.00 per period")

    def test_not_a_recurring_price(self, app, stripe_fake):
        team, _lead = _led()
        stripe_fake.price["recurring"] = {"interval": "month", "interval_count": 6}

        with pytest.raises(ValidationError) as caught:
            _once_per_period(team)

        assert "one-time price" in caught.value.message


@pytest.mark.usefixtures("switched_on")
class TestJoining:
    def test_the_checkout_is_a_payment_for_the_period_under_way(self, app, stripe_fake):
        team, lead = _led()
        _once_per_period(team)
        anna, membership = _approved(team, lead)

        team_payments.start_checkout(anna, team)

        [(_name, _args, params)] = stripe_fake.named("session.create")
        assert params["mode"] == "payment"
        assert params["line_items"] == [{"price": ONE_TIME_PRICE, "quantity": 1}]
        assert params["metadata"]["covers_until"] == team_payments.joining_period(team)["paid_until"].isoformat()
        assert params["payment_intent_data"]["metadata"]["purpose"] == "team"
        assert params["invoice_creation"]["enabled"] is True

    def test_paid_by_card_makes_a_member_at_once(self, app, client, monkeypatch, stripe_fake):
        _team, lead, anna, membership = _member_paid_once(client, monkeypatch, stripe_fake)

        assert (membership.status, membership.payment_state) == (teams.ACTIVE, team_payments.PAID)
        [payment] = db.session.query(Payment).all()
        assert (payment.stripe_payment_intent_id, payment.amount_cents) == ("pi_1", 2500)
        assert _emails("team_approved", anna.email) and _emails("team_member_joined", lead.email)

    def test_sepa_on_its_way_then_confirmed_once(self, app, client, monkeypatch, stripe_fake):
        team, lead = _led()
        _once_per_period(team)
        _anna, membership = _approved(team, lead)
        until = team_payments.joining_period(team)["paid_until"]

        post_event(client, monkeypatch, _session_event("evt_1", "checkout.session.completed", membership,
                                                       covers_until=until, payment_status="unpaid"))
        db.session.refresh(membership)
        assert (membership.status, membership.payment_state) == (teams.APPROVED, team_payments.PROCESSING)

        for event_id in ("evt_2", "evt_3"):  # Stripe may say it twice
            post_event(client, monkeypatch, _session_event(event_id, "checkout.session.async_payment_succeeded",
                                                           membership, covers_until=until))
        db.session.refresh(membership)
        assert membership.status == teams.ACTIVE and db.session.query(Payment).count() == 1

    def test_a_sepa_debit_that_fails(self, app, client, monkeypatch, stripe_fake):
        team, lead = _led()
        _once_per_period(team)
        _anna, membership = _approved(team, lead)
        until = team_payments.joining_period(team)["paid_until"]

        post_event(client, monkeypatch, _session_event("evt_f", "checkout.session.async_payment_failed",
                                                       membership, covers_until=until, payment_status="unpaid"))

        db.session.refresh(membership)
        assert (membership.status, membership.payment_state) == (teams.APPROVED, team_payments.FAILED)

    def test_the_receipt_invoice_is_no_alarm(self, app, client, monkeypatch, stripe_fake):
        _team, _lead, _anna, membership = _member_paid_once(client, monkeypatch, stripe_fake)

        post_event(client, monkeypatch, {"id": "evt_inv", "type": "invoice.paid", "data": {"object": {
            "id": "in_receipt", "object": "invoice", "amount_paid": 2500, "currency": "eur",
            "metadata": {"purpose": "team", "team_membership_id": str(membership.id)}, "lines": {"data": []},
        }}})

        assert not db.session.query(NotificationEvent).filter_by(event_type="team_payment_unmatched").count()
        assert db.session.query(Payment).count() == 1


@pytest.mark.usefixtures("switched_on")
class TestRenewing:
    def test_reminded_two_weeks_before_and_once(self, app, client, monkeypatch, stripe_fake):
        _team, _lead, anna, membership = _member_paid_once(client, monkeypatch, stripe_fake)
        today = membership.paid_until - timedelta(days=team_payments.RENEWAL_NOTICE_DAYS)

        assert team_payments.send_renewal_notices(today - timedelta(days=1)) == 0
        assert team_payments.send_renewal_notices(today) == 1
        assert team_payments.send_renewal_notices(today) == 0

        [email] = _emails("team_renewal_due", anna.email)
        assert email.payload["next_until"] == team_payments.next_period_until(membership).strftime("%d.%m.%Y")
        assert team_payments.renewal_open(membership, today)

    def test_paying_in_advance_keeps_it_without_a_gap(self, app, client, monkeypatch, stripe_fake):
        team, _lead, anna, membership = _member_paid_once(client, monkeypatch, stripe_fake)
        team_payments.send_renewal_notices(membership.paid_until - timedelta(days=10))
        db.session.commit()
        next_until = team_payments.next_period_until(membership)

        team_payments.start_checkout(anna, team)
        params = stripe_fake.named("session.create")[-1][2]
        assert params["metadata"]["covers_until"] == next_until.isoformat()
        post_event(client, monkeypatch, _session_event("evt_renew", "checkout.session.completed", membership,
                                                       covers_until=next_until, payment_intent="pi_2"))

        db.session.refresh(membership)
        assert (membership.status, membership.paid_until) == (teams.ACTIVE, next_until)
        team_payments.end_finished_team_memberships(next_until - timedelta(days=1))
        assert membership.status == teams.ACTIVE  # paid ahead; only the lead, who never paid, is gone

    def test_not_renewed_ends_it_and_the_leads_hear_of_everybody_at_once(
            self, app, client, monkeypatch, stripe_fake):
        team, lead, anna, membership = _member_paid_once(client, monkeypatch, stripe_fake)
        lead_membership = teams.ongoing_membership(lead, team)
        lead_membership.payment_mode, lead_membership.paid_until = "one_time", membership.paid_until
        db.session.commit()

        assert team_payments.end_finished_team_memberships(membership.paid_until + timedelta(days=1)) == 2

        assert (membership.status, membership.end_reason) == (teams.ENDED, teams.END_NOT_RENEWED)
        assert _emails("team_not_renewed", anna.email)
        assert len(_emails("team_members_lapsed")) == 1  # one summary; the lead is gone too, so the admins get it
        assert teams.join_or_apply(anna, team).status == teams.APPROVED  # back by paying

    def test_leaving_runs_to_the_end_of_what_is_paid(self, app, client, monkeypatch, stripe_fake):
        team, _lead, anna, membership = _member_paid_once(client, monkeypatch, stripe_fake)

        teams.leave(anna, team)

        assert (membership.status, membership.ends_on) == (teams.ACTIVE, membership.paid_until)
        assert not stripe_fake.named("subscription.modify")


@pytest.mark.usefixtures("switched_on")
def test_a_refund_comes_off_the_payment(app, client, monkeypatch, stripe_fake):
    import stripe

    _member_paid_once(client, monkeypatch, stripe_fake)
    # The charge names only its payment; Stripe is asked whose that is.
    monkeypatch.setattr(stripe.PaymentIntent, "retrieve", staticmethod(
        lambda pi_id, **k: {"id": pi_id, "object": "payment_intent", "metadata": {"purpose": "team"}}))

    response = post_event(client, monkeypatch, {"id": "evt_ref", "type": "charge.refunded", "data": {"object": {
        "id": "ch_1", "object": "charge", "payment_intent": "pi_1", "amount": 2500, "amount_refunded": 1000,
        "metadata": {},
    }}})

    assert response.status_code == 200
    assert db.session.query(Payment).one().refunded_cents == 1000


@pytest.mark.usefixtures("switched_on")
class TestSwitchingTheWayOfPaying:
    def test_from_subscription_to_once_per_period(self, app, client, monkeypatch, stripe_fake):
        team, lead = _led()
        stripe_fake.price = {"id": PRICE_ID, "active": True, "currency": "eur", "unit_amount": 1000,
                             "product": "prod_rocket", "recurring": {"interval": "month", "interval_count": 6}}
        _charging(team)
        anna, membership = _paid_member(client, monkeypatch, team, lead)
        stripe_fake.price = {"id": ONE_TIME_PRICE, "active": True, "currency": "eur", "unit_amount": 2500,
                             "product": "prod_rocket", "recurring": None}

        outcome = team_payments.update_payment_settings(None, team, payment_mode="one_time",
                                                        stripe_price_id=ONE_TIME_PRICE, period_starts="01.10, 01.04")
        db.session.commit()

        assert outcome["switched"] == 1
        assert stripe_fake.named("subscription.modify")[-1][2] == {"cancel_at_period_end": True}
        assert membership.payment_mode == "one_time" and _emails("team_now_once_per_period", anna.email)
        post_event(client, monkeypatch, _subscription_event(
            membership, "customer.subscription.deleted", "evt_gone", status="canceled",
            cancellation_details={"reason": "cancellation_requested"}))
        db.session.refresh(membership)
        assert membership.status == teams.ACTIVE  # the subscription ran out; the membership did not

    def test_from_once_per_period_to_subscription(self, app, client, monkeypatch, stripe_fake):
        team, _lead, anna, membership = _member_paid_once(client, monkeypatch, stripe_fake)
        paid_until = membership.paid_until
        stripe_fake.price = {"id": PRICE_ID, "active": True, "currency": "eur", "unit_amount": 1000,
                             "product": "prod_rocket", "recurring": {"interval": "month", "interval_count": 6}}

        outcome = team_payments.update_payment_settings(None, team, payment_mode="subscription",
                                                        stripe_price_id=PRICE_ID, period_starts="01.10, 01.04")

        assert outcome["switched"] == 2  # Anna and the lead
        assert (membership.payment_mode, membership.paid_until) == ("subscription", paid_until)
        assert team_payments.needs_to_pay(membership)
        assert _emails("team_now_subscription", anna.email)


@pytest.mark.usefixtures("outbox")
@pytest.mark.parametrize("event_type, subject", [
    ("team_renewal_due", "Rocket: pay for the next period"),
    ("team_not_renewed", "Your membership in Rocket has ended"),
    ("team_now_once_per_period", "Rocket is paid once per period from now on"),
    ("team_now_subscription", "Rocket is paid by subscription from 01.04.2027"),
])
def test_the_emails_go_out(app, event_type, subject):
    from test_emails import FakeSMTP, _user_status_event

    from aeronautics_members.services.notifications import get_notification_service

    service = get_notification_service()
    event = _user_status_event(event_type, team_name="Rocket", team_slug="rocket", fee="€25.00 per period",
                               until="31.03.2027", next_until="30.09.2027", from_date="01.04.2027")
    with app.test_request_context():
        built_subject, template_vars = service._build_user_status_message(event)
        ok, _error = service._send_user_status_mail(event, built_subject, template_vars)

    assert ok and built_subject == subject
    assert FakeSMTP.sent


_ = (date, start_of_day_unix, get_membership_today, AuditLog, _login, _person)
