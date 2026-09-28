"""Let imported profiles be published to the forum

Two columns on imported_forum_profiles.

``avatar_public_token`` exists because Discourse fetches an avatar itself, as
an unauthenticated server somewhere else, so the admin-only route these files
are served through today cannot give it one. The staging directory also holds
avatars waiting for review, so nothing in it may be reachable by guessing a
filename -- hence a token per profile rather than a public directory, which is
the same answer ForumAvatarSubmission already uses.

``forum_synced_at`` records when a profile was last published, so a re-run can
tell what it has already done from what it has yet to do. Publishing 740
people is one API call each; a run that cannot be resumed is a run that has to
start again from the beginning every time something goes wrong halfway.

Revision ID: a4e2c81b9f37
Revises: f3a17c92de48
Create Date: 2026-09-22

"""
from alembic import op
import sqlalchemy as sa


revision = "a4e2c81b9f37"
down_revision = "f3a17c92de48"
branch_labels = None
depends_on = None

TABLE = "imported_forum_profiles"
COLUMNS = (
    ("avatar_public_token", sa.String(length=64)),
    ("forum_synced_at", sa.DateTime()),
)


def _existing_columns():
    bind = op.get_bind()
    return {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}


def upgrade():
    existing = _existing_columns()
    for name, column_type in COLUMNS:
        if name not in existing:
            op.add_column(TABLE, sa.Column(name, column_type, nullable=True))

    # Unique so a token identifies exactly one profile, which is what makes it
    # safe to serve a file by. A unique index rather than a unique constraint:
    # SQLite cannot ALTER a constraint in, and the tests run these migrations
    # against SQLite.
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(TABLE)}
    if "uq_imported_forum_profiles_avatar_public_token" not in indexes:
        op.create_index(
            "uq_imported_forum_profiles_avatar_public_token",
            TABLE,
            ["avatar_public_token"],
            unique=True,
        )


def downgrade():
    existing = _existing_columns()
    try:
        op.drop_index("uq_imported_forum_profiles_avatar_public_token", table_name=TABLE)
    except Exception:  # noqa: BLE001 -- absent on a database that never had it
        pass
    for name, _column_type in reversed(COLUMNS):
        if name in existing:
            op.drop_column(TABLE, name)
