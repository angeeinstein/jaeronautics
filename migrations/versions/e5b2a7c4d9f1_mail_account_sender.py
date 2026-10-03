"""A mail account's sender, when it is not the login

Relays such as Brevo sign in with an account id of their own and send from a
verified address. Both columns are optional; empty, the login is the sender,
as it always was.

Revision ID: e5b2a7c4d9f1
Revises: d4a8e1f2c3b7
Create Date: 2026-10-03

"""
from alembic import op
import sqlalchemy as sa


revision = "e5b2a7c4d9f1"
down_revision = "d4a8e1f2c3b7"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("mail_accounts") as batch_op:
        batch_op.add_column(sa.Column("from_email", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("from_name", sa.String(length=120), nullable=True))


def downgrade():
    with op.batch_alter_table("mail_accounts") as batch_op:
        batch_op.drop_column("from_name")
        batch_op.drop_column("from_email")
