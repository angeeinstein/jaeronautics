"""Shared pytest fixtures for the membership app test suite.

The application normally targets MySQL and reads all of its configuration from
the process environment at import time. For tests we:

* set ``DATABASE_URL`` to a throwaway SQLite file (honored by ``create_app``),
* disable CSRF and rate limiting through ``config_overrides`` so form/webhook
  posts can be driven directly, and
* build the schema with ``db.create_all()`` instead of the MySQL-specific
  ``db-init`` command (which runs ``ALTER TABLE`` statements SQLite rejects).
"""

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Importing the app package reads config from the environment; make sure a
# SECRET_KEY exists so module import does not leave it as ``None``.
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from aeronautics_members import app as app_module  # noqa: E402
from aeronautics_members.db_models import (  # noqa: E402
    Member,
    ProcessedStripeEvent,
    User,
    db,
)

# Re-export commonly used names so tests can ``from conftest import app_module``.
__all__ = ["app_module", "Member", "User", "ProcessedStripeEvent", "db"]


@pytest.fixture
def app(tmp_path):
    db_file = tmp_path / "test.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_file}"
    application = app_module.create_app(
        config_overrides={
            "TESTING": True,
            "SECRET_KEY": "test-secret-key",
            "WTF_CSRF_ENABLED": False,
            "RATELIMIT_ENABLED": False,
            "PUBLIC_BASE_URL": "https://members.test",
        }
    )
    with application.app_context():
        db.create_all()
        try:
            yield application
        finally:
            db.session.remove()
            db.drop_all()
    os.environ.pop("DATABASE_URL", None)


@pytest.fixture
def client(app):
    return app.test_client()


def make_member(email="member@example.com", **overrides):
    """Create and persist a Member (plus linked User) with valid required fields.

    Returns the committed Member. Must be called inside an application context.
    """
    member_fields = dict(
        salutation="Mr",
        first_name="Test",
        last_name="Member",
        street="Main Street",
        house_number="1",
        postal_code="8010",
        city="Graz",
        country="Austria",
        phone_private="+43000000000",
        email_private=email,
        year_group="2020",
        terms_accepted=True,
        payment_status="unpaid",
        is_active=False,
    )
    member_fields.update(overrides)
    member = Member(**member_fields)
    user = User(email=email, forum_username=None)
    user.set_password("initial-password")
    member.user = user
    db.session.add_all([user, member])
    db.session.commit()
    return member
