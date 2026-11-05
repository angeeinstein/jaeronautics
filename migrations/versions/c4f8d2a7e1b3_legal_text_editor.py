"""The legal texts' editor

``legal_text_versions.edited_at`` (last saved in the editor),
``edit_count`` (counted up by every save, so that a second person's save over
it is refused) and ``imported_from`` (a draft file in
legal/ copied into the portal), and ``legal_text_snapshots``: a draft's
earlier states, for *Discard changes* and *History*
(docs/legal-texts-in-portal-plan.md, "The editor").

Safe to run again after it stopped part-way.

Revision ID: c4f8d2a7e1b3
Revises: b3e7c1f9d2a6
Create Date: 2026-11-04

"""
from alembic import op
import sqlalchemy as sa


revision = "c4f8d2a7e1b3"
down_revision = "b3e7c1f9d2a6"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("legal_text_versions")}
    with op.batch_alter_table("legal_text_versions") as batch:
        if "edited_at" not in columns:
            batch.add_column(sa.Column("edited_at", sa.DateTime(), nullable=True))
        if "edit_count" not in columns:
            batch.add_column(sa.Column("edit_count", sa.Integer(), nullable=False, server_default="0"))
        if "imported_from" not in columns:
            batch.add_column(sa.Column("imported_from", sa.String(255), nullable=True))
    if "legal_text_snapshots" not in set(inspector.get_table_names()):
        op.create_table(
            "legal_text_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("record_id", sa.Integer(),
                      sa.ForeignKey("legal_text_versions.id", ondelete="CASCADE",
                                    name="fk_legal_text_snapshots_record"),
                      nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_user_id", sa.Integer(),
                      sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_legal_text_snapshots_created_by"),
                      nullable=True),
            sa.Column("content", sa.JSON(), nullable=False),
        )
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("legal_text_snapshots")}
    if "ix_legal_text_snapshots_record_id" not in indexes:
        op.create_index("ix_legal_text_snapshots_record_id", "legal_text_snapshots", ["record_id"])


def downgrade():
    op.drop_index("ix_legal_text_snapshots_record_id", table_name="legal_text_snapshots")
    op.drop_table("legal_text_snapshots")
    with op.batch_alter_table("legal_text_versions") as batch:
        batch.drop_column("imported_from")
        batch.drop_column("edit_count")
        batch.drop_column("edited_at")
