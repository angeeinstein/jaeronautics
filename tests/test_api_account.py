"""My Account's API (api/account.py), what the member forms offer
(api/form_options.py), the footer (api/site.py), and what Flask said before
sending the browser to one of the app's pages (GET /api/v1/messages).

What the page does with each answer is in frontend/src/pages/account/;
the behaviour carried over from the old page is tested where it always was
(test_member_journey.py, test_rejoin.py, test_email_confirmation_ux.py ...).
"""
from datetime import date

import pytest

from api_helpers import send, signed_in
from conftest import db, make_member
from aeronautics_members.db_models import AuditLog, Member, MemberProfileChangeRequest, User
from aeronautics_members.services import workflows

CONTACT = {"street": "Main Street", "house_number": "1", "postal_code": "8010", "city": "Graz",
           "country": "Austria", "phone_private": "+43000000000", "email_private": "anna@example.com",
           "email_work": "anna@edu.fh-joanneum.at"}
IDENTITY = {"salutation": "Ms", "first_name": "Anna", "last_name": "Berger",
            "member_category": "student", "year_group": "LAV25"}


@pytest.fixture(autouse=True)
def no_stripe(monkeypatch):
    """The page asks Stripe for the latest on every load; there is no Stripe here."""
    monkeypatch.setattr(workflows, "refresh_member_billing_state", lambda *a, **k: (False, None, None))


def _anna(**overrides):
    fields = dict(salutation="Ms", first_name="Anna", last_name="Berger", year_group="LAV25",
                  member_category="student", email_work="anna@edu.fh-joanneum.at")
    fields.update(overrides)
    return make_member(email="anna@example.com", **fields)


class TestTheFormChoices:
    def test_open_to_anybody(self, app, client):
        options = client.get("/api/v1/forms/options").get_json()

        assert {"value": "Austria", "label": "Austria"} in options["countries"]
        assert all(choice["value"] for choice in options["countries"]), "no '-- Select --' entry"
        assert [choice["value"] for choice in options["salutations"]] == ["Mr", "Ms", "Diverse"]
        assert options["invoice_payments"] is False

    def test_each_kind_of_member_says_what_it_is_asked(self, app, client):
        kinds = {kind["value"]: kind for kind in client.get("/api/v1/forms/options").get_json()["member_categories"]}

        assert (kinds["student"]["year_group"], kinds["student"]["university_email_required"]) == ("required", True)
        assert (kinds["alumni"]["year_group"], kinds["alumni"]["university_email_required"]) == ("optional", False)
        assert kinds["partner"]["year_group"] == "hidden"
        assert list(kinds)[0] == "student", "the common case first"

    def test_and_which_addresses(self, app, client):
        options = client.get("/api/v1/forms/options").get_json()
        kinds = {kind["value"]: kind for kind in options["member_categories"]}

        assert (kinds["student"]["work_email_whose"], kinds["student"]["account_email"]) == ("student", "private")
        assert (kinds["staff"]["work_email_whose"], kinds["staff"]["account_email"]) == (
            "staff", "private_or_institute")
        assert kinds["staff"]["work_email_at_joining"] is True and kinds["staff"]["university_email_required"] is False
        assert (kinds["partner"]["work_email_whose"], kinds["partner"]["account_email"]) == (None, "any")
        assert (options["student_domains"], options["staff_domains"]) == (["edu.fh-joanneum.at"], ["fh-joanneum.at"])
        assert [programme["code"] for programme in options["programmes"]] == ["LAV", "MAV"]


class TestOneAddressForBoth:
    def test_to_be_confirmed_once(self, app, client):
        member = make_member(email="lena@fh-joanneum.at", email_work="lena@fh-joanneum.at",
                             member_category="staff", first_name="Lena")

        account = signed_in(client, member.user).get("/api/v1/account").get_json()

        assert account["to_confirm"]["text"] == "Please confirm your email address. We sent you a link."


class TestWhatFlaskSaid:
    def test_handed_out_once(self, app, client):
        with client.session_transaction() as session:
            session["_flashes"] = [("warning", "This deletion link has expired."), ("message", "Plain.")]

        first = client.get("/api/v1/messages").get_json()["messages"]
        second = client.get("/api/v1/messages").get_json()["messages"]

        assert first == [{"tone": "warning", "text": "This deletion link has expired."},
                         {"tone": "info", "text": "Plain."}]
        assert second == []


class TestTheFooter:
    def test_the_portals_own_legal_texts(self, app, client):
        links = {link["label"]: link for link in client.get("/api/v1/site").get_json()["footer"]}

        assert links["Impressum"] == {"label": "Impressum", "url": "/legal/legal-notice", "external": False}
        assert links["Contact"]["url"].startswith("mailto:")
        assert links["Website"]["external"] is True

    def test_or_an_address_elsewhere(self, app, client, monkeypatch):
        from aeronautics_members.api import site

        monkeypatch.setattr(site, "PRIVACY_URL", "https://example.org/privacy")

        links = {link["label"]: link for link in client.get("/api/v1/site").get_json()["footer"]}

        assert links["Privacy"] == {"label": "Privacy", "url": "https://example.org/privacy", "external": True}


