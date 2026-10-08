"""The admins' account list (api/admin_accounts.py, services/account_directory.py;
the page is frontend/src/pages/admin/Accounts.tsx).

Everybody with an account, members or not, and the old forum's people who have
not come back yet. Searched, filtered, sorted and paged by the database, not
the page: the list is paged, so sorting the fifty on screen would put the
wrong fifty there.
"""
from datetime import date, datetime

import pytest

from conftest import app_module, db, make_member
from aeronautics_members.permissions import ROLE_PERMISSIONS, Permission
from aeronautics_members.services import account_directory
from aeronautics_members.services.forum_import import claim_archived_account
from aeronautics_members.services.access import set_account_disabled
from api_helpers import signed_in
from test_admin_reviews import _staff
from test_forum_account_claim import OLD_EMAIL, _archived, _returning

URL = "/api/v1/admin/accounts"
ADMIN_EMAIL = "zz-admin@example.com"
THIS_YEAR_END = date(date.today().year, 12, 31)


@pytest.fixture
def admin(app):
    return _staff(ADMIN_EMAIL, "admin")


@pytest.fixture
def admin_client(client, admin):
    return signed_in(client, admin)


def _list(client, query=""):
    response = client.get(f"{URL}?{query}" if query else URL)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _ids(client, query=""):
    return [row["id"] for row in _list(client, query)["items"]]


def _row(client, user_id, query=""):
    return next(row for row in _list(client, query)["items"] if row["id"] == user_id)


def _only(ids, wanted):
    wanted = set(wanted)
    return [i for i in ids if i in wanted]


@pytest.fixture
def people(app):
    """Three members, deliberately not in any order by address or by name."""
    anna = make_member(email="c-anna@example.com", first_name="Anna", last_name="Zeller",
                       payment_status="paid", is_active=True, membership_ends_on=THIS_YEAR_END,
                       member_category="alumni")
    bernd = make_member(email="a-bernd@example.com", first_name="Bernd", last_name="Huber",
                        payment_status="unpaid", is_active=False)
    clara = make_member(email="b-clara@example.com", first_name="Clara", last_name="Maier",
                        payment_status="paid", is_active=True, membership_ends_on=date(2099, 12, 31))
    anna.user.forum_username = "ZellerA_L25"
    bernd.user.forum_username = "HuberB_L25"
    clara.user.forum_username = "MaierC_L25"
    db.session.commit()
    return {"anna": anna.user_id, "bernd": bernd.user_id, "clara": clara.user_id}


class TestWhoMayLook:
    def test_signed_out(self, client):
        assert client.get(URL).status_code == 401

    def test_admin_access_alone_is_not_enough(self, client):
        """The treasurer reaches the admin area, but sees no members."""
        signed_in(client, _staff("money@example.org", "treasurer"))

        response = client.get(URL)

        assert response.status_code == 403
        assert response.get_json()["error"]["code"] == "forbidden"

    def test_an_admin(self, admin_client):
        body = _list(admin_client)

        assert body["total"] == 1 and body["page"] == 1 and body["pages"] == 1 and body["per_page"] == 50


class TestWhatARowSays:
    def test_a_member(self, admin_client, people):
        row = _row(admin_client, people["anna"])

        assert row == {
            "id": people["anna"], "email": "c-anna@example.com", "name": "Anna Zeller", "year_group": "2020",
            "category": "alumni", "category_label": "Alumni", "old_forum": None, "old_forum_username": None,
            "account_state": "active", "disabled_reason": None, "forum_username": "ZellerA_L25", "roles": [],
            "membership": "active", "membership_until": THIS_YEAR_END.isoformat(),
        }

    def test_an_account_without_a_membership(self, admin_client, admin):
        row = _row(admin_client, admin.id)

        assert row["name"] is None and row["email"] == ADMIN_EMAIL
        assert row["membership"] == "none" and row["membership_until"] is None and row["category"] is None
        assert row["roles"] == [{"slug": "admin", "label": "Admin"}]

    def test_an_archived_person_shows_who_it_was_not_a_placeholder_address(self, admin_client):
        """forum-mybb-645@imported.invalid tells an admin nothing."""
        profile = _archived()

        row = _row(admin_client, profile.user_id, "kind=archived")

        assert row["email"] == OLD_EMAIL
        assert row["name"] == profile.display_name and row["year_group"] == "LAV23"
        assert row["old_forum"] == "unclaimed" and row["old_forum_username"] == "PopovicA_L23"
        assert row["membership"] == "none"

    def test_a_reconnected_person_reads_as_a_member_with_the_history_still_on_show(self, admin_client):
        """Claiming makes them a member; filing them under "old forum" for ever would be wrong."""
        profile = _archived()
        member = _returning()
        claim_archived_account(member.user)
        db.session.commit()

        row = _row(admin_client, profile.user_id)

        assert row["old_forum"] == "reconnected"
        assert row["name"] == "Test Member" and row["email"] == OLD_EMAIL

    def test_the_account_state_is_its_own_question(self, admin_client):
        """A member can be paid up and barred from signing in; the row says both."""
        member = make_member(email="barred@example.com", payment_status="paid", is_active=True,
                             membership_ends_on=THIS_YEAR_END)
        set_account_disabled(member.user, disable=True, actor_user=None, reason="Conduct")
        never = make_member(email="never@example.com")
        never.user.password_hash = None
        db.session.commit()

        barred = _row(admin_client, member.user_id)
        assert barred["account_state"] == "disabled" and barred["disabled_reason"] == "Conduct"
        assert barred["membership"] == "active"
        assert _row(admin_client, never.user_id)["account_state"] == "no_sign_in"

    def test_an_erased_account(self, admin_client):
        member = make_member(email="erased@example.com")
        member.user.deleted_at = datetime.utcnow()
        db.session.commit()

        assert _row(admin_client, member.user_id)["account_state"] == "erased"


