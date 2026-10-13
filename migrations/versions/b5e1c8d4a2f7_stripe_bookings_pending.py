"""Stripe's books: a debit not cleared yet

``stripe_bookings.pending``: the payment of a booking is still being debited
-- a SEPA debit Stripe books when it is announced, days before it clears.
The check against the portal's records waits for it (docs/money-ledger-plan.md).

Payments of the last 14 days without the portal's record are marked pending
here; the next fetch asks Stripe about each and clears what has cleared.

Safe to run again after it stopped part-way.

Revision ID: b5e1c8d4a2f7
Revises: e7b2d5a8c3f0
Create Date: 2026-10-13

"""
from datetime import datetime, timedelta, timezone

from alembic import op
import sqlalchemy as sa


revision = "b5e1c8d4a2f7"
down_revision = "e7b2d5a8c3f0"
branch_labels = None
depends_on = None


def _columns():
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("stripe_bookings")}


def upgrade():
    if "pending" not in _columns():
        with op.batch_alter_table("stripe_bookings") as batch_op:
            batch_op.add_column(sa.Column("pending", sa.Boolean(), nullable=False, server_default=sa.false()))
    # A SEPA debit clears within 14 days: anything older has cleared or failed.
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=14)
    bookings = sa.table("stripe_bookings", sa.column("pending", sa.Boolean()), sa.column("type", sa.String()),
                        sa.column("charge_id", sa.String()), sa.column("payment_id", sa.Integer()),
                        sa.column("booked_at", sa.DateTime()))
    op.execute(bookings.update().where(
        bookings.c.type.in_(("charge", "payment")), bookings.c.charge_id.is_not(None),
        bookings.c.payment_id.is_(None), bookings.c.booked_at >= since,
    ).values(pending=True))


def downgrade():
    if "pending" in _columns():
        with op.batch_alter_table("stripe_bookings") as batch_op:
            batch_op.drop_column("pending")
