"""Which versions of the legal texts a member accepted at signup

Revision ID: f4c8d2e6a9b3
Revises: e2b5c9d4f8a1
Create Date: 2026-10-04

"""
from alembic import op
import sqlalchemy as sa


revision = "f4c8d2e6a9b3"
down_revision = "e2b5c9d4f8a1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("member") as batch_op:
        batch_op.add_column(sa.Column("legal_versions_accepted", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("legal_accepted_at", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("member") as batch_op:
        batch_op.drop_column("legal_accepted_at")
        batch_op.drop_column("legal_versions_accepted")
