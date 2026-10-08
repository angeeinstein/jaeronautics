"""The admins' account list: everybody with an account, searched, filtered, sorted and paged.

The rules -- who is in it, what each filter means, how a column sorts -- are
in services/account_directory.py; this says what a row carries. Drawn by
frontend/src/pages/admin/Accounts.tsx.
"""

from datetime import date
from typing import Literal

from pydantic import Field, field_validator

from ..member_categories import CATEGORY_LABELS
from ..permissions import Permission, role_label
from ..services import account_directory as directory
from ._core import Model, endpoint

MembershipState = Literal["active", "ending", "pending", "failed", "ended", "none"]
MembershipFilter = Literal["all", "active", "ending", "pending", "failed", "ended", "none"]
AccountState = Literal["active", "no_sign_in", "disabled", "erased"]


class AccountListQuery(Model):
    q: str = Field("", max_length=200, description="Name, address or forum username; part of one is enough.")
    membership: MembershipFilter = "all"
    role: str = Field("all", description='"all", "staff" (any admin role), "none" (no role) or a role\'s slug.')
    account: Literal["all", "active", "no_sign_in", "disabled", "erased"] = "all"
    kind: Literal["all", "portal", "archived"] = Field(
        directory.DEFAULT_KIND,
        description='"archived": carried over from the old forum and not reconnected yet. Left out unless asked for.')
    sort: Literal["name", "kind", "membership", "until", "forum"] = "name"
    dir: Literal["asc", "desc"] = "asc"
    page: int = Field(1, ge=1)

    @field_validator("q")
    @classmethod
    def _trimmed(cls, value):
        return value.strip()

    @field_validator("role")
    @classmethod
    def _known_role(cls, value):
        if not directory.is_role_filter(value):
            raise ValueError("There is no such role.")
        return value


class Role(Model):
    slug: str
    label: str


class AccountRow(Model):
    id: int
    #: The account's address -- for somebody still only in the old forum's
    #: archive, the address the archive recorded (their account holds a
    #: placeholder nobody would recognise), which may be missing.
    email: str | None
    #: The member's name, or the name the old forum knew; ``None`` for an
    #: account without either.
    name: str | None
    year_group: str | None
    category: str | None
    category_label: str | None
    #: ``unclaimed``: only an archive of the old forum so far; ``reconnected``:
    #: came back and claimed it.
    old_forum: Literal["unclaimed", "reconnected"] | None
    old_forum_username: str | None
    account_state: AccountState
    disabled_reason: str | None
    forum_username: str | None
    roles: list[Role]
    membership: MembershipState
    #: The last day of the paid period.
    membership_until: date | None


class MembershipCounts(Model):
    """How many rows each membership filter shows, with the other filters as they are."""

    all: int
    active: int
    ending: int
    pending: int
    failed: int
    ended: int
    none: int


class AccountListOut(Model):
    items: list[AccountRow]
    total: int
    #: The page shown: the one asked for, or the last there is.
    page: int
    pages: int
    per_page: int
    membership_counts: MembershipCounts
    #: Every role, for the role filter.
    role_choices: list[Role]
    #: A search among portal accounts: how many of the old forum's people,
    #: left out, would match it too -- so they are one click away. ``None``
    #: when nothing is searched, or the old forum's people are already in.
    old_forum_matching: int | None = None


def _row(user, state):
    member = user.member
    archive = user.imported_forum_profile
    unclaimed = archive is not None and member is None
    if member is not None:
        name = f"{member.first_name} {member.last_name}".strip() or None
        year_group = member.year_group
    elif unclaimed:
        name, year_group = archive.display_name, archive.year_group
    else:
        name = year_group = None
    category = member.member_category if member is not None else None
    return AccountRow(
        id=user.id,
        email=archive.source_email if unclaimed else user.email,
        name=name,
        year_group=year_group,
        category=category,
        category_label=str(CATEGORY_LABELS[category]) if category in CATEGORY_LABELS else None,
        old_forum=("unclaimed" if unclaimed else "reconnected") if archive is not None else None,
        old_forum_username=archive.source_username if archive is not None else None,
        account_state=directory.account_state(user),
        disabled_reason=user.disabled_reason if user.disabled_at is not None else None,
        forum_username=user.forum_username,
        # Named from the permission table, like the role filter: a stored
        # label can be older than the table's.
        roles=sorted((Role(slug=role.slug, label=role_label(role.slug)) for role in user.roles),
                     key=lambda role: role.label.lower()),
        membership=state,
        membership_until=member.membership_ends_on if member is not None else None,
    )


@endpoint("GET", "/admin/accounts", response=AccountListOut, query=AccountListQuery,
          permissions=[Permission.ACCOUNTS_VIEW], tag="Admin")
def admin_accounts(query):
    """One page of the account list, with how many each membership filter would show."""
    others = {"search": query.q, "role": query.role, "account": query.account, "kind": query.kind}
    rows, total, page = directory.page_of_accounts(
        **others, membership=query.membership, sort=query.sort, direction=query.dir, page=query.page,
    )
    per_page = directory.PAGE_SIZE
    return AccountListOut(
        items=[_row(user, state) for user, state in rows],
        total=total,
        page=page,
        pages=max(1, -(-total // per_page)),
        per_page=per_page,
        membership_counts=MembershipCounts(**directory.membership_counts(**others)),
        role_choices=[Role(slug=slug, label=label) for slug, label in directory.role_choices()],
        old_forum_matching=(directory.count_accounts(**{**others, "kind": "archived"}, membership=query.membership)
                            if query.q and query.kind == "portal" else None),
    )
