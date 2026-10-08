"""One account, for an administrator: everything about it, and every action on it.

The page (frontend/src/pages/admin/Account.tsx) has a tab per subject --
Profile, Membership, Forum, Teams, Roles, Danger zone -- and reads all of them
from one answer, except what is slow or rarely wanted: what erasing would do
(it asks Stripe) and the old forum's accounts to reconnect to.

What the person looking may do is part of the answer (``actions``), worked out
here with the same checks the actions run, so the page never offers a button
that can only be refused. The actions themselves are services/account_admin.py.
"""

from datetime import date
from typing import Any, Literal

from flask import url_for
from flask_login import current_user
from pydantic import Field
from sqlalchemy.orm import selectinload

from ..db_models import AuditLog, User, db
from ..member_categories import CATEGORY_LABELS
from ..permissions import PERMISSION_LABELS, ROLE_PERMISSIONS, Permission, covering_role, role_description, role_label
from ..services import NotFoundError
from ..services import account_admin as actions
from ..services import account_directory as directory
from ..services.access import assignable_roles, describe_account_disable, describe_role_change
from ..services.audit import about_user, redact_sensitive_audit_value
from ..services.membership import PAYMENT_STATUS_LABELS
from ..services.privacy import describe_deletion_impact, refresh_subscription_state_before_deletion
from ._core import Model, UtcDateTime, endpoint
from .admin_accounts import AccountState, MembershipState, Role

TAG = "Admin"
RECENT_ACTIVITY = 15

ForumStatus = Literal[
    "disabled", "no_membership", "account_disabled", "payment_processing", "inactive_membership", "active",
    "reconnect_waiting", "pending_avatar", "rejected_avatar", "needs_avatar",
]

#: The forum status in an administrator's words (the member's own are on My Account).
FORUM_STATUS_LABELS = {
    "disabled": "The forum integration is off",
    "no_membership": "No membership",
    "account_disabled": "Account deactivated",
    "payment_processing": "Waiting for the payment to clear",
    "inactive_membership": "Membership not active",
    "active": "Has forum access",
    "reconnect_waiting": "Can get the old forum account back by confirming the university address",
    "pending_avatar": "Profile picture waiting for review",
    "rejected_avatar": "Profile picture rejected, waiting for a new one",
    "needs_avatar": "Has not uploaded a profile picture yet",
}


# --- What the page reads -------------------------------------------------------------------


class Membership(Model):
    title: str | None
    first_name: str
    last_name: str
    email_private: str
    category: str
    category_label: str
    year_group: str | None
    #: The company a partner member joins for.
    company_name: str | None
    state: MembershipState
    payment_status: str
    payment_status_label: str
    is_active: bool
    #: The last day of the paid period.
    ends_on: date | None
    #: ``None`` when it does not renew (cancelled).
    renews_on: date | None
    #: Whether Stripe holds a customer or subscription for it, which a billing sync needs.
    has_billing: bool
    #: Since when the member may replace their approved profile picture.
    picture_replacement_allowed_since: UtcDateTime | None


class OldForum(Model):
    display_name: str
    username: str
    year_group: str | None
    email: str | None
    group: str | None
    group_reason: str | None
    posts: int | None
    joined_on: date | None
    last_posted_on: date | None
    #: When somebody reconnected it; ``None`` while it is only an archive.
    claimed_at: UtcDateTime | None
    picture_url: str | None


class ForumCleanup(Model):
    """The forum account a reconnect left behind, while it is not removed yet."""

    failed: bool
    error: str | None


class Forum(Model):
    #: The account's state on the forum; ``None`` before one was prepared.
    account_state: str | None
    status: ForumStatus
    status_label: str
    #: The latest profile picture's review: pending, approved, rejected, superseded.
    latest_picture: str | None
    review_note: str | None
    last_synced_at: UtcDateTime | None
    error: str | None
    cleanup: ForumCleanup | None
    has_picture: bool


class TeamRole(Model):
    role_label: str
    team: str


class TeamMembership(Model):
    team: str
    #: The person's page in that team's management, where it can be opened.
    link_url: str | None
    status_label: str
    end_reason_label: str | None
    since: UtcDateTime | None
    until: UtcDateTime | None


class Teams(Model):
    roles: list[TeamRole]
    memberships: list[TeamMembership]


class RoleOption(Model):
    slug: str
    label: str
    description: str | None
    held: bool
    #: The role held that already grants everything this one does.
    covered_by: str | None
    #: What the role lets somebody do.
    permissions: list[str]


