"""Answers to contact messages, written in the portal

``contact_replies``: an answer to a contact-form message, emailed to its
sender from the mail account chosen for replies (docs/messages-plan.md,
"Answering from the portal"). Deleted with its message.

Safe to run again after it stopped part-way.

Revision ID: f1d7b3a9c2e5
Revises: e8b4c2d6f1a3
Create Date: 2026-10-16

"""
from alembic import op
import sqlalchemy as sa


revision = "f1d7b3a9c2e5"
down_revision = "e8b4c2d6f1a3"
branch_labels = None
depends_on = None


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade():
    if "contact_replies" not in _tables():
        op.create_table(
            "contact_replies",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("message_id", sa.Integer(), sa.ForeignKey("contact_messages.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("author_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("account_key", sa.String(length=80), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("sent_at", sa.DateTime(), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error", sa.Text(), nullable=True),
        )
        op.create_index("ix_contact_replies_message_id", "contact_replies", ["message_id"])


def downgrade():
    if "contact_replies" in _tables():
        op.drop_table("contact_replies")