class TestMembershipStates:
    """One word per row, worked out by the database -- the pill, the filter and the sort agree."""

    @pytest.fixture
    def everyone(self, app):
        last_year = date(date.today().year - 1, 12, 31)

        def member(name, **fields):
            return make_member(email=f"{name}@example.com", first_name=name.title(), **fields).user_id

        return {
            "active": member("active", payment_status="paid", is_active=True, membership_ends_on=THIS_YEAR_END),
            "free": member("free", payment_status="free_period", is_active=True, membership_ends_on=THIS_YEAR_END),
            "ending": member("ending", payment_status="cancel_scheduled", cancel_at_period_end=True,
                             is_active=True, membership_ends_on=THIS_YEAR_END),
            "stopped": member("stopped", payment_status="canceled", is_active=True, membership_ends_on=THIS_YEAR_END),
            "pending": member("pending", payment_status="pending_checkout"),
            "debit": member("debit", payment_status="processing"),
            "failed": member("failed", payment_status="failed"),
            "bounced": member("bounced", payment_status="failed", is_active=True, membership_ends_on=THIS_YEAR_END),
            "expired": member("expired", payment_status="expired", membership_ends_on=last_year),
            "was_ending": member("was_ending", payment_status="expired", cancel_at_period_end=True,
                                 membership_ends_on=last_year),
        }

    def test_each_row(self, admin_client, everyone):
        states = {row["id"]: row["membership"] for row in _list(admin_client)["items"]}

        assert {name: states[user_id] for name, user_id in everyone.items()} == {
            "active": "active", "free": "active", "ending": "ending", "stopped": "ending",
            "pending": "pending", "debit": "pending", "failed": "failed", "bounced": "failed",
            "expired": "ended", "was_ending": "ended",
        }

    def test_each_filter_and_its_count(self, admin_client, everyone, admin):
        expected = {
            # Everybody who is a member today, ending or not: the dashboard's figure.
            "active": {"active", "free", "ending", "stopped", "bounced"},
            "ending": {"ending", "stopped"},
            "pending": {"pending", "debit"},
            "failed": {"failed", "bounced"},
            "ended": {"expired", "was_ending"},
        }
        counts = _list(admin_client)["membership_counts"]

        for state, names in expected.items():
            assert set(_ids(admin_client, f"membership={state}")) == {everyone[n] for n in names}, state
            assert counts[state] == len(names), state
        assert _ids(admin_client, "membership=none") == [admin.id]
        assert counts["none"] == 1 and counts["all"] == len(everyone) + 1

    def test_the_counts_follow_the_other_filters_but_not_the_membership_one(self, admin_client, everyone):
        counts = _list(admin_client, "q=stopped&membership=pending")["membership_counts"]

        assert counts == {"all": 1, "active": 1, "ending": 1, "pending": 0, "failed": 0, "ended": 0, "none": 0}

    def test_sorted_by_membership_the_active_come_first(self, admin_client, everyone, admin):
        states = [row["membership"] for row in _list(admin_client, "sort=membership")["items"]]

        assert states == sorted(states, key=account_directory.MEMBERSHIP_STATES.index)
        assert states[0] == "active" and states[-1] == "none"


