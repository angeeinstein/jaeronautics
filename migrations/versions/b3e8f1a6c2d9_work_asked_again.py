"""Outbox: a request for work already running makes it run once more

``external_work_items.asked_again`` is set when the same work is asked for
while it runs; finishing then puts it back in the queue instead of marking it
done, so the change that came in meanwhile is not lost.

Safe to run again after it stopped part-way.

Revision ID: b3e8f1a6c2d9
Revises: e2b7c4f9a1d3
Create Date: 2026-10-11

"""
from alembic import op
import sqlalchemy as sa


revision = "b3e8f1a6c2d9"
down_revision = "e2b7c4f9a1d3"
branch_labels = None
depends_on = None


def _columns():
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("external_work_items")}


def upgrade():
    if "asked_again" not in _columns():
        with op.batch_alter_table("external_work_items") as batch_op:
            batch_op.add_column(sa.Column("asked_again", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    if "asked_again" in _columns():
        with op.batch_alter_table("external_work_items") as batch_op:
            batch_op.drop_column("asked_again")
