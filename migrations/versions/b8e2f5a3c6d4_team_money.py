"""A team's money: one-time payments, refunds, payouts and bank details

Payments can be one-time (found by their payment intent rather than an
invoice) and partly refunded; payouts record what the association transferred
to a team; a team has the bank account it is paid to.

Revision ID: b8e2f5a3c6d4
Revises: a7d1c4e9f2b3
Create Date: 2026-10-04

"""
from alembic import op
import sqlalchemy as sa


revision = "b8e2f5a3c6d4"
down_revision = "a7d1c4e9f2b3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("teams") as batch_op:
        batch_op.add_column(sa.Column("bank_account_holder", sa.String(length=70), nullable=True))
        batch_op.add_column(sa.Column("bank_iban", sa.String(length=34), nullable=True))
        batch_op.add_column(sa.Column("bank_bic", sa.String(length=11), nullable=True))

    with op.batch_alter_table("team_memberships") as batch_op:
        batch_op.add_column(sa.Column("renewal_notice_for", sa.Date(), nullable=True))

    with op.batch_alter_table("payments") as batch_op:
        batch_op.alter_column("stripe_invoice_id", existing_type=sa.String(length=100), nullable=True)
        batch_op.add_column(sa.Column("stripe_payment_intent_id", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("refunded_cents", sa.Integer(), nullable=False, server_default="0"))
        batch_op.create_unique_constraint("uq_payments_stripe_payment_intent_id", ["stripe_payment_intent_id"])

    op.create_table(
        "team_payouts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("reference", sa.String(length=140), nullable=True),
        sa.Column("account_holder", sa.String(length=70), nullable=True),
        sa.Column("iban", sa.String(length=34), nullable=True),
        sa.Column("recorded_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_team_payouts_team_id", "team_payouts", ["team_id"])


def downgrade():
    op.drop_index("ix_team_payouts_team_id", table_name="team_payouts")
    op.drop_table("team_payouts")

    with op.batch_alter_table("payments") as batch_op:
        batch_op.drop_constraint("uq_payments_stripe_payment_intent_id", type_="unique")
        batch_op.drop_column("refunded_cents")
        batch_op.drop_column("stripe_payment_intent_id")
        batch_op.alter_column("stripe_invoice_id", existing_type=sa.String(length=100), nullable=False)

    with op.batch_alter_table("team_memberships") as batch_op:
        batch_op.drop_column("renewal_notice_for")

    with op.batch_alter_table("teams") as batch_op:
        batch_op.drop_column("bank_bic")
        batch_op.drop_column("bank_iban")
        batch_op.drop_column("bank_account_holder")
