"""Cards are linked only with a reader's QR code

``card_link_waits`` -- an admin waiting at a reader for the next unknown
card -- is dropped: linking a card is the reader's QR code now, and the
admin's ways went with Web NFC (docs/cards-plan.md, "Linking a card (the
first version, removed 2026-10-14)"). A wait lasted a minute, so nothing
worth keeping is lost.

Safe to run again after it stopped part-way.

Revision ID: b8f2d6a4c1e7
Revises: e5a1c7d3b9f2
Create Date: 2026-10-14

"""
from alembic import op
import sqlalchemy as sa


revision = "b8f2d6a4c1e7"
down_revision = "e5a1c7d3b9f2"
branch_labels = None
depends_on = None


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade():
    if "card_link_waits" in _tables():
        op.drop_table("card_link_waits")


def downgrade():
    if "card_link_waits" not in _tables():
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
