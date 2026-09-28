"""Verify the university or company email address

The address was collected and never used for anything -- not login, not mail,
not a check. It now gets its own confirmation link, which is what makes it
evidence rather than a typed claim: for a student it is the thing that says
they study here now, and for somebody who was on the old forum it is the
address that archive registered them under, so confirming it is what
reconnects the two.

Existing rows are left unverified rather than assumed good. Nobody has ever
proved one of these addresses, so marking them verified would be inventing
evidence -- and the forum claim is built on that evidence meaning something.

Revision ID: d18c4a7f3b95
Revises: c47a9e1b5d82
Create Date: 2026-09-20

"""
from alembic import op
import sqlalchemy as sa


revision = "d18c4a7f3b95"
down_revision = "c47a9e1b5d82"
branch_labels = None
depends_on = None

COLUMNS = (
    ("email_work_verified_at", sa.DateTime()),
    ("email_work_verification_nonce", sa.String(length=255)),
)


def upgrade():
    with op.batch_alter_table("member") as batch_op:
        for name, column_type in COLUMNS:
            batch_op.add_column(sa.Column(name, column_type, nullable=True))


def downgrade():
    """Drops the confirmations, which cannot be reconstructed.

    Losing them is not destructive in the way losing a category would be --
    members simply become unconfirmed again and can be sent a fresh link -- so
    unlike the other two this one does not refuse.
    """
    with op.batch_alter_table("member") as batch_op:
        for name, _column_type in reversed(COLUMNS):
            batch_op.drop_column(name)
