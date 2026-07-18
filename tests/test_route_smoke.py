"""Exercise every route (GET + POST) with external services mocked.

Asserts no route returns a 500 (NameError / BuildError / bad import / wrong
endpoint), and that a few key POSTs perform real state changes. This is the
test that catches blueprint-refactor breakage which import- and URL-map-level
checks miss, because it actually runs each handler and renders each template.
"""
import types
from datetime import date, datetime

import pytest

from conftest import app_module, db, make_member
from aeronautics_members.blueprints import public, account, auth, forum, admin
from aeronautics_members.db_models import (
    User, Member, MemberProfileChangeRequest, ForumAvatarSubmission, MailAccount,
)
from aeronautics_members.forum_service import ForumProviderError

BLUEPRINT_MODULES = [app_module, public, account, auth, forum, admin]


class _FakeCheckout:
    url = "https://checkout.example/session"


class _FakeForumService:
    settings = {"forum_avatar_max_bytes": 5 * 1024 * 1024}
    def is_enabled(self): return False
    def is_ready(self): return False
    def get_pending_submission(self, m): return None
    def get_current_approved_submission(self, m): return None
    def get_latest_submission(self, m): return None
    def get_upload_request_limit(self): return 10 * 1024 * 1024
    def get_desired_state(self, m): return "inactive"
    def sync_member(self, m): return types.SimpleNamespace(changed=False, desired_state=None, forum_account=None, error=None)
    def build_forum_redirect(self, destination_path=None): return "/forum"
    def create_avatar_submission(self, *a, **k): raise ForumProviderError("forum disabled in test")
    def handle_provider_request(self, *a, **k): raise ForumProviderError("forum disabled in test")
    def approve_avatar_submission(self, submission, reviewer=None, review_note=None):
        submission.status = "approved"
        return types.SimpleNamespace(error=None, desired_state="active", changed=False, forum_account=None)
    def reject_avatar_submission(self, submission, reviewer=None, review_note=None):
        submission.status = "rejected"
        return types.SimpleNamespace(error=None, desired_state="onboarding", changed=False, forum_account=None)
    def test_connection(self): return (True, "ok")


@pytest.fixture
def mocked(app, monkeypatch):
    """Neutralize Stripe/email/forum I/O across app + blueprint modules."""
    def patch_all(name, value):
        for mod in BLUEPRINT_MODULES:
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, value)

    patch_all("send_member_welcome_email", lambda *a, **k: None)
    patch_all("send_email_verification_email", lambda *a, **k: True)
    patch_all("send_password_reset_email", lambda *a, **k: True)
    patch_all("send_mail", lambda *a, **k: None)
    patch_all("probe_mail_account_connection", lambda *a, **k: (True, "ok"))
    patch_all("create_checkout_session_for_member", lambda m: (_FakeCheckout(), {"free_period": False, "thank_you_phase": "prorated"}))
    patch_all("create_invoice_membership_for_member", lambda m: (types.SimpleNamespace(id="sub_x"), {"free_period": True, "thank_you_phase": "free_period"}))
    patch_all("refresh_member_billing_state", lambda *a, **k: (False, None, None))
    patch_all("get_forum_service", lambda: _FakeForumService())

    import stripe
    monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(lambda **k: _FakeCheckout()), raising=False)
    monkeypatch.setattr(stripe.Subscription, "retrieve", staticmethod(lambda *a, **k: types.SimpleNamespace(get=lambda *x, **y: None)), raising=False)
    monkeypatch.setattr(stripe.Subscription, "modify", staticmethod(lambda *a, **k: None), raising=False)
    monkeypatch.setattr(stripe.Subscription, "delete", staticmethod(lambda *a, **k: None), raising=False)
    monkeypatch.setattr(stripe.Customer, "create", staticmethod(lambda **k: types.SimpleNamespace(id="cus_x")), raising=False)
    monkeypatch.setattr(stripe.Customer, "retrieve", staticmethod(lambda *a, **k: {"email": "x@example.com"}), raising=False)
    return app


@pytest.fixture
def seeded(mocked):
    admin_user = User(email="admin@t.co"); admin_user.set_password("password123")
    admin_user.grant_role(app_module.get_role("admin")); admin_user.email_verified_at = datetime.utcnow()
    db.session.add(admin_user); db.session.commit()

    member = make_member(email="m@t.co", payment_status="paid", is_active=True,
                         stripe_customer_id="cus_1", stripe_subscription_id="sub_1")
    member.membership_ends_on = date(date.today().year, 12, 31)
    member.email_verified_at = datetime.utcnow(); member.user.email_verified_at = member.email_verified_at
    member.user.set_password("password123")
    db.session.commit()

    nomember = User(email="nomember@t.co"); nomember.set_password("password123")
    nomember.email_verified_at = datetime.utcnow()
    db.session.add(nomember); db.session.commit()

    pcr = MemberProfileChangeRequest(
        member_id=member.id, requested_by_user_id=member.user_id, status="pending",
        requested_salutation="Mr", requested_first_name="New", requested_last_name="Name",
        requested_year_group="LAV25")
    sub = ForumAvatarSubmission(user_id=member.user_id, member_id=member.id, status="pending")
    mail = MailAccount(account_key="office", host="smtp.x", port=587, username="u", password="p", starttls=True)
    db.session.add_all([pcr, sub, mail]); db.session.commit()
    return dict(admin_id=admin_user.id, member_uid=member.user_id, nomember_id=nomember.id,
                member_id=member.id, pcr_id=pcr.id, sub_id=sub.id, mail_id=mail.id)


PROFILE = {"street": "Main", "house_number": "1", "postal_code": "8010", "city": "Graz",
           "country": "Austria", "phone_private": "+43123", "email_private": "m@t.co"}