class Access(Model):
    """Roles and switching the account off -- for whoever manages access."""

    options: list[RoleOption]
    #: What the account can do now, from all its roles.
    effective_permissions: list[str]
    #: Why the roles cannot be changed here at all (one's own account).
    roles_locked: str | None
    #: Why clearing every role would be refused, said before anybody tries.
    removal_warning: str | None
    #: Why the account cannot be switched off; empty when it can.
    disable_blockers: list[str]


class AccountActivity(Model):
    id: int
    at: UtcDateTime
    category: str
    event_type: str
    actor: str | None
    before: Any
    after: Any
    details: Any


class Actions(Model):
    """What the person looking may do to this account, by the same checks the actions run."""

    sync_billing: bool
    resync_forum: bool
    picture_replacement: bool
    correct_email: bool
    reconnect: bool
    manage_access: bool
    export_data: bool
    erase: bool


class AccountOut(Model):
    id: int
    email: str
    email_verified: bool
    #: The member's name, the old forum's, or ``None``.
    name: str | None
    forum_username: str | None
    roles: list[Role]
    account_state: AccountState
    disabled_at: UtcDateTime | None
    disabled_reason: str | None
    erased_at: UtcDateTime | None
    membership: Membership | None
    old_forum: OldForum | None
    forum: Forum
    #: ``None`` for somebody who may not manage teams.
    teams: Teams | None
    #: ``None`` for somebody who may not manage access.
    access: Access | None
    recent_activity: list[AccountActivity]
    actions: Actions


