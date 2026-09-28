"""Member categories: student, alumni, staff, partner, honorary

The statutes allow several kinds of member, and they differ in ways the year
group cannot express -- an alumnus has one, and so may a lecturer who studied
here. So the kind becomes a column of its own.

Everyone existing becomes a student, which is not a guess: year_group was NOT
NULL until the migration before this one, so every row that predates it is a
member who gave one. Admins can recategorise anyone who has since graduated.

No fee logic here. Every category pays the same annual fee today, and this
column is only what a future fee rule would key off.

Revision ID: c47a9e1b5d82
Revises: b93f6c27a1d4
Create Date: 2026-09-19

"""
from alembic import op
import sqlalchemy as sa


revision = "c47a9e1b5d82"
down_revision = "b93f6c27a1d4"
branch_labels = None
depends_on = None

DEFAULT_CATEGORY = "student"

TABLES = (
    ("member", "member_category"),
    ("member_profile_change_requests", "requested_member_category"),
)


def upgrade():
    for table, column in TABLES:
        # Added with a server default so the existing rows are filled as the
        # column appears -- there is no window where it is NOT NULL and empty.
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(
                sa.Column(
                    column,
                    sa.String(length=20),
                    nullable=False,
                    server_default=DEFAULT_CATEGORY,
                )
            )


def downgrade():
    """Drops the categories, which loses which members were not students.

    Nothing else can reconstruct it: a year group does not distinguish a
    student from an alumnus, and staff, partners and honorary members have
    none at all. Refuses while any non-student exists rather than discarding
    that quietly.
    """
    connection = op.get_bind()
    remaining = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM member WHERE member_category <> :default"
        ),
        {"default": DEFAULT_CATEGORY},
    ).scalar()
    if remaining:
        raise RuntimeError(
            f"{remaining} member(s) are not students. Dropping member_category "
            f"would lose which, and nothing else records it. Recategorise them "
            f"first if this genuinely has to be reversed."
        )

    for table, column in TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column(column)
