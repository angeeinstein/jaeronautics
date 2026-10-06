"""Team rules only as files in legal/teams/: drop the text typed into the portal

Revision ID: a7d3e9f1c5b2
Revises: f4c8d2e6a9b3
Create Date: 2026-10-06

"""
from alembic import op
import sqlalchemy as sa


revision = "a7d3e9f1c5b2"
down_revision = "f4c8d2e6a9b3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("teams") as batch_op:
        batch_op.drop_column("terms_updated_at")
        batch_op.drop_column("terms_text")


def downgrade():
    with op.batch_alter_table("teams") as batch_op:
        batch_op.add_column(sa.Column("terms_text", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("terms_updated_at", sa.DateTime(), nullable=True))