def _load(user_id):
    user = db.session.execute(
        db.select(User)
        .options(selectinload(User.member), selectinload(User.roles), selectinload(User.imported_forum_profile))
        .filter_by(id=user_id)
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundError("There is no such account.")
    return user


def _membership(user):
    member = user.member
    if member is None:
        return None
    return Membership(
        title=member.title or None,
        first_name=member.first_name,
        last_name=member.last_name,
        email_private=member.email_private,
        category=member.member_category,
        category_label=str(CATEGORY_LABELS.get(member.member_category, member.member_category)),
        year_group=member.year_group,
        company_name=member.company_name,
        state=directory.membership_state_of(user),
        payment_status=member.payment_status,
        payment_status_label=PAYMENT_STATUS_LABELS.get(member.payment_status,
                                                       member.payment_status.replace("_", " ").capitalize()),
        is_active=bool(member.is_active),
        ends_on=member.membership_ends_on,
        renews_on=None if member.cancel_at_period_end else member.renewal_due_on,
        has_billing=bool(member.stripe_customer_id or member.stripe_subscription_id),
        picture_replacement_allowed_since=member.avatar_replacement_allowed_at,
    )


def _old_forum(user):
    archive = user.imported_forum_profile
    if archive is None:
        return None
    return OldForum(
        display_name=archive.display_name,
        username=archive.source_username,
        year_group=archive.year_group,
        email=archive.source_email,
        group=archive.source_group,
        group_reason=archive.source_group_reason,
        posts=archive.post_count,
        joined_on=archive.joined_on,
        last_posted_on=archive.last_posted_on,
        claimed_at=archive.claimed_at,
        picture_url=f"/admin/accounts/{user.id}/archived-avatar" if archive.avatar_path else None,
    )


def _forum(user):
    from ..app import build_forum_context
    from ..services.forum import get_forum_service
    from ..services.workflows import forum_cleanup_waiting

    context = build_forum_context(user.member)
    forum_account = user.forum_account
    latest = get_forum_service().get_latest_submission(user.member) if user.member else None
    cleanup = forum_cleanup_waiting(user)
    return Forum(
        account_state=forum_account.state if forum_account is not None else None,
        status=context["status_key"],
        status_label=FORUM_STATUS_LABELS.get(context["status_key"], context["status_key"]),
        latest_picture=latest.status if latest is not None else None,
        review_note=latest.review_note if latest is not None else None,
        last_synced_at=forum_account.last_synced_at if forum_account is not None else None,
        error=context["forum_error"],
        cleanup=ForumCleanup(failed=cleanup.status == "failed", error=cleanup.last_error) if cleanup else None,
        has_picture=bool(context["has_picture"]),
    )


def _teams(user):
    from ..services import teams

    if not current_user.can(Permission.TEAMS_MANAGE):
        return None
    on = teams.teams_enabled()
    memberships = []
    for membership in teams.memberships_of(user, include_archived=True):
        team = membership.team
        memberships.append(TeamMembership(
            team=team.name,
            link_url=(url_for("teams.team_person", slug=team.slug, user_id=user.id)
                      if on and team.status == "active" else None),
            status_label=teams.STATUS_LABELS.get(membership.status, membership.status),
            end_reason_label=(teams.END_REASON_LABELS.get(membership.end_reason, membership.end_reason)
                              if membership.end_reason else None),
            since=membership.started_at or membership.applied_at,
            until=membership.ended_at,
        ))
    roles = [TeamRole(role_label=teams.TEAM_ROLE_LABELS.get(role.role, role.role), team=role.team.name)
             for role in teams.roles_of(user)]
    return Teams(roles=roles, memberships=memberships)


def _permission_labels(permissions):
    return sorted(PERMISSION_LABELS.get(permission, permission) for permission in permissions)


def _access(user):
    if not current_user.can(Permission.ROLES_MANAGE):
        return None
    held = {role.slug for role in user.roles}
    options = []
    for slug in assignable_roles():
        covering = covering_role(slug, held)
        options.append(RoleOption(
            slug=slug,
            label=role_label(slug),
            description=role_description(slug),
            held=slug in held,
            # Naming the role that already grants all of this is the whole
            # answer to "why is Admin not ticked on a Super Admin?".
            covered_by=role_label(covering) if covering else None,
            permissions=_permission_labels(ROLE_PERMISSIONS[slug]),
        ))
    removal_warning = None
    if user.deleted_at is None:
        blockers = describe_role_change(user, [])["blockers"]
        removal_warning = blockers[0][1] if blockers else None
    disable_blockers = []
    if user.disabled_at is None:
        disable_blockers = [message for _code, message in
                            describe_account_disable(user, actor_user=current_user, disable=True)["blockers"]]
    return Access(
        options=options,
        effective_permissions=_permission_labels(user.permissions),
        roles_locked=("You cannot change your own roles. Another administrator can, or grant-superadmin on the "
                      "server." if user.id == current_user.id else None),
        removal_warning=removal_warning,
        disable_blockers=disable_blockers,
    )


def _activity(user):
    entries = db.session.execute(
        db.select(AuditLog)
        .options(selectinload(AuditLog.actor_user))
        .where(about_user(user))
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(RECENT_ACTIVITY)
    ).scalars().all()
    return [
        AccountActivity(
            id=entry.id,
            at=entry.created_at,
            category=entry.category,
            event_type=entry.event_type,
            actor=entry.actor_user.email if entry.actor_user is not None else None,
            # Secrets in a settings change stay out of the answer, as on the log page.
            before=redact_sensitive_audit_value(entry.before_state) if entry.before_state else None,
            after=redact_sensitive_audit_value(entry.after_state) if entry.after_state else None,
            details=redact_sensitive_audit_value(entry.event_metadata) if entry.event_metadata else None,
        )
        for entry in entries
    ]


def _name(user):
    if user.member is not None:
        return f"{user.member.first_name} {user.member.last_name}".strip() or None
    if user.imported_forum_profile is not None:
        return user.imported_forum_profile.display_name
    return None


def _account(user):
    member = user.member
    erased = user.deleted_at is not None
    forum = _forum(user)
    return AccountOut(
        id=user.id,
        email=user.email,
        email_verified=user.email_verified_at is not None,
        name=_name(user),
        forum_username=user.forum_username,
        roles=sorted((Role(slug=role.slug, label=role_label(role.slug)) for role in user.roles),
                     key=lambda role: role.label.lower()),
        account_state=directory.account_state(user),
        disabled_at=user.disabled_at,
        disabled_reason=user.disabled_reason if user.disabled_at is not None else None,
        erased_at=user.deleted_at,
        membership=_membership(user),
        old_forum=_old_forum(user),
        forum=forum,
        teams=_teams(user),
        access=_access(user),
        recent_activity=_activity(user),
        actions=Actions(
            sync_billing=(current_user.can(Permission.ACCOUNTS_BILLING) and member is not None
                          and bool(member.stripe_customer_id or member.stripe_subscription_id)),
            resync_forum=current_user.can(Permission.FORUM_MODERATE) and member is not None,
            picture_replacement=(current_user.can(Permission.FORUM_MODERATE) and member is not None
                                 and forum.has_picture),
            correct_email=actions.may_correct_email(user, current_user),
            reconnect=actions.may_reconnect(user, current_user),
            manage_access=current_user.can(Permission.ROLES_MANAGE) and not erased,
            export_data=current_user.can(Permission.ACCOUNTS_PRIVACY),
            erase=current_user.can(Permission.ACCOUNTS_PRIVACY),
        ),
    )


@endpoint("GET", "/admin/accounts/<int:user_id>", response=AccountOut, permissions=[Permission.ACCOUNTS_VIEW],
          tag=TAG)
def admin_account(user_id):
    """One account: profile, membership, forum, teams, access, recent activity, and what may be done."""
    return _account(_load(user_id))


# --- Erasing: what it would do -------------------------------------------------------------

ERASURE_BLOCKERS = {
    "already_erased": "The personal data of this account was erased already.",
    "self_deletion_via_admin_page": "You cannot erase your own account here. Use My Account, so the confirmation "
                                    "goes to your mailbox.",
    "last_admin": "This is the only administrator account left. Give another account an admin role first.",
    "last_superadmin": "This is the only account that can install updates. Give another account the Super Admin "
                       "role first.",
}


class ErasureOut(Model):
    """What erasing this account would do, and what forbids it -- said before anybody confirms."""

    #: Why it cannot be erased; empty when it can.
    blockers: list[str]
    #: The address to type as the confirmation.
    confirm_email: str
    paid_periods: int
    has_forum_account: bool
    has_stripe_customer: bool
    #: A subscription that erasing cancels, without a refund.
    subscription_active: bool
    #: What Stripe says about it right now, where it could be asked.
    subscription_status: str | None
    #: Paid coverage that ends with the erasure.
    coverage_end: date | None


@endpoint("GET", "/admin/accounts/<int:user_id>/erasure", response=ErasureOut,
          permissions=[Permission.ACCOUNTS_PRIVACY], tag=TAG)
def admin_account_erasure(user_id):
    """What erasing the account's personal data would do. Asks Stripe, so it is read only when wanted."""
    user = _load(user_id)
    impact = describe_deletion_impact(user, actor_user=current_user)
    live = refresh_subscription_state_before_deletion(user.member) if impact["subscription_active"] else None
    return ErasureOut(
        blockers=[ERASURE_BLOCKERS.get(code, code) for code in impact["blockers"]],
        confirm_email=user.email,
        paid_periods=impact["paid_periods"],
        has_forum_account=impact["has_forum_account"],
        has_stripe_customer=impact["has_stripe_customer"],
        subscription_active=impact["subscription_active"],
        subscription_status=(live or {}).get("status"),
        coverage_end=impact["coverage_end"],
    )


# --- Reconnecting to the old forum ---------------------------------------------------------


class CandidatesQuery(Model):
    q: str = Field("", max_length=100, description="Old username, name or address; none for the likely ones.")


class Candidate(Model):
    id: int
    username: str
    display_name: str
    year_group: str | None
    email: str | None
    posts: int | None
    #: Why it is probably theirs, when nothing was searched: "address" (differs
    #: only in dots or spelling) or "name" (the same name).
    likely_because: Literal["address", "name"] | None


class CandidatesOut(Model):
    items: list[Candidate]
    #: Whether these are the likely ones rather than a search's results.
    likely: bool


@endpoint("GET", "/admin/accounts/<int:user_id>/old-forum-candidates", response=CandidatesOut,
          query=CandidatesQuery, permissions=[Permission.APPROVALS_REVIEW], tag=TAG)
def admin_account_old_forum_candidates(user_id, query):
    """Old forum accounts nobody has claimed, to reconnect this account to by hand."""
    user = _load(user_id)
    if not actions.may_reconnect(user, current_user):
        return CandidatesOut(items=[], likely=False)
    search = query.q.strip()
    found = actions.old_forum_candidates(user, search)
    return CandidatesOut(
        items=[Candidate(id=profile.id, username=profile.source_username, display_name=profile.display_name,
                         year_group=profile.year_group, email=profile.source_email, posts=profile.post_count,
                         likely_because=reason)
               for profile, reason in found],
        likely=not search,
    )


# --- The actions -----------------------------------------------------------------------------


class BillingSyncOut(Model):
    #: Whether Stripe said something the portal did not know.
    changed: bool
    #: What went wrong telling the forum, if anything.
    forum_error: str | None


@endpoint("POST", "/admin/accounts/<int:user_id>/billing-sync", response=BillingSyncOut,
          permissions=[Permission.ACCOUNTS_BILLING], tag=TAG)
def admin_account_billing_sync(user_id):
    """Ask Stripe about this member's subscription and repair what a missed webhook left behind."""
    return BillingSyncOut(**actions.sync_billing(_load(user_id), actor_user=current_user))


class ForumResyncOut(Model):
    error: str | None


@endpoint("POST", "/admin/accounts/<int:user_id>/forum-resync", response=ForumResyncOut,
          permissions=[Permission.FORUM_MODERATE], tag=TAG)
def admin_account_forum_resync(user_id):
    """Bring the forum in line with this account now."""
    return ForumResyncOut(**actions.resync_forum(_load(user_id), actor_user=current_user))


class PictureReplacementIn(Model):
    allow: bool


class PictureReplacementOut(Model):
    allowed_since: UtcDateTime | None


@endpoint("PUT", "/admin/accounts/<int:user_id>/picture-replacement", response=PictureReplacementOut,
          body=PictureReplacementIn, permissions=[Permission.FORUM_MODERATE], tag=TAG)
def admin_account_picture_replacement(user_id, body):
    """Allow the member one new profile picture, or withdraw that."""
    since = actions.set_picture_replacement(user_id, allow=body.allow, actor_user=current_user)
    return PictureReplacementOut(allowed_since=since)


class EmailIn(Model):
    email: str = Field(max_length=255)


class EmailOut(Model):
    email: str


@endpoint("PUT", "/admin/accounts/<int:user_id>/email", response=EmailOut, body=EmailIn,
          permissions=[Permission.APPROVALS_REVIEW], tag=TAG)
def admin_account_email(user_id, body):
    """Correct the private address of a member locked out by a wrong one. A confirmation link goes there."""
    user = actions.locked_user(user_id)
    return EmailOut(email=actions.correct_private_email(user, body.email, actor_user=current_user))


class ReconnectIn(Model):
    profile_id: int


class ReconnectOut(Model):
    #: The account now lives on the old forum's row; this is its id.
    account_id: int
    old_username: str
    #: "synced": the forum was told now; "background": it will be over the next minutes.
    forum: Literal["synced", "background"]
    forum_error: str | None


@endpoint("POST", "/admin/accounts/<int:user_id>/reconnect", response=ReconnectOut, body=ReconnectIn,
          permissions=[Permission.APPROVALS_REVIEW], tag=TAG)
def admin_account_reconnect(user_id, body):
    """Reconnect this account to an old forum account, for a member the address check did not find."""
    return ReconnectOut(**actions.reconnect(user_id, body.profile_id, actor_user=current_user))


class DisabledIn(Model):
    disabled: bool
    reason: str | None = Field(None, max_length=255)


class ChangedOut(Model):
    changed: bool


@endpoint("PUT", "/admin/accounts/<int:user_id>/disabled", response=ChangedOut, body=DisabledIn,
          permissions=[Permission.ROLES_MANAGE], tag=TAG)
def admin_account_disabled(user_id, body):
    """Switch the account off -- no signing in, no forum -- or back on. The membership is left as it is."""
    return ChangedOut(**actions.set_disabled(user_id, disable=body.disabled, reason=body.reason,
                                             actor_user=current_user))


class RolesIn(Model):
    roles: list[str] = Field(max_length=20)


class RolesOut(Model):
    changed: bool
    #: Roles not stored because another one chosen already covers them.
    redundant: list[str]


@endpoint("PUT", "/admin/accounts/<int:user_id>/roles", response=RolesOut, body=RolesIn,
          permissions=[Permission.ROLES_MANAGE], tag=TAG)
def admin_account_roles(user_id, body):
    """Set the account's roles to exactly these."""
    return RolesOut(**actions.set_roles(user_id, body.roles, actor_user=current_user))


class EraseIn(Model):
    #: The account's address, typed as the confirmation.
    confirm_email: str = Field(max_length=255)
    #: Recorded in the log.
    reason: str | None = Field(None, max_length=255)


class EraseOut(Model):
    subscription_cancelled: bool
    #: The forum could not be reached; its account is anonymised once it can.
    forum_deferred: bool


@endpoint("POST", "/admin/accounts/<int:user_id>/erase", response=EraseOut, body=EraseIn,
          permissions=[Permission.ACCOUNTS_PRIVACY], tag=TAG)
def admin_account_erase(user_id, body):
    """Erase the account's personal data, keeping the payment record. Cannot be undone."""
    return EraseOut(**actions.erase(user_id, confirm_email=body.confirm_email, reason=body.reason,
                                    actor_user=current_user))
