"""Stripe's books: a debit not cleared yet, where it was skipped

``stripe_bookings.pending`` comes with b5e1c8d4a2f7, which was merged while
the cards and devices migrations were still on their branch; they were put
after it then. A server that had already run them on that branch (the test
server) counts b5e1c8d4a2f7 as done without having run it, so its column is
added here. Everywhere else it is there already and nothing happens.

Safe to run again after it stopped part-way.

Revision ID: f1d6b3a9c8e5
Revises: c3f8a1d6e9b4
Create Date: 2026-10-13

"""
from datetime import datetime, timedelta, timezone

from alembic import op
import sqlalchemy as sa


revision = "f1d6b3a9c8e5"
down_revision = "c3f8a1d6e9b4"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("stripe_bookings")}
    if "pending" in columns:
        return
    with op.batch_alter_table("stripe_bookings") as batch_op:
        batch_op.add_column(sa.Column("pending", sa.Boolean(), nullable=False, server_default=sa.false()))
    # As b5e1c8d4a2f7: the last 14 days' payments without the portal's record are looked at again.
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=14)
    bookings = sa.table("stripe_bookings", sa.column("pending", sa.Boolean()), sa.column("type", sa.String()),
                        sa.column("charge_id", sa.String()), sa.column("payment_id", sa.Integer()),
                        sa.column("booked_at", sa.DateTime()))
    op.execute(bookings.update().where(
        bookings.c.type.in_(("charge", "payment")), bookings.c.charge_id.is_not(None),
        bookings.c.payment_id.is_(None), bookings.c.booked_at >= since,
    ).values(pending=True))


def downgrade():
    pass  # the column belongs to b5e1c8d4a2f7