MEMBERSHIP = {"salutation": "Mr", "first_name": "A", "last_name": "B", "year_group": "LAV25", **PROFILE}


def _login(client, uid):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(uid)


def test_all_routes_no_server_error(client, seeded):
    ids = seeded
    a, mu, nm = ids["admin_id"], ids["member_uid"], ids["nomember_id"]
    calls = []

    def hit(method, path, uid=None, data=None):
        if uid is not None:
            _login(client, uid)
        resp = client.open(path, method=method, data=data)
        calls.append((resp.status_code, method, path))
        assert resp.status_code < 500, f"{method} {path} -> {resp.status_code}"

    # public
    for p in ["/", "/__health", "/legal", "/thank-you", "/cancel"]:
        hit("GET", p)
    hit("POST", "/process-membership", data={**MEMBERSHIP, "email_private": "brand@new.co",
        "password": "password123", "confirm_password": "password123", "payment_method": "checkout", "terms_accepted": "y"})
    # auth
    for p in ["/login", "/register", "/forgot-password", "/verify-email/bad", "/reset-password/bad"]:
        hit("GET", p)
    hit("POST", "/login", data={"email": "m@t.co", "password": "password123"})
    hit("POST", "/forgot-password", data={"email": "m@t.co"})
    hit("POST", "/register", data={"email": "new2@t.co", "password": "password123", "confirm_password": "password123"})
    hit("POST", "/reset-password/bad", data={"password": "password123", "confirm_password": "password123"})
    hit("GET", "/change-password", uid=mu)
    hit("POST", "/change-password", uid=mu, data={"current_password": "password123", "new_password": "password124", "confirm_new_password": "password124"})
    hit("POST", "/logout", uid=mu)
    # account
    hit("GET", "/account?rt=1", uid=mu)
    hit("GET", "/account/create-membership", uid=nm)
    hit("POST", "/account/profile", uid=mu, data={f"profile-{k}": v for k, v in {**PROFILE, "city": "Vienna"}.items()})
    hit("POST", "/account/identity-request", uid=mu, data={"identity-salutation": "Mr", "identity-first_name": "X", "identity-last_name": "Y", "identity-year_group": "LAV25", "identity-member_note": "n"})
    hit("POST", f"/account/identity-request/{ids['pcr_id']}/cancel", uid=mu)
    hit("POST", "/account/billing", uid=mu, data={"action": "cancel"})
    hit("POST", "/account/resume-payment", uid=mu)
    hit("POST", "/account/resend-verification", uid=mu)
    # forum
    hit("GET", "/forum", uid=mu)
    hit("GET", "/forum/discourse/connect", uid=mu)
    hit("GET", "/forum/logout", uid=mu)
    hit("GET", "/forum/avatar/public/bad")
    hit("POST", "/forum/avatar", uid=mu, data={})
    # admin
    for p in ["/admin", "/admin/accounts", f"/admin/accounts/{mu}", "/admin/approvals", "/admin/logs", "/admin/forum", "/admin/settings"]:
        hit("GET", p, uid=a)
    hit("POST", f"/admin/accounts/{mu}/billing-sync", uid=a)
    hit("POST", f"/admin/accounts/{mu}/forum-resync", uid=a)
    hit("POST", f"/admin/accounts/{mu}/grant-admin", uid=a)
    hit("POST", f"/admin/accounts/{mu}/revoke-admin", uid=a)
    hit("POST", f"/admin/forum/submissions/{ids['sub_id']}/approve", uid=a, data={"review_note": "ok"})
    hit("POST", f"/admin/forum/submissions/{ids['sub_id']}/reject", uid=a, data={"review_note": "no"})
    hit("POST", f"/admin/profile-requests/{ids['pcr_id']}/approve", uid=a, data={"admin_note": "ok"})
    hit("POST", f"/admin/profile-requests/{ids['pcr_id']}/reject", uid=a, data={"admin_note": "no"})
    hit("POST", "/admin/settings/mail-accounts", uid=a, data={"mail-account_key": "office2", "mail-host": "smtp.x", "mail-port": "587", "mail-username": "u", "mail-password": "p", "mail-starttls": "y"})
    hit("POST", f"/admin/settings/mail-accounts/{ids['mail_id']}/test-connection", uid=a)
    hit("POST", "/admin/settings/mail-accounts/export", uid=a, data={"export_password": "secretsecret"})
    hit("POST", "/admin/settings/send-test-email", uid=a, data={"sender": "office", "recipient": "x@t.co", "template": "welcome_email"})
    hit("POST", "/admin/settings/test-forum-connection", uid=a)
    hit("POST", f"/admin/settings/mail-accounts/{ids['mail_id']}/delete", uid=a)

    assert len(calls) >= 45


def test_signup_creates_user_and_member(client, seeded):
    client.post("/process-membership", data={**MEMBERSHIP, "email_private": "brand@new.co",
        "password": "password123", "confirm_password": "password123", "payment_method": "checkout", "terms_accepted": "y"})
    user = db.session.execute(db.select(User).filter_by(email="brand@new.co")).scalar_one_or_none()
    assert user is not None and user.member is not None


def test_grant_admin_grants_role(client, seeded):
    _login(client, seeded["admin_id"])
    client.post(f"/admin/accounts/{seeded['member_uid']}/grant-admin")
    assert db.session.get(User, seeded["member_uid"]).has_role("admin")


def test_profile_save_persists(client, seeded):
    _login(client, seeded["member_uid"])
    resp = client.post("/account/profile", data={f"profile-{k}": v for k, v in {**PROFILE, "city": "Vienna"}.items()})
    assert resp.status_code == 302
    assert db.session.get(Member, seeded["member_id"]).city == "Vienna"
