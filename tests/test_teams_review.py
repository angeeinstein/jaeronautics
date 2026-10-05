"""What the review before going live found in the teams, each kept fixed.

- Stripe's word about a subscription the membership has moved on from (one
  cancelled when the person paid again) changes nothing.
- A changed fee or way of paying: the memberships are locked before Stripe
  is told, a failure half-way is undone, open payment pages at the old fee
  close, and subscriptions still running out carry on when a team charges
  again.
- Approved under one way of paying and paying another: the membership takes
  the way actually paid.
- Paid once per period by SEPA debit at the last moment: not ended while the
  money is on its way.
- Teams switched off or a team archived with approvals still able to pay.
- Withdrawing while the payment is on its way; places taken by approvals.
- Forum groups the portal or Discourse use otherwise.
- Spreadsheet formulas in exports; renewals across summer time; one nightly
  step failing.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import db
from aeronautics_members.config import MEMBERSHIP_TIMEZONE
from aeronautics_members.db_models import ExternalWorkItem, Setting
from aeronautics_members.services import ConflictError, ExternalServiceError, ValidationError, team_payments, teams
from aeronautics_members.services.clock import get_membership_today
from test_team_one_time import ONE_TIME_PRICE, _once_per_period, _session_event, stripe_fake  # noqa: F401
from test_team_payments import (  # noqa: F401
    PRICE_ID, _approved, _charging, _events, _invoice, _paid_member, _subscription_event, fake_stripe,
)
from test_emails import outbox  # noqa: F401
from test_teams_flow import _led, _person, switched_on  # noqa: F401
from test_webhook import post_event


@pytest.fixture
def calls(fake_stripe):  # noqa: F811 -- the fixture of test_team_payments
    return fake_stripe


@pytest.fixture
def once_calls(stripe_fake):  # noqa: F811 -- the fixture of test_team_one_time
    return stripe_fake



def _second_paid_member(client, monkeypatch, team, lead):
    bea, membership = _approved(team, lead, email="bea@example.com")
    event = _invoice(membership, "in_bea", until=date(2027, 3, 31), subscription="sub_bea")
    event["id"] = "evt_in_bea"
    assert post_event(client, monkeypatch, event).status_code == 200
    db.session.refresh(membership)
    return bea, membership


@pytest.mark.usefixtures("switched_on")
class TestAnOldSubscription:
    def test_its_end_does_not_undo_a_new_payment_on_its_way(self, app, client, monkeypatch, calls):
        team, lead = _led()
        _charging(team)
        _anna, membership = _approved(team, lead)
        membership.stripe_subscription_id = "sub_new"
        membership.payment_state = team_payments.PROCESSING
        db.session.commit()

        post_event(client, monkeypatch, _subscription_event(
            membership, "customer.subscription.deleted", "evt_old_gone", status="canceled"))

        db.session.refresh(membership)
        assert (membership.payment_state, membership.stripe_subscription_id) == ("processing", "sub_new")

    def test_its_changes_are_not_somebody_leaving(self, app, client, monkeypatch, calls):
        team, lead = _led()
        _charging(team)
        _anna, membership = _paid_member(client, monkeypatch, team, lead)
        membership.stripe_subscription_id = "sub_2"
        db.session.commit()

        post_event(client, monkeypatch, _subscription_event(
            membership, "customer.subscription.updated", "evt_old_cancel", cancel_at_period_end=True))

        db.session.refresh(membership)
        assert membership.ends_on is None and not _events("team_member_leaving")


@pytest.mark.usefixtures("switched_on")
class TestChangingTheFee:
    def test_a_failure_half_way_is_undone(self, app, client, monkeypatch, calls):
        import stripe

        team, lead = _led()
        _charging(team)
        _anna, first = _paid_member(client, monkeypatch, team, lead)
        _bea, second = _second_paid_member(client, monkeypatch, team, lead)

        def modify(sub_id, **changes):
            calls.record("subscription.modify", sub_id, **changes)
            if sub_id == "sub_bea" and changes.get("cancel_at_period_end"):
                raise stripe.APIConnectionError("Stripe is down")

        monkeypatch.setattr(stripe.Subscription, "modify", staticmethod(modify))

        with pytest.raises(ExternalServiceError):
            team_payments.update_payment_settings(None, team, payment_mode="none", stripe_price_id="",
                                                  period_starts="")

        calls = [(call[1][0], call[2]["cancel_at_period_end"]) for call in calls.named("subscription.modify")]
        assert calls == [("sub_1", True), ("sub_bea", True), ("sub_1", False)]

    def test_open_payment_pages_at_the_old_fee_close(self, app, calls):
        team, lead = _led()
        _charging(team)
        anna, membership = _approved(team, lead)
        team_payments.start_checkout(anna, team)
        db.session.commit()
        opened = membership.stripe_checkout_session_id

        team_payments.update_payment_settings(None, team, payment_mode="subscription",
                                              stripe_price_id="price_new6m", period_starts="01.10, 01.04")

        assert ("session.expire", (opened,), {}) in calls.calls
        assert membership.stripe_checkout_session_id is None

    def test_charging_again_lets_the_running_out_subscriptions_carry_on(
            self, app, client, monkeypatch, calls):
        team, lead = _led()
        _charging(team)
        anna, membership = _paid_member(client, monkeypatch, team, lead)
        team_payments.update_payment_settings(None, team, payment_mode="none", stripe_price_id="", period_starts="")
        db.session.commit()
        assert membership.payment_mode == "none" and membership.stripe_subscription_id == "sub_1"

        outcome = team_payments.update_payment_settings(None, team, payment_mode="subscription",
                                                        stripe_price_id=PRICE_ID, period_starts="01.10, 01.04")
        db.session.commit()

        assert membership.payment_mode == "subscription" and not team_payments.needs_to_pay(membership)
        assert calls.named("subscription.modify")[-1][2] == {"cancel_at_period_end": False}
        assert outcome["moving"] == 1  # the lead, free so far, is asked to pay as usual
        assert db.session.query(ExternalWorkItem).filter_by(kind=ExternalWorkItem.KIND_STRIPE_PRICE_MOVE).count() == 1
        assert _events("team_charging_again", anna.email) and not _events("team_now_charges", anna.email)


@pytest.mark.usefixtures("switched_on")
class TestTheWayActuallyPaid:
    def test_approved_by_subscription_and_paid_once_per_period(self, app, client, monkeypatch, once_calls):
        team, lead = _led()
        _charging(team)
        _anna, membership = _approved(team, lead)
        assert membership.payment_mode == "subscription"
        _once_per_period(team)
        until = team_payments.joining_period(team)["paid_until"]

        post_event(client, monkeypatch, _session_event("evt_paid", "checkout.session.completed", membership,
                                                       covers_until=until))

        db.session.refresh(membership)
        assert (membership.status, membership.payment_mode) == (teams.ACTIVE, "one_time")
        assert not team_payments.needs_to_pay(membership)
        team_payments.end_finished_team_memberships(today=get_membership_today() + timedelta(days=1))
        assert membership.status == teams.ACTIVE


@pytest.mark.usefixtures("switched_on")
class TestPaidOnceAtTheLastMoment:
    def test_kept_while_the_debit_is_on_its_way_then_ended(self, app, client, monkeypatch, once_calls):
        team, lead = _led()
        _once_per_period(team)
        _anna, membership = _approved(team, lead)
        until = team_payments.joining_period(team)["paid_until"]
        post_event(client, monkeypatch, _session_event("evt_join", "checkout.session.completed", membership,
                                                       covers_until=until))
        db.session.refresh(membership)
        membership.payment_state = team_payments.PROCESSING  # the renewal, paid by SEPA on the last day
        db.session.commit()

        team_payments.end_finished_team_memberships(today=until + timedelta(days=3))
        assert membership.status == teams.ACTIVE

        team_payments.end_finished_team_memberships(
            today=until + timedelta(days=team_payments.PROCESSING_GRACE_DAYS + 1))
        assert membership.status == teams.ENDED


@pytest.mark.usefixtures("switched_on")
class TestApprovalsThatCanStillPay:
    def test_teams_are_not_switched_off_under_them(self, app, calls):
        team, lead = _led()
        _charging(team)
        _approved(team, lead)

        with pytest.raises(ConflictError, match="approved applicant"):
            teams.save_team_settings(None, enabled=False, label_singular="", label_plural="")

    def test_archiving_ends_them_and_closes_the_payment_page(self, app, calls):
        team, lead = _led()
        _charging(team)
        anna, membership = _approved(team, lead)
        team_payments.start_checkout(anna, team)
        opened = membership.stripe_checkout_session_id
        applicant = _person("carl@example.com", "Carl", "Applicant")
        team.admission_mode = teams.ADMISSION_APPROVAL
        application = teams.join_or_apply(applicant, team)
        db.session.commit()

        teams.set_team_archived(None, team, True, confirmed_name=team.name)

        assert (membership.status, membership.end_reason) == (teams.WITHDRAWN, teams.END_TEAM_CLOSED)
        assert application.status == teams.WITHDRAWN
        assert ("session.expire", (opened,), {}) in calls.calls
        assert _events("team_closed", anna.email) and _events("team_closed", applicant.email)

    def test_withdrawing_waits_for_a_payment_on_its_way(self, app, calls):
        team, lead = _led()
        _charging(team)
        anna, membership = _approved(team, lead)
        membership.payment_state = team_payments.PROCESSING
        db.session.commit()

        with pytest.raises(ConflictError, match="on its way"):
            teams.withdraw(anna, team)

    def test_they_take_a_place(self, app, calls):
        team, lead = _led(max_members=2)
        _charging(team)
        _approved(team, lead)

        assert teams.is_full(team)

    def test_on_its_way_for_weeks_it_lapses_after_all(self, app, calls):
        team, lead = _led()
        _charging(team)
        _anna, membership = _approved(team, lead)
        membership.payment_state = team_payments.PROCESSING
        db.session.commit()

        teams.lapse_unpaid_approvals(now=datetime.now(timezone.utc) + timedelta(days=20))
        assert membership.status == teams.APPROVED
        teams.lapse_unpaid_approvals(
            now=datetime.now(timezone.utc) + timedelta(days=teams.APPROVAL_PROCESSING_DAYS + 1))
        assert membership.status == teams.WITHDRAWN


@pytest.mark.usefixtures("switched_on")
class TestForumGroups:
    @pytest.mark.parametrize("group", ["admins", "trust_level_1", "Members"])
    def test_one_used_otherwise_is_refused(self, app, group):
        db.session.add(Setting(key="forum_member_group", value="members"))
        db.session.commit()
        team, _lead = _led()

        with pytest.raises(ValidationError, match="used for something else"):
            teams.update_team(None, team, name=team.name, admission_mode=team.admission_mode,
                              max_members=None, forum_group=group)

    def test_leaving_a_team_never_takes_somebody_out_of_their_cohort(self, app):
        from aeronautics_members.forum_service import (
            FORUM_STATE_ACTIVE, DiscourseConnectProvider, group_name_for_year_group, normalize_forum_settings,
        )

        anna = _person(year_group="LAV25")
        cohort = group_name_for_year_group("LAV25")
        team, _lead = _led()
        team.forum_group = cohort  # set by hand, as before the check
        db.session.commit()

        provider = DiscourseConnectProvider(normalize_forum_settings({}))
        fields = provider._build_group_fields(FORUM_STATE_ACTIVE, user=anna, member=anna.member)

        assert cohort in fields["add_groups"].split(",")
        assert cohort not in fields.get("remove_groups", "").split(",")


class TestExports:
    @pytest.mark.parametrize("value, shown", [
        ("=HYPERLINK(\"http://x\")", "'=HYPERLINK(\"http://x\")"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("-2+3", "'-2+3"),
        ("-cmd", "'-cmd"),
        ("+43 660 1234567", "+43 660 1234567"),
        ("Anna Berger", "Anna Berger"),
        (None, ""),
    ])
    def test_a_cell_is_never_a_formula(self, value, shown):
        assert teams.csv_cell(value) == shown


class TestRenewalsAcrossSummerTime:
    @pytest.mark.parametrize("moment, last_day", [
        (datetime(2027, 8, 1, 1, 0), date(2027, 7, 31)),    # started in winter, renews in summer
        (datetime(2028, 1, 31, 23, 0), date(2028, 1, 31)),  # started in summer, renews in winter
        (datetime(2027, 10, 1, 0, 0), date(2027, 9, 30)),
    ])
    def test_the_last_day_is_the_one_meant(self, moment, last_day):
        stamp = moment.replace(tzinfo=MEMBERSHIP_TIMEZONE).timestamp()

        assert team_payments._last_day(stamp) == last_day


def test_one_nightly_team_step_failing_does_not_stop_the_others(app, monkeypatch):
    from aeronautics_members.services import teams as teams_module

    ran = []

    def broken(*args, **kwargs):
        raise RuntimeError("broken")

    monkeypatch.setattr(team_payments, "follow_association_ends", broken)
    monkeypatch.setattr(teams_module, "send_due_access_lists", lambda: ran.append("access lists") or 0)

    result = app.test_cli_runner().invoke(args=["reconcile-billing"])

    assert "Team step broken failed" in result.output
    assert ran == ["access lists"]


@pytest.mark.usefixtures("outbox")
@pytest.mark.parametrize("event_type, subject", [
    ("team_charging_again", "Rocket charges its fee again"),
    ("team_closed", "Rocket is no longer taking members"),
])
def test_the_new_emails_go_out(app, event_type, subject):
    from test_emails import FakeSMTP, _user_status_event

    from aeronautics_members.services.notifications import get_notification_service

    service = get_notification_service()
    event = _user_status_event(event_type, team_name="Rocket", team_slug="rocket", fee="€10.00 every 6 months")
    with app.test_request_context():
        built_subject, template_vars = service._build_user_status_message(event)
        ok, _error = service._send_user_status_mail(event, built_subject, template_vars)

    assert ok and built_subject == subject
    assert FakeSMTP.sent
