from datetime import date, datetime, timezone

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

# Initialize SQLAlchemy instance. This will be imported by the main app.
db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=True)
    role = db.Column(db.String(80), nullable=False, default='member')
    forum_username = db.Column(db.String(255), unique=True, nullable=True)
    email_verified_at = db.Column(db.DateTime, nullable=True)

    member = db.relationship('Member', back_populates='user', uselist=False)
    requested_profile_changes = db.relationship(
        'MemberProfileChangeRequest',
        back_populates='requested_by',
        foreign_keys='MemberProfileChangeRequest.requested_by_user_id',
    )
    reviewed_profile_changes = db.relationship(
        'MemberProfileChangeRequest',
        back_populates='reviewed_by',
        foreign_keys='MemberProfileChangeRequest.reviewed_by_user_id',
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    @property
    def email_is_verified(self):
        return self.email_verified_at is not None


class Member(db.Model):
    """
    Database model for a Joanneum Aeronautics member profile.
    Membership and billing state live here; login/identity live on User.
    """

    __tablename__ = 'member'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=True)

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
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    pending_checkout_started_at = db.Column(db.DateTime, nullable=True)
    stripe_customer_id = db.Column(db.String(255), unique=True, nullable=True)
    stripe_subscription_id = db.Column(db.String(255), unique=True, nullable=True)
    payment_status = db.Column(db.String(50), nullable=False, default='unpaid')
    is_active = db.Column(db.Boolean, nullable=False, default=False)
    membership_starts_on = db.Column(db.Date, nullable=True)
    membership_ends_on = db.Column(db.Date, nullable=True)
    renewal_due_on = db.Column(db.Date, nullable=True)
    cancel_at_period_end = db.Column(db.Boolean, nullable=False, default=False)

    user = db.relationship('User', back_populates='member', uselist=False)
    profile_change_requests = db.relationship(
        'MemberProfileChangeRequest',
        back_populates='member',
        order_by='desc(MemberProfileChangeRequest.created_at)',
        cascade='all, delete-orphan',
    )

    def __repr__(self):
        return f'<Member {self.first_name} {self.last_name}>'

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
            (
                request
                for request in self.profile_change_requests
                if request.status == 'pending'
            ),
            None,
        )


class MemberProfileChangeRequest(db.Model):
    __tablename__ = 'member_profile_change_requests'

    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('member.id'), nullable=False)
    requested_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    reviewed_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    requested_salutation = db.Column(db.String(20), nullable=False)
    requested_title = db.Column(db.String(50), nullable=True)
    requested_first_name = db.Column(db.String(100), nullable=False)
    requested_last_name = db.Column(db.String(100), nullable=False)
    requested_year_group = db.Column(db.String(50), nullable=False)

    status = db.Column(db.String(20), nullable=False, default='pending')
    member_note = db.Column(db.Text, nullable=True)
    admin_note = db.Column(db.Text, nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    member = db.relationship('Member', back_populates='profile_change_requests')
    requested_by = db.relationship('User', foreign_keys=[requested_by_user_id], back_populates='requested_profile_changes')
    reviewed_by = db.relationship('User', foreign_keys=[reviewed_by_user_id], back_populates='reviewed_profile_changes')

    @property
    def requested_full_name(self):
        title = f"{self.requested_title} " if self.requested_title else ''
        return f"{title}{self.requested_first_name} {self.requested_last_name}".strip()


class Setting(db.Model):
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(255), nullable=False)


class MailAccount(db.Model):
    __tablename__ = 'mail_accounts'

    id = db.Column(db.Integer, primary_key=True)
    account_key = db.Column(db.String(80), unique=True, nullable=False)
    host = db.Column(db.String(255), nullable=False)
    port = db.Column(db.Integer, nullable=False)
    username = db.Column(db.String(255), nullable=False)
    password = db.Column(db.String(255), nullable=False)
    starttls = db.Column(db.Boolean, nullable=False, default=False)

    def to_config(self):
        config = {
            'host': self.host,
            'port': self.port,
            'user': self.username,
            'pass': self.password,
        }
        if self.starttls:
            config['starttls'] = True
        return config
