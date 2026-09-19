"""Data export and erasure -- the two GDPR rights that need code.

**Export** (Art. 15 and 20) is the easy half: gather everything the association
holds about one person and hand it over as JSON.

**Erasure** (Art. 17) is not deletion, and the difference matters. Art. 17(3)(b)
yields to legal retention obligations, and under Austrian law (§ 132 BAO)
accounting records must be kept for seven years. Membership fees are income, so
the record of who paid what and when is one. The rows also cannot simply go:
``membership_periods``, ``audit_logs``, notification and delivery history all
reference the member and the user, and deleting a user who was once an
administrator would take the record of everything they ever did with it.

So erasure overwrites the person and keeps the skeleton. Afterwards the row
still says a membership existed, was paid for on these dates, against this
Stripe invoice -- and nothing says who it was.

Three details are easy to get wrong and are handled here on purpose:

* **The audit trail is a copy of the personal data.** Profile changes store
  before/after snapshots containing names and addresses. Erasing the member
  while leaving those intact would erase nothing. Snapshots on entries that
  *target* this person are cleared; entries where they were the *actor* are left
  alone, because those describe what was done to somebody else.
* **Erasure must not itself record what it erased.** The audit entry written
  here deliberately carries no before-state.
* **Billing has to stop first.** Anonymising a member with a live subscription
  leaves Stripe renewing it every January against a customer nobody can identify
  any more. If the subscription cannot be cancelled, nothing is erased.

Stripe itself is deliberately left untouched beyond that cancellation. It is the
association's accounting record, subject to the same seven-year retention, and
its finalised invoices keep the name and address they were issued to no matter
what is done to the customer object -- so blanking that would be theatre rather
than erasure, while closing the one remaining path from an anonymous membership
period back to who paid for it. ``docs/maintenance.md`` records the reasoning;
the deletion confirmation page tells the member plainly that Stripe keeps its
own copy.
"""

import os

from flask_babel import _

from ..security_utils import build_public_url
from ..db_models import (
    AuditLog,
    EmailDeliveryJob,
    ExternalWorkItem,
    MembershipPeriod,
    NotificationEvent,
    User,
    db,
)
from . import ConflictError, ExternalServiceError, ValidationError
from .audit import log_audit_event, serialize_audit_value
from .billing import cancel_member_subscription, get_latest_stripe_subscription_for_member
from .clock import get_now_utc
from .forum import anonymise_forum_account
from .identity import generate_token
from .members import MEMBER_PROFILE_FIELDS
from .notifications import send_account_action_email
from .periods import describe_coverage

# What an erased text column holds. A visible marker beats an empty string: an
# admin looking at the row should be able to tell erasure from bad data.
ERASED_TEXT = "(erased)"

# NotificationEvent.summary is NOT NULL and is prose meant for an administrator,
# so it gets a replacement sentence rather than a blank.
ERASED_SUMMARY = "Details removed when the account was erased."

# .invalid is reserved by RFC 2606 precisely so it can never resolve, which
# matters because these addresses sit in columns a mail job might otherwise read.
ERASED_EMAIL_DOMAIN = "erased.invalid"

INITIATED_BY_ADMIN = "admin"
INITIATED_BY_MEMBER = "member"

# Columns on Member that describe the person. Everything else on the row --
# dates, payment status, Stripe ids -- is the record we are keeping.
ERASABLE_MEMBER_FIELDS = MEMBER_PROFILE_FIELDS

# Nullable among those, so the rest need a placeholder rather than None.
NULLABLE_MEMBER_FIELDS = {"title", "phone_work", "email_work"}


def erased_email_for(prefix, row_id):
    return f"{prefix}-{row_id}@{ERASED_EMAIL_DOMAIN}"