class TestFilters:
    def test_old_forum_or_portal(self, admin_client):
        _archived()
        make_member(email="current@example.com")

        # The admin doing the looking is an account too.
        assert _list(admin_client, "kind=all")["total"] == 3
        assert _list(admin_client, "kind=archived")["total"] == 1
        assert _list(admin_client, "kind=portal")["total"] == 2

    def test_the_old_forum_is_left_out_unless_asked_for(self, admin_client):
        profile = _archived()
        make_member(email="current@example.com")

        listed = _list(admin_client)
        assert profile.user_id not in [row["id"] for row in listed["items"]]
        # The counts on the quick filters leave them out too.
        assert listed["total"] == listed["membership_counts"]["all"] == 2

    def test_a_search_says_how_many_of_the_old_forum_match_too(self, admin_client):
        profile = _archived()
        archived = profile.source_username

        searched = _list(admin_client, f"q={archived}")
        assert searched["total"] == 0
        assert searched["old_forum_matching"] == 1
        # Not when nothing is searched, nor when the old forum is already in.
        assert _list(admin_client)["old_forum_matching"] is None
        assert _list(admin_client, f"q={archived}&kind=all")["old_forum_matching"] is None

    def test_a_reconnected_person_is_not_offered_as_an_archive_any_more(self, admin_client):
        profile = _archived()
        member = _returning()
        claim_archived_account(member.user)
        db.session.commit()

        assert profile.user_id not in _ids(admin_client, "kind=archived")
        assert profile.user_id in _ids(admin_client, "kind=portal")

    def test_asking_for_members_leaves_the_archive_out(self, admin_client):
        _archived()

        assert _list(admin_client, "membership=active")["total"] == 0

    def test_by_account_state(self, admin_client):
        member = make_member(email="barred@example.com")
        set_account_disabled(member.user, disable=True, actor_user=None)
        erased = make_member(email="erased@example.com")
        erased.user.deleted_at = datetime.utcnow()
        db.session.commit()

        assert _ids(admin_client, "account=disabled") == [member.user_id]
        assert _ids(admin_client, "account=erased") == [erased.user_id]
        assert member.user_id not in _ids(admin_client, "account=active")
        assert _list(admin_client, "account=active")["total"] == 1  # the admin

    @pytest.mark.parametrize("search", ["anna", "ZELLER", "c-anna@", "zellera_l25"])
    def test_search_by_name_address_or_forum_username(self, admin_client, people, search):
        assert _ids(admin_client, f"q={search}") == [people["anna"]]

    def test_search_by_the_private_address(self, admin_client):
        member = make_member(email="login@example.com")
        member.email_private = "home@example.net"
        db.session.commit()

        assert _ids(admin_client, "q=home@example") == [member.user_id]

    @pytest.mark.parametrize("search", ["PopovicA", "a.popovic"])
    def test_an_archived_person_by_what_the_old_forum_recorded(self, admin_client, search):
        """An admin asked "is my old account in there" has a name or an address."""
        profile = _archived()

        assert _ids(admin_client, f"q={search}&kind=all") == [profile.user_id]

    def test_surrounding_spaces_do_not_count(self, admin_client, people):
        assert _ids(admin_client, "q=%20anna%20") == [people["anna"]]


class TestTheRoleFilter:
    """It asks about the capability, not about Role.slug == "admin", so a role
    added to the permission table is filterable without anything being edited."""

    @pytest.fixture
    def reviewer_role(self, app, monkeypatch):
        monkeypatch.setitem(ROLE_PERMISSIONS, "photo_reviewer",
                            frozenset({Permission.ADMIN_ACCESS, Permission.FORUM_MODERATE}))
        app_module.seed_default_roles()
        db.session.commit()

    def test_a_role_it_never_heard_of(self, admin_client, admin, reviewer_role):
        mod = _staff("filtermod@example.com", "photo_reviewer")
        member = make_member(email="plain@example.com")

        assert set(_ids(admin_client, "role=staff")) == {admin.id, mod.id}
        assert _ids(admin_client, "role=photo_reviewer") == [mod.id]
        assert _ids(admin_client, "role=none") == [member.user_id]

    def test_the_choices_list_it(self, admin_client, reviewer_role):
        choices = _list(admin_client)["role_choices"]

        assert {"slug": "photo_reviewer", "label": "Photo Reviewer"} in choices
        assert [c["label"].lower() for c in choices] == sorted(c["label"].lower() for c in choices)

    def test_a_role_that_does_not_exist(self, admin_client):
        response = admin_client.get(f"{URL}?role=emperor")

        assert response.status_code == 400
        assert response.get_json()["error"]["fields"] == {"role": "Value error, There is no such role."}


