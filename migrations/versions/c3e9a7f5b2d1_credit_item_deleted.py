"""A price list's item can be deleted; a device knows it was paired

``credit_items.deleted_at``: an item deleted from its price list keeps its
row, off every list and menu, so what was sold still names it and shows its
icon. ``devices.paired_at``: its key came from pairing, so its page shows no
key panel -- filled in from the device's own record of it (docs/todo.md).

Safe to run again after it stopped part-way.

Revision ID: c3e9a7f5b2d1
Revises: b8f2d6a4c1e7
Create Date: 2026-10-14

"""
from alembic import op
import sqlalchemy as sa


revision = "c3e9a7f5b2d1"
down_revision = "b8f2d6a4c1e7"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if "deleted_at" not in _columns("credit_items"):
        with op.batch_alter_table("credit_items") as batch_op:
            batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
    if "paired_at" not in _columns("devices"):
        with op.batch_alter_table("devices") as batch_op:
            batch_op.add_column(sa.Column("paired_at", sa.DateTime(), nullable=True))
        # A device paired before: its key's own record says so.
        op.execute(
            "UPDATE devices SET paired_at = key_created_at WHERE id IN ("
            "SELECT device_id FROM device_events WHERE kind = 'key' "
            "AND detail = 'Key given to the device at pairing')"
        )


def downgrade():
    if "paired_at" in _columns("devices"):
        with op.batch_alter_table("devices") as batch_op:
            batch_op.drop_column("paired_at")
    if "deleted_at" in _columns("credit_items"):
        with op.batch_alter_table("credit_items") as batch_op:
            batch_op.drop_column("deleted_at")
