"""A setting's value: text of any length

``setting.value`` held at most 255 characters; the translation glossary
(services/translation.py) is longer, so saving Settings › Translation failed
on MariaDB.

Safe to run again after it stopped part-way.

Revision ID: a9d4b2e7c1f5
Revises: e3c8a1f6b9d2
Create Date: 2026-11-05

"""
from alembic import op
import sqlalchemy as sa


revision = "a9d4b2e7c1f5"
down_revision = "e3c8a1f6b9d2"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    value = next(column for column in inspector.get_columns("setting") if column["name"] == "value")
    if isinstance(value["type"], sa.String) and getattr(value["type"], "length", None):
        with op.batch_alter_table("setting") as batch:
            batch.alter_column("value", existing_type=sa.String(255), type_=sa.Text(), existing_nullable=False)


def downgrade():
    with op.batch_alter_table("setting") as batch:
        batch.alter_column("value", existing_type=sa.Text(), type_=sa.String(255), existing_nullable=False)
