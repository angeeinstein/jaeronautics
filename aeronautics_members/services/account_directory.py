"""The admins' list of accounts: who is in it, what each row says, how it is narrowed and sorted.

Everybody with a portal account is in it, members or not -- and so are the
people carried over from the old forum who have not come back yet: a former
member is somebody the association still has a record of, and reconnecting
one is done from their account page like anything else. They are shown only
when asked for, though (``DEFAULT_KIND``) -- and so are erased accounts (the
account filter *Erased*).

Moved here from app.py with the list's move to the new front end
(api/admin_accounts.py). What is new is the membership *state*, one word per
row worked out by the database (``membership_state``), so the row's pill, the
filter and the sort can never disagree about who is "ending".
"""

from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import selectinload

from ..db_models import ImportedForumProfile, Member, Role, User, db
from ..member_categories import CATEGORY_ORDER
from ..permissions import ROLE_PERMISSIONS, Permission, role_label, roles_with

PAGE_SIZE = 50

#: What a row's membership is, in the order the membership column sorts.
#: ``active`` is a member in good standing; ``ending`` still a member but not
#: renewing; ``pending`` signed up and not paid yet; ``failed`` a payment that
#: did not go through (whether or not the paid period still runs); ``ended`` a
#: membership that is over; ``none`` an account without one.
MEMBERSHIP_STATES = ("active", "ending", "pending", "failed", "ended", "none")

#: The membership filter. ``active`` is everybody who is a member today --
#: ending or not -- which is the dashboard's "Active members"; the others are
#: one state each.
MEMBERSHIP_FILTERS = ("all", *MEMBERSHIP_STATES)

#: The account's own state, which is a different question from the membership's.
ACCOUNT_FILTERS = ("all", "active", "no_sign_in", "disabled", "erased")

KIND_FILTERS = ("all", "portal", "archived")
#: The list leaves the old forum's people out unless they are asked for: on a
#: site carried over from the old forum they are most of the rows, and nobody
#: looking for a member wants to wade through them.
DEFAULT_KIND = "portal"

#: Besides these, a role filter can be a role's slug.
ROLE_FILTERS = ("all", "staff", "none")

SORT_KEYS = ("name", "kind", "membership", "until", "forum")
DEFAULT_SORT = ("name", "asc")

# Not paid yet: a checkout left open, a debit being collected, or nothing at all.
_PENDING_STATUSES = ("pending_checkout", "processing", "unpaid")
# Stripe's word for a subscription set to stop, and for one that has stopped
# while the paid period still runs.
_ENDING_STATUSES = ("cancel_scheduled", "canceled")


def ending():
    """A membership that runs out at the end of its paid period instead of renewing.

    The same condition the dashboard counts, so its "Ending" figure and the
    list it links to always agree.
    """
    return and_(
        Member.is_active.is_(True),
        or_(Member.cancel_at_period_end.is_(True), Member.payment_status.in_(_ENDING_STATUSES)),
    )


def membership_state(value=lambda state: state):
    """The row's membership as one of ``MEMBERSHIP_STATES`` -- or as ``value(state)``,
    which the sort uses for each state's place. Order matters: the first match wins."""
    return case(
        (Member.id.is_(None), value("none")),
        (Member.payment_status == "failed", value("failed")),
        (ending(), value("ending")),
        (Member.is_active.is_(True), value("active")),
        (Member.payment_status.in_(_PENDING_STATUSES), value("pending")),
        else_=value("ended"),
    )


def _unclaimed():
    """Still only an archive: an old-forum profile nobody has claimed.

    Once somebody comes back they are an ordinary account that happens to
    carry its history; filing them under "from the old forum" for the next
    decade would describe where they came from rather than what they are.
    """
    return User.imported_forum_profile.has(ImportedForumProfile.claimed_at.is_(None))


def _sort_expressions():
    """What each column sorts by -- what it shows, not merely what is stored."""
    return {
        # Surname first. An archive has no surname column, but the old board's
        # usernames are surname-first (HuberA_L15), which sorts the same way;
        # an account with neither is shown -- and sorted -- by its address.
        "name": [
            func.lower(func.coalesce(Member.last_name, ImportedForumProfile.source_username, User.email)),
            func.lower(Member.first_name),
        ],
        "kind": case(*((Member.member_category == slug, index) for index, slug in enumerate(CATEGORY_ORDER)),
                     else_=None),
        "membership": membership_state(MEMBERSHIP_STATES.index),
        "until": Member.membership_ends_on,
        "forum": func.lower(User.forum_username),
    }


def _order_by(sort, direction):
    """ORDER BY for the list. Empty values go last either way round."""
    expressions = _sort_expressions()[sort]
    if not isinstance(expressions, list):
        expressions = [expressions]
    order = []
    for expression in expressions:
        order.append(expression.is_(None))
        order.append(expression.desc() if direction == "desc" else expression.asc())
    # Then by name, and finally a stable order within equal values, so paging
    # never repeats or skips anybody.
    if sort != "name":
        order += [expression.asc() for expression in _sort_expressions()["name"]]
    order += [User.id.asc()]
    return order


def _joined(*columns):
    """A select of ``columns`` over every account, its membership and its archive profile."""
    return (
        db.select(*columns)
        .select_from(User)
        .outerjoin(Member, Member.user_id == User.id)
        .outerjoin(ImportedForumProfile, ImportedForumProfile.user_id == User.id)
    )