def is_erased(user):
    return bool(user is not None and user.deleted_at)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def export_account_data(user):
    """Everything the association holds about ``user``, as plain JSON data.

    Serves both Art. 15 (a copy of the data) and Art. 20 (portability), which is
    why it is structured data rather than a rendered page. Returned rather than
    written to a response so a mobile client or an admin handling a request by
    email can use the same function.
    """
    if user is None:
        raise ValidationError("An account is required to export data for.")

    member = user.member

    payload = {
        "export_generated_at": get_now_utc(),
        "account": {
            "id": user.id,
            "email": user.email,
            "forum_username": user.forum_username,
            "email_verified_at": user.email_verified_at,
            "roles": sorted(role.slug for role in user.roles),
            "erased_at": user.deleted_at,
        },
        "member_profile": None,
        "membership_periods": [],
        "membership_summary": None,
        "profile_change_requests": [],
        "forum_account": None,
        "forum_avatar_submissions": [],
        "emails_sent_to_you": [],
        "account_history": [],
    }

    if member is not None:
        payload["member_profile"] = {
            **{field: getattr(member, field) for field in ERASABLE_MEMBER_FIELDS},
            "id": member.id,
            "created_at": member.created_at,
            "terms_accepted": member.terms_accepted,
            "payment_status": member.payment_status,
            "is_active": member.is_active,
            "membership_starts_on": member.membership_starts_on,
            "membership_ends_on": member.membership_ends_on,
            "renewal_due_on": member.renewal_due_on,
            "cancel_at_period_end": member.cancel_at_period_end,
            "erased_at": member.deleted_at,
            # The Stripe ids are included deliberately: they are how someone can
            # ask Stripe, as a separate controller, for its copy of the data.
            "stripe_customer_id": member.stripe_customer_id,
            "stripe_subscription_id": member.stripe_subscription_id,
        }
        payload["membership_summary"] = describe_coverage(member)
        payload["membership_periods"] = [
            {
                "starts_on": period.starts_on,
                "ends_on": period.ends_on,
                "reason": period.reason,
                "stripe_invoice_id": period.stripe_invoice_id,
                "note": period.note,
                "granted_at": period.created_at,
                "revoked_at": period.revoked_at,
                "revoked_reason": period.revoked_reason,
            }
            for period in member.membership_periods
        ]
        payload["profile_change_requests"] = [
            {
                "requested_at": entry.created_at,
                "status": entry.status,
                "requested_salutation": entry.requested_salutation,
                "requested_title": entry.requested_title,
                "requested_first_name": entry.requested_first_name,
                "requested_last_name": entry.requested_last_name,
                "requested_year_group": entry.requested_year_group,
                "member_note": entry.member_note,
                "admin_note": entry.admin_note,
                "reviewed_at": entry.reviewed_at,
            }
            for entry in member.profile_change_requests
        ]
        payload["forum_avatar_submissions"] = [
            {
                "uploaded_at": entry.uploaded_at,
                "status": entry.status,
                "original_filename": entry.original_filename,
                "content_type": entry.content_type,
                "file_size": entry.file_size,
                "review_note": entry.review_note,
                "reviewed_at": entry.reviewed_at,
            }
            for entry in member.forum_avatar_submissions
        ]

    forum_account = user.forum_account
    if forum_account is not None:
        payload["forum_account"] = {
            "provider": forum_account.provider,
            "state": forum_account.state,
            "last_synced_username": forum_account.last_synced_username,
            "last_synced_email": forum_account.last_synced_email,
            "last_synced_at": forum_account.last_synced_at,
            "created_at": forum_account.created_at,
        }

    payload["emails_sent_to_you"] = [
        {
            "email_type": job.email_type,
            "recipient_email": job.recipient_email,
            "status": job.status,
            "created_at": job.created_at,
            "sent_at": job.sent_at,
        }
        for job in db.session.execute(
            db.select(EmailDeliveryJob)
            .where(EmailDeliveryJob.target_user_id == user.id)
            .order_by(EmailDeliveryJob.created_at)
        ).scalars()
    ]

    # What was done to this account, by whom. Entries where this person acted on
    # somebody else are left out: those are the other person's data, and the
    # before/after snapshots would leak it into this download.
    #
    # The member condition is appended rather than compared against a possibly
    # None id -- `target_member_id == None` renders as IS NULL, which would match
    # every entry that has no member at all and turn an export for a user without
    # a membership profile into a dump of unrelated records.
    history_conditions = [AuditLog.target_user_id == user.id]
    if member is not None:
        history_conditions.append(AuditLog.target_member_id == member.id)

    payload["account_history"] = [
        {
            "at": entry.created_at,
            "category": entry.category,
            "event": entry.event_type,
            "before": entry.before_state,
            "after": entry.after_state,
        }
        for entry in db.session.execute(
            db.select(AuditLog)
            .where(db.or_(*history_conditions))
            .order_by(AuditLog.created_at)
        ).scalars()
    ]

    return serialize_audit_value(payload)


def export_filename_for(user):
    """A stable, non-identifying filename for the download."""
    return f"joanneum-aeronautics-data-export-account-{user.id}.json"


