"""external work outbox

Adds ``external_work_items``, so a call to another system (currently Discourse
synchronisation) is recorded in the same transaction as the local change that
requires it, then carried out by a worker with retries.

Nothing is backfilled: the table describes work still to be done, and at upgrade
time there is none outstanding.

Revision ID: c3a81f6d2e47
Revises: b7e2d15a4c83
Create Date: 2026-09-18 13:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'c3a81f6d2e47'
down_revision = 'b7e2d15a4c83'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'external_work_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=60), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('member_id', sa.Integer(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('dedupe_key', sa.String(length=255), nullable=True),
        sa.Column('reason', sa.String(length=255), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('not_before', sa.DateTime(), nullable=True),
        sa.Column('claimed_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['member_id'], ['member.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('dedupe_key', name='uq_external_work_items_dedupe_key'),
    )
    op.create_index(op.f('ix_external_work_items_kind'), 'external_work_items', ['kind'], unique=False)
    op.create_index(op.f('ix_external_work_items_status'), 'external_work_items', ['status'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_external_work_items_status'), table_name='external_work_items')
    op.drop_index(op.f('ix_external_work_items_kind'), table_name='external_work_items')
    op.drop_table('external_work_items')
