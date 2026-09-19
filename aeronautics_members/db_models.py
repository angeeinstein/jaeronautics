from datetime import date, datetime, timezone

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint
from werkzeug.security import check_password_hash, generate_password_hash

from .permissions import Permission, permissions_for


db = SQLAlchemy()

ROLE_ADMIN = "admin"
ROLE_SUPERADMIN = "superadmin"


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
    # Rotated whenever the address changes or a verification link is used, so a
    # verification token issued for an older address cannot be replayed.
    email_verification_nonce = db.Column(db.String(255), nullable=True)
    # Set when the person's data was erased. The row survives because the
    # membership ledger and the audit trail reference it and must stay readable;
    # what made it a person is gone. See services/privacy.py.
    deleted_at = db.Column(db.DateTime, nullable=True)

    member = db.relationship("Member", back_populates="user", uselist=False)
    forum_account = db.relationship("ForumAccount", back_populates="user", uselist=False)
    imported_forum_profile = db.relationship(
        "ImportedForumProfile", back_populates="user", uselist=False
    )
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
    def permissions(self):
        """Everything this account may do, from the roles it holds."""
        return permissions_for(role.slug for role in self.roles)

    def can(self, permission):
        """The access check. Nothing outside permissions.py asks about roles.

        An erased account can do nothing: its rows survive as the record, and
        ``load_user`` already refuses the session, but a check that reached here
        with one must not answer yes.
        """
        if self.deleted_at is not None:
            return False
        return permission in self.permissions

    @property
    def is_admin(self):
        return self.can(Permission.ADMIN_ACCESS)

    def has_role(self, slug):
        """Whether this role is granted. About the grant, not about access.

        Use :meth:`can` to decide what somebody may do. This is for the places
        where the role itself is the subject: revoking it, counting holders, and
        showing which badges an account carries.
        """
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
        """One role name for display. The most capable one the account holds."""
        held = [role.slug for role in self.roles]
        if not held:
            return "user"
        return max(held, key=lambda slug: len(permissions_for([slug])))


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
    # Null means "not a student". Membership is open to non-students under the
    # statutes, and they are full members -- same rights, same fee, no year
    # group to give. Deliberately not paired with an is_student flag: two
    # columns can disagree and nothing would reconcile them, where one cannot.
    year_group = db.Column(db.String(50), nullable=True)
    terms_accepted = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    pending_checkout_started_at = db.Column(db.DateTime, nullable=True)
    stripe_customer_id = db.Column(db.String(255), unique=True, nullable=True)
    stripe_subscription_id = db.Column(db.String(255), unique=True, nullable=True)
    # The last Checkout session started for this member. Kept so resuming an
    # abandoned signup returns to the session that is already open instead of
    # creating a second one -- two open sessions can both be completed, which
    # buys the association two subscriptions for one member.
    stripe_checkout_session_id = db.Column(db.String(255), nullable=True)
    payment_status = db.Column(db.String(50), nullable=False, default="unpaid")
    is_active = db.Column(db.Boolean, nullable=False, default=False)
    membership_starts_on = db.Column(db.Date, nullable=True)
    membership_ends_on = db.Column(db.Date, nullable=True)
    renewal_due_on = db.Column(db.Date, nullable=True)
    cancel_at_period_end = db.Column(db.Boolean, nullable=False, default=False)
    # Erasure marker. The profile columns above are overwritten with placeholders
    # rather than dropped, because they are NOT NULL and because the membership
    # periods, invoices and audit entries that must be kept all point here.
    deleted_at = db.Column(db.DateTime, nullable=True)

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
    membership_periods = db.relationship(
        "MembershipPeriod",
        back_populates="member",
        order_by="desc(MembershipPeriod.ends_on)",
        cascade="all, delete-orphan",
    )

    @property
    def is_student(self):
        """Whether this member is a student, which is what having a year group means.

        Derived rather than stored so it cannot contradict the year group. Reads
        as a question about the person instead of a null check about a column.
        """
        return bool((self.year_group or "").strip())

    @property
    def full_address(self):
        return f"{self.street} {self.house_number}, {self.postal_code} {self.city}, {self.country}"

    @property
    def open_identity_change_request(self):
        return next(
            (request for request in self.profile_change_requests if request.status == "pending"),
            None,
        )

    def __repr__(self):
        return f"<Member {self.first_name} {self.last_name}>"


