"""A device says how many outputs it has

``devices.outputs``: what its firmware reports with ``X-Device-Outputs`` --
relays, a lock -- so its owner can have a sale switch one (docs/todo.md,
"Sales points: switch outputs after a sale"). None until it says.

Safe to run again after it stopped part-way.

Revision ID: d7a3f1c9e5b8
Revises: c3e9a7f5b2d1
Create Date: 2026-10-15

"""
from alembic import op
import sqlalchemy as sa


revision = "d7a3f1c9e5b8"
down_revision = "c3e9a7f5b2d1"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if "outputs" not in _columns("devices"):
        with op.batch_alter_table("devices") as batch_op:
            batch_op.add_column(sa.Column("outputs", sa.Integer(), nullable=True))


def downgrade():
    if "outputs" in _columns("devices"):
        with op.batch_alter_table("devices") as batch_op:
            batch_op.drop_column("outputs")
