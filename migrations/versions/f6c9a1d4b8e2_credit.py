"""Credit: a balance members top up

One row per person with credit (the balance, and the top-up open in
Checkout), and every change of it as an entry that is never changed
afterwards. See docs/credit-plan.md.

Revision ID: f6c9a1d4b8e2
Revises: e5b8d3f1a2c4
Create Date: 2026-10-08

"""
from alembic import op
import sqlalchemy as sa


revision = "f6c9a1d4b8e2"
down_revision = "e5b8d3f1a2c4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "credit_accounts",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("balance_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stripe_checkout_session_id", sa.String(length=255), nullable=True),
        sa.Column("checkout_amount_cents", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "credit_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("balance_after_cents", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=140), nullable=False),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id"), nullable=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("booked_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    with op.batch_alter_table("credit_entries") as batch_op:
        batch_op.create_index("ix_credit_entries_user_id", ["user_id"])
        batch_op.create_index("ix_credit_entries_kind", ["kind"])
        batch_op.create_index("ix_credit_entries_payment_id", ["payment_id"])
        batch_op.create_index("ix_credit_entries_team_id", ["team_id"])
        batch_op.create_index("ix_credit_entries_created_at", ["created_at"])


def downgrade():
    op.drop_table("credit_entries")
    op.drop_table("credit_accounts")
