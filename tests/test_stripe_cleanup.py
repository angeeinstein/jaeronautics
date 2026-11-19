"""Tests for graceful handling of deleted Stripe subscriptions/customers.

A stale reference to a subscription that no longer exists in Stripe (e.g. a
deleted sandbox subscription) must not make billing sync fail; it should clear
the dead reference and fall back to date-based state.
"""
from datetime import date, timedelta

import pytest
import stripe

from conftest import app_module, db, make_member, workflows


def _resource_missing():
    return stripe.InvalidRequestError("No such subscription: 'sub_x'", param="subscription", code="resource_missing")


def test_deleted_subscription_reference_is_cleared(app, monkeypatch):
    member = make_member(
        email="dead@example.com", payment_status="paid", is_active=True,
        stripe_customer_id="cus_x", stripe_subscription_id="sub_dead",
        membership_ends_on=date.today() - timedelta(days=1),  # already lapsed
    )
    monkeypatch.setattr(app_module, "apply_runtime_stripe_config", lambda: None)
    monkeypatch.setattr(app_module.stripe.Subscription, "retrieve",
                        staticmethod(lambda *a, **k: (_ for _ in ()).throw(_resource_missing())))
    monkeypatch.setattr(app_module.stripe.Subscription, "list",
                        staticmethod(lambda *a, **k: {"data": []}))

    changed, sub, _forum = workflows.refresh_member_billing_state(member, force_stripe_sync=True, sync_forum=False)
    db.session.commit()

    assert sub is None
    assert member.stripe_subscription_id is None  # dead reference cleared
    assert changed is True


def test_a_dead_subscription_with_the_customer_kept_cleared_for_good(app, client, monkeypatch):
    # The account page commits only a change: the subscription cleared while the customer stays
    # was not counted as one, so every view asked Stripe and cleared it again.
    member = make_member(
        email="kept@example.com", payment_status="paid", is_active=True,
        stripe_customer_id="cus_kept", stripe_subscription_id="sub_dead",
        membership_ends_on=date.today() + timedelta(days=30),
    )
    asked = []

    def retrieve(*_args, **_kwargs):
        asked.append(1)
        raise _resource_missing()

    monkeypatch.setattr(app_module, "apply_runtime_stripe_config", lambda: None)
    monkeypatch.setattr(app_module.stripe.Subscription, "retrieve", staticmethod(retrieve))
    monkeypatch.setattr(app_module.stripe.Subscription, "list", staticmethod(lambda *a, **k: {"data": []}))

    changed, _sub, _forum = workflows.refresh_member_billing_state(member, force_stripe_sync=True, sync_forum=False)
    assert changed is True
    db.session.commit()
    db.session.expire_all()

    workflows.refresh_member_billing_state(db.session.get(type(member), member.id), force_stripe_sync=True,
                                           sync_forum=False)

    assert len(asked) == 1  # the second lookup no longer asks Stripe for it
    assert db.session.get(type(member), member.id).stripe_customer_id == "cus_kept"


def test_other_stripe_error_still_raises(app, monkeypatch):
    member = make_member(email="neterr@example.com", stripe_subscription_id="sub_x")
    monkeypatch.setattr(app_module, "apply_runtime_stripe_config", lambda: None)
    monkeypatch.setattr(app_module.stripe.Subscription, "retrieve",
                        staticmethod(lambda *a, **k: (_ for _ in ()).throw(stripe.APIConnectionError("network down"))))

    with pytest.raises(stripe.StripeError):
        workflows.refresh_member_billing_state(member, force_stripe_sync=True, sync_forum=False)
