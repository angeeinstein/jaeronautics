"""The account list: a row opens the account, and a header sorts by its column.

The View button in its own column was one more thing to aim at on every row;
the row itself is the target now. Sorting is done by the database, not the
page, because the list is paged -- sorting the fifty on screen would put the
wrong fifty there.
"""
import re

import pytest

from conftest import app_module, db, make_member
from aeronautics_members.db_models import User
from test_forum_account_claim import _archived

ADMIN_EMAIL = "zz-admin@example.com"


@pytest.fixture
def admin_client(app, client):
    admin = User(email=ADMIN_EMAIL)
    admin.set_password("x")
    admin.grant_role(app_module.get_role("admin"))
    db.session.add(admin)
    db.session.commit()
    with client.session_transaction() as session:
        session["_user_id"] = str(admin.id)
    return client


@pytest.fixture
def people(app):
    """Three members, deliberately not in any order by address or by name."""
    anna = make_member(email="c-anna@example.com", first_name="Anna", last_name="Zeller",
                       payment_status="paid", is_active=True)
    bernd = make_member(email="a-bernd@example.com", first_name="Bernd", last_name="Huber",
                        payment_status="unpaid", is_active=False)
    clara = make_member(email="b-clara@example.com", first_name="Clara", last_name="Maier",
                        payment_status="paid", is_active=True)
    anna.user.forum_username = "ZellerA_L25"
    bernd.user.forum_username = "HuberB_L25"
    clara.user.forum_username = "MaierC_L25"
    db.session.commit()
    return {"anna": anna.user_id, "bernd": bernd.user_id, "clara": clara.user_id}


def _order(response):
    """The account ids, top to bottom, as the rows link to them."""
    body = response.get_data(as_text=True)
    return [int(i) for i in re.findall(r'<tr class="account-row" data-href="/admin/accounts/(\d+)"', body)]


def _members_only(ids, people):
    wanted = set(people.values())
    return [i for i in ids if i in wanted]


class TestTheRowIsTheLink:
    def test_every_row_opens_its_account_and_there_is_no_view_button(self, admin_client, people):
        body = admin_client.get("/admin/accounts").get_data(as_text=True)

        for user_id in people.values():
            assert f'data-href="/admin/accounts/{user_id}"' in body
            assert f'<a class="account-row-link" href="/admin/accounts/{user_id}"' in body, \
                "still a real link, for the keyboard and a middle click"
        assert ">Actions<" not in body
        assert "btn btn-secondary btn-sm" not in body

    def test_the_script_that_makes_rows_clickable_is_loaded(self, admin_client, people):
        body = admin_client.get("/admin/accounts").get_data(as_text=True)

        assert "accounts-table.js" in body


class TestSorting:
    def test_by_default_the_list_is_in_address_order(self, admin_client, people):
        ids = _order(admin_client.get("/admin/accounts"))

        assert _members_only(ids, people) == [people["bernd"], people["clara"], people["anna"]]

    def test_by_member_name_both_ways(self, admin_client, people):
        up = _order(admin_client.get("/admin/accounts?sort=member&dir=asc"))
        down = _order(admin_client.get("/admin/accounts?sort=member&dir=desc"))

        assert _members_only(up, people) == [people["bernd"], people["clara"], people["anna"]]
        assert _members_only(down, people) == [people["anna"], people["clara"], people["bernd"]]

    def test_accounts_with_nothing_to_sort_by_come_last_either_way(self, admin_client, people):
        """The admin has no membership: at the bottom, not flipping to the top."""
        admin_id = db.session.execute(db.select(User.id).filter_by(email=ADMIN_EMAIL)).scalar_one()

        for direction in ("asc", "desc"):
            ids = _order(admin_client.get(f"/admin/accounts?sort=member&dir={direction}"))
            assert ids[-1] == admin_id, direction

    def test_by_forum_username(self, admin_client, people):
        ids = _order(admin_client.get("/admin/accounts?sort=forum&dir=desc"))

        assert _members_only(ids, people) == [people["anna"], people["clara"], people["bernd"]]

    def test_by_active_puts_active_members_first(self, admin_client, people):
        ids = _members_only(_order(admin_client.get("/admin/accounts?sort=active&dir=asc")), people)

        assert ids[-1] == people["bernd"]

    def test_an_archived_person_sorts_by_the_address_they_are_shown_with(self, admin_client, people):
        """Their account holds a placeholder; the row shows the old address."""
        archived = _archived(email="b-bert@example.com", username="BertB_L20", uid="9")

        ids = _order(admin_client.get("/admin/accounts?sort=email&dir=asc"))

        assert ids.index(people["bernd"]) < ids.index(archived.user_id) < ids.index(people["clara"])

    def test_nonsense_falls_back_to_the_default(self, admin_client, people):
        default = _order(admin_client.get("/admin/accounts"))

        response = admin_client.get("/admin/accounts?sort=password_hash&dir=sideways")

        assert response.status_code == 200
        assert _order(response) == default


class TestTheHeaders:
    def test_the_sorted_column_says_so_and_the_next_click_reverses_it(self, admin_client, people):
        body = admin_client.get("/admin/accounts?sort=forum&dir=asc").get_data(as_text=True)

        assert 'aria-sort="ascending"' in body
        assert "sort=forum&amp;dir=desc" in body

        body = admin_client.get("/admin/accounts?sort=forum&dir=desc").get_data(as_text=True)

        assert 'aria-sort="descending"' in body
        assert "sort=forum&amp;dir=asc" in body

    def test_a_header_keeps_the_filters(self, admin_client, people):
        body = admin_client.get("/admin/accounts?q=anna&membership_status=paid").get_data(as_text=True)

        link = re.search(r'href="([^"]*sort=subscription[^"]*)"', body).group(1)
        assert "q=anna" in link
        assert "membership_status=paid" in link

    def test_changing_a_filter_keeps_the_sort(self, admin_client, people):
        body = admin_client.get("/admin/accounts?sort=forum&dir=desc").get_data(as_text=True)

        assert '<input type="hidden" name="sort" value="forum">' in body
        assert '<input type="hidden" name="dir" value="desc">' in body

    def test_the_default_sort_adds_nothing_to_the_form(self, admin_client, people):
        body = admin_client.get("/admin/accounts").get_data(as_text=True)

        assert 'name="sort"' not in body

    def test_the_next_page_keeps_the_sort(self, admin_client, people):
        for n in range(55):
            make_member(email=f"filler{n:02d}@example.com")

        body = admin_client.get("/admin/accounts?sort=member&dir=desc").get_data(as_text=True)

        pages = re.findall(r'href="([^"]*page=2[^"]*)"', body)
        assert pages
        assert all("sort=member" in p and "dir=desc" in p for p in pages)
