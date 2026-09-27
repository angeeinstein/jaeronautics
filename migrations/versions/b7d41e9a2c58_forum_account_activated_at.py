"""Remember when a forum account was first activated

Discourse welcomes somebody the first time their account is activated, and
again every time it is activated after being switched off -- which is what
happens when a member changes their email address: the forum takes the new,
unconfirmed address and deactivates the account until it is confirmed. The
member got the "welcome to the forum" message a second time for changing an
address. With this the portal knows the forum has already welcomed somebody,
and asks it not to again.

Filled in for accounts that have already been synced with a confirmed
address: those were activated, and welcomed, then.

Revision ID: b7d41e9a2c58
Revises: a4e2c81b9f37
Create Date: 2026-09-27

"""
from alembic import op
import sqlalchemy as sa


revision = "b7d41e9a2c58"
down_revision = "a4e2c81b9f37"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("forum_accounts") as batch_op:
        batch_op.add_column(sa.Column("activated_at", sa.DateTime(), nullable=True))
    op.execute(
        """
        UPDATE forum_accounts
        SET activated_at = last_synced_at
        WHERE last_synced_at IS NOT NULL
          AND user_id IN (SELECT id FROM users WHERE email_verified_at IS NOT NULL)
        """
    )


def downgrade():
    with op.batch_alter_table("forum_accounts") as batch_op:
        batch_op.drop_column("activated_at")
