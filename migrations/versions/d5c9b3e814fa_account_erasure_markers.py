"""account erasure markers

Adds ``users.deleted_at`` and ``member.deleted_at``.

Erasing someone's data cannot mean deleting their rows. The membership periods
are an accounting record Austrian law requires the association to keep for
seven years, and the audit trail references both tables -- deleting a user who
was ever an administrator would take the record of everything they did with it.
So the personal columns are overwritten and these markers say the row no longer
describes a person.

Nothing is backfilled: no account has been erased before this migration runs,
so every existing row is correctly left NULL.

Revision ID: d5c9b3e814fa
Revises: c3a81f6d2e47
Create Date: 2026-09-18 21:30:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'd5c9b3e814fa'
down_revision = 'c3a81f6d2e47'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('deleted_at', sa.DateTime(), nullable=True))
    with op.batch_alter_table('member') as batch_op:
        batch_op.add_column(sa.Column('deleted_at', sa.DateTime(), nullable=True))


def downgrade():
    # Dropping these columns does not restore anything: the personal data was
    # overwritten in place, and reversing the migration only loses the record of
    # which rows that happened to.
    with op.batch_alter_table('member') as batch_op:
        batch_op.drop_column('deleted_at')
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('deleted_at')
