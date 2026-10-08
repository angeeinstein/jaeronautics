"""Every team has an access list

The admins' switch "has rooms that need an access list" went: every team can
keep its list, and nothing is sent until somebody sends it or sets the
automatic sending up with a receiving address. Existing teams are switched on;
their recipients, dates and automatic sending stay as they were, so a team
that had none still sends nothing.

Revision ID: e5b8d3f1a2c4
Revises: d4a7c2e9f1b3
Create Date: 2026-10-08

"""
from alembic import op
import sqlalchemy as sa


revision = "e5b8d3f1a2c4"
down_revision = "d4a7c2e9f1b3"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("UPDATE teams SET access_list_enabled = :on").bindparams(on=True))
    with op.batch_alter_table("teams") as batch_op:
        batch_op.alter_column("access_list_enabled", existing_type=sa.Boolean(), existing_nullable=False,
                              server_default=sa.true())


def downgrade():
    # Which teams had it off before is not known any more; they keep it on.
    with op.batch_alter_table("teams") as batch_op:
        batch_op.alter_column("access_list_enabled", existing_type=sa.Boolean(), existing_nullable=False,
                              server_default=sa.false())
