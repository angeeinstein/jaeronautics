from flask_sqlalchemy import SQLAlchemy
from datetime import date, datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin

# Initialize SQLAlchemy instance. This will be imported by the main app.
db = SQLAlchemy()

class Member(db.Model):
    """
    Database model for a new Aeronautics Member.
    This is the single source of truth for the Member model.
    """
    __tablename__ = "member"
    
    # Required Fields
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
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
    stripe_customer_id = db.Column(db.String(255), unique=True, nullable=True)
    stripe_subscription_id = db.Column(db.String(255), unique=True, nullable=True)
    payment_status = db.Column(db.String(50), nullable=False, default='unpaid')
    is_active = db.Column(db.Boolean, nullable=False, default=False)
    membership_starts_on = db.Column(db.Date, nullable=True)
    membership_ends_on = db.Column(db.Date, nullable=True)
    renewal_due_on = db.Column(db.Date, nullable=True)
    cancel_at_period_end = db.Column(db.Boolean, nullable=False, default=False)

    def __repr__(self):
        return f'<Member {self.first_name} {self.last_name}>'

    @property
    def full_address(self):
        """Generates a formatted address string."""
        return f"{self.street} {self.house_number}, {self.postal_code} {self.city}, {self.country}"

    @property
    def has_current_coverage(self):
        if not self.membership_ends_on:
            return self.is_active
        return self.membership_ends_on >= date.today()

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256))
    role = db.Column(db.String(80), nullable=False, default='user')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Setting(db.Model):
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(100), nullable=False)

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

