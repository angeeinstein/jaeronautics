"""Stripe's books in the portal; differences with them; the fee setting for all payments

A copy of Stripe's balance transactions, what was fetched when, and what the
check found to differ. ``payments.team_bears_fee`` goes: whether a team
carries Stripe's fee is the money setting as it is now, for every payment.
The share the association could keep of team credit sales goes too: each
sale now carries its exact share of the top-up's fee instead (sales that kept
a share keep ``kept_cents``). See docs/money-ledger-plan.md.

Safe to run again after it stopped part-way: MariaDB commits each table
change at once, so a failure late in it (as the first run on the live server
did, at the setting row) leaves the earlier steps done.

Revision ID: e2b7c4f9a1d3
Revises: d7a3e9c2f5b1
Create Date: 2026-10-11

"""
from alembic import op
import sqlalchemy as sa


revision = "e2b7c4f9a1d3"
down_revision = "d7a3e9c2f5b1"
branch_labels = None
depends_on = None


def _have():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    indexes = {index["name"] for table in tables for index in inspector.get_indexes(table)}
    payments = {column["name"] for column in inspector.get_columns("payments")}
    return tables, indexes, payments


def upgrade():
    tables, indexes, payments = _have()
    if "stripe_bookings" not in tables:
        _create_bookings()
    for column in ("type", "booked_at", "charge_id", "payment_intent_id", "invoice_id", "product", "payment_id"):
        if f"ix_stripe_bookings_{column}" not in indexes:
            op.create_index(f"ix_stripe_bookings_{column}", "stripe_bookings", [column])
    if "stripe_books_state" not in tables:
        _create_state()
    if "money_differences" not in tables:
        _create_differences()
    if "ix_money_differences_resolved_at" not in indexes:
        op.create_index("ix_money_differences_resolved_at", "money_differences", ["resolved_at"])

    if "team_bears_fee" in payments:
        with op.batch_alter_table("payments") as batch_op:
            batch_op.drop_column("team_bears_fee")
    # ``key`` is a reserved word in MariaDB: built with SQLAlchemy so it is quoted.
    setting = sa.table("setting", sa.column("key", sa.String))
    op.execute(setting.delete().where(setting.c.key == "team_credit_sales_share_bps"))


def _create_bookings():
    op.create_table(
        "stripe_bookings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stripe_id", sa.String(100), nullable=False, unique=True),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("fee_cents", sa.Integer(), nullable=False),
        sa.Column("net_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("booked_at", sa.DateTime(), nullable=False),
        sa.Column("charge_id", sa.String(100), nullable=True),
        sa.Column("payment_intent_id", sa.String(100), nullable=True),
        sa.Column("invoice_id", sa.String(100), nullable=True),
        sa.Column("product", sa.String(20), nullable=True),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id"), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
    )


def _create_state():
    op.create_table(
        "stripe_books_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fetched_through", sa.DateTime(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=True),
        sa.Column("stripe_balance_cents", sa.Integer(), nullable=True),
        sa.Column("books_balance_cents", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
    )


def _create_differences():
    op.create_table(
        "money_differences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(200), nullable=False, unique=True),
        sa.Column("product", sa.String(20), nullable=False),
        sa.Column("what", sa.String(200), nullable=False),
        sa.Column("reference", sa.String(100), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=True),
        sa.Column("stripe_amount_cents", sa.Integer(), nullable=True),
        sa.Column("who", sa.String(255), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("emailed_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    with op.batch_alter_table("payments") as batch_op:
        batch_op.add_column(sa.Column("team_bears_fee", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.drop_index("ix_money_differences_resolved_at", table_name="money_differences")
    op.drop_table("money_differences")
    op.drop_table("stripe_books_state")
    for column in ("type", "booked_at", "charge_id", "payment_intent_id", "invoice_id", "product", "payment_id"):
        op.drop_index(f"ix_stripe_bookings_{column}", table_name="stripe_bookings")
    op.drop_table("stripe_bookings")
