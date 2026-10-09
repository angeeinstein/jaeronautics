"""Credit: price lists, sales that name what was sold; who carries Stripe's fees

The association's and each team's price list, and on every credit entry the
item a sale was for, how it was made, the association's share of a team's
sale, and -- for a sale taken back -- the sale it undoes. On every payment
Stripe's fee and whether the team's share carries it. See
docs/credit-plan.md, "Selling", and docs/maintenance.md, "Stripe's fees".

Revision ID: a7d2c5e8f9b1
Revises: f6c9a1d4b8e2
Create Date: 2026-10-09

"""
from alembic import op
import sqlalchemy as sa


revision = "a7d2c5e8f9b1"
down_revision = "f6c9a1d4b8e2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "credit_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    with op.batch_alter_table("credit_items") as batch_op:
        batch_op.create_index("ix_credit_items_team_id", ["team_id"])
    with op.batch_alter_table("credit_entries") as batch_op:
        batch_op.add_column(sa.Column("item_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("channel", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("reverses_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("kept_cents", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_credit_entries_item_id", "credit_items", ["item_id"], ["id"])
        batch_op.create_foreign_key("fk_credit_entries_reverses_id", "credit_entries", ["reverses_id"], ["id"])
        batch_op.create_index("ix_credit_entries_item_id", ["item_id"])
        batch_op.create_unique_constraint("uq_credit_entries_reverses_id", ["reverses_id"])
    with op.batch_alter_table("payments") as batch_op:
        batch_op.add_column(sa.Column("fee_cents", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("team_bears_fee", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("payments") as batch_op:
        batch_op.drop_column("team_bears_fee")
        batch_op.drop_column("fee_cents")
    with op.batch_alter_table("credit_entries") as batch_op:
        batch_op.drop_constraint("uq_credit_entries_reverses_id", type_="unique")
        batch_op.drop_index("ix_credit_entries_item_id")
        batch_op.drop_constraint("fk_credit_entries_reverses_id", type_="foreignkey")
        batch_op.drop_constraint("fk_credit_entries_item_id", type_="foreignkey")
        batch_op.drop_column("kept_cents")
        batch_op.drop_column("reverses_id")
        batch_op.drop_column("channel")
        batch_op.drop_column("item_id")
    op.drop_table("credit_items")
