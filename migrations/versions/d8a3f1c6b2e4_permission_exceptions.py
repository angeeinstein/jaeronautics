"""Permissions per person: exceptions to the roles, site-wide and per team

``user_permissions``: a permission added to a person's roles (``granted``
true) or taken away from them (false). ``team_member_permissions``: the same
for one person in one team, beside their team roles. docs/permissions-plan.md.

Safe to run again after it stopped part-way.

Revision ID: d8a3f1c6b2e4
Revises: c5f2a8d1e7b3
Create Date: 2026-11-19

"""
from alembic import op
import sqlalchemy as sa


revision = "d8a3f1c6b2e4"
down_revision = "c5f2a8d1e7b3"
branch_labels = None
depends_on = None


def upgrade():
    have = set(sa.inspect(op.get_bind()).get_table_names())
    if "user_permissions" not in have:
        op.create_table(
            "user_permissions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("permission", sa.String(80), nullable=False),
            sa.Column("granted", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("granted_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"),
                      nullable=True),
            sa.UniqueConstraint("user_id", "permission", name="uq_user_permissions_user_permission"),
        )
        op.create_index("ix_user_permissions_user_id", "user_permissions", ["user_id"])
    if "team_member_permissions" not in have:
        op.create_table(
            "team_member_permissions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("permission", sa.String(80), nullable=False),
            sa.Column("granted", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("granted_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"),
                      nullable=True),
            sa.UniqueConstraint("team_id", "user_id", "permission", name="uq_team_member_permissions"),
        )
        op.create_index("ix_team_member_permissions_team_user", "team_member_permissions", ["team_id", "user_id"])


def downgrade():
    op.drop_table("team_member_permissions")
    op.drop_table("user_permissions")
