"""The legal texts' editor: one person edits a draft at a time

``legal_text_versions.lock_user_id``, ``lock_since``, ``lock_seen_at`` and
``lock_typed_at``: who has a draft open in the editor, since when, the
editor's last sign of life and the last typing (docs/legal-texts-in-portal-plan.md,
"The editor").

Safe to run again after it stopped part-way.

Revision ID: d7b2e5a9c4f1
Revises: c4f8d2a7e1b3
Create Date: 2026-11-05

"""
from alembic import op
import sqlalchemy as sa


revision = "d7b2e5a9c4f1"
down_revision = "c4f8d2a7e1b3"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("legal_text_versions")}
    with op.batch_alter_table("legal_text_versions") as batch:
        if "lock_user_id" not in columns:
            batch.add_column(sa.Column("lock_user_id", sa.Integer(), nullable=True))
            batch.create_foreign_key("fk_legal_text_versions_lock_user", "users", ["lock_user_id"], ["id"],
                                     ondelete="SET NULL")
        for name in ("lock_since", "lock_seen_at", "lock_typed_at"):
            if name not in columns:
                batch.add_column(sa.Column(name, sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("legal_text_versions") as batch:
        batch.drop_constraint("fk_legal_text_versions_lock_user", type_="foreignkey")
        for name in ("lock_typed_at", "lock_seen_at", "lock_since", "lock_user_id"):
            batch.drop_column(name)
