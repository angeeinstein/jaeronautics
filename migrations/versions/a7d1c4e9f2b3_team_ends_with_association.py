"""A team membership that ends with the association membership

Set when somebody cancels their association membership: their team
memberships end on the same day, and taking the cancellation back lifts that
again -- but not an end they chose themselves by leaving.

Revision ID: a7d1c4e9f2b3
Revises: f6c3d8a1b2e4
Create Date: 2026-10-03

"""
from alembic import op
import sqlalchemy as sa


revision = "a7d1c4e9f2b3"
down_revision = "f6c3d8a1b2e4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("team_memberships") as batch_op:
        batch_op.add_column(sa.Column("ends_with_association", sa.Boolean(), nullable=False,
                                      server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("team_memberships") as batch_op:
        batch_op.drop_column("ends_with_association")
