"""An admin email's addresses it has reached

Each person's admin digest holds only the kinds whose permission they hold
(docs/permissions-plan.md, "Emails"), so one event can go out in several
digests. ``delivered_to`` keeps the addresses it reached: it counts as sent
once everybody it is for has it, and a digest that failed is sent again to its
own recipients only.

Safe to run again.

Revision ID: f1c8a3d6b9e2
Revises: e4b7c2a9d1f6
Create Date: 2026-11-20

"""
from alembic import op
import sqlalchemy as sa


revision = "f1c8a3d6b9e2"
down_revision = "e4b7c2a9d1f6"
branch_labels = None
depends_on = None

TABLE = "notification_events"


def upgrade():
    have = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(TABLE)}
    if "delivered_to" not in have:
        with op.batch_alter_table(TABLE) as batch:
            batch.add_column(sa.Column("delivered_to", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_column("delivered_to")
