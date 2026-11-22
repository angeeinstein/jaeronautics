"""Attendance: remote work

A stay can be **remote** -- work a member did away from the readers and added
themselves -- with what they did (``note``), and a lead's mark that it does not
count, with who set it, when and why (docs/attendance-plan.md, "Remote work").
``teams.remote_work_enabled``: a team's members add remote work only once it is
switched on; the association's switch is a setting.

Safe to run again after it stopped part-way.

Revision ID: b6d2f8a4c1e9
Revises: f1c8a3d6b9e2
Create Date: 2026-11-22

"""
from alembic import op
import sqlalchemy as sa


revision = "b6d2f8a4c1e9"
down_revision = "f1c8a3d6b9e2"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if "remote_work_enabled" not in _columns("teams"):
        with op.batch_alter_table("teams") as batch_op:
            batch_op.add_column(sa.Column("remote_work_enabled", sa.Boolean(), nullable=False,
                                          server_default=sa.false()))
    columns = _columns("attendance_stays")
    with op.batch_alter_table("attendance_stays") as batch_op:
        if "remote" not in columns:
            batch_op.add_column(sa.Column("remote", sa.Boolean(), nullable=False, server_default=sa.false()))
        if "note" not in columns:
            batch_op.add_column(sa.Column("note", sa.String(200), nullable=True))
        if "not_counted_at" not in columns:
            batch_op.add_column(sa.Column("not_counted_at", sa.DateTime(), nullable=True))
        if "not_counted_reason" not in columns:
            batch_op.add_column(sa.Column("not_counted_reason", sa.String(200), nullable=True))
        if "not_counted_by_user_id" not in columns:
            batch_op.add_column(sa.Column("not_counted_by_user_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key("fk_attendance_stays_not_counted_by", "users",
                                        ["not_counted_by_user_id"], ["id"], ondelete="SET NULL")


def downgrade():
    columns = _columns("attendance_stays")
    with op.batch_alter_table("attendance_stays") as batch_op:
        if "not_counted_by_user_id" in columns:
            batch_op.drop_constraint("fk_attendance_stays_not_counted_by", type_="foreignkey")
            batch_op.drop_column("not_counted_by_user_id")
        for name in ("not_counted_reason", "not_counted_at", "note", "remote"):
            if name in columns:
                batch_op.drop_column(name)
    if "remote_work_enabled" in _columns("teams"):
        with op.batch_alter_table("teams") as batch_op:
            batch_op.drop_column("remote_work_enabled")
