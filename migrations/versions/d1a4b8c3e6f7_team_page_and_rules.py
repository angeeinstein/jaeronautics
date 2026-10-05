"""A team's own page: a longer text, a picture, and rules to accept

Revision ID: d1a4b8c3e6f7
Revises: c9f3a6b7d2e5
Create Date: 2026-10-04

"""
from alembic import op
import sqlalchemy as sa


revision = "d1a4b8c3e6f7"
down_revision = "c9f3a6b7d2e5"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("teams") as batch_op:
        batch_op.add_column(sa.Column("about", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("picture_path", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("picture_token", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("terms_text", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("terms_updated_at", sa.DateTime(), nullable=True))
        batch_op.create_unique_constraint("uq_teams_picture_token", ["picture_token"])

    with op.batch_alter_table("team_memberships") as batch_op:
        batch_op.add_column(sa.Column("terms_accepted_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("terms_version", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("team_memberships") as batch_op:
        batch_op.drop_column("terms_version")
        batch_op.drop_column("terms_accepted_at")

    with op.batch_alter_table("teams") as batch_op:
        batch_op.drop_constraint("uq_teams_picture_token", type_="unique")
        batch_op.drop_column("terms_updated_at")
        batch_op.drop_column("terms_text")
        batch_op.drop_column("picture_token")
        batch_op.drop_column("picture_path")
        batch_op.drop_column("about")
