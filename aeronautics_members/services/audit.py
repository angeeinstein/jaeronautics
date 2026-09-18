"""The audit trail: who changed what, and what it looked like before.

Every entry stores a before/after snapshot, which means this module routinely
handles values that must never be written to a log -- Stripe secrets, SMTP
passwords, forum API keys. Redaction therefore happens here, at the point of
capture, rather than being left to each caller to remember.

Snapshots are plain dicts rather than model references so an entry keeps
describing what happened even after the underlying row changes or is deleted.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import selectinload

from ..config import SENSITIVE_AUDIT_FIELD_NAMES, SENSITIVE_SETTING_KEYS
from ..db_models import AuditLog, db
from .members import MEMBER_PROFILE_FIELDS




def serialize_audit_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: serialize_audit_value(inner_value) for key, inner_value in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [serialize_audit_value(inner_value) for inner_value in value]
    return value


def is_sensitive_audit_field_name(field_name):
    normalized_name = str(field_name or "").strip().lower()
    if not normalized_name:
        return False
    if normalized_name in SENSITIVE_AUDIT_FIELD_NAMES:
        return True
    return any(token in normalized_name for token in ("secret", "password", "api_key", "webhook_secret"))


def redact_sensitive_audit_value(value, placeholder="<configured>"):
    serialized = serialize_audit_value(value)
    if isinstance(serialized, dict):
        redacted = {}
        for key, inner_value in serialized.items():
            if is_sensitive_audit_field_name(key):
                has_secret_value = inner_value is not None and inner_value != "" and inner_value != [] and inner_value != {}
                redacted[key] = placeholder if has_secret_value else None
            else:
                redacted[key] = redact_sensitive_audit_value(inner_value, placeholder=placeholder)
        return redacted
    if isinstance(serialized, list):
        return [redact_sensitive_audit_value(item, placeholder=placeholder) for item in serialized]
    return serialized


def redact_settings_states_for_audit(before_settings, after_settings):
    redacted_before = dict(before_settings or {})
    redacted_after = dict(after_settings or {})
    for key in SENSITIVE_SETTING_KEYS:
        before_value = redacted_before.get(key)
        after_value = redacted_after.get(key)
        before_present = before_value not in {None, ""}
        after_present = after_value not in {None, ""}
        redacted_before[key] = "<configured>" if before_present else None
        if not after_present:
            redacted_after[key] = "<cleared>" if before_present else None
        elif before_present and before_value != after_value:
            redacted_after[key] = "<changed>"
        else:
            redacted_after[key] = "<configured>"
    return redacted_before, redacted_after


def snapshot_user_for_audit(user):
    if user is None:
        return None
    return serialize_audit_value(
        {
            "id": user.id,
            "email": user.email,
            "forum_username": user.forum_username,
            "roles": sorted(role.slug for role in user.roles),
            "email_verified_at": user.email_verified_at,
        }
    )


def snapshot_member_for_audit(member, fields=None):
    if member is None:
        return None
    snapshot_fields = fields or MEMBER_PROFILE_FIELDS
    payload = {field_name: getattr(member, field_name) for field_name in snapshot_fields}
    payload.update(
        {
            "id": member.id,
            "payment_status": member.payment_status,
            "is_active": member.is_active,
            "membership_starts_on": member.membership_starts_on,
            "membership_ends_on": member.membership_ends_on,
            "renewal_due_on": member.renewal_due_on,
            "cancel_at_period_end": member.cancel_at_period_end,
            "stripe_customer_id": member.stripe_customer_id,
            "stripe_subscription_id": member.stripe_subscription_id,
        }
    )
    return serialize_audit_value(payload)


def snapshot_mail_account_for_audit(mail_account):
    if mail_account is None:
        return None
    return serialize_audit_value(
        {
            "id": mail_account.id,
            "account_key": mail_account.account_key,
            "host": mail_account.host,
            "port": mail_account.port,
            "username": mail_account.username,
            "starttls": mail_account.starttls,
        }
    )


def snapshot_forum_account_for_audit(forum_account):
    if forum_account is None:
        return None
    return serialize_audit_value(
        {
            "id": forum_account.id,
            "provider": forum_account.provider,
            "external_id": forum_account.external_id,
            "remote_user_id": forum_account.remote_user_id,
            "state": forum_account.state,
            "last_synced_email": forum_account.last_synced_email,
            "last_synced_username": forum_account.last_synced_username,
            "last_synced_at": forum_account.last_synced_at,
            "last_error": forum_account.last_error,
            "member_id": forum_account.member_id,
            "user_id": forum_account.user_id,
        }
    )


def snapshot_forum_avatar_submission_for_audit(submission):
    if submission is None:
        return None
    return serialize_audit_value(
        {
            "id": submission.id,
            "status": submission.status,
            "original_filename": submission.original_filename,
            "content_type": submission.content_type,
            "file_size": submission.file_size,
            "file_hash": submission.file_hash,
            "storage_path": submission.storage_path,
            "review_note": submission.review_note,
            "sync_error": submission.sync_error,
            "forum_synced_at": submission.forum_synced_at,
            "uploaded_at": submission.uploaded_at,
            "reviewed_at": submission.reviewed_at,
            "member_id": submission.member_id,
            "user_id": submission.user_id,
            "reviewed_by_user_id": submission.reviewed_by_user_id,
        }
    )


def log_audit_event(category, event_type, actor_user=None, target_user=None, target_member=None, before=None, after=None, metadata=None):
    db.session.add(
        AuditLog(
            actor_user=actor_user,
            target_user=target_user,
            target_member=target_member,
            category=category,
            event_type=event_type,
            before_state=redact_sensitive_audit_value(before) if before is not None else None,
            after_state=redact_sensitive_audit_value(after) if after is not None else None,
            event_metadata=redact_sensitive_audit_value(metadata) if metadata is not None else None,
        )
    )


def get_recent_audit_logs(limit=10, category=None):
    query = db.select(AuditLog).options(
        selectinload(AuditLog.actor_user),
        selectinload(AuditLog.target_user),
        selectinload(AuditLog.target_member),
    )
    if category:
        query = query.where(AuditLog.category == category)
    return db.session.execute(query.order_by(AuditLog.created_at.desc()).limit(limit)).scalars().all()