def _narrow(query, *, search="", role="all", account="all", kind=DEFAULT_KIND):
    """Every filter but the membership one, which the counts are taken across."""
    if search:
        pattern = f"%{search}%"
        query = query.where(or_(
            User.email.ilike(pattern),
            User.forum_username.ilike(pattern),
            Member.email_private.ilike(pattern),
            Member.first_name.ilike(pattern),
            Member.last_name.ilike(pattern),
            # Somebody carried over from the old forum has no name and a
            # placeholder address, so the only things worth searching them by
            # are what the archive recorded.
            ImportedForumProfile.display_name.ilike(pattern),
            ImportedForumProfile.source_username.ilike(pattern),
            ImportedForumProfile.source_email.ilike(pattern),
        ))

    if kind == "archived":
        query = query.where(_unclaimed())
    elif kind == "portal":
        query = query.where(~_unclaimed())

    # "Any admin role" is a capability question, not a role-name one: asking
    # for Role.slug == "admin" would miss a role added later, and would have
    # to be edited every time one is -- which permissions.py exists to avoid.
    if role == "staff":
        query = query.where(User.roles.any(Role.slug.in_(roles_with(Permission.ADMIN_ACCESS))))
    elif role == "none":
        query = query.where(~User.roles.any())
    elif role != "all":
        query = query.where(User.roles.any(Role.slug == role))

    if account == "active":
        query = query.where(User.disabled_at.is_(None), User.deleted_at.is_(None), User.password_hash.is_not(None))
    elif account == "no_sign_in":
        query = query.where(User.password_hash.is_(None), User.deleted_at.is_(None))
    elif account == "disabled":
        query = query.where(User.disabled_at.is_not(None), User.deleted_at.is_(None))
    elif account == "erased":
        query = query.where(User.deleted_at.is_not(None))
    else:
        # An erased account stays a row (the bookkeeping and the log refer to
        # it), but like the old forum's people it is shown only when asked for:
        # in no other list, count or chip.
        query = query.where(User.deleted_at.is_(None))
    return query


def _membership_filter(query, membership):
    if membership == "active":
        return query.where(Member.is_active.is_(True))
    if membership != "all":
        return query.where(membership_state() == membership)
    return query


def role_choices():
    """Every role there is, by its label: built from the permission table, so a
    role added there can be filtered by without anything else being edited."""
    return sorted(((slug, role_label(slug)) for slug in ROLE_PERMISSIONS), key=lambda choice: choice[1].lower())


def is_role_filter(value):
    return value in ROLE_FILTERS or value in ROLE_PERMISSIONS


def membership_counts(*, search="", role="all", account="all", kind=DEFAULT_KIND):
    """How many rows each membership filter would show, with the other filters as they are."""
    state = membership_state().label("state")
    query = _narrow(_joined(state, Member.is_active), search=search, role=role, account=account, kind=kind).subquery()
    rows = db.session.execute(
        db.select(query.c.state, query.c.is_active, func.count()).group_by(query.c.state, query.c.is_active)
    ).all()
    counts = dict.fromkeys(MEMBERSHIP_FILTERS, 0)
    for state, is_active, number in rows:
        counts["all"] += number
        if state != "active":  # "active" counts everybody who is a member today, below
            counts[state] += number
        if is_active:
            counts["active"] += number
    return counts


def page_of_accounts(*, search="", membership="all", role="all", account="all", kind=DEFAULT_KIND,
                     sort=DEFAULT_SORT[0], direction=DEFAULT_SORT[1], page=1, per_page=PAGE_SIZE):
    """One page of the list: ``(rows, total, page)``, each row ``(user, membership_state)``.

    A page past the end is the last one, so a list that shrank under a filter
    does not come back empty.
    """
    if sort not in SORT_KEYS:
        sort, direction = DEFAULT_SORT
    query = _membership_filter(_narrow(_joined(User, membership_state()), search=search, role=role, account=account, kind=kind), membership)

    # No DISTINCT needed: both joins are one to one (member.user_id and
    # imported_forum_profiles.user_id are unique) and the role filters are
    # subqueries, so no account appears twice.
    total = db.session.scalar(db.select(func.count()).select_from(query.order_by(None).subquery())) or 0
    last_page = max(1, -(-total // per_page))
    page = min(max(page, 1), last_page)
    rows = db.session.execute(
        query.options(selectinload(User.member), selectinload(User.roles), selectinload(User.imported_forum_profile))
        .order_by(*_order_by(sort, direction))
        .limit(per_page)
        .offset((page - 1) * per_page)
    ).all()
    return [(user, state) for user, state in rows], total, page


def count_accounts(*, search="", membership="all", role="all", account="all", kind=DEFAULT_KIND):
    """How many rows these filters show."""
    query = _membership_filter(_narrow(_joined(User.id), search=search, role=role, account=account, kind=kind),
                               membership)
    return db.session.scalar(db.select(func.count()).select_from(query.subquery())) or 0


def membership_state_of(user):
    """One account's membership state, by the same rule as the list's rows."""
    return db.session.scalar(_joined(membership_state()).where(User.id == user.id))


def account_state(user):
    """The account's own state: ``erased``, ``disabled``, ``no_sign_in`` (never set a password) or ``active``."""
    if user.deleted_at is not None:
        return "erased"
    if user.disabled_at is not None:
        return "disabled"
    if not user.password_hash:
        return "no_sign_in"
    return "active"
