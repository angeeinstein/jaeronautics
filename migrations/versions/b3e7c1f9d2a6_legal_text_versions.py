"""Legal texts uploaded in the portal

``legal_text_versions``: drafts and published versions of the association's
texts and the teams' rules, uploaded under Admin > Legal Texts (and by a
team's leads, for their rules) instead of added to legal/ in the repository
(docs/legal-texts-in-portal-plan.md). The files in legal/ stay as they are.

Safe to run again after it stopped part-way.

Revision ID: b3e7c1f9d2a6
Revises: a8d3f5b2c9e1
Create Date: 2026-11-04

"""
from alembic import op
import sqlalchemy as sa


revision = "b3e7c1f9d2a6"
down_revision = "a8d3f5b2c9e1"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "legal_text_versions" in set(inspector.get_table_names()):
        return
    op.create_table(
        "legal_text_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document", sa.String(40), nullable=False),
        sa.Column("team_slug", sa.String(64), nullable=True),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("translation_of_id", sa.Integer(),
                  sa.ForeignKey("legal_text_versions.id", ondelete="CASCADE",
                                name="fk_legal_text_versions_translation_of"),
                  nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("version", sa.Date(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("revision", sa.String(100), nullable=True),
        sa.Column("body", sa.Text(length=4_000_000), nullable=False),
        sa.Column("approved_by", sa.String(100), nullable=True),
        sa.Column("approved_on", sa.Date(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_legal_text_versions_uploaded_by"),
                  nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("published_by_user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_legal_text_versions_published_by"),
                  nullable=True),
        sa.Column("corrected_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_legal_text_versions_text", "legal_text_versions", ["document", "team_slug", "language"])


def downgrade():
    op.drop_index("ix_legal_text_versions_text", table_name="legal_text_versions")
    op.drop_table("legal_text_versions")
