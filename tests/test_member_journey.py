"""What a member meets on the way from signup to the forum.

Each of these was found by walking the path as a member would, and each left
somebody stuck with nothing on screen saying why.
"""
import types
from datetime import date
from pathlib import Path

import pytest

from conftest import db, make_member
from aeronautics_members import app as app_module
from aeronautics_members.blueprints import forum as forum_blueprint
from aeronautics_members.forum_service import ForumProviderError
from aeronautics_members.services import forum as forum_services

FORUM_SSO = "https://forum.test/session/sso?return_path=%2F"


class FakeForum:
    """A forum that is set up, and where this member's access is ready."""

    settings = {"forum_avatar_max_bytes": 5_000_000, "forum_onboarding_path": "/",
                "forum_avatar_allowed_types": ["jpg", "png"]}
    handoff_error = None
    photo_approved = True

    def is_enabled(self): return True
    def is_ready(self): return True
    def get_pending_submission(self, member): return None
    def get_current_approved_submission(self, member): return object() if self.photo_approved else None
    def get_reclaimed_avatar(self, member): return None
    def get_latest_submission(self, member): return None
    def get_upload_request_limit(self): return 10_000_000

    def sync_member(self, member):
        return types.SimpleNamespace(changed=False, error=None, desired_state="active", forum_account=None)

    def build_forum_redirect(self, destination_path=None):
        return FORUM_SSO

    def handle_provider_request(self, *args, **kwargs):
        if self.handoff_error:
            raise ForumProviderError(self.handoff_error)
        return "https://forum.test/session/sso_login?sso=ok"


@pytest.fixture
def forum(monkeypatch):
    fake = FakeForum()
    for module in (app_module, forum_services, forum_blueprint):
        if hasattr(module, "get_forum_service"):
            monkeypatch.setattr(module, "get_forum_service", lambda: fake)
    return fake


@pytest.fixture
def links_sent(monkeypatch):
    sent = []
    monkeypatch.setattr(
        forum_blueprint, "send_email_verification_email",
        lambda app, user: sent.append(user.email) or True,
    )
    return sent


def _paid_member(client, verified):
    member = make_member(
        email="journey@example.com", payment_status="paid", is_active=True,
        membership_starts_on=date(2026, 1, 1), membership_ends_on=date(2099, 12, 31),
    )
    if verified:
        from aeronautics_members.services.clock import get_now_utc
        member.user.email_verified_at = get_now_utc()
        db.session.commit()
    with client.session_transaction() as session:
        session["_user_id"] = str(member.user.id)
    return member


def _flashes(client):
    with client.session_transaction() as session:
        return [text for _category, text in session.get("_flashes", [])]


class TestAnUnconfirmedAddressDoesNotLoop:
    """Paid, photo approved, first confirmation mail never opened: the forum
    and the portal sent them back and forth until the browser gave up."""

    def test_the_forum_page_does_not_send_them_to_the_forum(self, app, client, forum, links_sent):
        _paid_member(client, verified=False)

        response = client.get("/forum")

        assert response.headers["Location"].endswith("/account")
        assert links_sent == ["journey@example.com"]
        assert any("just sent a new link" in text for text in _flashes(client))

    def test_the_sign_in_from_the_forum_ends_on_the_account_page(self, app, client, forum, links_sent):
        _paid_member(client, verified=False)

        response = client.get("/forum/discourse/connect?sso=x&sig=y")

        assert response.headers["Location"].endswith("/account")
        assert "forum.test" not in response.headers["Location"]

    def test_a_confirmed_member_still_goes_straight_in(self, app, client, forum, links_sent):
        _paid_member(client, verified=True)

        assert client.get("/forum").headers["Location"] == FORUM_SSO
        assert links_sent == []


def test_a_failed_forum_sign_in_is_reported_not_looped(app, client, forum, links_sent):
    forum.handoff_error = "Invalid DiscourseConnect signature."
    _paid_member(client, verified=True)

    response = client.get("/forum/discourse/connect?sso=x&sig=y")

    assert response.headers["Location"].endswith("/account")
    assert any("could not be completed" in text for text in _flashes(client))