class TestThePage:
    def test_signing_in_is_required(self, app, client):
        assert client.get("/api/v1/account").status_code == 401
        assert client.get("/account").status_code == 302

    def test_what_is_on_it(self, app, client):
        member = _anna(payment_status="paid", is_active=True, membership_starts_on=date(2026, 1, 1),
                       membership_ends_on=date(2099, 12, 31), renewal_due_on=date(2099, 12, 31))
        account = signed_in(client, member.user).get("/api/v1/account").get_json()

        assert account["export_url"] == "/account/data-export"
        assert account["member"]["contact"] == {**CONTACT, "phone_work": None, "company_name": None}
        assert account["member"]["identity"] == {**IDENTITY, "title": None, "member_category_label": "Student"}
        membership = account["member"]["membership"]
        assert (membership["status_label"], membership["tone"], membership["active"]) == ("Paid", "active", True)
        assert (membership["renews_on"], membership["auto_renew"]) == ("2099-12-31", True)
        assert membership["note"] is None

    def test_a_cancelled_renewal_says_until_when(self, app, client):
        member = _anna(payment_status="cancel_scheduled", is_active=True, cancel_at_period_end=True,
                       membership_ends_on=date(2099, 12, 31))
        membership = signed_in(client, member.user).get("/api/v1/account").get_json()["member"]["membership"]

        assert membership["renews_on"] is None
        assert membership["note"] == {"tone": "warning",
                                      "text": "Your membership stays active until 31.12.2099, but it will not renew."}

    def test_deleting_says_what_it_costs_while_paid_up(self, app, client):
        member = _anna(payment_status="paid", is_active=True, stripe_customer_id="cus_a",
                       stripe_subscription_id="sub_a", membership_ends_on=date(2099, 12, 31))
        account = signed_in(client, member.user).get("/api/v1/account").get_json()["member"]

        assert "already paid until 31.12.2099" in account["deletion_note"]["text"]
        assert account["may_cancel_instead"] is True


class TestContactDetails:
    def test_saved_with_the_page_back(self, app, client):
        member = _anna()

        response = send(signed_in(client, member.user), "PUT", "/api/v1/account/contact",
                        {**CONTACT, "city": "Vienna"})

        assert response.status_code == 200
        answer = response.get_json()
        assert answer["messages"] == [{"tone": "success", "text": "Saved."}]
        assert answer["account"]["member"]["contact"]["city"] == "Vienna"
        assert db.session.execute(db.select(AuditLog).filter_by(event_type="contact_details_updated")).scalar_one()

    def test_the_rules_of_the_kind_of_member_apply(self, app, client):
        """A student cannot move their university address to a private one here either."""
        member = _anna()

        response = send(signed_in(client, member.user), "PUT", "/api/v1/account/contact",
                        {**CONTACT, "email_work": "anna@gmail.com"})

        assert response.status_code == 400
        assert "student address" in response.get_json()["error"]["fields"]["email_work"]

    def test_a_field_it_does_not_know_is_refused(self, app, client):
        member = _anna()

        response = send(signed_in(client, member.user), "PUT", "/api/v1/account/contact",
                        {**CONTACT, "first_name": "Someone else"})

        assert response.status_code == 400
        assert db.session.get(User, member.user_id).member.first_name == "Anna"

    def test_the_company_is_saved_and_kept_when_left_out(self, app, client):
        member = _anna()

        send(signed_in(client, member.user), "PUT", "/api/v1/account/contact",
             {**CONTACT, "company_name": "Example Aero GmbH"})
        assert db.session.get(Member, member.id).company_name == "Example Aero GmbH"

        # An older page that does not know the field leaves it as it is.
        response = send(client, "PUT", "/api/v1/account/contact", {**CONTACT, "city": "Vienna"})
        assert response.get_json()["account"]["member"]["contact"]["company_name"] == "Example Aero GmbH"

    def test_an_account_without_a_membership_has_none(self, app, client):
        staff = User(email="staff@example.org")
        staff.set_password("a-password-1")
        db.session.add(staff)
        db.session.commit()

        response = send(signed_in(client, staff), "PUT", "/api/v1/account/contact", CONTACT)

        assert (response.status_code, response.get_json()["error"]["code"]) == (409, "no_membership")


