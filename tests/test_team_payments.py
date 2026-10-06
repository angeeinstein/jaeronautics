"""Teams that charge: settings, paying, what Stripe reports, and the endings.

Stripe is faked: every call the portal makes is recorded, and every event is
put through the real webhook route. A team's events are marked
``purpose: team`` and must reach the team's handler, never the membership's.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from api_helpers import send
from conftest import db
from aeronautics_members.db_models import MembershipPeriod, NotificationEvent, Payment, Setting
from aeronautics_members.services import ConflictError, ValidationError, payments, privacy, team_payments, teams
from aeronautics_members.services.clock import get_membership_today, start_of_day_unix
from test_admin_reviews import _staff
from test_teams_flow import _led, _login, _person, switched_on  # noqa: F401
from test_emails import FakeSMTP, _user_status_event, outbox  # noqa: F401
from test_webhook import post_event

PRICE_ID = "price_team6m"


class FakeStripe:
    def __init__(self):
        self.calls = []
        self.price = {
            "id": PRICE_ID, "active": True, "currency": "eur", "unit_amount": 1000,
            "product": "prod_rocket", "recurring": {"interval": "month", "interval_count": 6},
        }
        self.sessions = {}

    def record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))

    def named(self, name):
        return [call for call in self.calls if call[0] == name]


@pytest.fixture
def fake_stripe(monkeypatch):
    import stripe

    fake = FakeStripe()

    def create_session(idempotency_key=None, **params):
        fake.record("session.create", idempotency_key=idempotency_key, **params)
        session = {"id": f"cs_{len(fake.sessions) + 1}", "url": f"https://checkout.example/{len(fake.sessions) + 1}",
                   "status": "open"}
        fake.sessions[session["id"]] = session
        return session

    def retrieve_session(session_id, *a, **k):
        return fake.sessions[session_id]

    monkeypatch.setattr(stripe.Price, "retrieve", staticmethod(lambda price_id, **k: dict(fake.price, id=price_id)))
    monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(create_session))
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", staticmethod(retrieve_session))
    monkeypatch.setattr(stripe.checkout.Session, "expire",
                        staticmethod(lambda session_id, **k: fake.record("session.expire", session_id)))
    monkeypatch.setattr(stripe.Subscription, "cancel",
                        staticmethod(lambda sub_id, **k: fake.record("subscription.cancel", sub_id, **k)))
    monkeypatch.setattr(stripe.Subscription, "modify",
                        staticmethod(lambda sub_id, **k: fake.record("subscription.modify", sub_id, **k)))
    monkeypatch.setattr(stripe.Customer, "modify", staticmethod(lambda customer_id, **k: {"id": customer_id}))
    return fake


def _charging(team):
    team.payment_mode = team_payments.PAYMENT_SUBSCRIPTION
    team.stripe_price_id = PRICE_ID
    team.period_starts = "01.10, 01.04"
    team.fee_display = "€10.00 every 6 months"
    db.session.commit()
    return team


def _approved(team, lead, email="anna@example.com"):
    anna = _person(email)
    membership = teams.join_or_apply(anna, team)
    if membership.status != teams.APPROVED:
        teams.approve(lead, team, membership.id)
    db.session.commit()
    return anna, membership


def _metadata(membership):
    return {"purpose": "team", "team": membership.team.slug,
            "team_membership_id": str(membership.id), "user_id": str(membership.user_id)}


def _invoice(membership, invoice_id="in_1", *, until, amount=1000, subscription="sub_1"):
    return {
        "id": f"evt_{invoice_id}", "type": "invoice.paid",
        "data": {"object": {
            "id": invoice_id, "object": "invoice", "customer": "cus_anna", "amount_paid": amount,
            "currency": "eur", "billing_reason": "subscription_create",
            "parent": {"subscription_details": {"subscription": subscription, "metadata": _metadata(membership)}},
            "lines": {"data": [{"period": {"start": 1, "end": start_of_day_unix(until + timedelta(days=1))}}]},
        }},
    }


def _subscription_event(membership, event_type, event_id, **fields):
    subscription = {"id": "sub_1", "object": "subscription", "metadata": _metadata(membership), "status": "active"}
    subscription.update(fields)
    return {"id": event_id, "type": event_type, "data": {"object": subscription}}


def _events(event_type, recipient=None):
    query = db.session.query(NotificationEvent).filter_by(event_type=event_type)
    if recipient:
        query = query.filter_by(recipient_email=recipient)
    return query.all()


def _paid_member(client, monkeypatch, team, lead, until=date(2027, 3, 31)):
    anna, membership = _approved(team, lead)
    assert post_event(client, monkeypatch, _invoice(membership, until=until)).status_code == 200
    db.session.refresh(membership)
    return anna, membership


# --- Settings ----------------------------------------------------------------------


@pytest.mark.usefixtures("switched_on")
class TestTheSettings:
    def _save(self, team, **fields):
        values = {"payment_mode": "subscription", "stripe_price_id": PRICE_ID, "period_starts": "01.10, 01.04"}
        values.update(fields)
        return team_payments.update_payment_settings(None, team, **values)

    def test_a_six_month_price_with_two_periods(self, app, fake_stripe):
        team, _lead = _led()

        self._save(team)

        assert (team.payment_mode, team.stripe_price_id, team.period_starts) == ("subscription", PRICE_ID, "01.04, 01.10")
        assert team.fee_display == "€10.00 every 6 months"

    @pytest.mark.parametrize("fields, code", [
        ({"stripe_price_id": ""}, "team_price_missing"),
        ({"period_starts": ""}, "team_period_starts_missing"),
        ({"period_starts": "01.10, 15.03"}, "team_period_starts_uneven"),
        ({"period_starts": "01.10"}, "team_price_unsuitable"),
    ])
    def test_what_does_not_fit_is_refused(self, app, fake_stripe, fields, code):
        team, _lead = _led()

        with pytest.raises(ValidationError) as caught:
            self._save(team, **fields)

        assert caught.value.code == code

    def test_not_the_membership_price(self, app, fake_stripe):
        db.session.add(Setting(key="stripe_price_id", value=PRICE_ID))
        team, _lead = _led()

        with pytest.raises(ValidationError) as caught:
            self._save(team)

        assert caught.value.code == "team_price_is_membership"

    def test_the_admin_form_saves_it(self, app, client, fake_stripe):
        team, _lead = _led()
        _login(client, _staff("admin@example.com", "admin").id)

        send(client, "PUT", f"/api/v1/admin/teams/{team.slug}/fee", {
            "payment_mode": "subscription", "stripe_price_id": PRICE_ID, "period_starts": "1.4, 1.10",
        })

        db.session.refresh(team)
        assert (team.payment_mode, team.period_starts) == ("subscription", "01.04, 01.10")

    def test_the_leads_settings_leave_it_alone(self, app, client, fake_stripe):
        team, lead = _led()
        _charging(team)
        _login(client, lead.id)

        client.post(f"/teams/{team.slug}/manage/settings", data={"description": "New."})

        db.session.refresh(team)
        assert team.payment_mode == "subscription"


class TestThePeriods:
    def _team(self):
        class T:
            period_starts = "01.10, 01.04"
        return T()

    @pytest.mark.parametrize("today, paid_until, charged_from", [
        (date(2026, 10, 3), date(2027, 3, 31), date(2027, 4, 1)),
        (date(2027, 2, 15), date(2027, 3, 31), date(2027, 4, 1)),
        # In the last days before a period, paying covers the coming one.
        (date(2027, 3, 30), date(2027, 9, 30), date(2027, 10, 1)),
        (date(2026, 9, 29), date(2027, 3, 31), date(2027, 4, 1)),
        (date(2026, 9, 28), date(2026, 9, 30), date(2026, 10, 1)),
    ])
    def test_what_paying_today_buys(self, today, paid_until, charged_from):
        period = team_payments.joining_period(self._team(), today)

        assert (period["paid_until"], period["charged_from"]) == (paid_until, charged_from)


# --- Paying ------------------------------------------------------------------------------


@pytest.mark.usefixtures("switched_on")
class TestPaying:
    def test_approval_asks_for_the_payment(self, app, fake_stripe):
        team, lead = _led()
        _charging(team)

        anna, membership = _approved(team, lead)

        assert membership.status == teams.APPROVED
        assert _events("team_payment_due", anna.email) and not _events("team_approved", anna.email)

    def test_checkout_charges_the_period_and_starts_the_subscription(self, app, fake_stripe):
        team, lead = _led()
        _charging(team)
        anna, membership = _approved(team, lead)

        url = team_payments.start_checkout(anna, team)

        [(_name, _args, params)] = fake_stripe.named("session.create")
        one_off, recurring = params["line_items"]
        assert url.startswith("https://checkout.example/")
        assert one_off["price_data"] == {"currency": "eur", "product": "prod_rocket", "unit_amount": 1000}
        assert recurring == {"price": PRICE_ID, "quantity": 1}
        charged_from = team_payments.joining_period(team)["charged_from"]
        assert params["subscription_data"]["trial_end"] == start_of_day_unix(charged_from)
        assert params["metadata"]["purpose"] == params["subscription_data"]["metadata"]["purpose"] == "team"
        assert params["metadata"]["team_membership_id"] == str(membership.id)
        assert membership.stripe_checkout_session_id == "cs_1"

    def test_the_open_checkout_is_used_again(self, app, fake_stripe):
        team, lead = _led()
        _charging(team)
        anna, _membership = _approved(team, lead)

        first = team_payments.start_checkout(anna, team)
        second = team_payments.start_checkout(anna, team)

        assert first == second and len(fake_stripe.named("session.create")) == 1

    def test_the_page_sends_the_person_to_stripe(self, app, client, fake_stripe):
        team, lead = _led()
        _charging(team)
        anna, _membership = _approved(team, lead)
        _login(client, anna.id)

        assert "Pay and join" in client.get("/teams").get_data(as_text=True)
        response = client.post(f"/teams/{team.slug}/pay")

        assert response.status_code == 303 and response.location.startswith("https://checkout.example/")

    def test_a_free_team_has_nothing_to_pay(self, app, fake_stripe):
        team, lead = _led()
        anna = _person()
        teams.join_or_apply(anna, team)
        teams.approve(lead, team, teams.ongoing_membership(anna, team).id)

        with pytest.raises(ConflictError):
            team_payments.start_checkout(anna, team)


# --- What Stripe reports ------------------------------------------------------------------


@pytest.mark.usefixtures("switched_on")
class TestWhatStripeReports:
    def test_the_first_payment_makes_a_member(self, app, client, monkeypatch, fake_stripe):
        team, lead = _led()
        _charging(team)
        before = db.session.query(MembershipPeriod).count()

        anna, membership = _paid_member(client, monkeypatch, team, lead)

        assert (membership.status, membership.paid_until, membership.payment_state) == (
            teams.ACTIVE, date(2027, 3, 31), team_payments.PAID)
        [payment] = db.session.query(Payment).all()
        assert (payment.purpose, payment.amount_cents, payment.covers_until) == ("team", 1000, date(2027, 3, 31))
        assert _events("team_approved", anna.email) and _events("team_member_joined", lead.email)
        # The association membership did not hear of it.
        assert db.session.query(MembershipPeriod).count() == before

    def test_the_same_payment_twice_is_recorded_once(self, app, client, monkeypatch, fake_stripe):
        team, lead = _led()
        _charging(team)
        _anna, membership = _paid_member(client, monkeypatch, team, lead)
        again = _invoice(membership, until=date(2027, 3, 31))
        again["id"], again["type"] = "evt_again", "invoice.payment_succeeded"

        post_event(client, monkeypatch, again)

        assert db.session.query(Payment).count() == 1

    def test_a_renewal_moves_paid_until_on(self, app, client, monkeypatch, fake_stripe):
        team, lead = _led()
        _charging(team)
        _anna, membership = _paid_member(client, monkeypatch, team, lead)

        post_event(client, monkeypatch, _invoice(membership, "in_2", until=date(2027, 9, 30)))

        db.session.refresh(membership)
        assert membership.paid_until == date(2027, 9, 30) and db.session.query(Payment).count() == 2

    def test_a_sepa_checkout_is_on_its_way(self, app, client, monkeypatch, fake_stripe):
        team, lead = _led()
        _charging(team)
        anna, membership = _approved(team, lead)
        membership.approved_at = datetime.now(timezone.utc) - timedelta(days=20)
        db.session.commit()

        post_event(client, monkeypatch, {"id": "evt_cs", "type": "checkout.session.completed", "data": {"object": {
            "id": "cs_1", "object": "checkout.session", "metadata": _metadata(membership),
            "customer": "cus_anna", "subscription": "sub_1", "payment_status": "unpaid",
        }}})

        db.session.refresh(membership)
        assert (membership.payment_state, membership.stripe_subscription_id) == (team_payments.PROCESSING, "sub_1")
        assert anna.member.stripe_customer_id in (None, "cus_anna")
        # Not lapsed for being unpaid: the money is on its way.
        assert teams.lapse_unpaid_approvals() == 0

    def test_a_failed_renewal_and_stripe_giving_up(self, app, client, monkeypatch, fake_stripe):
        team, lead = _led()
        _charging(team)
        anna, membership = _paid_member(client, monkeypatch, team, lead)

        post_event(client, monkeypatch, _subscription_event(membership, "customer.subscription.updated", "evt_pd",
                                                            status="past_due"))
        db.session.refresh(membership)
        assert (membership.status, membership.payment_state) == (teams.ACTIVE, team_payments.FAILED)

        post_event(client, monkeypatch, _subscription_event(
            membership, "customer.subscription.deleted", "evt_del", status="canceled",
            cancellation_details={"reason": "payment_failed"}))

        db.session.refresh(membership)
        assert (membership.status, membership.end_reason) == (teams.ENDED, teams.END_PAYMENT_FAILED)
        assert _events("team_payment_ended", anna.email)

    def test_coming_back_after_that_is_paying_again(self, app, client, monkeypatch, fake_stripe):
        team, lead = _led()
        _charging(team)
        anna, membership = _paid_member(client, monkeypatch, team, lead)
        post_event(client, monkeypatch, _subscription_event(
            membership, "customer.subscription.deleted", "evt_del", status="canceled",
            cancellation_details={"reason": "payment_failed"}))

        again = teams.join_or_apply(anna, team, "Back again.")

        assert again.status == teams.APPROVED

    def test_after_a_removal_it_is_a_new_application(self, app, client, monkeypatch, fake_stripe):
        team, lead = _led()
        _charging(team)
        anna, membership = _paid_member(client, monkeypatch, team, lead)
        teams.remove(lead, team, membership.id, "Vanished.")

        assert teams.join_or_apply(anna, team, "Sorry.").status == teams.APPLIED

    def test_a_membership_event_still_goes_to_the_membership(self, app):
        from aeronautics_members.services.stripe_scope import Scope, OURS

        assert payments.handler_for(Scope(OURS, "marked as the membership")) is None


# --- Ending ----------------------------------------------------------------------------


@pytest.mark.usefixtures("switched_on")
class TestEnding:
    def test_leaving_runs_to_the_end_of_the_paid_period(self, app, client, monkeypatch, fake_stripe):
        team, lead = _led()
        _charging(team)
        anna, membership = _paid_member(client, monkeypatch, team, lead)

        teams.leave(anna, team)

        assert (membership.status, membership.ends_on) == (teams.ACTIVE, date(2027, 3, 31))
        assert fake_stripe.named("subscription.modify")[-1][2] == {"cancel_at_period_end": True}
        assert _events("team_member_leaving", lead.email)

    def test_staying_after_all(self, app, client, monkeypatch, fake_stripe):
        team, lead = _led()
        _charging(team)
        anna, membership = _paid_member(client, monkeypatch, team, lead)
        teams.leave(anna, team)

        teams.stay(anna, team)

        assert membership.ends_on is None
        assert fake_stripe.named("subscription.modify")[-1][2] == {"cancel_at_period_end": False}

    def test_the_subscription_ending_ends_it(self, app, client, monkeypatch, fake_stripe):
        team, lead = _led()
        _charging(team)
        anna, membership = _paid_member(client, monkeypatch, team, lead)
        teams.leave(anna, team)
        db.session.commit()

        post_event(client, monkeypatch, _subscription_event(
            membership, "customer.subscription.deleted", "evt_del", status="canceled",
            cancellation_details={"reason": "cancellation_requested"}))

        db.session.refresh(membership)
        assert (membership.status, membership.end_reason) == (teams.ENDED, teams.END_LEFT)

    def test_leaving_on_stripes_billing_page_counts_too(self, app, client, monkeypatch, fake_stripe):
        team, lead = _led()
        _charging(team)
        _anna, membership = _paid_member(client, monkeypatch, team, lead)

        post_event(client, monkeypatch, _subscription_event(
            membership, "customer.subscription.updated", "evt_upd", cancel_at_period_end=True,
            items={"data": [{"current_period_end": start_of_day_unix(date(2027, 4, 1))}]}))

        db.session.refresh(membership)
        assert membership.ends_on == date(2027, 3, 31)
        assert _events("team_member_leaving", lead.email)

    def test_removal_cancels_at_once(self, app, client, monkeypatch, fake_stripe):
        team, lead = _led()
        _charging(team)
        _anna, membership = _paid_member(client, monkeypatch, team, lead)

        teams.remove(lead, team, membership.id, "Misconduct.")

        assert fake_stripe.named("subscription.cancel") == [("subscription.cancel", ("sub_1",), {"prorate": False})]
        assert membership.stripe_subscription_id is None

    def test_erasure_cancels_at_once(self, app, client, monkeypatch, fake_stripe):
        team, lead = _led()
        _charging(team)
        anna, membership = _paid_member(client, monkeypatch, team, lead)

        privacy.erase_account(anna, initiated_by=privacy.INITIATED_BY_MEMBER)

        assert ("subscription.cancel", ("sub_1",), {"prorate": False}) in fake_stripe.calls

    def test_an_unpaid_approval_lapsing_closes_its_checkout(self, app, fake_stripe):
        team, lead = _led()
        _charging(team)
        anna, membership = _approved(team, lead)
        team_payments.start_checkout(anna, team)
        membership.approved_at = datetime.now(timezone.utc) - timedelta(days=15)
        db.session.commit()

        assert teams.lapse_unpaid_approvals() == 1
        assert fake_stripe.named("session.expire")

    def test_the_night_ends_what_stripe_never_reported(self, app, client, monkeypatch, fake_stripe):
        team, lead = _led()
        _charging(team)
        _anna, membership = _paid_member(client, monkeypatch, team, lead)
        today = get_membership_today()

        membership.ends_on = today - timedelta(days=1)
        assert team_payments.end_finished_team_memberships(today) == 1
        assert (membership.status, membership.end_reason) == (teams.ENDED, teams.END_LEFT)

    def test_the_night_ends_one_long_unpaid(self, app, client, monkeypatch, fake_stripe):
        team, lead = _led()
        _charging(team)
        _anna, membership = _paid_member(client, monkeypatch, team, lead)
        today = membership.paid_until + timedelta(days=team_payments.OVERDUE_DAYS + 1)

        assert team_payments.end_finished_team_memberships(today) == 1
        assert membership.end_reason == teams.END_PAYMENT_FAILED
        assert fake_stripe.named("subscription.cancel")


@pytest.mark.usefixtures("switched_on")
def test_the_data_export_has_the_payments(app, client, monkeypatch, fake_stripe):
    team, lead = _led()
    _charging(team)
    anna, _membership = _paid_member(client, monkeypatch, team, lead)

    exported = privacy.export_account_data(anna)

    assert exported["teams"]["payments"][0]["amount_cents"] == 1000
    assert exported["teams"]["memberships"][0]["paid_until"] == "2027-03-31"


@pytest.mark.usefixtures("switched_on")
def test_the_leads_see_paid_until(app, client, monkeypatch, fake_stripe):
    team, lead = _led()
    _charging(team)
    _paid_member(client, monkeypatch, team, lead)
    _login(client, lead.id)

    body = client.get(f"/teams/{team.slug}/manage").get_data(as_text=True)

    assert "Paid until" in body and "31.03.2027" in body


@pytest.mark.usefixtures("outbox")
@pytest.mark.parametrize("event_type, subject", [
    ("team_payment_due", "Rocket: one step left"),
    ("team_payment_ended", "Your membership in Rocket has ended"),
    ("team_member_leaving", "A member is leaving Rocket"),
])
def test_the_new_emails_go_out(app, event_type, subject):
    from aeronautics_members.services.notifications import get_notification_service

    service = get_notification_service()
    event = _user_status_event(event_type, team_name="Rocket", team_slug="rocket")
    with app.test_request_context():
        built_subject, template_vars = service._build_user_status_message(event)
        ok, _error = service._send_user_status_mail(event, built_subject, template_vars)

    assert ok and built_subject == subject
    assert FakeSMTP.sent