class TestJustAfterPaying:
    """Stripe's confirmation arrives seconds after the payment page closes. In
    between, the account page offered to resume a payment just made."""

    def _pending(self, client):
        member = make_member(email="justpaid@example.com", payment_status="pending_checkout",
                             stripe_checkout_session_id="cs_done")
        with client.session_transaction() as session:
            session["_user_id"] = str(member.user.id)
        return member

    def _stripe_says(self, monkeypatch, status):
        from conftest import billing

        monkeypatch.setattr(billing, "apply_runtime_stripe_config", lambda: {})
        monkeypatch.setattr(
            billing.stripe.checkout.Session, "retrieve",
            staticmethod(lambda session_id, **kwargs: {"id": session_id, "status": status}),
        )

    def test_a_finished_checkout_is_not_offered_again(self, app, client, monkeypatch):
        self._pending(client)
        self._stripe_says(monkeypatch, "complete")

        body = client.get("/account", follow_redirects=True).get_data(as_text=True)

        assert "Payment received" in body
        assert "Resume Payment" not in body

    def test_an_abandoned_one_still_is(self, app, client, monkeypatch):
        self._pending(client)
        self._stripe_says(monkeypatch, "open")

        body = client.get("/account", follow_redirects=True).get_data(as_text=True)

        assert "Resume Payment" in body
        assert "Payment received" not in body


class TestWhileASepaDebitClears:
    """Paid by SEPA, told "set up successfully" -- and then days of "not
    active" with nothing saying the money simply takes a while."""

    def test_the_account_page_says_it_is_on_its_way(self, app, client, forum, monkeypatch):
        member = make_member(email="sepa-first@example.com", payment_status="processing",
                             is_active=False, stripe_customer_id="cus_p")
        with client.session_transaction() as session:
            session["_user_id"] = str(member.user.id)
        # The page asks Stripe for the latest on every load; there is no Stripe here.
        monkeypatch.setattr(app_module, "refresh_member_billing_state", lambda *a, **k: (False, None, None))

        body = client.get("/account", follow_redirects=True).get_data(as_text=True)

        assert "SEPA takes a few days" in body
        assert "starts as soon as your payment has cleared" in body
        assert "not active" not in body


class TestAReturningStudentBeforeConfirming:
    """Their old account -- username, posts, usually a picture -- comes back
    when they confirm the university address. Until then the page asked for a
    photo and showed a new username they would never use."""

    def test_they_are_told_to_confirm_not_to_upload(self, app, client, forum, monkeypatch):
        from aeronautics_members.services.forum_import import import_forum_people

        forum.photo_approved = False
        import_forum_people([{"source_user_id": "9", "source_username": "BackB_L21",
                              "source_email": "back@edu.fh-joanneum.at", "year_group": "LAV21"}])
        member = _paid_member(client, verified=True)
        member.email_work = "back@edu.fh-joanneum.at"
        member.user.forum_username = "BackB_L21-2"
        db.session.commit()
        monkeypatch.setattr(app_module, "refresh_member_billing_state", lambda *a, **k: (False, None, None))

        body = client.get("/account", follow_redirects=True).get_data(as_text=True)

        assert "Confirm your university email address to get your old account back" in body
        assert "Upload Profile Picture" not in body
        assert "BackB_L21-2" not in body   # the username they are about to lose
        assert "BackB_L21" not in body     # nor the one that is not proven theirs yet


