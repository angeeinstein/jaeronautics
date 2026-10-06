"""The admin dashboard's data (api/admin_dashboard.py; the page is
frontend/src/pages/admin/AdminDashboard.tsx).

Two questions, in this order: is anything waiting for me -- each kind only for
whoever may act on it -- and how is the membership doing, in four figures with
the one number that explains each. Then the latest log entries.
"""
from datetime import date

import pytest

from conftest import db, make_member
from aeronautics_members.db_models import AuditLog
from api_helpers import signed_in
from test_admin_reviews import _name_change, _picture, _staff

URL = "/api/v1/admin/dashboard"


@pytest.fixture
def admin(app):
    return _staff("boss@example.org", "admin")


def _dashboard(client):
    response = client.get(URL)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


class TestWhatIsWaiting:
    def test_each_kind_with_its_oldest(self, client, admin):
        member = make_member(email="dash@example.com")
        _name_change(member)
        _picture(member)
        signed_in(client, admin)

        attention = _dashboard(client)["attention"]

        assert attention["name_changes"]["count"] == 1
        assert attention["name_changes"]["summary"] == "Test Member → Test Photograph"
        assert attention["pictures"]["count"] == 1 and attention["pictures"]["summary"] == "Test Member"
        assert attention["pictures"]["at"].endswith("Z")

    def test_a_change_that_is_not_a_name_names_its_fields(self, client, admin):
        member = make_member(email="dash@example.com")
        _name_change(member, last_name=member.last_name, street="New Street")
        signed_in(client, admin)

        assert _dashboard(client)["attention"]["name_changes"]["summary"].startswith("Test Member: ")

    def test_nothing_waiting(self, client, admin):
        signed_in(client, admin)

        attention = _dashboard(client)["attention"]

        assert attention["name_changes"] == {"count": 0, "summary": None, "at": None}
        assert attention["pictures"]["count"] == 0 and attention["sync_problems"]["count"] == 0

    def test_a_kind_the_person_may_not_act_on_is_left_out(self, client):
        treasurer = _staff("money@example.org", "treasurer")
        signed_in(client, treasurer)

        attention = _dashboard(client)["attention"]

        assert attention == {"name_changes": None, "pictures": None, "sync_problems": None,
                             "health_problems": None}


class TestTheFigures:
    def test_four_figures_not_eleven(self, client, admin):
        signed_in(client, admin)

        figures = _dashboard(client)["figures"]

        assert [figure["key"] for figure in figures] == ["active", "cancelled", "forum"]
        assert figures[0]["label"] == "Active members" and figures[0]["note"].startswith("of ")
        assert figures[1]["note"] == "nobody has cancelled"

    def test_a_shared_end_date_is_named(self, client, admin):
        ends = date(date.today().year, 12, 31)
        for index in range(2):
            make_member(email=f"leaving{index}@example.com", cancel_at_period_end=True,
                        membership_ends_on=ends, is_active=True)
        signed_in(client, admin)

        cancelled = _dashboard(client)["figures"][1]

        assert cancelled["label"] == f"Ending {ends:%d.%m.%Y}" and cancelled["value"] == 2

    def test_waiting_for_payment_is_named_under_the_members(self, client, admin):
        make_member(email="paying@example.com", payment_status="pending_checkout")
        signed_in(client, admin)

        assert "1 waiting for payment" in _dashboard(client)["figures"][0]["note"]

    def test_the_figures_lead_to_the_list_behind_them(self, client, admin):
        signed_in(client, admin)

        links = {figure["key"]: figure["link_url"] for figure in _dashboard(client)["figures"]}

        assert links == {"active": "/admin/accounts?membership=active",
                         "cancelled": "/admin/accounts?membership=ending", "forum": None}

    def test_the_ending_figure_and_its_list_count_the_same_people(self, client, admin):
        """Still a member, not renewing. One whose membership is already over
        is not "ending" any more, whatever the cancellation flag still says."""
        ends = date(date.today().year, 12, 31)
        make_member(email="leaving@example.com", cancel_at_period_end=True, membership_ends_on=ends,
                    payment_status="cancel_scheduled", is_active=True)
        make_member(email="stopped@example.com", payment_status="canceled", membership_ends_on=ends, is_active=True)
        make_member(email="gone@example.com", cancel_at_period_end=True, payment_status="expired",
                    membership_ends_on=date(date.today().year - 1, 12, 31), is_active=False)
        signed_in(client, admin)

        figure = _dashboard(client)["figures"][1]
        listed = client.get("/api/v1/admin/accounts?membership=ending").get_json()

        assert figure["value"] == 2
        assert listed["total"] == 2 and listed["membership_counts"]["ending"] == 2

    def test_but_not_for_somebody_who_may_not_open_it(self, client):
        signed_in(client, _staff("money@example.org", "treasurer"))

        assert {figure["link_url"] for figure in _dashboard(client)["figures"]} == {None}


class TestRecentActivity:
    def test_the_latest_entries(self, client, admin):
        member = make_member(email="seen@example.com")
        db.session.add(AuditLog(category="account", event_type="email_changed", target_user=member.user))
        db.session.commit()
        signed_in(client, admin)

        [entry] = _dashboard(client)["recent_activity"]

        assert (entry["category"], entry["event_type"], entry["target"]) == ("account", "email_changed",
                                                                              "seen@example.com")

    def test_none_without_the_log(self, client):
        signed_in(client, _staff("money@example.org", "treasurer"))

        assert _dashboard(client)["recent_activity"] is None


class TestWhoMaySeeIt:
    def test_signed_out(self, client):
        assert client.get(URL).status_code == 401

    def test_a_member(self, app, client):
        signed_in(client, make_member().user)

        assert client.get(URL).status_code == 403
