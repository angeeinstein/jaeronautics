"""Let an admin allow a member to replace an approved profile picture

A picture, once approved, stays: members cannot change it themselves, which
is deliberate. When somebody asks, an admin allows one replacement; this is
when. The old picture and the member's forum access stay until the new one
is approved, which ends the permission.

Revision ID: c3f8a1d6e27b
Revises: b7d41e9a2c58
Create Date: 2026-09-30

"""
from alembic import op
import sqlalchemy as sa


revision = "c3f8a1d6e27b"
down_revision = "b7d41e9a2c58"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("member") as batch_op:
        batch_op.add_column(sa.Column("avatar_replacement_allowed_at", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("member") as batch_op:
        batch_op.drop_column("avatar_replacement_allowed_at")
