"""When somebody whose signup was never paid was told it will be removed

Revision ID: c9f3a6b7d2e5
Revises: b8e2f5a3c6d4
Create Date: 2026-10-04

"""
from alembic import op
import sqlalchemy as sa


revision = "c9f3a6b7d2e5"
down_revision = "b8e2f5a3c6d4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("member") as batch_op:
        batch_op.add_column(sa.Column("unfinished_signup_notice_at", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("member") as batch_op:
        batch_op.drop_column("unfinished_signup_notice_at")
