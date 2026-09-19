"""Year group optional, because membership is open to non-students

Membership does not require being a student under the association's statutes,
and a non-student is a full member -- same rights, same fee. They simply have
no year group to give, so NULL now means "not a student".

No data changes: the column was NOT NULL until now, so every existing row
already carries a value and none of them becomes ambiguous. Every NULL from
here on is a deliberate statement about a person rather than a gap.

Revision ID: b93f6c27a1d4
Revises: a7d41e8b93c5
Create Date: 2026-09-19

"""
from alembic import op
import sqlalchemy as sa


revision = "b93f6c27a1d4"
down_revision = "a7d41e8b93c5"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("member") as batch_op:
        batch_op.alter_column(
            "year_group", existing_type=sa.String(length=50), nullable=True
        )
    with op.batch_alter_table("member_profile_change_requests") as batch_op:
        batch_op.alter_column(
            "requested_year_group", existing_type=sa.String(length=50), nullable=True
        )


def downgrade():
    """Refuses rather than destroying the distinction.

    Going back to NOT NULL needs a value for every non-student member, and
    there is no right one to invent: any placeholder would read as a year group
    and be indistinguishable from a real one afterwards. Give them a value by
    hand first if this genuinely has to be reversed.
    """
    connection = op.get_bind()
    for table, column in (
        ("member", "year_group"),
        ("member_profile_change_requests", "requested_year_group"),
    ):
        remaining = connection.execute(
            sa.text(f"SELECT COUNT(*) FROM {table} WHERE {column} IS NULL")  # noqa: S608
        ).scalar()
        if remaining:
            raise RuntimeError(
                f"{remaining} row(s) in {table} have no {column}. Set one on each "
                f"before downgrading; this migration will not invent a year group."
            )

    with op.batch_alter_table("member") as batch_op:
        batch_op.alter_column(
            "year_group", existing_type=sa.String(length=50), nullable=False
        )
    with op.batch_alter_table("member_profile_change_requests") as batch_op:
        batch_op.alter_column(
            "requested_year_group", existing_type=sa.String(length=50), nullable=False
        )