class TestAFailedRenewal:
    """A bounced renewal leaves the subscription running while Stripe retries.
    The page offered Rejoin, which was then refused."""

    def _failed(self, client, monkeypatch, subscription_status):
        member = make_member(email="bounced@example.com", payment_status="failed", is_active=False,
                             stripe_customer_id="cus_b", stripe_subscription_id="sub_b",
                             membership_ends_on=date(2020, 12, 31))
        with client.session_transaction() as session:
            session["_user_id"] = str(member.user.id)
        monkeypatch.setattr(
            app_module, "refresh_member_billing_state",
            lambda *a, **k: (False, {"id": "sub_b", "status": subscription_status}, None),
        )
        return client.get("/account", follow_redirects=True).get_data(as_text=True)

    def test_while_stripe_retries_they_are_asked_to_update_payment(self, app, client, monkeypatch):
        body = self._failed(client, monkeypatch, "past_due")

        assert "update your payment method under Manage Billing" in body
        assert "/account/rejoin" not in body

    def test_once_the_subscription_has_ended_they_can_rejoin(self, app, client, monkeypatch):
        body = self._failed(client, monkeypatch, "canceled")

        assert "/account/rejoin" in body
        assert "update your payment method" not in body


class TestErrorsSayWhatIsWrong:
    def test_the_signup_form_names_fields_as_the_form_does(self, app, client):
        body = client.post("/process-membership", data={
            "email_private": "someone@example.com", "member_category": "student",
            "email_work": "someone@gmail.com",
        }).get_data(as_text=True)

        assert "University or Company Email:" in body
        assert "Email Work" not in body

    def test_a_rejected_profile_change_says_why(self, app, client, monkeypatch):
        """The page said "please correct the profile form" and showed nothing."""
        member = make_member(email="typo@example.com")
        with client.session_transaction() as session:
            session["_user_id"] = str(member.user.id)
        monkeypatch.setattr(app_module, "refresh_member_billing_state", lambda *a, **k: (False, None, None))

        body = client.post("/account/profile", data={
            "profile-street": "Main", "profile-house_number": "1", "profile-postal_code": "8010",
            "profile-city": "Graz", "profile-country": "Austria",
            "profile-phone_private": "call me maybe", "profile-email_private": "typo@example.com",
        }).get_data(as_text=True)

        assert "Private Phone:" in body
        assert "Invalid phone number format" in body

    def test_a_forum_error_is_not_shown_raw(self, app, client, forum, monkeypatch):
        from aeronautics_members.db_models import ForumAccount

        member = _paid_member(client, verified=True)
        db.session.add(ForumAccount(user=member.user, member=member, provider="discourse",
                                    external_id=str(member.user.id), state="active",
                                    last_error="Discourse API request failed (422): Primary email has already been taken"))
        db.session.commit()
        monkeypatch.setattr(app_module, "refresh_member_billing_state", lambda *a, **k: (False, None, None))

        body = client.get("/account", follow_redirects=True).get_data(as_text=True)

        assert "Discourse API request failed" not in body
        assert "could not be updated just now" in body


class TestThePhotoUpload:
    def _page(self, client, forum, monkeypatch, **forum_state):
        forum.photo_approved = False
        for key, value in forum_state.items():
            setattr(forum, key, value)
        _paid_member(client, verified=True)
        monkeypatch.setattr(app_module, "refresh_member_billing_state", lambda *a, **k: (False, None, None))
        return client.get("/account", follow_redirects=True).get_data(as_text=True)

    def test_says_what_photo_is_wanted(self, app, client, forum, monkeypatch):
        body = self._page(client, forum, monkeypatch)

        assert "A real photo of you" in body
        assert "optimized down to the avatar limit" not in body

    def test_a_rejection_gives_its_reason_as_a_reason(self, app, client, forum, monkeypatch):
        rejected = types.SimpleNamespace(status="rejected", review_note="That is a giraffe.")
        forum.get_latest_submission = lambda member: rejected

        body = self._page(client, forum, monkeypatch)

        assert "Reason: That is a giraffe." in body


def test_the_forum_card_says_its_status_once(app, client, forum, monkeypatch):
    forum.photo_approved = False
    _paid_member(client, verified=True)
    monkeypatch.setattr(app_module, "refresh_member_billing_state", lambda *a, **k: (False, None, None))

    body = client.get("/account", follow_redirects=True).get_data(as_text=True)

    assert body.count("Upload a profile picture to complete your forum access.") == 1
    assert "Please upload a profile picture before your forum access can be completed." not in body


