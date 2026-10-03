"""Teams: remember the last access list sent, to mark who is new

Revision ID: d4a8e1f2c3b7
Revises: c91f4d2b6e85
Create Date: 2026-10-03

"""
from alembic import op
import sqlalchemy as sa


revision = "d4a8e1f2c3b7"
down_revision = "c91f4d2b6e85"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "team_access_list_sends",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("sent_on", sa.Date(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("entries", sa.JSON(), nullable=False),
    )
    op.create_index("ix_team_access_list_sends_team_id", "team_access_list_sends", ["team_id"])


def downgrade():
    op.drop_index("ix_team_access_list_sends_team_id", table_name="team_access_list_sends")
    op.drop_table("team_access_list_sends")
