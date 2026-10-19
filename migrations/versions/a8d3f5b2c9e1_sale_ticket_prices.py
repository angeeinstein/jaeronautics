"""The prices a sales point's menu showed, kept with its ticket

A tap buys at the price the member saw: one changed in the seconds between
menu and tap is refused ("price_changed") instead of charged. Tickets from
before have none and are bought as before. Safe to run again.

Revision ID: a8d3f5b2c9e1
Revises: f6c1d8e2a4b7
Create Date: 2026-10-19

"""
from alembic import op
import sqlalchemy as sa


revision = "a8d3f5b2c9e1"
down_revision = "f6c1d8e2a4b7"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if "prices" not in _columns("sale_tickets"):
        with op.batch_alter_table("sale_tickets") as batch:
            batch.add_column(sa.Column("prices", sa.JSON(), nullable=True))


def downgrade():
    if "prices" in _columns("sale_tickets"):
        with op.batch_alter_table("sale_tickets") as batch:
            batch.drop_column("prices")