class TestChangeRequests:
    def test_asked_for_and_shown_until_decided(self, app, client):
        member = _anna()

        response = send(signed_in(client, member.user), "POST", "/api/v1/account/change-request",
                        {**IDENTITY, "last_name": "Huber", "note": "Married."})

        assert response.status_code == 201
        waiting = response.get_json()["account"]["member"]["change_request"]
        assert (waiting["last_name"], waiting["note"], waiting["member_category_label"]) == (
            "Huber", "Married.", "Student")
        assert member.last_name == "Berger", "an admin decides"

    def test_one_at_a_time(self, app, client):
        member = _anna()
        signed_in(client, member.user)
        send(client, "POST", "/api/v1/account/change-request", {**IDENTITY, "last_name": "Huber"})

        response = send(client, "POST", "/api/v1/account/change-request", {**IDENTITY, "last_name": "Maier"})

        assert (response.status_code, response.get_json()["error"]["code"]) == (409, "request_pending")

    def test_nothing_different_is_not_a_request(self, app, client):
        member = _anna()

        response = send(signed_in(client, member.user), "POST", "/api/v1/account/change-request", IDENTITY)

        assert (response.status_code, response.get_json()["error"]["code"]) == (400, "nothing_changed")

    def test_the_year_group_rules_apply(self, app, client):
        member = _anna()

        response = send(signed_in(client, member.user), "POST", "/api/v1/account/change-request",
                        {**IDENTITY, "year_group": "2025"})

        assert response.get_json()["error"]["fields"] == {
            "year_group": "Three letters and two digits: the programme and the year you started, like LAV25."}

    def test_withdrawn(self, app, client):
        member = _anna()
        signed_in(client, member.user)
        send(client, "POST", "/api/v1/account/change-request", {**IDENTITY, "last_name": "Huber"})
        request_id = db.session.execute(db.select(MemberProfileChangeRequest.id)).scalar_one()

        response = send(client, "DELETE", f"/api/v1/account/change-request/{request_id}")

        assert response.get_json()["account"]["member"]["change_request"] is None
        assert db.session.get(MemberProfileChangeRequest, request_id).status == "canceled"

    def test_not_somebody_elses(self, app, client):
        other = make_member(email="other@example.com")
        send(signed_in(client, other.user), "POST", "/api/v1/account/change-request",
             {"salutation": "Mr", "first_name": "Otto", "last_name": "Other", "member_category": "alumni"})
        request_id = db.session.execute(db.select(MemberProfileChangeRequest.id)).scalar_one()
        member = _anna()
        from flask import g

        g.pop("_login_user", None)
        response = send(signed_in(client, member.user), "DELETE", f"/api/v1/account/change-request/{request_id}")

        assert response.status_code == 404
        assert db.session.get(MemberProfileChangeRequest, request_id).status == "pending"


class TestPaying:
    def test_no_billing_page_without_a_stripe_customer(self, app, client):
        member = _anna()

        response = send(signed_in(client, member.user), "POST", "/api/v1/account/billing")

        assert (response.status_code, response.get_json()["error"]["code"]) == (409, "no_billing")

    def test_the_billing_page_comes_back_to_my_account(self, app, client, monkeypatch):
        from aeronautics_members.services import billing

        made = []
        monkeypatch.setattr(billing, "apply_runtime_stripe_config", lambda: {})
        monkeypatch.setattr(billing.stripe.billing_portal.Session, "create",
                            staticmethod(lambda **kwargs: made.append(kwargs) or type(
                                "Portal", (), {"url": "https://billing.stripe.test/p"})()))
        member = _anna(stripe_customer_id="cus_a")

        response = send(signed_in(client, member.user), "POST", "/api/v1/account/billing")

        assert response.get_json() == {"url": "https://billing.stripe.test/p"}
        assert made[0]["return_url"].endswith("/account")

    def test_nothing_to_resume_for_a_paid_membership(self, app, client):
        member = _anna(payment_status="paid", is_active=True, membership_ends_on=date(2099, 12, 31))

        response = send(signed_in(client, member.user), "POST", "/api/v1/account/payment")

        assert (response.status_code, response.get_json()["error"]["code"]) == (409, "nothing_to_resume")


class TestThePassword:
    def test_changed_after_the_current_one(self, app, client):
        member = _anna()

        response = send(signed_in(client, member.user), "PUT", "/api/v1/account/password",
                        {"current_password": "initial-password", "new_password": "a-new-password"})

        assert response.get_json() == {"tone": "success", "text": "Your password has been changed."}
        assert db.session.get(User, member.user_id).check_password("a-new-password")

    def test_not_without_it(self, app, client):
        member = _anna()

        response = send(signed_in(client, member.user), "PUT", "/api/v1/account/password",
                        {"current_password": "a-guess", "new_password": "a-new-password"})

        assert response.get_json()["error"]["fields"] == {"current_password": "This is not your current password."}
        assert db.session.get(User, member.user_id).check_password("initial-password")

    def test_not_too_short(self, app, client):
        member = _anna()

        response = send(signed_in(client, member.user), "PUT", "/api/v1/account/password",
                        {"current_password": "initial-password", "new_password": "short"})

        assert "new_password" in response.get_json()["error"]["fields"]