# ---------------------------------------------------------------------------
# Erasure
# ---------------------------------------------------------------------------


def describe_deletion_impact(user, actor_user=None):
    """What an administrator should see before confirming a deletion.

    Deleting an expelled member is legitimate and is not blocked here, but it
    should never be a surprise -- so the consequences that cost money or lock
    people out are stated up front rather than discovered afterwards.
    """
    if user is None:
        raise ValidationError("An account is required.")

    member = user.member
    is_admin = any(role.slug == "admin" for role in user.roles)

    impact = {
        "user_id": user.id,
        "member_id": member.id if member else None,
        "already_erased": is_erased(user),
        "is_admin": is_admin,
        "is_last_admin": is_admin and _count_active_admins() <= 1,
        "is_self": bool(actor_user is not None and actor_user.id == user.id),
        "has_forum_account": user.forum_account is not None,
        "subscription_active": False,
        "subscription_id": member.stripe_subscription_id if member else None,
        # Whether Stripe holds its own copy of this person, which erasure here
        # does not and cannot remove. Drives the sentence saying so before the
        # member confirms, rather than leaving them to find out afterwards.
        "has_stripe_customer": bool(member.stripe_customer_id) if member else False,
        "coverage_end": None,
        "paid_periods": 0,
        "blockers": [],
        "warnings": [],
    }

    if member is not None:
        coverage = describe_coverage(member)
        impact["coverage_end"] = coverage.get("coverage_end")
        impact["paid_periods"] = sum(
            1
            for period in member.membership_periods
            if period.reason == MembershipPeriod.REASON_PAID and period.revoked_at is None
        )
        impact["subscription_active"] = bool(
            member.stripe_subscription_id and not member.cancel_at_period_end
        )

    # Blockers are the two cases where going ahead breaks the system itself
    # rather than being someone's judgement call.
    if impact["already_erased"]:
        impact["blockers"].append("already_erased")
    if impact["is_last_admin"]:
        impact["blockers"].append("last_admin")
    if impact["is_self"]:
        impact["blockers"].append("self_deletion_via_admin_page")

    if impact["subscription_active"]:
        impact["warnings"].append("subscription_will_be_cancelled")
    if impact["coverage_end"]:
        impact["warnings"].append("paid_coverage_remaining")
    if impact["paid_periods"]:
        impact["warnings"].append("payment_history_retained")
    if impact["has_forum_account"]:
        impact["warnings"].append("forum_account_anonymised")

    return impact


def _count_active_admins():
    from ..db_models import Role

    return db.session.scalar(
        db.select(db.func.count())
        .select_from(User)
        .where(User.roles.any(Role.slug == "admin"), User.deleted_at.is_(None))
    ) or 0