def test_the_portal_is_english_whatever_the_browser_asks(app, client):
    """The German translation lags the portal; a German browser picked it up
    on its own and showed a half-translated site."""
    body = client.get("/?lang=de", headers={"Accept-Language": "de-AT,de;q=0.9"}).get_data(as_text=True)

    assert '<html lang="en"' in body
    assert "language-selector" not in body


def test_the_portal_is_dark_whatever_the_device_prefers(app, client):
    """The site has one look, dark. It must not follow a device set to light
    mode into a half-styled light page."""
    body = client.get("/").get_data(as_text=True)
    stylesheet = (Path(app.static_folder) / "style.css").read_text()

    assert 'data-bs-theme="dark"' in body
    assert '<meta name="color-scheme" content="dark">' in body
    assert "prefers-color-scheme" not in stylesheet


class TestTheSignupSentTwice:
    """A double click while Stripe was being asked for the payment page -- or
    filling the form in again after cancelling -- answered "already exists"."""

    FORM = {
        "salutation": "Ms", "first_name": "Dora", "last_name": "Double", "street": "Main",
        "house_number": "1", "postal_code": "8010", "city": "Graz", "country": "Austria",
        "phone_private": "+43123", "email_private": "dora@example.com",
        "email_work": "dora.double@edu.fh-joanneum.at", "member_category": "student",
        "year_group": "LAV25", "password": "right-password", "confirm_password": "right-password",
        "payment_method": "checkout", "terms_accepted": "y",
    }

    @pytest.fixture
    def checkouts(self, monkeypatch):
        from aeronautics_members.blueprints import _signup, public

        opened = []

        def open_checkout(member):
            opened.append(member.id)
            return types.SimpleNamespace(url=f"https://checkout.stripe.test/{member.id}"), {}

        monkeypatch.setattr(_signup, "create_checkout_session_for_member", open_checkout)
        monkeypatch.setattr(public, "create_checkout_session_for_member", open_checkout)
        monkeypatch.setattr(_signup, "send_email_verification_email", lambda *a, **k: True)
        monkeypatch.setattr(_signup, "send_work_email_verification_email", lambda *a, **k: True)
        return opened

    def test_the_second_one_goes_on_to_payment(self, app, client, checkouts):
        first = client.post("/process-membership", data=self.FORM)
        client.post("/logout")

        second = client.post("/process-membership", data=self.FORM)

        assert first.headers["Location"].startswith("https://checkout.stripe.test/")
        assert second.headers["Location"] == first.headers["Location"]
        from aeronautics_members.db_models import User
        assert len(db.session.execute(db.select(User).filter_by(email="dora@example.com")).scalars().all()) == 1

    def test_not_with_a_different_password(self, app, client, checkouts):
        client.post("/process-membership", data=self.FORM)
        client.post("/logout")

        second = client.post("/process-membership", data={
            **self.FORM, "password": "wrong-password", "confirm_password": "wrong-password",
        })

        assert second.headers["Location"].endswith("/login")
        assert len(checkouts) == 1

    def test_not_once_payment_has_started_at_stripe(self, app, client, checkouts):
        client.post("/process-membership", data=self.FORM)
        client.post("/logout")
        from aeronautics_members.db_models import Member
        member = db.session.execute(db.select(Member).filter_by(email_private="dora@example.com")).scalar_one()
        member.stripe_customer_id = "cus_started"
        db.session.commit()

        second = client.post("/process-membership", data=self.FORM)

        assert second.headers["Location"].endswith("/login")


def test_payment_buttons_show_they_are_working(app, client):
    body = client.get("/join").get_data(as_text=True)

    assert 'data-busy-text="Taking you to payment…"' in body
    assert "submit-once.js" in body