def _half_red_half_blue():
    from io import BytesIO

    from PIL import Image

    image = Image.new("RGB", (400, 300), (0, 0, 255))
    image.paste((255, 0, 0), (0, 0, 200, 300))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


class TestTheForumPicture:
    @pytest.fixture
    def forum_on(self, app, tmp_path, monkeypatch):
        import aeronautics_members.forum_service as forum_service
        from aeronautics_members.db_models import Setting

        db.session.merge(Setting(key="forum_integration_enabled", value="True"))
        db.session.commit()
        monkeypatch.setattr(forum_service, "get_forum_storage_dir", lambda: tmp_path)

    def _paid(self):
        return _anna(payment_status="paid", is_active=True, membership_starts_on=date(2026, 1, 1),
                     membership_ends_on=date(2099, 12, 31))

    def test_uploaded_cropped_to_the_square_chosen(self, app, client, forum_on):
        from PIL import Image

        from aeronautics_members.db_models import ForumAvatarSubmission

        member = self._paid()
        signed_in(client, member.user)

        response = client.post("/api/v1/account/picture?zoom=2&x=0&y=0.5",
                               data={"image": (_half_red_half_blue(), "me.png")},
                               content_type="multipart/form-data")

        assert response.status_code == 201, response.get_json()
        assert response.get_json()["status"] == "pending_avatar"
        submission = db.session.execute(db.select(ForumAvatarSubmission)).scalar_one()
        with Image.open(submission.storage_path) as stored:
            assert stored.width == stored.height
            # The right edge of the square is still the red left half: the square was cut on the left.
            red, _green, blue = stored.convert("RGB").getpixel((stored.width - 2, stored.height // 2))
            assert red > 200 and blue < 60
        assert db.session.execute(db.select(AuditLog).filter_by(event_type="avatar_uploaded")).scalar_one()

    def test_not_a_picture_is_said_at_the_field(self, app, client, forum_on):
        from io import BytesIO

        member = self._paid()
        signed_in(client, member.user)

        response = client.post("/api/v1/account/picture", data={"image": (BytesIO(b"not a picture"), "me.png")},
                               content_type="multipart/form-data")

        assert response.status_code == 400
        assert "image" in response.get_json()["error"]["fields"]

    def test_a_crop_out_of_range_is_refused(self, app, client, forum_on):
        member = self._paid()
        signed_in(client, member.user)

        response = client.post("/api/v1/account/picture?zoom=9",
                               data={"image": (_half_red_half_blue(), "me.png")},
                               content_type="multipart/form-data")

        assert response.status_code == 400

    def test_not_before_the_membership_is_active(self, app, client, forum_on):
        member = _anna()
        signed_in(client, member.user)

        response = client.post("/api/v1/account/picture", data={"image": (_half_red_half_blue(), "me.png")},
                               content_type="multipart/form-data")

        assert (response.status_code, response.get_json()["error"]["code"]) == (409, "not_active")

    def test_the_forum_page_has_the_card(self, app, client, forum_on):
        member = self._paid()

        card = signed_in(client, member.user).get("/api/v1/account/forum").get_json()

        assert card["status"] == "needs_avatar"
        assert card["picture"]["upload"] is True


class TestTheDeletionLink:
    def test_what_it_would_do(self, app, client):
        from aeronautics_members.services import privacy

        member = _anna(payment_status="paid", is_active=True, stripe_customer_id="cus_a",
                       stripe_subscription_id="sub_a", membership_ends_on=date(2099, 12, 31))
        token = privacy.build_account_deletion_token(member.user)

        impact = signed_in(client, member.user).get(f"/api/v1/account/deletion/{token}").get_json()

        assert (impact["subscription_active"], impact["has_stripe_customer"], impact["is_last_admin"]) == (
            True, True, False)
        assert impact["export_url"] == "/account/data-export"

    def test_only_with_a_yes(self, app, client):
        from aeronautics_members.services import privacy

        member = _anna()
        token = privacy.build_account_deletion_token(member.user)

        response = send(signed_in(client, member.user), "POST", f"/api/v1/account/deletion/{token}",
                        {"confirm": False})

        assert response.status_code == 400
        assert member.deleted_at is None

    def test_done_it_says_so_on_the_start_page(self, app, client):
        from aeronautics_members.services import privacy

        member = _anna()
        token = privacy.build_account_deletion_token(member.user)

        send(signed_in(client, member.user), "POST", f"/api/v1/account/deletion/{token}", {"confirm": True})

        assert member.deleted_at is not None
        with client.session_transaction() as session:
            assert any("have been deleted" in text for _tone, text in session["_flashes"])