def erase_account(user, *, actor_user=None, initiated_by=INITIATED_BY_ADMIN, note=None):
    """Erase the person, keep the record. Returns a summary of what was done.

    Does not commit: the caller owns the transaction, so the erasure and the
    audit entry land together or not at all.
    """
    if user is None:
        raise ValidationError("An account is required to erase.")
    if initiated_by not in {INITIATED_BY_ADMIN, INITIATED_BY_MEMBER}:
        raise ValidationError(f"Unknown deletion initiator: {initiated_by!r}")
    if is_erased(user):
        raise ConflictError("This account has already been erased.", code="already_erased")

    impact = describe_deletion_impact(user, actor_user=actor_user)
    if "last_admin" in impact["blockers"]:
        raise ConflictError(
            "This is the only administrator account; grant admin access to "
            "someone else before erasing it.",
            code="last_admin",
        )
    if initiated_by == INITIATED_BY_ADMIN and impact["is_self"]:
        raise ConflictError(
            "Administrators cannot erase their own account from the admin page; "
            "use the account page so the confirmation goes to your mailbox.",
            code="self_deletion_via_admin_page",
        )

    member = user.member
    now = get_now_utc()
    summary = {
        "user_id": user.id,
        "member_id": member.id if member else None,
        "initiated_by": initiated_by,
        "subscription_cancelled": False,
        "forum_anonymised": False,
        "forum_deferred": False,
        "avatar_files_deleted": 0,
        "audit_entries_redacted": 0,
        "periods_retained": 0,
    }

    # 1. Stop the money first. Erasing a member whose subscription keeps
    #    renewing produces a yearly charge against a customer that nothing in
    #    this database can identify, so a failure here stops the whole thing.
    if member is not None and member.stripe_subscription_id:
        try:
            summary["subscription_cancelled"] = cancel_member_subscription(
                member, reason=f"account_erasure_{initiated_by}"
            )
        except ExternalServiceError:
            raise
        except Exception as exc:  # noqa: BLE001 -- surfaced as a service error
            raise ExternalServiceError(
                "The membership subscription could not be cancelled, so nothing "
                "was erased. Cancel it in Stripe and try again.",
                code="subscription_cancel_failed",
                details={"reason": str(exc)},
            ) from exc

    # 2. The forum is a separate system and may be down. Erasure must not depend
    #    on it, so a failure is queued for retry rather than aborting.
    if user.forum_account is not None:
        anonymised, deferred = anonymise_forum_account(user)
        summary["forum_anonymised"] = anonymised
        summary["forum_deferred"] = deferred

    # 3. Local data.
    if member is not None:
        summary["periods_retained"] = len(member.membership_periods)
        summary["avatar_files_deleted"] = _erase_member_rows(member)
        _erase_member_profile(member, now)

    # Runs whether or not there is a membership profile: an account with none
    # still has password resets and verification emails addressed to it.
    _scrub_messaging_history(user, member)
    summary["audit_entries_redacted"] = _redact_audit_snapshots(user, member)
    _erase_user_record(user, now)

    # 4. The audit entry for an erasure must not be a copy of what it erased, so
    #    it records only the shape of what happened.
    log_audit_event(
        category="privacy",
        event_type="account_erased",
        actor_user=actor_user,
        target_user=user,
        target_member=member,
        metadata={
            "initiated_by": initiated_by,
            "note": note,
            "subscription_cancelled": summary["subscription_cancelled"],
            "forum_anonymised": summary["forum_anonymised"],
            "forum_deferred": summary["forum_deferred"],
            "avatar_files_deleted": summary["avatar_files_deleted"],
            "audit_entries_redacted": summary["audit_entries_redacted"],
            "membership_periods_retained": summary["periods_retained"],
        },
    )

    return summary


def _erase_member_profile(member, now):
    for field in ERASABLE_MEMBER_FIELDS:
        if field in NULLABLE_MEMBER_FIELDS:
            setattr(member, field, None)
        else:
            setattr(member, field, ERASED_TEXT)
    # Unique column, so every erased member needs a distinct value.
    member.email_private = erased_email_for("member", member.id)
    member.is_active = False
    member.deleted_at = now


def _erase_user_record(user, now):
    user.email = erased_email_for("user", user.id)
    # A null hash means no password can ever match, so the login is dead even
    # before the address becomes unusable.
    user.password_hash = None
    user.forum_username = None
    user.email_verified_at = None
    user.password_reset_nonce = None
    user.email_verification_nonce = None
    # Any outstanding signed link (password reset, verification, forum entry)
    # stops working once the nonces are gone.
    user.roles.clear()
    user.deleted_at = now


def _erase_member_rows(member):
    """Delete the related rows that exist only to describe the person.

    Avatars are photographs and change requests are proposed names and notes;
    neither is an accounting record, so these rows go rather than being blanked.
    Returns the number of stored avatar files removed.
    """
    from ..forum_service import delete_submission_file

    files_deleted = 0
    for submission in list(member.forum_avatar_submissions):
        if submission.storage_path and os.path.exists(submission.storage_path):
            try:
                delete_submission_file(submission)
                files_deleted += 1
            except OSError:
                # A file that cannot be removed must not strand the erasure; the
                # database row still goes, and the orphan is reported by the
                # health check rather than silently retried forever.
                pass
        db.session.delete(submission)

    for request_row in list(member.profile_change_requests):
        db.session.delete(request_row)

    return files_deleted