class MembershipPeriod(db.Model):
    """A window of membership coverage, and the reason the member has it.

    The Member row carries ``payment_status``, ``is_active`` and the coverage
    dates, but those are a *summary*: several code paths write them, and they
    record only the current state, never why it is what it is. That is how a
    member could be marked paid without any payment having happened -- nothing in
    the row could contradict it.

    This table is the evidence behind that summary. Each row says which window
    was granted, on what grounds, and -- for a payment -- which Stripe invoice
    proves it. A grant that turns out to be invalid (a lost dispute, a refund) is
    revoked rather than deleted, so the history of what was believed and when
    stays intact.

    The Member fields remain as a cached projection of these rows, because most
    reads want "is this member active" without a join; ``services/periods.py``
    owns recomputing them so the two cannot drift silently.
    """

    __tablename__ = "membership_periods"

    # Why coverage was granted.
    REASON_PAID = "paid"
    REASON_FREE_PERIOD = "free_period"
    REASON_ADMIN_GRANT = "admin_grant"

    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey("member.id"), nullable=False, index=True)

    starts_on = db.Column(db.Date, nullable=False)
    ends_on = db.Column(db.Date, nullable=False)
    reason = db.Column(db.String(30), nullable=False)

    # Evidence. A paid period should carry the invoice that paid for it; this is
    # also the idempotency key, so a redelivered webhook cannot grant twice.
    stripe_invoice_id = db.Column(db.String(255), nullable=True, unique=True)
    stripe_subscription_id = db.Column(db.String(255), nullable=True)
    granted_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    note = db.Column(db.String(500), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    revoked_at = db.Column(db.DateTime, nullable=True)
    revoked_reason = db.Column(db.String(255), nullable=True)

    member = db.relationship("Member", back_populates="membership_periods")
    granted_by = db.relationship("User", foreign_keys=[granted_by_user_id])

    @property
    def is_revoked(self):
        return self.revoked_at is not None

    def covers(self, day):
        return not self.is_revoked and self.starts_on <= day <= self.ends_on

    def __repr__(self):
        state = " revoked" if self.is_revoked else ""
        return f"<MembershipPeriod member_id={self.member_id} {self.starts_on}..{self.ends_on} {self.reason}{state}>"


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


class ImportedForumProfile(db.Model):
    """A person carried over from the old forum, who is not a member.

    Roughly 500-600 of these exist: students from the last decade whose posts
    should keep a name and a face beside them. They are not members and mostly
    never will be again, but some may come back, so this is deliberately *not*
    a status like "alumni" -- a label about somebody's past contradicts their
    being able to rejoin. Two facts already say everything needed, and neither
    is stored here:

    * whether they can sign in -- ``users.password_hash IS NULL`` says no;
    * whether they are a member -- the coverage ledger says.

    This table holds only what the old forum knew and this database otherwise
    has nowhere to put: a display name, a year group, an avatar. ``Member``
    cannot hold them, because it requires a postal address, a phone number and
    a unique private email address, none of which exist for somebody who left
    in 2016.

    ``source_user_id`` is the old forum's own key, which makes re-running the
    import idempotent rather than duplicating six hundred people.
    """

    __tablename__ = "imported_forum_profiles"
    __table_args__ = (
        UniqueConstraint("source_system", "source_user_id", name="uq_imported_forum_source"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)

    source_system = db.Column(db.String(40), nullable=False, default="mybb")
    source_user_id = db.Column(db.String(64), nullable=False)
    source_username = db.Column(db.String(255), nullable=False)
    # Kept as history, never as identity. The old addresses are university
    # accounts, disabled when a student leaves, and the university may reissue
    # one to a later student of the same name -- so this must never reach
    # users.email, where the sign-in and password-reset paths would find it.
    source_email = db.Column(db.String(255), nullable=True)

    display_name = db.Column(db.String(200), nullable=False)
    year_group = db.Column(db.String(50), nullable=True)
    avatar_path = db.Column(db.String(255), nullable=True)
    post_count = db.Column(db.Integer, nullable=True)
    joined_on = db.Column(db.Date, nullable=True)
    last_posted_on = db.Column(db.Date, nullable=True)

    imported_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    # Set when a returning student takes the account over, by an administrator
    # who recognises them. Never by matching an email address: that match is
    # the takeover route, not a convenience.
    claimed_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User", back_populates="imported_forum_profile")


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
    # Nullable for the same reason as Member.year_group, and so the change
    # request can carry "I have graduated and am no longer a student" -- which
    # approving applies by assigning the null straight across.
    requested_year_group = db.Column(db.String(50), nullable=True)

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


class ExternalWorkItem(db.Model):
    """Work to be carried out against another system, recorded before it is done.

    Forum synchronisation and similar calls used to run inline, in the middle of
    the request or webhook that caused them. Two problems followed. The remote
    call sat inside the handler with its own network timeout, so a slow Discourse
    made Stripe's webhook time out; and when the call failed after the local
    change had already been committed, the two systems simply disagreed and
    nothing remembered that they did.

    A row here is written in the *same transaction* as the change that requires
    it, so either both happen or neither does. A worker then claims the row,
    performs the call, and records success or a retry. Nothing is lost if the
    worker dies holding a claim: the lease expires and the item is picked up
    again, exactly as with the webhook inbox.

    ``dedupe_key`` collapses repeated requests for the same outcome -- five
    membership changes in a minute need one forum sync, not five.
    """

    __tablename__ = "external_work_items"

    KIND_FORUM_SYNC = "forum_sync"
    # Queued when an erasure could not reach Discourse. The local data is already
    # gone at that point, so this has to keep retrying on its own.
    KIND_FORUM_ANONYMISE = "forum_anonymise"

    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(60), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default=STATUS_PENDING, index=True)

    member_id = db.Column(db.Integer, db.ForeignKey("member.id"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    payload = db.Column(db.JSON, nullable=True)

    # Set while an item is outstanding and cleared once it finishes, so a unique
    # index can hold at most one open item per outcome without blocking history.
    dedupe_key = db.Column(db.String(255), nullable=True, unique=True)
    reason = db.Column(db.String(255), nullable=True)

    attempts = db.Column(db.Integer, nullable=False, default=0)
    # Honoured by the worker so a failing item backs off instead of spinning.
    not_before = db.Column(db.DateTime, nullable=True)
    claimed_at = db.Column(db.DateTime, nullable=True)
    last_error = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

    member = db.relationship("Member", foreign_keys=[member_id])
    user = db.relationship("User", foreign_keys=[user_id])

    def __repr__(self):
        return f"<ExternalWorkItem {self.kind} {self.status} member_id={self.member_id}>"


class ProcessedStripeEvent(db.Model):
    """Durable inbox for incoming Stripe webhook events.

    This is more than a "seen it" marker: the row records whether the work the
    event describes actually *finished*. A row claimed but never completed (for
    example because the process was killed mid-handler) holds an expired lease,
    which lets a later redelivery take it over instead of being waved through as
    a duplicate. Only ``status == "completed"`` suppresses reprocessing.
    """

    __tablename__ = "processed_stripe_events"

    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.String(255), unique=True, nullable=False)
    event_type = db.Column(db.String(120), nullable=True)
    # Null until the handler finishes; set when the event reaches "completed".
    processed_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), nullable=False, default=STATUS_PROCESSING)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    claimed_at = db.Column(db.DateTime, nullable=True, default=utcnow)
    last_error = db.Column(db.Text, nullable=True)


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

