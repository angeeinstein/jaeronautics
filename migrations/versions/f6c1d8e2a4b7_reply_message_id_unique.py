"""Each email's Message-ID once among the replies; joins the two lines

The notification and the 15-minute look could both take the same incoming
reply; the Message-ID is unique now, and a reply stored twice before keeps
its first copy. Also the point where the session generation
(e4b7c2a9d3f1) and Microsoft 365 email (d5a9e3c7b1f4), made side by side,
meet again. Safe to run again.

Revision ID: f6c1d8e2a4b7
Revises: e4b7c2a9d3f1, d5a9e3c7b1f4
Create Date: 2026-10-17

"""
from alembic import op
import sqlalchemy as sa


revision = "f6c1d8e2a4b7"
down_revision = ("e4b7c2a9d3f1", "d5a9e3c7b1f4")
branch_labels = None
depends_on = None

INDEX = "ix_contact_replies_email_message_id"


def _index():
    for index in sa.inspect(op.get_bind()).get_indexes("contact_replies"):
        if index["name"] == INDEX:
            return index
    return None


def upgrade():
    index = _index()
    if index is not None and index.get("unique"):
        return
    bind = op.get_bind()
    twice = bind.execute(sa.text(
        "SELECT email_message_id, MIN(id) FROM contact_replies WHERE email_message_id IS NOT NULL "
        "GROUP BY email_message_id HAVING COUNT(*) > 1")).fetchall()
    for message_id, first in twice:
        bind.execute(sa.text("DELETE FROM contact_replies WHERE email_message_id = :message_id AND id <> :first"),
                     {"message_id": message_id, "first": first})
    with op.batch_alter_table("contact_replies") as batch:
        if index is not None:
            batch.drop_index(INDEX)
        batch.create_index(INDEX, ["email_message_id"], unique=True)


def downgrade():
    index = _index()
    if index is None or not index.get("unique"):
        return
    with op.batch_alter_table("contact_replies") as batch:
        batch.drop_index(INDEX)
        batch.create_index(INDEX, ["email_message_id"])
