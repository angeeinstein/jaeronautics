"""Devices: the last requests each made, and the end of its key

``device_requests`` keeps the last 100 requests of each device and what each
was answered, for writing its firmware; ``devices.key_tail`` the last four
characters of its key, to tell keys apart; ``devices.key_revoked_at`` when
its key was revoked without a new one (docs/devices-plan.md).

Safe to run again after it stopped part-way.

Revision ID: e7b2d5a8c3f0
Revises: d4a9c7e2f1b6
Create Date: 2026-10-12

"""
from alembic import op
import sqlalchemy as sa


revision = "e7b2d5a8c3f0"
down_revision = "d4a9c7e2f1b6"
branch_labels = None
depends_on = None


def _have():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    indexes = {index["name"] for table in tables for index in inspector.get_indexes(table)}
    columns = {column["name"] for column in inspector.get_columns("devices")}
    return tables, indexes, columns


def upgrade():
    tables, indexes, columns = _have()
    if "key_tail" not in columns:
        with op.batch_alter_table("devices") as batch_op:
            batch_op.add_column(sa.Column("key_tail", sa.String(8), nullable=True))
    if "key_revoked_at" not in columns:
        with op.batch_alter_table("devices") as batch_op:
            batch_op.add_column(sa.Column("key_revoked_at", sa.DateTime(), nullable=True))
    if "device_requests" not in tables:
        op.create_table(
            "device_requests",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
            sa.Column("at", sa.DateTime(), nullable=False),
            sa.Column("method", sa.String(8), nullable=False),
            sa.Column("path", sa.String(200), nullable=False),
            sa.Column("status", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(60), nullable=True),
            sa.Column("address", sa.String(45), nullable=True),
        )
    if "ix_device_requests_device_id" not in indexes:
        op.create_index("ix_device_requests_device_id", "device_requests", ["device_id"])


def downgrade():
    tables, _indexes, columns = _have()
    if "device_requests" in tables:
        op.drop_table("device_requests")
    for column in ("key_tail", "key_revoked_at"):
        if column in columns:
            with op.batch_alter_table("devices") as batch_op:
                batch_op.drop_column(column)