class TestSorting:
    def test_by_default_by_surname(self, admin_client, people):
        assert _only(_ids(admin_client), people.values()) == [people["bernd"], people["clara"], people["anna"]]

    def test_by_name_the_other_way(self, admin_client, people):
        ids = _only(_ids(admin_client, "sort=name&dir=desc"), people.values())

        assert ids == [people["anna"], people["clara"], people["bernd"]]

    def test_an_account_without_a_name_sorts_by_the_address_it_is_shown_with(self, admin_client, people, admin):
        assert _ids(admin_client)[-1] == admin.id  # zz-admin@ after Zeller
        assert _ids(admin_client, "sort=name&dir=desc")[0] == admin.id

    def test_an_archived_person_sorts_by_their_old_username(self, admin_client, people):
        """The old board's names are surname-first (HuberA_L15), which sorts like a surname."""
        archived = _archived(email="b-bert@example.com", username="KellerB_L20", uid="9")

        ids = _ids(admin_client, "kind=all")

        assert ids.index(people["bernd"]) < ids.index(archived.user_id) < ids.index(people["clara"])

    def test_by_forum_username(self, admin_client, people):
        ids = _only(_ids(admin_client, "sort=forum&dir=desc"), people.values())

        assert ids == [people["anna"], people["clara"], people["bernd"]]

    def test_empty_values_come_last_either_way(self, admin_client, people, admin):
        """The admin has no forum name and no paid period: at the bottom, not flipping to the top."""
        for sort in ("forum", "until", "kind"):
            for direction in ("asc", "desc"):
                assert _ids(admin_client, f"sort={sort}&dir={direction}")[-1] == admin.id, (sort, direction)

    def test_by_the_end_of_the_paid_period(self, admin_client, people):
        ids = _only(_ids(admin_client, "sort=until&dir=desc"), people.values())

        assert ids == [people["clara"], people["anna"], people["bernd"]]

    def test_by_kind_in_the_order_the_forms_offer_them(self, admin_client, people):
        """Students first, then alumni -- not the alphabet."""
        ids = _only(_ids(admin_client, "sort=kind"), people.values())

        assert ids == [people["bernd"], people["clara"], people["anna"]]

    def test_nonsense_is_refused_not_guessed(self, admin_client):
        response = admin_client.get(f"{URL}?sort=password_hash&dir=sideways")

        assert response.status_code == 400
        assert set(response.get_json()["error"]["fields"]) == {"sort", "dir"}

    def test_an_unknown_parameter_is_refused(self, admin_client):
        """A misspelt filter would otherwise silently show everybody."""
        assert admin_client.get(f"{URL}?membership_status=paid").status_code == 400


class TestPages:
    @pytest.fixture
    def many(self, app):
        return [make_member(email=f"many{index:02d}@example.com", last_name=f"M{index:02d}").user_id
                for index in range(55)]

    def test_fifty_to_a_page(self, admin_client, many, admin):
        first = _list(admin_client)
        second = _list(admin_client, "page=2")

        assert len(first["items"]) == 50 and len(second["items"]) == 6
        assert first["total"] == second["total"] == 56 and first["pages"] == 2
        assert not set(_ids(admin_client)) & set(_ids(admin_client, "page=2")), "nobody twice"

    def test_the_next_page_keeps_the_sort_and_the_filters(self, admin_client, many):
        first = _ids(admin_client, "sort=name&dir=desc&membership=pending")
        second = _ids(admin_client, "sort=name&dir=desc&membership=pending&page=2")

        assert first + second == list(reversed(many))

    def test_a_page_past_the_end_is_the_last_one(self, admin_client, many):
        """A list that shrank under a filter does not come back empty."""
        body = _list(admin_client, "page=9")

        assert body["page"] == 2 and len(body["items"]) == 6

    def test_an_empty_list_is_one_empty_page(self, admin_client):
        body = _list(admin_client, "q=nobody-at-all")

        assert body["items"] == [] and body["total"] == 0 and body["page"] == 1 and body["pages"] == 1

    def test_page_zero_is_refused(self, admin_client):
        assert admin_client.get(f"{URL}?page=0").status_code == 400


class TestTheDeclaredChoicesMatchTheService:
    """The API's Literal types are what the front end's types come from; the
    service holds the rules. They must name the same things."""

    def test_they_agree(self):
        from typing import get_args

        from aeronautics_members.api import admin_accounts as api

        assert get_args(api.MembershipState) == account_directory.MEMBERSHIP_STATES
        assert get_args(api.MembershipFilter) == account_directory.MEMBERSHIP_FILTERS
        fields = api.AccountListQuery.model_fields
        assert get_args(fields["account"].annotation) == account_directory.ACCOUNT_FILTERS
        assert get_args(fields["kind"].annotation) == account_directory.KIND_FILTERS
        assert get_args(fields["sort"].annotation) == account_directory.SORT_KEYS
        assert (fields["sort"].default, fields["dir"].default) == account_directory.DEFAULT_SORT
        assert set(api.MembershipCounts.model_fields) == set(account_directory.MEMBERSHIP_FILTERS)

    def test_the_page_itself_is_the_app(self, admin_client):
        body = admin_client.get("/admin/accounts").get_data(as_text=True)

        assert '<div id="root">' in body
