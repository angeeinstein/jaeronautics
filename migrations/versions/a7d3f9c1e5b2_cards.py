"""Cards linked to people, and sales at card readers

``cards`` holds each card linked to a person (only an HMAC of its ID, and
its last characters); ``card_link_waits`` an admin waiting at a reader for
the next unknown card; ``credit_entries.card_id`` and ``device_id`` the card
and reader a sale was made with (docs/cards-plan.md).

Safe to run again after it stopped part-way.

Revision ID: a7d3f9c1e5b2
Revises: e7b2d5a8c3f0
Create Date: 2026-10-13

"""
from alembic import op
import sqlalchemy as sa


revision = "a7d3f9c1e5b2"
down_revision = "e7b2d5a8c3f0"
branch_labels = None
depends_on = None


def _have():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    indexes = {index["name"] for table in tables for index in inspector.get_indexes(table)}
    columns = {column["name"] for column in inspector.get_columns("credit_entries")}
    return tables, indexes, columns


def upgrade():
    tables, indexes, columns = _have()
    if "cards" not in tables:
        op.create_table(
            "cards",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("name", sa.String(40), nullable=False),
            sa.Column("id_hash", sa.String(64), nullable=True),
            sa.Column("linked_hash", sa.String(64), nullable=True, unique=True),
            sa.Column("id_tail", sa.String(8), nullable=False),
            sa.Column("linked_how", sa.String(20), nullable=False),
            sa.Column("linked_at", sa.DateTime(), nullable=False),
            sa.Column("linked_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("disabled_at", sa.DateTime(), nullable=True),
            sa.Column("blocked_at", sa.DateTime(), nullable=True),
            sa.Column("blocked_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("removed_at", sa.DateTime(), nullable=True),
            sa.Column("removed_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.Column("last_used_device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="SET NULL"),
                      nullable=True),
        )
    if "ix_cards_user_id" not in indexes:
        op.create_index("ix_cards_user_id", "cards", ["user_id"])
    if "ix_cards_id_hash" not in indexes:
        op.create_index("ix_cards_id_hash", "cards", ["id_hash"])
    if "card_link_waits" not in tables:
        op.create_table(
            "card_link_waits",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False,
                      unique=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("requested_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
        )
    with op.batch_alter_table("credit_entries") as batch_op:
        if "card_id" not in columns:
            batch_op.add_column(sa.Column("card_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key("fk_credit_entries_card_id", "cards", ["card_id"], ["id"])
        if "device_id" not in columns:
            batch_op.add_column(sa.Column("device_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key("fk_credit_entries_device_id", "devices", ["device_id"], ["id"],
                                        ondelete="SET NULL")
    _tables, indexes, _columns = _have()
    if "ix_credit_entries_card_id" not in indexes:
        op.create_index("ix_credit_entries_card_id", "credit_entries", ["card_id"])
    if "ix_credit_entries_device_id" not in indexes:
        op.create_index("ix_credit_entries_device_id", "credit_entries", ["device_id"])


def downgrade():
    tables, indexes, columns = _have()
    # The foreign key first: MariaDB will not drop an index a foreign key needs.
    for column in ("card_id", "device_id"):
        if column not in columns:
            continue
        with op.batch_alter_table("credit_entries") as batch_op:
            batch_op.drop_constraint(f"fk_credit_entries_{column}", type_="foreignkey")
        if f"ix_credit_entries_{column}" in indexes:
            op.drop_index(f"ix_credit_entries_{column}", table_name="credit_entries")
        with op.batch_alter_table("credit_entries") as batch_op:
            batch_op.drop_column(column)
    for table in ("card_link_waits", "cards"):
        if table in tables:
            op.drop_table(table)
