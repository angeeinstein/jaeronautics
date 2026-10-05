"""A new fee: new people pay it at once, running subscriptions from their next renewal.

The fee changes by entering a new Stripe price where the old one was -- the
membership under Settings -> Billing, a team in its form. Every running
subscription is then moved in the background, through the outbox, with no
proration: nothing is charged now, the next charge is the new amount, and its
holder is emailed what changes and from when. Stripe is faked.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import db, make_member
from aeronautics_members.db_models import ExternalWorkItem, NotificationEvent, Setting
from aeronautics_members.services import ValidationError, outbox, payments
from aeronautics_members.services.billing import change_membership_price
from aeronautics_members.services.clock import start_of_day_unix
from test_admin_reviews import _staff
from test_emails import FakeSMTP, _user_status_event, outbox as outbox_fixture  # noqa: F401
from test_team_payments import _approved, _charging, fake_stripe as team_fake_stripe  # noqa: F401
from test_teams_flow import _led, _login, switched_on  # noqa: F401

RENEWAL = date(2027, 1, 1)
#: When the email about a new fee for that renewal is due, and a moment after.
#: Midnight on 1 January in Vienna is 23:00 UTC the day before.
NOTICE_DUE = datetime(2026, 12, 17, 23, 0, tzinfo=timezone.utc)


def _price(price_id, amount, months=12, product="prod_membership", active=True):
    interval = {"interval": "year", "interval_count": 1} if months == 12 else {"interval": "month", "interval_count": months}
    return {"id": price_id, "unit_amount": amount, "currency": "eur", "product": product,
            "active": active, "recurring": interval}


class FakeStripe:
    def __init__(self):
        self.prices = {
            "price_15": _price("price_15", 1500),
            "price_20": _price("price_20", 2000),
            "price_25": _price("price_25", 2500),
            "price_20_monthly": _price("price_20_monthly", 2000, months=1),
            "price_20_other": _price("price_20_other", 2000, product="prod_other"),
            "price_team10": _price("price_team10", 1000, months=6, product="prod_team"),
            "price_team12": _price("price_team12", 1200, months=6, product="prod_team"),
            "price_team12y": _price("price_team12y", 1200, months=12, product="prod_team"),
        }
        self.subscriptions = {}
        self.modified = []

    def subscription(self, sub_id, price_id, status="active", renews_on=RENEWAL, **fields):
        self.subscriptions[sub_id] = {
            "id": sub_id, "status": status, "cancel_at_period_end": False, "cancel_at": None,
            "items": {"data": [{"id": f"si_{sub_id}", "price": self.prices[price_id],
                                "current_period_end": start_of_day_unix(renews_on)}]},
            **fields,
        }


@pytest.fixture
def stripe_fake(monkeypatch):
    import stripe

    fake = FakeStripe()

    def retrieve_price(price_id, **kwargs):
        if price_id not in fake.prices:
            raise stripe.InvalidRequestError("No such price", "price", code="resource_missing")
        return dict(fake.prices[price_id])

    def retrieve_subscription(sub_id, **kwargs):
        if sub_id not in fake.subscriptions:
            raise stripe.InvalidRequestError("No such subscription", "id", code="resource_missing")
        return fake.subscriptions[sub_id]

    def modify(sub_id, **kwargs):
        fake.modified.append((sub_id, kwargs))
        item = fake.subscriptions[sub_id]["items"]["data"][0]
        item["price"] = fake.prices[kwargs["items"][0]["price"]]
        return fake.subscriptions[sub_id]

    monkeypatch.setattr(stripe.Price, "retrieve", staticmethod(retrieve_price))
    monkeypatch.setattr(stripe.Subscription, "retrieve", staticmethod(retrieve_subscription))
    monkeypatch.setattr(stripe.Subscription, "modify", staticmethod(modify))
    return fake


def _membership_price(price_id):
    row = db.session.get(Setting, "stripe_price_id")
    if row is None:
        db.session.add(Setting(key="stripe_price_id", value=price_id))
    else:
        row.value = price_id
    db.session.commit()


def _paying_member(fake, email, sub_id, price_id="price_15", **sub_fields):
    member = make_member(email=email)
    member.stripe_subscription_id = sub_id
    fake.subscription(sub_id, price_id, **sub_fields)
    db.session.commit()
    return member


def _run_worker(now=None):
    return outbox.process_pending(
        limit=100, now=now,
        kinds=[ExternalWorkItem.KIND_STRIPE_PRICE_MOVE, ExternalWorkItem.KIND_FEE_CHANGE_NOTICE],
    )


def _when_the_notice_is_due():
    return _run_worker(now=NOTICE_DUE + timedelta(hours=1))


def _emails(event_type):
    return db.session.query(NotificationEvent).filter_by(event_type=event_type).all()


# --- Moving one subscription ------------------------------------------------------------


class TestMovingOneSubscription:
    def test_from_the_next_renewal_and_without_proration(self, app, stripe_fake):
        stripe_fake.subscription("sub_1", "price_15")

        result = payments.move_to_price_at_renewal("sub_1", "price_20")

        assert stripe_fake.modified == [
            ("sub_1", {"items": [{"id": "si_sub_1", "price": "price_20"}], "proration_behavior": "none"})
        ]
        assert result["moved"] and result["renews_at"] == start_of_day_unix(RENEWAL)
        assert (result["old_price"]["unit_amount"], result["new_price"]["unit_amount"]) == (1500, 2000)

    def test_a_trial_is_charged_the_new_fee_when_it_ends(self, app, stripe_fake):
        trial_end = start_of_day_unix(date(2027, 4, 1))
        stripe_fake.subscription("sub_1", "price_15", status="trialing", trial_end=trial_end)

        assert payments.move_to_price_at_renewal("sub_1", "price_20")["renews_at"] == trial_end

    @pytest.mark.parametrize("setup, why", [
        ({"status": "canceled"}, "ended"),
        ({"cancel_at_period_end": True}, "ending"),
    ])
    def test_nothing_to_do_for_one_that_ends(self, app, stripe_fake, setup, why):
        stripe_fake.subscription("sub_1", "price_15", **setup)

        assert payments.move_to_price_at_renewal("sub_1", "price_20") == {"moved": False, "why": why}
        assert stripe_fake.modified == []

    def test_nothing_to_do_twice(self, app, stripe_fake):
        stripe_fake.subscription("sub_1", "price_20")

        assert payments.move_to_price_at_renewal("sub_1", "price_20")["why"] == "already"
        assert payments.move_to_price_at_renewal("sub_gone", "price_20")["why"] == "gone"

    def test_never_to_another_interval(self, app, stripe_fake):
        """Stripe would restart the billing cycle and charge at once."""
        from aeronautics_members.services import ExternalServiceError

        stripe_fake.subscription("sub_1", "price_15")

        with pytest.raises(ExternalServiceError):
            payments.move_to_price_at_renewal("sub_1", "price_20_monthly")
        assert stripe_fake.modified == []


# --- The membership ---------------------------------------------------------------------


class TestANewMembershipFee:
    def test_every_running_subscription_is_queued(self, app, stripe_fake):
        _membership_price("price_15")
        _paying_member(stripe_fake, "a@example.com", "sub_a")
        _paying_member(stripe_fake, "b@example.com", "sub_b")
        make_member(email="never-paid@example.com")

        assert change_membership_price(None, "price_20") == 2
        db.session.commit()

        queued = db.session.query(ExternalWorkItem).filter_by(kind=ExternalWorkItem.KIND_STRIPE_PRICE_MOVE).all()
        assert sorted(item.payload["subscription_id"] for item in queued) == ["sub_a", "sub_b"]

    def test_the_worker_moves_them_now_and_tells_each_member_two_weeks_ahead(self, app, stripe_fake):
        _membership_price("price_15")
        member = _paying_member(stripe_fake, "a@example.com", "sub_a")
        change_membership_price(None, "price_20")
        _membership_price("price_20")

        assert _run_worker() == (1, 0)
        assert stripe_fake.modified[0][0] == "sub_a"
        assert not _emails("membership_fee_changed")
        assert _run_worker(now=NOTICE_DUE - timedelta(hours=1)) == (0, 0)

        assert _when_the_notice_is_due() == (1, 0)
        [email] = _emails("membership_fee_changed")
        assert email.recipient_email == member.user.email
        assert (email.payload["old_fee"], email.payload["new_fee"], email.payload["from_date"]) == (
            "€15.00 per year", "€20.00 per year", "01.01.2027")

    def test_changed_again_before_the_worker_ran(self, app, stripe_fake):
        _membership_price("price_15")
        _paying_member(stripe_fake, "a@example.com", "sub_a")
        change_membership_price(None, "price_20")
        _membership_price("price_20")
        change_membership_price(None, "price_25")
        _membership_price("price_25")

        _run_worker()
        _when_the_notice_is_due()

        assert [kwargs["items"][0]["price"] for _sub, kwargs in stripe_fake.modified] == ["price_25"]
        [email] = _emails("membership_fee_changed")
        assert email.payload["new_fee"] == "€25.00 per year"

    def test_changed_back_before_the_worker_ran(self, app, stripe_fake):
        _membership_price("price_15")
        _paying_member(stripe_fake, "a@example.com", "sub_a")
        change_membership_price(None, "price_20")
        # The setting was never saved with price_20, or was put back.

        _run_worker()
        _when_the_notice_is_due()

        assert stripe_fake.modified == [] and not _emails("membership_fee_changed")

    def test_a_renewal_closer_than_two_weeks_is_told_at_once(self, app, stripe_fake):
        _membership_price("price_15")
        _paying_member(stripe_fake, "a@example.com", "sub_a", renews_on=date.today() + timedelta(days=5))
        change_membership_price(None, "price_20")
        _membership_price("price_20")

        _run_worker()
        _run_worker()

        assert len(_emails("membership_fee_changed")) == 1

    def test_somebody_who_cancelled_meanwhile_is_not_told(self, app, stripe_fake):
        _membership_price("price_15")
        member = _paying_member(stripe_fake, "a@example.com", "sub_a")
        change_membership_price(None, "price_20")
        _membership_price("price_20")
        _run_worker()
        member.cancel_at_period_end = True
        db.session.commit()

        _when_the_notice_is_due()

        assert not _emails("membership_fee_changed")

    def test_a_waiting_email_is_not_a_queue_piling_up(self, app, stripe_fake):
        from aeronautics_members.services.diagnostics import get_queue_summary

        _membership_price("price_15")
        for n in range(25):
            _paying_member(stripe_fake, f"m{n}@example.com", f"sub_{n}")
        change_membership_price(None, "price_20")
        _membership_price("price_20")
        _run_worker()

        assert get_queue_summary()["external_work_pending"] == 0

    def test_the_same_price_moves_nobody_and_asks_nothing(self, app, stripe_fake):
        _membership_price("price_15")
        _paying_member(stripe_fake, "a@example.com", "sub_a")

        assert change_membership_price(None, "price_15") == 0

    @pytest.mark.parametrize("price_id, code", [
        ("price_20_monthly", "membership_price_unsuitable"),
        ("price_20_other", "membership_price_other_product"),
        ("price_nope", "membership_price_unknown"),
    ])
    def test_a_price_that_does_not_fit_is_refused(self, app, stripe_fake, price_id, code):
        _membership_price("price_15")

        with pytest.raises(ValidationError) as caught:
            change_membership_price(None, price_id)

        assert caught.value.code == code

    def test_the_settings_page_does_it_and_says_so(self, app, client, stripe_fake):
        from test_teams_flow import _login

        _membership_price("price_15")
        _paying_member(stripe_fake, "a@example.com", "sub_a")
        _login(client, _staff("boss@example.com", "superadmin").id)

        body = client.post("/admin/settings", data={
            "save_settings": "1", "settings_section": "billing", "stripe_price_id": "price_20",
        }, follow_redirects=True).get_data(as_text=True)

        assert db.session.get(Setting, "stripe_price_id").value == "price_20"
        assert "1 running subscription(s) move to the new price" in body
        assert db.session.query(ExternalWorkItem).filter_by(kind=ExternalWorkItem.KIND_STRIPE_PRICE_MOVE).count() == 1

    def test_the_settings_page_refuses_an_unsuitable_price(self, app, client, stripe_fake):
        from test_teams_flow import _login

        _membership_price("price_15")
        _login(client, _staff("boss@example.com", "superadmin").id)

        body = client.post("/admin/settings", data={
            "save_settings": "1", "settings_section": "billing", "stripe_price_id": "price_20_monthly",
        }, follow_redirects=True).get_data(as_text=True)

        assert db.session.get(Setting, "stripe_price_id").value == "price_15"
        assert "yearly recurring price" in body


# --- A team -------------------------------------------------------------------------------


@pytest.mark.usefixtures("switched_on")
class TestANewTeamFee:
    def _team_with_subscription(self, stripe_fake):
        from aeronautics_members.services import teams

        team, lead = _led()
        _charging(team)
        team.stripe_price_id = "price_team10"
        _anna, membership = _approved(team, lead)
        membership.status = teams.ACTIVE
        membership.stripe_subscription_id = "sub_t"
        stripe_fake.subscription("sub_t", "price_team10")
        db.session.commit()
        return team, membership

    def _save(self, team, price_id):
        from aeronautics_members.services.team_payments import update_payment_settings

        return update_payment_settings(None, team, payment_mode="subscription",
                                       stripe_price_id=price_id, period_starts="01.10, 01.04")

    def test_the_running_subscriptions_move_and_their_holders_hear(self, app, stripe_fake):
        team, membership = self._team_with_subscription(stripe_fake)

        assert self._save(team, "price_team12")["moving"] == 1
        db.session.commit()
        assert _run_worker() == (1, 0)
        _when_the_notice_is_due()

        assert stripe_fake.modified[0][1]["items"][0]["price"] == "price_team12"
        [email] = _emails("team_fee_changed")
        assert email.recipient_email == membership.user.email
        assert (email.payload["old_fee"], email.payload["new_fee"]) == ("€10.00 every 6 months", "€12.00 every 6 months")
        assert team.fee_display == "€12.00 every 6 months"

    def test_another_interval_is_refused_while_subscriptions_run(self, app, stripe_fake):
        team, _membership = self._team_with_subscription(stripe_fake)

        with pytest.raises(ValidationError) as caught:
            team.period_starts = "01.10"
            from aeronautics_members.services.team_payments import update_payment_settings

            update_payment_settings(None, team, payment_mode="subscription",
                                    stripe_price_id="price_team12y", period_starts="01.10")

        assert caught.value.code == "team_price_interval_changed"

    def test_the_admin_page_says_how_many_move(self, app, client, stripe_fake):
        team, _membership = self._team_with_subscription(stripe_fake)
        _login(client, _staff("admin@example.com", "admin").id)

        body = client.post(f"/admin/teams/{team.slug}", data={
            "name": team.name, "admission_mode": team.admission_mode, "applications_open": "on",
            "payment_mode": "subscription", "stripe_price_id": "price_team12", "period_starts": "01.10, 01.04",
        }, follow_redirects=True).get_data(as_text=True)

        assert "1 running subscription(s) move to the new price" in body


@pytest.mark.usefixtures("outbox_fixture")
@pytest.mark.parametrize("event_type, subject", [
    ("membership_fee_changed", "The membership fee changes"),
    ("team_fee_changed", "The fee for Rocket changes"),
])
def test_the_emails_go_out(app, event_type, subject):
    from aeronautics_members.services.notifications import get_notification_service

    service = get_notification_service()
    event = _user_status_event(event_type, team_name="Rocket", team_slug="rocket",
                               old_fee="€15.00 per year", new_fee="€20.00 per year", from_date="01.01.2027")
    with app.test_request_context():
        built_subject, template_vars = service._build_user_status_message(event)
        ok, _error = service._send_user_status_mail(event, built_subject, template_vars)

    assert ok and built_subject == subject
    assert "€20.00 per year" in " ".join(str(line) for line in template_vars["body_lines"] if line)
    assert FakeSMTP.sent
