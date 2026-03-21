from datetime import date, datetime, timezone

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint
from werkzeug.security import check_password_hash, generate_password_hash


db = SQLAlchemy()



def utcnow():
    return datetime.now(timezone.utc)


class UserRole(db.Model):
    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_role"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Role(db.Model):
    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(80), unique=True, nullable=False)
    label = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    users = db.relationship("User", secondary="user_roles", back_populates="roles")


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=True)
    forum_username = db.Column(db.String(255), unique=True, nullable=True)
    email_verified_at = db.Column(db.DateTime, nullable=True)
    password_reset_nonce = db.Column(db.String(255), nullable=True)

    member = db.relationship("Member", back_populates="user", uselist=False)
    forum_account = db.relationship("ForumAccount", back_populates="user", uselist=False)
    roles = db.relationship("Role", secondary="user_roles", back_populates="users")
    requested_profile_changes = db.relationship(
        "MemberProfileChangeRequest",
        back_populates="requested_by",
        foreign_keys="MemberProfileChangeRequest.requested_by_user_id",
    )
    reviewed_profile_changes = db.relationship(
        "MemberProfileChangeRequest",
        back_populates="reviewed_by",
        foreign_keys="MemberProfileChangeRequest.reviewed_by_user_id",
    )
    audit_logs_as_actor = db.relationship(
        "AuditLog",
        back_populates="actor_user",
        foreign_keys="AuditLog.actor_user_id",
    )
    audit_logs_as_target = db.relationship(
        "AuditLog",
        back_populates="target_user",
        foreign_keys="AuditLog.target_user_id",
    )
    forum_avatar_submissions = db.relationship(
        "ForumAvatarSubmission",
        back_populates="user",
        foreign_keys="ForumAvatarSubmission.user_id",
        order_by="desc(ForumAvatarSubmission.uploaded_at)",
        cascade="all, delete-orphan",
    )
    reviewed_forum_avatar_submissions = db.relationship(
        "ForumAvatarSubmission",
        back_populates="reviewed_by",
        foreign_keys="ForumAvatarSubmission.reviewed_by_user_id",
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        self.password_reset_nonce = None

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    @property
    def email_is_verified(self):
        return self.email_verified_at is not None

    @property
    def is_admin(self):
        return self.has_role("admin")

    def has_role(self, slug):
        return any(role.slug == slug for role in self.roles)

    def grant_role(self, role):
        if not any(existing_role.id == role.id for existing_role in self.roles):
            self.roles.append(role)

    def revoke_role(self, slug):
        role = next((existing_role for existing_role in self.roles if existing_role.slug == slug), None)
        if role is not None:
            self.roles.remove(role)

    @property
    def role(self):
        if self.has_role("admin"):
            return "admin"
        return "user"


class Member(db.Model):
    __tablename__ = "member"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=True)

    salutation = db.Column(db.String(20), nullable=False)
    title = db.Column(db.String(50), nullable=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    street = db.Column(db.String(255), nullable=False)
    house_number = db.Column(db.String(20), nullable=False)
    postal_code = db.Column(db.String(20), nullable=False)
    city = db.Column(db.String(100), nullable=False)
    country = db.Column(db.String(100), nullable=False)
    phone_private = db.Column(db.String(50), nullable=False)
    email_private = db.Column(db.String(255), nullable=False, unique=True)
    phone_work = db.Column(db.String(50), nullable=True)
    email_work = db.Column(db.String(255), nullable=True)
    year_group = db.Column(db.String(50), nullable=False)
    terms_accepted = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    pending_checkout_started_at = db.Column(db.DateTime, nullable=True)
    stripe_customer_id = db.Column(db.String(255), unique=True, nullable=True)
    stripe_subscription_id = db.Column(db.String(255), unique=True, nullable=True)
    payment_status = db.Column(db.String(50), nullable=False, default="unpaid")
    is_active = db.Column(db.Boolean, nullable=False, default=False)
    membership_starts_on = db.Column(db.Date, nullable=True)
    membership_ends_on = db.Column(db.Date, nullable=True)
    renewal_due_on = db.Column(db.Date, nullable=True)
    cancel_at_period_end = db.Column(db.Boolean, nullable=False, default=False)

    user = db.relationship("User", back_populates="member", uselist=False)
    forum_account = db.relationship("ForumAccount", back_populates="member", uselist=False)
    profile_change_requests = db.relationship(
        "MemberProfileChangeRequest",
        back_populates="member",
        order_by="desc(MemberProfileChangeRequest.created_at)",
        cascade="all, delete-orphan",
    )
    audit_logs = db.relationship("AuditLog", back_populates="target_member")
    forum_avatar_submissions = db.relationship(
        "ForumAvatarSubmission",
        back_populates="member",
        order_by="desc(ForumAvatarSubmission.uploaded_at)",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Member {self.first_name} {self.last_name}>"

    @property
    def full_address(self):
        return f"{self.street} {self.house_number}, {self.postal_code} {self.city}, {self.country}"

    @property
    def has_current_coverage(self):
        if not self.membership_ends_on:
            return self.is_active
        return self.membership_ends_on >= date.today()

    @property
    def open_identity_change_request(self):
        return next(
            (request for request in self.profile_change_requests if request.status == "pending"),
            None,
        )


class ForumAccount(db.Model):
    __tablename__ = "forum_accounts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    member_id = db.Column(db.Integer, db.ForeignKey("member.id"), unique=True, nullable=True)
    provider = db.Column(db.String(80), nullable=False, default="discourse")
    external_id = db.Column(db.String(255), nullable=False)
    remote_user_id = db.Column(db.Integer, nullable=True)
    state = db.Column(db.String(40), nullable=False, default="inactive")
    last_synced_email = db.Column(db.String(255), nullable=True)
    last_synced_username = db.Column(db.String(255), nullable=True)
    last_synced_at = db.Column(db.DateTime, nullable=True)
    last_error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    user = db.relationship("User", back_populates="forum_account")
    member = db.relationship("Member", back_populates="forum_account")


class ForumAvatarSubmission(db.Model):
    __tablename__ = "forum_avatar_submissions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    member_id = db.Column(db.Integer, db.ForeignKey("member.id"), nullable=False)
    reviewed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    status = db.Column(db.String(40), nullable=False, default="pending")
    original_filename = db.Column(db.String(255), nullable=True)
    content_type = db.Column(db.String(120), nullable=True)
    file_size = db.Column(db.Integer, nullable=True)
    file_hash = db.Column(db.String(128), nullable=True)
    storage_path = db.Column(db.String(500), nullable=True)
    public_token = db.Column(db.String(255), unique=True, nullable=True)
    review_note = db.Column(db.Text, nullable=True)
    sync_error = db.Column(db.Text, nullable=True)
    forum_synced_at = db.Column(db.DateTime, nullable=True)
    uploaded_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    reviewed_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User", back_populates="forum_avatar_submissions", foreign_keys=[user_id])
    member = db.relationship("Member", back_populates="forum_avatar_submissions", foreign_keys=[member_id])
    reviewed_by = db.relationship(
        "User",
        back_populates="reviewed_forum_avatar_submissions",
        foreign_keys=[reviewed_by_user_id],
    )


class MemberProfileChangeRequest(db.Model):
    __tablename__ = "member_profile_change_requests"

    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey("member.id"), nullable=False)
    requested_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    reviewed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    requested_salutation = db.Column(db.String(20), nullable=False)
    requested_title = db.Column(db.String(50), nullable=True)
    requested_first_name = db.Column(db.String(100), nullable=False)
    requested_last_name = db.Column(db.String(100), nullable=False)
    requested_year_group = db.Column(db.String(50), nullable=False)

    status = db.Column(db.String(20), nullable=False, default="pending")
    member_note = db.Column(db.Text, nullable=True)
    admin_note = db.Column(db.Text, nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    member = db.relationship("Member", back_populates="profile_change_requests")
    requested_by = db.relationship(
        "User",
        foreign_keys=[requested_by_user_id],
        back_populates="requested_profile_changes",
    )
    reviewed_by = db.relationship(
        "User",
        foreign_keys=[reviewed_by_user_id],
        back_populates="reviewed_profile_changes",
    )

    @property
    def requested_full_name(self):
        title = f"{self.requested_title} " if self.requested_title else ""
        return f"{title}{self.requested_first_name} {self.requested_last_name}".strip()


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    target_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    target_member_id = db.Column(db.Integer, db.ForeignKey("member.id"), nullable=True)
    category = db.Column(db.String(80), nullable=False)
    event_type = db.Column(db.String(120), nullable=False)
    before_state = db.Column(db.JSON, nullable=True)
    after_state = db.Column(db.JSON, nullable=True)
    event_metadata = db.Column("metadata", db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    actor_user = db.relationship("User", foreign_keys=[actor_user_id], back_populates="audit_logs_as_actor")
    target_user = db.relationship("User", foreign_keys=[target_user_id], back_populates="audit_logs_as_target")
    target_member = db.relationship("Member", foreign_keys=[target_member_id], back_populates="audit_logs")


class NotificationBatch(db.Model):
    __tablename__ = "notification_batches"

    id = db.Column(db.Integer, primary_key=True)
    channel = db.Column(db.String(80), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="sent")
    recipient_scope = db.Column(db.String(120), nullable=False)
    recipient_count = db.Column(db.Integer, nullable=False, default=0)
    event_count = db.Column(db.Integer, nullable=False, default=0)
    subject = db.Column(db.String(255), nullable=True)
    error_text = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    sent_at = db.Column(db.DateTime, nullable=True)

    events = db.relationship("NotificationEvent", back_populates="batch")


class NotificationChannelState(db.Model):
    __tablename__ = "notification_channel_states"

    channel = db.Column(db.String(80), primary_key=True)
    cooldown_stage = db.Column(db.Integer, nullable=False, default=0)
    next_allowed_at = db.Column(db.DateTime, nullable=True)
    last_activity_at = db.Column(db.DateTime, nullable=True)
    last_sent_at = db.Column(db.DateTime, nullable=True)
    rolling_sent_count = db.Column(db.Integer, nullable=False, default=0)
    failure_stage = db.Column(db.Integer, nullable=False, default=0)
    failure_backoff_until = db.Column(db.DateTime, nullable=True)
    last_failure_at = db.Column(db.DateTime, nullable=True)
    last_failure_message = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class NotificationEvent(db.Model):
    __tablename__ = "notification_events"

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("notification_batches.id"), nullable=True)
    channel = db.Column(db.String(80), nullable=False)
    audience = db.Column(db.String(80), nullable=False)
    severity = db.Column(db.String(40), nullable=False, default="info")
    event_type = db.Column(db.String(120), nullable=False)
    summary = db.Column(db.String(255), nullable=False)
    payload = db.Column(db.JSON, nullable=True)
    target_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    target_member_id = db.Column(db.Integer, db.ForeignKey("member.id"), nullable=True)
    recipient_email = db.Column(db.String(255), nullable=True)
    object_type = db.Column(db.String(80), nullable=True)
    object_id = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="pending")
    queued_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_attempted_at = db.Column(db.DateTime, nullable=True)
    processed_at = db.Column(db.DateTime, nullable=True)
    delivery_error = db.Column(db.Text, nullable=True)

    batch = db.relationship("NotificationBatch", back_populates="events")
    target_user = db.relationship("User", foreign_keys=[target_user_id])
    target_member = db.relationship("Member", foreign_keys=[target_member_id])


class EmailDeliveryJob(db.Model):
    __tablename__ = "email_delivery_jobs"

    id = db.Column(db.Integer, primary_key=True)
    email_type = db.Column(db.String(80), nullable=False)
    recipient_email = db.Column(db.String(255), nullable=True)
    target_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    target_member_id = db.Column(db.Integer, db.ForeignKey("member.id"), nullable=True)
    payload = db.Column(db.JSON, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="pending")
    retry_count = db.Column(db.Integer, nullable=False, default=0)
    next_attempt_at = db.Column(db.DateTime, nullable=True)
    last_attempted_at = db.Column(db.DateTime, nullable=True)
    last_error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    sent_at = db.Column(db.DateTime, nullable=True)

    target_user = db.relationship("User", foreign_keys=[target_user_id])
    target_member = db.relationship("Member", foreign_keys=[target_member_id])


class Setting(db.Model):
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(255), nullable=False)


class MailAccount(db.Model):
    __tablename__ = "mail_accounts"

    id = db.Column(db.Integer, primary_key=True)
    account_key = db.Column(db.String(80), unique=True, nullable=False)
    host = db.Column(db.String(255), nullable=False)
    port = db.Column(db.Integer, nullable=False)
    username = db.Column(db.String(255), nullable=False)
    password = db.Column(db.String(255), nullable=False)
    starttls = db.Column(db.Boolean, nullable=False, default=False)

    def to_config(self):
        config = {
            "host": self.host,
            "port": self.port,
            "user": self.username,
            "pass": self.password,
        }
        if self.starttls:
            config["starttls"] = True
        return config

