"""A machine-made English: which service made it, and why the first one did not

``translated_by``: the services that translated its paragraphs ("deepl",
"azure", or both). ``translation_note``: what the first service in the order
said when it failed and another stepped in -- before, only the server's log
had it, and it was guessed wrong which service had translated.

Safe to run again after it stopped part-way.

Revision ID: c5f2a8d1e7b3
Revises: a9d4b2e7c1f5
Create Date: 2026-11-19

"""
from alembic import op
import sqlalchemy as sa


revision = "c5f2a8d1e7b3"
down_revision = "a9d4b2e7c1f5"
branch_labels = None
depends_on = None

TABLE = "legal_text_versions"


def upgrade():
    have = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(TABLE)}
    with op.batch_alter_table(TABLE) as batch:
        if "translated_by" not in have:
            batch.add_column(sa.Column("translated_by", sa.String(40), nullable=True))
        if "translation_note" not in have:
            batch.add_column(sa.Column("translation_note", sa.String(500), nullable=True))


def downgrade():
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_column("translation_note")
        batch.drop_column("translated_by")
