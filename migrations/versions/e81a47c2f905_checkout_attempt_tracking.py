"""checkout attempt tracking

Adds ``member.stripe_checkout_session_id``.

Signup used to forget which Checkout session it had opened, so resuming an
abandoned payment created another one. Two open sessions for the same member can
both be completed, which produces two subscriptions. Remembering the session lets
the resume path send the member back to the one that is still open.

Nothing is backfilled: the column describes an attempt in flight, and any
session open when this migration runs is not worth chasing -- it expires within
a day and the member simply starts a new one.

Revision ID: e81a47c2f905
Revises: d5c9b3e814fa
Create Date: 2026-09-19 10:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'e81a47c2f905'
down_revision = 'd5c9b3e814fa'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('member') as batch_op:
        batch_op.add_column(sa.Column('stripe_checkout_session_id', sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table('member') as batch_op:
        batch_op.drop_column('stripe_checkout_session_id')
