"""Teams: groups inside the association with their own members and leads

Four tables, all new and all empty until somebody switches teams on and
creates one. Nothing existing changes.

Revision ID: a4d2e7c91b30
Revises: c3f8a1d6e27b
Create Date: 2026-10-03

"""
from alembic import op
import sqlalchemy as sa


revision = "a4d2e7c91b30"
down_revision = "c3f8a1d6e27b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("admission_mode", sa.String(length=20), nullable=False),
        sa.Column("applications_open", sa.Boolean(), nullable=False),
        sa.Column("application_prompt", sa.String(length=255), nullable=True),
        sa.Column("max_members", sa.Integer(), nullable=True),
        sa.Column("forum_group", sa.String(length=100), nullable=True),
        sa.Column("logo_path", sa.String(length=500), nullable=True),
        sa.Column("logo_token", sa.String(length=64), nullable=True),
        sa.Column("payment_mode", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("slug"),
        sa.UniqueConstraint("logo_token"),
    )

    op.create_table(
        "team_roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
        sa.Column("granted_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.UniqueConstraint("team_id", "user_id", "role", name="uq_team_roles_team_user_role"),
    )
    op.create_index("ix_team_roles_team_id", "team_roles", ["team_id"])
    op.create_index("ix_team_roles_user_id", "team_roles", ["user_id"])

    op.create_table(
        "team_memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("application_text", sa.Text(), nullable=True),
        sa.Column("meeting_details", sa.Text(), nullable=True),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.Column("invited_at", sa.DateTime(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("payment_mode", sa.String(length=20), nullable=True),
        sa.Column("payment_settled_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("decided_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("end_reason", sa.String(length=40), nullable=True),
        sa.Column("end_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_team_memberships_team_id", "team_memberships", ["team_id"])
    op.create_index("ix_team_memberships_user_id", "team_memberships", ["user_id"])
    op.create_index("ix_team_memberships_status", "team_memberships", ["status"])

    op.create_table(
        "team_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("author_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_team_notes_team_id", "team_notes", ["team_id"])
    op.create_index("ix_team_notes_user_id", "team_notes", ["user_id"])


def downgrade():
    op.drop_index("ix_team_notes_user_id", table_name="team_notes")
    op.drop_index("ix_team_notes_team_id", table_name="team_notes")
    op.drop_table("team_notes")
    op.drop_index("ix_team_memberships_status", table_name="team_memberships")
    op.drop_index("ix_team_memberships_user_id", table_name="team_memberships")
    op.drop_index("ix_team_memberships_team_id", table_name="team_memberships")
    op.drop_table("team_memberships")
    op.drop_index("ix_team_roles_user_id", table_name="team_roles")
    op.drop_index("ix_team_roles_team_id", table_name="team_roles")
    op.drop_table("team_roles")
    op.drop_table("teams")