class TestAnEmailPerClickNoMore:
    """Buttons that send an email sent one per click -- and a password reset
    made each new one kill the last, so the first to arrive said "invalid"."""

    def test_confirmation_resent_once_within_the_minute(self, app, client, monkeypatch):
        from aeronautics_members.blueprints import account as account_module

        sent = []
        monkeypatch.setattr(account_module, "send_email_verification_email",
                            lambda app, user: sent.append(user.email) or True)
        member = make_member(email="twice@example.com")
        with client.session_transaction() as session:
            session["_user_id"] = str(member.user.id)

        client.post("/account/resend-verification")
        client.post("/account/resend-verification")

        assert sent == ["twice@example.com"]
        assert any("a moment ago" in text for text in _flashes(client))

    def test_university_confirmation_resent_once_within_the_minute(self, app, client, monkeypatch):
        from aeronautics_members.blueprints import account as account_module

        sent = []
        monkeypatch.setattr(account_module, "send_work_email_verification_email",
                            lambda app, member: sent.append(member.email_work) or True)
        member = make_member(email="twice-work@example.com", email_work="twice@edu.fh-joanneum.at")
        with client.session_transaction() as session:
            session["_user_id"] = str(member.user.id)

        client.post("/account/resend-work-verification")
        client.post("/account/resend-work-verification")

        assert sent == ["twice@edu.fh-joanneum.at"]

    def test_a_double_click_on_forgot_password_keeps_the_first_link_working(self, app, client, monkeypatch):
        from aeronautics_members.blueprints import auth as auth_module
        from aeronautics_members.db_models import User

        sent = []
        monkeypatch.setattr(auth_module, "send_password_reset_email",
                            lambda app, user: sent.append(user.password_reset_nonce) or True)
        make_member(email="forgot@example.com")

        client.post("/forgot-password", data={"email": "forgot@example.com"})
        client.post("/forgot-password", data={"email": "forgot@example.com"})

        user = db.session.execute(db.select(User).filter_by(email="forgot@example.com")).scalar_one()
        assert len(sent) == 1
        assert user.password_reset_nonce == sent[0]  # the link in that one email still works

    def test_the_forum_does_not_send_one_per_attempt(self, app, client, forum, links_sent):
        _paid_member(client, verified=False)

        client.get("/forum")
        client.get("/forum")

        assert links_sent == ["journey@example.com"]


def test_creating_a_profile_twice_goes_on_to_payment(app, client, monkeypatch):
    from aeronautics_members.blueprints import account as account_module

    member = make_member(email="profile-twice@example.com", payment_status="pending_checkout")
    with client.session_transaction() as session:
        session["_user_id"] = str(member.user.id)
    monkeypatch.setattr(
        account_module, "create_checkout_session_for_member",
        lambda m: (types.SimpleNamespace(url="https://checkout.stripe.test/again"), {}),
    )

    response = client.post("/account/create-membership", data={"payment_method": "checkout"})

    assert response.headers["Location"] == "https://checkout.stripe.test/again"


class TestDatesLookTheSameEverywhere:
    """Members saw 31.12.2026 while admins saw 2026-12-31 for the same date,
    and times were UTC -- an hour or two behind the clock in Graz."""

    def test_a_date(self, app):
        assert app.jinja_env.filters["date_display"](date(2026, 12, 31)) == "31.12.2026"

    def test_a_stored_time_is_shown_in_vienna_summer_time(self, app):
        from datetime import datetime

        show = app.jinja_env.filters["datetime_display"]
        assert show(datetime(2026, 7, 1, 12, 5)) == "01.07.2026 14:05"

    def test_and_in_vienna_winter_time(self, app):
        from datetime import datetime

        show = app.jinja_env.filters["datetime_display"]
        assert show(datetime(2026, 12, 1, 12, 5)) == "01.12.2026 13:05"

    def test_a_moment_late_at_night_falls_on_the_vienna_day(self, app):
        """23:30 UTC on 31 December is already New Year in Graz."""
        from datetime import datetime

        assert app.jinja_env.filters["date_display"](datetime(2026, 12, 31, 23, 30)) == "01.01.2027"

    def test_a_time_with_its_own_zone_is_converted(self, app):
        """The update runner writes ISO text in the server's own zone."""
        show = app.jinja_env.filters["datetime_display"]
        assert show("2026-09-27T08:30:00+00:00") == "27.09.2026 10:30"

    def test_nothing_shows_nothing(self, app):
        assert app.jinja_env.filters["date_display"](None) == ""
        assert app.jinja_env.filters["datetime_display"](None) == ""

    def test_no_page_or_email_formats_a_date_its_own_way(self):
        templates = Path(__file__).resolve().parent.parent / "aeronautics_members" / "templates"
        offenders = [
            str(path.relative_to(templates))
            for path in templates.rglob("*.html")
            if "strftime" in path.read_text() or "UTC" in path.read_text()
        ]
        assert not offenders, "use |date_display or |datetime_display: " + ", ".join(offenders)