def _scrub_messaging_history(user, member):
    """Blank the personal contents of notification, email and queued-work rows.

    The rows stay so the operational history stays readable -- something was
    sent, on this date, and it failed -- but nothing in them still names a
    person.

    Matched on the user *or* the member, not the member alone. Plenty of these
    rows carry only a user (password resets, verification failures), and an
    account with no membership profile at all -- an administrator, say -- has
    nothing but those. Filtering on the member would miss every one of them.
    """
    conditions_for = lambda model: [model.target_user_id == user.id] + (  # noqa: E731
        [model.target_member_id == member.id] if member is not None else []
    )

    for model in (NotificationEvent, EmailDeliveryJob):
        for row in db.session.execute(
            db.select(model).where(db.or_(*conditions_for(model)))
        ).scalars():
            row.payload = None
            row.recipient_email = None
            # An admin notification's one-line summary is written for a human
            # and routinely embeds the member's address ("a sync failed for
            # ..."), so clearing the payload alone leaves the address behind.
            if hasattr(row, "summary"):
                row.summary = ERASED_SUMMARY

    work_conditions = [ExternalWorkItem.user_id == user.id]
    if member is not None:
        work_conditions.append(ExternalWorkItem.member_id == member.id)
    for item in db.session.execute(
        db.select(ExternalWorkItem).where(db.or_(*work_conditions))
    ).scalars():
        # Except work still to be done -- including the erasure's own forum
        # follow-up, which needs its payload to run at all.
        if item.status in {ExternalWorkItem.STATUS_PENDING, ExternalWorkItem.STATUS_PROCESSING}:
            continue
        item.payload = None


def _redact_audit_snapshots(user, member):
    """Clear the personal snapshots the audit trail kept about this person.

    Profile approvals store the full before/after profile, so an erasure that
    left them alone would leave the name and address sitting in the log. The
    entries themselves stay: who did what, when, is the part that has to survive.

    Entries where this person was the *actor* are untouched -- an admin
    approving somebody else's change is a record about that other member.
    """
    conditions = [AuditLog.target_user_id == user.id]
    if member is not None:
        conditions.append(AuditLog.target_member_id == member.id)

    redacted = 0
    for entry in db.session.execute(
        db.select(AuditLog).where(db.or_(*conditions))
    ).scalars():
        if entry.before_state is None and entry.after_state is None and entry.event_metadata is None:
            continue
        entry.before_state = None
        entry.after_state = None
        entry.event_metadata = None
        redacted += 1
    return redacted


# ---------------------------------------------------------------------------
# Member-initiated deletion
# ---------------------------------------------------------------------------

# Short, because the link erases an account the moment it is confirmed. A
# password reset link lasts a day; this one does not need to.
TOKEN_MAX_AGE_ACCOUNT_DELETION = 60 * 60


def build_account_deletion_token(user):
    """A link that lets someone confirm erasing their own account.

    Requiring the mailbox as well as the session is the point: a borrowed or
    hijacked login should not be enough to destroy an account, and it is the
    same standard the association already applies to changing a password.

    Bound to the current address, so changing the address invalidates any
    outstanding link.
    """
    return generate_token(
        "delete-account",
        user_id=user.id,
        email=(user.email or "").strip().lower(),
    )


def account_deletion_claims_match(token_data, user):
    if user is None or not isinstance(token_data, dict):
        return False
    if is_erased(user):
        return False
    if token_data.get("user_id") != user.id:
        return False
    token_email = (token_data.get("email") or "").strip().lower()
    return bool(token_email) and token_email == (user.email or "").strip().lower()


def send_account_deletion_email(app, user):
    """Send the confirmation link for a member-initiated erasure."""
    confirm_url = build_public_url(
        "account.confirm_account_deletion", token=build_account_deletion_token(user)
    )
    return send_account_action_email(
        app,
        to_email=user.email,
        subject=_("Confirm deleting your Joanneum Aeronautics account"),
        preview_text=_("Confirm that you want your account and personal data deleted."),
        action_url=confirm_url,
        action_label=_("Confirm deletion"),
        heading=_("Confirm deleting your account"),
        body_lines=[
            _("You asked us to delete your Joanneum Aeronautics account and your personal data."),
            _("This cannot be undone. Your name, address and contact details will be erased, "
              "and any active membership subscription will be cancelled without a refund."),
            _("We keep a record of the membership fees you paid, without your personal details, "
              "because bookkeeping law requires it."),
            _("This link expires in one hour. If you did not request this, you can ignore this email."),
        ],
        failure_event_type="account_deletion_email_failed",
        failure_summary=_("An account deletion confirmation email could not be sent."),
        failure_payload={"email_type": "account_deletion"},
        target_user=user,
    )


def refresh_subscription_state_before_deletion(member):
    """Best-effort check of what Stripe currently thinks, for the warning text.

    The cached fields can be stale if a webhook was missed, and the admin is
    about to make an irreversible decision partly based on them. A failure here
    is not important enough to block the page.
    """
    if member is None or not member.stripe_subscription_id:
        return None
    try:
        return get_latest_stripe_subscription_for_member(member)
    except Exception:  # noqa: BLE001 -- display-only refinement
        return None
