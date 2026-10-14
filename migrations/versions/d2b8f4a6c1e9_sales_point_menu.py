"""Sales points: a menu of items with icons, bought with a ticket

``credit_items.icon``: the picture an item has on a device's screen (every
item there is gets ``other``); ``sale_tickets``: a card tapped on a sales
point, which may buy one item from its menu for a few seconds. A sales
point's ``item`` setting becomes ``items``, a list (docs/devices-plan.md,
"The screens").

Safe to run again after it stopped part-way.

Revision ID: d2b8f4a6c1e9
Revises: a4c9e2f7b1d8
Create Date: 2026-10-14

"""
import json

from alembic import op
import sqlalchemy as sa


revision = "d2b8f4a6c1e9"
down_revision = "a4c9e2f7b1d8"
branch_labels = None
depends_on = None


def _have():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    indexes = {index["name"] for table in tables for index in inspector.get_indexes(table)}
    columns = {column["name"] for column in inspector.get_columns("credit_items")}
    return tables, indexes, columns


def _settings(convert):
    """Each sales point's settings, through ``convert``."""
    bind = op.get_bind()
    devices = sa.table("devices", sa.column("id", sa.Integer), sa.column("type", sa.String),
                       sa.column("settings", sa.JSON))
    for row in bind.execute(sa.select(devices.c.id, devices.c.settings).where(devices.c.type == "sales_point")):
        settings = row.settings if isinstance(row.settings, dict) else json.loads(row.settings or "{}")
        changed = convert(dict(settings))
        if changed != settings:
            bind.execute(sa.update(devices).where(devices.c.id == row.id).values(settings=changed))


def _to_items(settings):
    if "items" not in settings:
        item = settings.pop("item", None)
        settings["items"] = [item] if item else []
    settings.pop("item", None)
    return settings


def _to_item(settings):
    items = settings.pop("items", None) or []
    settings["item"] = items[0] if items else None
    return settings


def upgrade():
    tables, indexes, columns = _have()
    if "icon" not in columns:
        with op.batch_alter_table("credit_items") as batch_op:
            batch_op.add_column(sa.Column("icon", sa.String(30), nullable=False, server_default="other"))
    if "sale_tickets" not in tables:
        op.create_table(
            "sale_tickets",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("token", sa.String(40), nullable=False),
            sa.Column("device_id", sa.Integer(),
                      sa.ForeignKey("devices.id", ondelete="CASCADE", name="fk_sale_tickets_device_id"),
                      nullable=False),
            sa.Column("card_id", sa.Integer(),
                      sa.ForeignKey("cards.id", ondelete="CASCADE", name="fk_sale_tickets_card_id"), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("used_at", sa.DateTime(), nullable=True),
            sa.Column("entry_id", sa.Integer(),
                      sa.ForeignKey("credit_entries.id", ondelete="SET NULL", name="fk_sale_tickets_entry_id"),
                      nullable=True),
            sa.UniqueConstraint("token", name="uq_sale_tickets_token"),
        )
    tables, indexes, columns = _have()
    if "ix_sale_tickets_device_id" not in indexes:
        op.create_index("ix_sale_tickets_device_id", "sale_tickets", ["device_id"])
    _settings(_to_items)


def downgrade():
    _settings(_to_item)
    tables, indexes, columns = _have()
    if "sale_tickets" in tables:
        op.drop_table("sale_tickets")
    if "icon" in columns:
        with op.batch_alter_table("credit_items") as batch_op:
            batch_op.drop_column("icon")
