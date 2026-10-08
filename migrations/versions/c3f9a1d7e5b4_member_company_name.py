"""The company a partner member joins for: member.company_name

Company and partner members were asked for a company address and phone but
never for the company itself, so the association could not tell from its own
records whom a partner member stands for.

Revision ID: c3f9a1d7e5b4
Revises: b8e4f2a6c1d9
Create Date: 2026-10-08

"""
from alembic import op
import sqlalchemy as sa


revision = "c3f9a1d7e5b4"
down_revision = "b8e4f2a6c1d9"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("member") as batch_op:
        batch_op.add_column(sa.Column("company_name", sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table("member") as batch_op:
        batch_op.drop_column("company_name")