def test_everything_the_stylesheet_loads_is_there(app):
    """A font file left out of a deploy falls back silently to another face."""
    import re

    static = Path(app.static_folder)
    stylesheet = (static / "style.css").read_text()
    referenced = re.findall(r'url\("(fonts/[^"]+)"\)', stylesheet)

    assert referenced, "the fonts are expected to come from static/fonts"
    assert [name for name in referenced if not (static / name).is_file()] == []


def test_the_navbar_marks_the_section_you_are_in(app, client):
    member = make_member(email="nav@example.com")
    with client.session_transaction() as session:
        session["_user_id"] = str(member.user_id)

    body = client.get("/account", follow_redirects=True).get_data(as_text=True)

    assert 'class="nav-link active" aria-current="page" href="/account"' in body


def test_a_success_message_carries_the_tick(app, client):
    with client.session_transaction() as session:
        session["_flashes"] = [("success", "Saved."), ("warning", "Careful.")]

    body = client.get("/login").get_data(as_text=True)

    assert body.count('class="success-check"') == 1
    assert '<div class="alert alert-warning">Careful.</div>' in body


def test_states_are_shown_as_status_labels_not_bootstrap_badges():
    templates = Path(__file__).resolve().parent.parent / "aeronautics_members" / "templates"
    offenders = [
        str(path.relative_to(templates))
        for path in templates.rglob("*.html")
        if "emails" not in path.parts and 'class="badge' in path.read_text()
    ]
    assert not offenders, "use status-label with a status-* tone: " + ", ".join(offenders)


def test_the_tab_icon_is_the_square_mark(app, client):
    """The full logo was unreadable at tab size."""
    body = client.get("/login").get_data(as_text=True)
    static = Path(app.static_folder)

    for name in ("favicon.svg", "favicon-32.png", "apple-touch-icon.png"):
        assert f"/static/{name}" in body
        assert (static / name).is_file()
    assert "logo_joanneum_aeronautics_negativ.svg\" type=\"image/svg+xml\"" not in body


class TestTheConfirmationReminder:
    """It said "Both email addresses need confirming" to members who had given
    only one -- every alumnus, partner and lecturer without a university address."""

    def _page(self, client):
        return client.get("/account", follow_redirects=True).get_data(as_text=True)

    def test_one_address_is_called_one(self, app, client, forum):
        _paid_member(client, verified=False)

        body = self._page(client)

        assert "Both email addresses" not in body
        assert "Please confirm your email address." in body

    def test_two_waiting_addresses_are_called_both(self, app, client, forum):
        member = _paid_member(client, verified=False)
        member.email_work = "journey@edu.fh-joanneum.at"
        db.session.commit()

        assert "Both email addresses need confirming." in self._page(client)

    def test_only_the_university_address_waiting(self, app, client, forum):
        member = _paid_member(client, verified=True)
        member.email_work = "journey@edu.fh-joanneum.at"
        db.session.commit()

        body = self._page(client)

        assert "Please confirm your university email address." in body
        assert "Both email addresses" not in body
