"""The legal texts' English translated by machine

``translations``: texts translated by machine, kept by the exact text they
were made from; ``legal_text_versions.machine_translated`` and
``source_hash``: an English version made by machine, and from which German
(docs/legal-translation-plan.md).

Safe to run again after it stopped part-way.

Revision ID: e3c8a1f6b9d2
Revises: d7b2e5a9c4f1
Create Date: 2026-11-05

"""
from alembic import op
import sqlalchemy as sa


revision = "e3c8a1f6b9d2"
down_revision = "d7b2e5a9c4f1"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "translations" not in set(inspector.get_table_names()):
        op.create_table(
            "translations",
            sa.Column("source_hash", sa.String(64), primary_key=True),
            sa.Column("source", sa.Text(length=4_000_000), nullable=False),
            sa.Column("target", sa.Text(length=4_000_000), nullable=False),
            sa.Column("service", sa.String(10), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    columns = {column["name"] for column in inspector.get_columns("legal_text_versions")}
    with op.batch_alter_table("legal_text_versions") as batch:
        if "machine_translated" not in columns:
            batch.add_column(sa.Column("machine_translated", sa.Boolean(), nullable=False, server_default=sa.false()))
        if "source_hash" not in columns:
            batch.add_column(sa.Column("source_hash", sa.String(64), nullable=True))


def downgrade():
    with op.batch_alter_table("legal_text_versions") as batch:
        batch.drop_column("source_hash")
        batch.drop_column("machine_translated")
    op.drop_table("translations")
