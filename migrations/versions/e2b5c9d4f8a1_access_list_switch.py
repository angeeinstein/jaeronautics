"""Whether a team has an access list at all

Not every team has rooms of its own. Teams that already have someone to send
the list to keep it switched on.

Revision ID: e2b5c9d4f8a1
Revises: d1a4b8c3e6f7
Create Date: 2026-10-04

"""
from alembic import op
import sqlalchemy as sa


revision = "e2b5c9d4f8a1"
down_revision = "d1a4b8c3e6f7"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("teams") as batch_op:
        batch_op.add_column(sa.Column("access_list_enabled", sa.Boolean(), nullable=False,
                                      server_default=sa.false()))
    teams = sa.table("teams", sa.column("access_list_enabled", sa.Boolean),
                     sa.column("access_list_recipients", sa.Text))
    op.execute(
        teams.update()
        .where(teams.c.access_list_recipients.isnot(None), teams.c.access_list_recipients != "")
        .values(access_list_enabled=True)
    )


def downgrade():
    with op.batch_alter_table("teams") as batch_op:
        batch_op.drop_column("access_list_enabled")
