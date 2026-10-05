"""Teams: the list of current members for access to the team's rooms

Who receives it, on which days of the year, what people joining are told, and
when it last went out. All optional; a team without recipients sends nothing.

Revision ID: b7e3c5a90d14
Revises: a4d2e7c91b30
Create Date: 2026-10-03

"""
from alembic import op
import sqlalchemy as sa


revision = "b7e3c5a90d14"
down_revision = "a4d2e7c91b30"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("teams") as batch_op:
        batch_op.add_column(sa.Column("access_list_recipients", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("access_list_dates", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("access_list_notice", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("access_list_last_sent_on", sa.Date(), nullable=True))


def downgrade():
    with op.batch_alter_table("teams") as batch_op:
        batch_op.drop_column("access_list_last_sent_on")
        batch_op.drop_column("access_list_notice")
        batch_op.drop_column("access_list_dates")
        batch_op.drop_column("access_list_recipients")
