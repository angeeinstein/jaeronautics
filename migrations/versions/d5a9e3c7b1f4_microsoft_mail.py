"""Email through Microsoft 365: a second kind of mail account, the portal's
app registration, and replies that came back by email

A mail account is SMTP, as before, or a Microsoft 365 mailbox reached through
Microsoft Graph, which has no server, login or password; the app it signs in
with, and its certificate, are one row of their own. An answer from Admin ›
Messages keeps its email's Message-ID, and the sender's reply to it is kept
beside it (docs/microsoft-mail-plan.md). Safe to run again.

Revision ID: d5a9e3c7b1f4
Revises: c3f8a2d6e1b9
Create Date: 2026-10-16

"""
from alembic import op
import sqlalchemy as sa


revision = "d5a9e3c7b1f4"
down_revision = "c3f8a2d6e1b9"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"]: column for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    columns = _columns("mail_accounts")
    with op.batch_alter_table("mail_accounts") as batch:
        if "kind" not in columns:
            batch.add_column(sa.Column("kind", sa.String(length=20), nullable=False, server_default="smtp"))
        for name, type_ in (("host", sa.String(length=255)), ("port", sa.Integer()),
                            ("username", sa.String(length=255)), ("password", sa.String(length=255))):
            if not columns[name]["nullable"]:
                batch.alter_column(name, existing_type=type_, nullable=True)
        for name, type_ in (("inbox_subscription_id", sa.String(length=100)),
                            ("inbox_subscription_expires_at", sa.DateTime()),
                            ("inbox_client_state", sa.String(length=64)),
                            ("inbox_checked_at", sa.DateTime())):
            if name not in columns:
                batch.add_column(sa.Column(name, type_, nullable=True))

    replies = _columns("contact_replies")
    with op.batch_alter_table("contact_replies") as batch:
        if "incoming" not in replies:
            batch.add_column(sa.Column("incoming", sa.Boolean(), nullable=False, server_default=sa.false()))
        if "email_message_id" not in replies:
            batch.add_column(sa.Column("email_message_id", sa.String(length=255), nullable=True))
            batch.create_index("ix_contact_replies_email_message_id", ["email_message_id"])
        if "has_attachments" not in replies:
            batch.add_column(sa.Column("has_attachments", sa.Boolean(), nullable=False, server_default=sa.false()))

    if not sa.inspect(op.get_bind()).has_table("mail_microsoft_connection"):
        op.create_table(
            "mail_microsoft_connection",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.String(length=100), nullable=True),
            sa.Column("client_id", sa.String(length=64), nullable=True),
            sa.Column("private_key", sa.Text(), nullable=True),
            sa.Column("certificate", sa.Text(), nullable=True),
            sa.Column("thumbprint", sa.String(length=40), nullable=True),
            sa.Column("certificate_expires_at", sa.DateTime(), nullable=True),
            sa.Column("new_private_key", sa.Text(), nullable=True),
            sa.Column("new_certificate", sa.Text(), nullable=True),
            sa.Column("new_thumbprint", sa.String(length=40), nullable=True),
            sa.Column("new_certificate_expires_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )


def downgrade():
    if sa.inspect(op.get_bind()).has_table("mail_microsoft_connection"):
        op.drop_table("mail_microsoft_connection")
    replies = _columns("contact_replies")
    with op.batch_alter_table("contact_replies") as batch:
        if "email_message_id" in replies:
            batch.drop_index("ix_contact_replies_email_message_id")
        for name in ("incoming", "email_message_id", "has_attachments"):
            if name in replies:
                batch.drop_column(name)
    # Microsoft accounts have none of what an SMTP account needs.
    op.execute("DELETE FROM mail_accounts WHERE kind = 'microsoft'")
    columns = _columns("mail_accounts")
    with op.batch_alter_table("mail_accounts") as batch:
        for name in ("kind", "inbox_subscription_id", "inbox_subscription_expires_at", "inbox_client_state",
                     "inbox_checked_at"):
            if name in columns:
                batch.drop_column(name)
        for name, type_ in (("host", sa.String(length=255)), ("port", sa.Integer()),
                            ("username", sa.String(length=255)), ("password", sa.String(length=255))):
            batch.alter_column(name, existing_type=type_, nullable=False)
