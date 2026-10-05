"""Teams: switch the automatic access list on and off; no notice when joining

Whether a team's access list goes out by itself on its dates is now a switch
of its own, off until somebody turns it on. The notice for people joining is
gone: that a team's members are listed for access to its rooms needs no
announcing.

Revision ID: c91f4d2b6e85
Revises: b7e3c5a90d14
Create Date: 2026-10-03

"""
from alembic import op
import sqlalchemy as sa


revision = "c91f4d2b6e85"
down_revision = "b7e3c5a90d14"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("teams") as batch_op:
        batch_op.add_column(
            sa.Column("access_list_auto_send", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.drop_column("access_list_notice")


def downgrade():
    with op.batch_alter_table("teams") as batch_op:
        batch_op.add_column(sa.Column("access_list_notice", sa.String(length=255), nullable=True))
        batch_op.drop_column("access_list_auto_send")
