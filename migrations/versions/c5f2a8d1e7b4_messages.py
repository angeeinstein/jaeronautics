"""Messages: contact messages, mailings and their recipients; switching the news off

The contact form's messages and the messages to a team's leads; the
association's announcements and the teams' mailings, sent through a paced
queue with a row per recipient; and on every account whether its holder
switched the association's news off. See docs/messages-plan.md.

Revision ID: c5f2a8d1e7b4
Revises: b3e8f1a6c4d7
Create Date: 2026-10-09

"""
from alembic import op
import sqlalchemy as sa


revision = "c5f2a8d1e7b4"
down_revision = "b3e8f1a6c4d7"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("news_unsubscribed_at", sa.DateTime(), nullable=True))

    op.create_table(
        "contact_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("topic", sa.String(length=20), nullable=True),
        sa.Column("sender_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("sender_name", sa.String(length=120), nullable=False),
        sa.Column("sender_email", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=150), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivery_error", sa.Text(), nullable=True),
        sa.Column("done_at", sa.DateTime(), nullable=True),
        sa.Column("done_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )
    with op.batch_alter_table("contact_messages") as batch_op:
        batch_op.create_index("ix_contact_messages_team_id", ["team_id"])
        batch_op.create_index("ix_contact_messages_sender_user_id", ["sender_user_id"])
        batch_op.create_index("ix_contact_messages_created_at", ["created_at"])

    op.create_table(
        "mailings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("audience", sa.JSON(), nullable=False),
        sa.Column("subject", sa.String(length=150), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("assembly_at", sa.DateTime(), nullable=True),
        sa.Column("author_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reply_to", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("recipient_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unsubscribed_count", sa.Integer(), nullable=False, server_default="0"),
    )
    with op.batch_alter_table("mailings") as batch_op:
        batch_op.create_index("ix_mailings_team_id", ["team_id"])
        batch_op.create_index("ix_mailings_created_at", ["created_at"])

    op.create_table(
        "mailing_recipients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mailing_id", sa.Integer(), sa.ForeignKey("mailings.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.UniqueConstraint("mailing_id", "user_id", name="uq_mailing_recipient"),
    )
    with op.batch_alter_table("mailing_recipients") as batch_op:
        batch_op.create_index("ix_mailing_recipients_mailing_id", ["mailing_id"])
        batch_op.create_index("ix_mailing_recipients_user_id", ["user_id"])
        batch_op.create_index("ix_mailing_recipients_status", ["status"])
        batch_op.create_index("ix_mailing_recipients_sent_at", ["sent_at"])


def downgrade():
    op.drop_table("mailing_recipients")
    op.drop_table("mailings")
    op.drop_table("contact_messages")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("news_unsubscribed_at")
