"""membership period ledger

Adds ``membership_periods``: a record of which coverage window a member was
granted and on what grounds, so access has evidence behind it instead of only a
``payment_status`` field that several code paths write.

Existing members are backfilled with one period covering the dates they already
have, marked with the reason their current status implies. That backfilled row
carries no invoice id, which is accurate -- for members created before this table
existed, the original evidence was never recorded.

Revision ID: b7e2d15a4c83
Revises: a1f4c73b92d0
Create Date: 2026-09-18 12:00:00.000000

"""
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'b7e2d15a4c83'
down_revision = 'a1f4c73b92d0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'membership_periods',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('member_id', sa.Integer(), nullable=False),
        sa.Column('starts_on', sa.Date(), nullable=False),
        sa.Column('ends_on', sa.Date(), nullable=False),
        sa.Column('reason', sa.String(length=30), nullable=False),
        sa.Column('stripe_invoice_id', sa.String(length=255), nullable=True),
        sa.Column('stripe_subscription_id', sa.String(length=255), nullable=True),
        sa.Column('granted_by_user_id', sa.Integer(), nullable=True),
        sa.Column('note', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_reason', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['member_id'], ['member.id'], ),
        sa.ForeignKeyConstraint(['granted_by_user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stripe_invoice_id', name='uq_membership_periods_stripe_invoice_id'),
    )
    op.create_index(
        op.f('ix_membership_periods_member_id'), 'membership_periods', ['member_id'], unique=False
    )

    # Backfill one period per member that already has a coverage window. The
    # reason follows the status they currently hold; a member whose status no
    # longer grants access (expired, failed, dispute_lost) gets no period, which
    # is correct -- there is nothing to support.
    member = sa.table(
        'member',
        sa.column('id', sa.Integer),
        sa.column('payment_status', sa.String),
        sa.column('membership_starts_on', sa.Date),
        sa.column('membership_ends_on', sa.Date),
        sa.column('stripe_subscription_id', sa.String),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(
            member.c.id,
            member.c.payment_status,
            member.c.membership_starts_on,
            member.c.membership_ends_on,
            member.c.stripe_subscription_id,
        ).where(member.c.membership_ends_on.isnot(None))
    ).fetchall()

    now = datetime.now(timezone.utc)
    granted = []
    for row in rows:
        status = (row.payment_status or "").strip()
        if status == "free_period":
            reason = "free_period"
        elif status in {"paid", "canceled", "cancel_scheduled"}:
            reason = "paid"
        else:
            continue
        starts_on = row.membership_starts_on or row.membership_ends_on.replace(month=1, day=1)
        granted.append({
            "member_id": row.id,
            "starts_on": starts_on,
            "ends_on": row.membership_ends_on,
            "reason": reason,
            "stripe_invoice_id": None,
            "stripe_subscription_id": row.stripe_subscription_id,
            "granted_by_user_id": None,
            "note": "Backfilled from the member record when the coverage ledger was introduced.",
            "created_at": now,
            "revoked_at": None,
            "revoked_reason": None,
        })

    if granted:
        periods = sa.table(
            'membership_periods',
            sa.column('member_id', sa.Integer),
            sa.column('starts_on', sa.Date),
            sa.column('ends_on', sa.Date),
            sa.column('reason', sa.String),
            sa.column('stripe_invoice_id', sa.String),
            sa.column('stripe_subscription_id', sa.String),
            sa.column('granted_by_user_id', sa.Integer),
            sa.column('note', sa.String),
            sa.column('created_at', sa.DateTime),
            sa.column('revoked_at', sa.DateTime),
            sa.column('revoked_reason', sa.String),
        )
        connection.execute(periods.insert(), granted)


def downgrade():
    op.drop_index(op.f('ix_membership_periods_member_id'), table_name='membership_periods')
    op.drop_table('membership_periods')
