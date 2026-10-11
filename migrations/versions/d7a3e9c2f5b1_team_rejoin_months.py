"""Teams: former members may rejoin without approval for a number of months

A team setting, off (null) by default. See docs/team-passive-members.md.

Revision ID: d7a3e9c2f5b1
Revises: c5f2a8d1e7b4
Create Date: 2026-10-11

"""
from alembic import op
import sqlalchemy as sa


revision = "d7a3e9c2f5b1"
down_revision = "c5f2a8d1e7b4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("teams") as batch_op:
        batch_op.add_column(sa.Column("rejoin_months", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("teams") as batch_op:
        batch_op.drop_column("rejoin_months")
