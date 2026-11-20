"""The two email permissions go: an email follows the permission that shows the same

``messages.receive`` and ``notifications.receive`` are no permissions any more
(docs/permissions-plan.md, "Emails"): the contact form's messages are emailed to
whoever may answer them, an admin email to whoever holds the permission of its
page. An exception naming either is removed. Nothing else changes: roles are
defined in the code.

Safe to run again.

Revision ID: e4b7c2a9d1f6
Revises: d8a3f1c6b2e4
Create Date: 2026-11-20

"""
from alembic import op
import sqlalchemy as sa


revision = "e4b7c2a9d1f6"
down_revision = "d8a3f1c6b2e4"
branch_labels = None
depends_on = None

GONE = ("messages.receive", "notifications.receive")


def upgrade():
    if "user_permissions" not in sa.inspect(op.get_bind()).get_table_names():
        return
    user_permissions = sa.table("user_permissions", sa.column("permission", sa.String))
    op.execute(user_permissions.delete().where(user_permissions.c.permission.in_(GONE)))


def downgrade():
    # The removed exceptions are not brought back: they named permissions that no longer exist.
    pass
