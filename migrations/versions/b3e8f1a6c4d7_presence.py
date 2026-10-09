"""Presence: who is using the portal right now, without saying who

One row per open browser tab somebody used in the last minutes -- a random
id, signed in or not, the page, when it last spoke and was typed in -- shown
on Settings › Updates. See docs/presence.md.

Revision ID: b3e8f1a6c4d7
Revises: a7d2c5e8f9b1
Create Date: 2026-10-09

"""
from alembic import op
import sqlalchemy as sa


revision = "b3e8f1a6c4d7"
down_revision = "a7d2c5e8f9b1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "presence",
        sa.Column("tab", sa.String(length=36), primary_key=True),
        sa.Column("signed_in", sa.Boolean(), nullable=False),
        sa.Column("page", sa.String(length=80), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("seen_at", sa.DateTime(), nullable=False),
        sa.Column("typed_at", sa.DateTime(), nullable=True),
    )
    with op.batch_alter_table("presence") as batch_op:
        batch_op.create_index("ix_presence_seen_at", ["seen_at"])


def downgrade():
    op.drop_table("presence")
