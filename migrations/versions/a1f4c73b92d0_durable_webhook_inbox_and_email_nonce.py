"""durable webhook inbox and email verification nonce

Adds:
* ``users.email_verification_nonce`` so a verification link can be bound to one
  address and revoked when that address changes.
* status/lease/attempt columns on ``processed_stripe_events`` so a webhook that
  was claimed but never finished (e.g. the process was killed) is retried on
  redelivery instead of being treated as already handled.

Existing rows predate the status column and were only ever written after a
handler ran to completion, so they are backfilled as ``completed``.

Revision ID: a1f4c73b92d0
Revises: ccc292820ec3
Create Date: 2026-09-18 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1f4c73b92d0'
down_revision = 'ccc292820ec3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('email_verification_nonce', sa.String(length=255), nullable=True))

    with op.batch_alter_table('processed_stripe_events') as batch_op:
        batch_op.add_column(sa.Column('status', sa.String(length=20), nullable=False, server_default='completed'))
        batch_op.add_column(sa.Column('attempts', sa.Integer(), nullable=False, server_default='1'))
        batch_op.add_column(sa.Column('claimed_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('last_error', sa.Text(), nullable=True))
        # Rows are only created once a handler has finished from now on, so the
        # completion timestamp becomes optional while an event is in flight.
        batch_op.alter_column('processed_at', existing_type=sa.DateTime(), nullable=True)

    # Drop the backfill defaults; the application sets these explicitly.
    with op.batch_alter_table('processed_stripe_events') as batch_op:
        batch_op.alter_column('status', existing_type=sa.String(length=20), server_default=None)
        batch_op.alter_column('attempts', existing_type=sa.Integer(), server_default=None)


def downgrade():
    with op.batch_alter_table('processed_stripe_events') as batch_op:
        batch_op.alter_column('processed_at', existing_type=sa.DateTime(), nullable=False)
        batch_op.drop_column('last_error')
        batch_op.drop_column('claimed_at')
        batch_op.drop_column('attempts')
        batch_op.drop_column('status')

    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('email_verification_nonce')
