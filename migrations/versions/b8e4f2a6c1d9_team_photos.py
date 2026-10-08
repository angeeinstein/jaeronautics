"""A gallery on a team's About page: team_photos

Revision ID: b8e4f2a6c1d9
Revises: a7d3e9f1c5b2
Create Date: 2026-10-07

"""
from alembic import op
import sqlalchemy as sa


revision = "b8e4f2a6c1d9"
down_revision = "a7d3e9f1c5b2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "team_photos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("caption", sa.String(length=200), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("token", name="uq_team_photos_token"),
    )
    op.create_index("ix_team_photos_team_id", "team_photos", ["team_id"])


def downgrade():
    op.drop_index("ix_team_photos_team_id", table_name="team_photos")
    op.drop_table("team_photos")
