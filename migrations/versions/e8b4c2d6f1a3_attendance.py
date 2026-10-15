"""Attendance: stays, and a switch per team

``attendance_stays``: somebody there, from checking in to checking out, for
a team or the association (docs/attendance-plan.md). ``teams.attendance_enabled``:
a team's readers record attendance only once it is switched on. The
association's switch is a setting.

Safe to run again after it stopped part-way.

Revision ID: e8b4c2d6f1a3
Revises: d7a3f1c9e5b8
Create Date: 2026-10-15

"""
from alembic import op
import sqlalchemy as sa


revision = "e8b4c2d6f1a3"
down_revision = "d7a3f1c9e5b8"
branch_labels = None
depends_on = None


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if "attendance_enabled" not in _columns("teams"):
        with op.batch_alter_table("teams") as batch_op:
            batch_op.add_column(sa.Column("attendance_enabled", sa.Boolean(), nullable=False,
                                          server_default=sa.false()))
    if "attendance_stays" not in _tables():
        op.create_table(
            "attendance_stays",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("ended_at", sa.DateTime(), nullable=True),
            sa.Column("closed_at", sa.DateTime(), nullable=True),
            sa.Column("in_device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="SET NULL"), nullable=True),
            sa.Column("out_device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("corrected_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("corrected_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_attendance_stays_user_id", "attendance_stays", ["user_id"])
        op.create_index("ix_attendance_stays_team_id", "attendance_stays", ["team_id"])


def downgrade():
    if "attendance_stays" in _tables():
        op.drop_table("attendance_stays")
    if "attendance_enabled" in _columns("teams"):
        with op.batch_alter_table("teams") as batch_op:
            batch_op.drop_column("attendance_enabled")
