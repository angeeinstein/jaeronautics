"""Devices: card readers and other things that talk to the portal

``devices`` holds each device, its type and settings, and the hash of its
key; ``device_events`` what happened with it, kept 90 days. See
docs/devices-plan.md.

Safe to run again after it stopped part-way.

Revision ID: d4a9c7e2f1b6
Revises: b3e8f1a6c2d9
Create Date: 2026-10-12

"""
from alembic import op
import sqlalchemy as sa


revision = "d4a9c7e2f1b6"
down_revision = "b3e8f1a6c2d9"
branch_labels = None
depends_on = None


def _have():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    indexes = {index["name"] for table in tables for index in inspector.get_indexes(table)}
    return tables, indexes


def upgrade():
    tables, indexes = _have()
    if "devices" not in tables:
        op.create_table(
            "devices",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(80), nullable=False, unique=True),
            sa.Column("type", sa.String(40), nullable=False),
            sa.Column("place", sa.String(120), nullable=True),
            sa.Column("settings", sa.JSON(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("auth_method", sa.String(20), nullable=False, server_default="key"),
            sa.Column("key_id", sa.String(16), nullable=False, unique=True),
            sa.Column("key_hash", sa.String(64), nullable=False),
            sa.Column("key_created_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.Integer(),
                      sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("last_seen_at", sa.DateTime(), nullable=True),
            sa.Column("last_address", sa.String(45), nullable=True),
            sa.Column("firmware", sa.String(60), nullable=True),
        )
    if "device_events" not in tables:
        op.create_table(
            "device_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
            sa.Column("at", sa.DateTime(), nullable=False),
            sa.Column("kind", sa.String(30), nullable=False),
            sa.Column("name", sa.String(40), nullable=True),
            sa.Column("event_id", sa.String(64), nullable=True),
            sa.Column("detail", sa.String(255), nullable=True),
            sa.Column("data", sa.JSON(), nullable=True),
            sa.Column("answer", sa.JSON(), nullable=True),
            sa.UniqueConstraint("device_id", "event_id", name="uq_device_events_device_event"),
        )
    for column in ("device_id", "at"):
        if f"ix_device_events_{column}" not in indexes:
            op.create_index(f"ix_device_events_{column}", "device_events", [column])


def downgrade():
    tables, _indexes = _have()
    if "device_events" in tables:
        op.drop_table("device_events")
    if "devices" in tables:
        op.drop_table("devices")
