"""Teams that charge: a Stripe price and periods per team, a payment record

A team gets its recurring Stripe price and the days its periods start; a team
membership its subscription, how far it is paid and when it ends after
leaving. ``payments`` records every payment Stripe confirms for anything but
the membership.

Revision ID: f6c3d8a1b2e4
Revises: e5b2a7c4d9f1
Create Date: 2026-10-03

"""
from alembic import op
import sqlalchemy as sa


revision = "f6c3d8a1b2e4"
down_revision = "e5b2a7c4d9f1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("teams") as batch_op:
        batch_op.add_column(sa.Column("stripe_price_id", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("period_starts", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("fee_display", sa.String(length=100), nullable=True))

    with op.batch_alter_table("team_memberships") as batch_op:
        batch_op.add_column(sa.Column("stripe_subscription_id", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("stripe_checkout_session_id", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("paid_until", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("payment_state", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("ends_on", sa.Date(), nullable=True))
        batch_op.create_index("ix_team_memberships_stripe_subscription_id", ["stripe_subscription_id"])

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("purpose", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("team_membership_id", sa.Integer(), sa.ForeignKey("team_memberships.id"), nullable=True),
        sa.Column("stripe_invoice_id", sa.String(length=100), nullable=False),
        sa.Column("stripe_subscription_id", sa.String(length=100), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("covers_until", sa.Date(), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("stripe_invoice_id", name="uq_payments_stripe_invoice_id"),
    )
    op.create_index("ix_payments_purpose", "payments", ["purpose"])
    op.create_index("ix_payments_user_id", "payments", ["user_id"])
    op.create_index("ix_payments_team_id", "payments", ["team_id"])
    op.create_index("ix_payments_team_membership_id", "payments", ["team_membership_id"])


def downgrade():
    op.drop_index("ix_payments_team_membership_id", table_name="payments")
    op.drop_index("ix_payments_team_id", table_name="payments")
    op.drop_index("ix_payments_user_id", table_name="payments")
    op.drop_index("ix_payments_purpose", table_name="payments")
    op.drop_table("payments")

    with op.batch_alter_table("team_memberships") as batch_op:
        batch_op.drop_index("ix_team_memberships_stripe_subscription_id")
        batch_op.drop_column("ends_on")
        batch_op.drop_column("payment_state")
        batch_op.drop_column("paid_until")
        batch_op.drop_column("stripe_checkout_session_id")
        batch_op.drop_column("stripe_subscription_id")

    with op.batch_alter_table("teams") as batch_op:
        batch_op.drop_column("fee_display")
        batch_op.drop_column("period_starts")
        batch_op.drop_column("stripe_price_id")
