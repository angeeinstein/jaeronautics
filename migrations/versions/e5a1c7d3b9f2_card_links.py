"""Cards linked at a reader with a QR code

``card_links``: an unknown card tapped on a reader, offered to be linked by
its owner's phone through a QR code for a few minutes (docs/cards-plan.md,
"Linking a card at a reader with a QR code").

Safe to run again after it stopped part-way.

Revision ID: e5a1c7d3b9f2
Revises: d2b8f4a6c1e9
Create Date: 2026-10-14

"""
from alembic import op
import sqlalchemy as sa


revision = "e5a1c7d3b9f2"
down_revision = "d2b8f4a6c1e9"
branch_labels = None
depends_on = None


def _have():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    indexes = {index["name"] for table in tables for index in inspector.get_indexes(table)}
    return tables, indexes


def upgrade():
    tables, indexes = _have()
    if "card_links" not in tables:
        op.create_table(
            "card_links",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("token", sa.String(40), nullable=False),
            sa.Column("device_id", sa.Integer(),
                      sa.ForeignKey("devices.id", ondelete="CASCADE", name="fk_card_links_device_id"),
                      nullable=False),
            sa.Column("card_hash", sa.String(64), nullable=False),
            sa.Column("card_tail", sa.String(8), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("opened_at", sa.DateTime(), nullable=True),
            sa.Column("claim", sa.String(40), nullable=True),
            sa.Column("ended_at", sa.DateTime(), nullable=True),
            sa.Column("linked_at", sa.DateTime(), nullable=True),
            sa.Column("card_id", sa.Integer(),
                      sa.ForeignKey("cards.id", ondelete="SET NULL", name="fk_card_links_card_id"), nullable=True),
            sa.UniqueConstraint("token", name="uq_card_links_token"),
        )
    tables, indexes = _have()
    if "ix_card_links_device_id" not in indexes:
        op.create_index("ix_card_links_device_id", "card_links", ["device_id"])
    if "ix_card_links_card_hash" not in indexes:
        op.create_index("ix_card_links_card_hash", "card_links", ["card_hash"])


def downgrade():
    tables, _indexes = _have()
    if "card_links" in tables:
        op.drop_table("card_links")
