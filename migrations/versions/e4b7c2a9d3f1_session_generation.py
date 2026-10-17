"""A session generation for each account

Part of every session's id: raised when the password or the login address
changes, which ends every session issued before (app.load_user). Existing
accounts start at 0, which their open sessions already name. Safe to run
again.

Revision ID: e4b7c2a9d3f1
Revises: c3f8a2d6e1b9
Create Date: 2026-10-17

"""
from alembic import op
import sqlalchemy as sa


revision = "e4b7c2a9d3f1"
down_revision = "c3f8a2d6e1b9"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if "session_generation" not in _columns("users"):
        with op.batch_alter_table("users") as batch:
            batch.add_column(sa.Column("session_generation", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    if "session_generation" in _columns("users"):
        with op.batch_alter_table("users") as batch:
            batch.drop_column("session_generation")
