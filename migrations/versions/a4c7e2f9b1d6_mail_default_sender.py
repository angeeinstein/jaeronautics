"""One default mail account (Settings › Email, Senders)

Until now the welcome email's sender served as the default -- the account
emails (password reset, confirming an address) and, when nothing else was
chosen, the notifications went out from it -- and each other kind fell back
to another. Now there is a default account of its own, and each kind is the
default unless chosen otherwise (services/mail_senders.py).

The welcome email's sender becomes the default, and the welcome email then
follows the default: everything goes out from where it did. Only settings
change; no table. Safe to run again.

Revision ID: a4c7e2f9b1d6
Revises: f1d7b3a9c2e5
Create Date: 2026-10-16

"""
from alembic import op
import sqlalchemy as sa


revision = "a4c7e2f9b1d6"
down_revision = "f1d7b3a9c2e5"
branch_labels = None
depends_on = None

setting = sa.table("setting", sa.column("key", sa.String), sa.column("value", sa.Text))


def _value(key):
    return op.get_bind().execute(sa.select(setting.c.value).where(setting.c.key == key)).scalar()


def upgrade():
    welcome = _value("welcome_email_sender")
    if welcome and not _value("mail_default_sender"):
        op.execute(setting.insert().values(key="mail_default_sender", value=welcome))
    if welcome:
        op.execute(setting.delete().where(setting.c.key == "welcome_email_sender"))


def downgrade():
    default = _value("mail_default_sender")
    if default and not _value("welcome_email_sender"):
        op.execute(setting.insert().values(key="welcome_email_sender", value=default))
    op.execute(setting.delete().where(setting.c.key == "mail_default_sender"))
