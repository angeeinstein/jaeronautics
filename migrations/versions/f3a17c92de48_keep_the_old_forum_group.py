"""Keep what group the old forum had somebody in

The converter exported it and the importer dropped it, so 230 people's status
and the seventy written reasons behind it reached people.json and went no
further. On that board "Banned" was not misconduct -- the ban log reads "non
active student", "Not active student/exchange semester", "Is now a Lecturer" --
it was how a member who stopped studying was deactivated, and it is the only
surviving record of who left and why.

History, not a decision. Nothing reads these columns to grant or refuse
anything; see the note on the model for why they are not users.disabled_at.

Revision ID: f3a17c92de48
Revises: e29d5b81c467
Create Date: 2026-09-20

"""
from alembic import op
import sqlalchemy as sa


revision = "f3a17c92de48"
down_revision = "e29d5b81c467"
branch_labels = None
depends_on = None

COLUMNS = (
    ("source_group", sa.String(length=64)),
    ("source_group_reason", sa.String(length=255)),
)


def upgrade():
    with op.batch_alter_table("imported_forum_profiles") as batch_op:
        for name, column_type in COLUMNS:
            batch_op.add_column(sa.Column(name, column_type, nullable=True))


def downgrade():
    """Safe to reverse: the import is idempotent and re-running restores these.

    Unlike the account and category columns, nothing here was decided in this
    system -- it was copied from a dump that still exists, and a second import
    run fills it in again.
    """
    with op.batch_alter_table("imported_forum_profiles") as batch_op:
        for name, _column_type in reversed(COLUMNS):
            batch_op.drop_column(name)
