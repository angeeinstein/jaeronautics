"""Switching an account off, separately from the membership

The two answer different questions and are allowed to disagree. A member paid
up for the year can be barred from signing in, and an account in good standing
can sit here with no membership yet or a lapsed one. Until now the only way to
stop somebody was to erase them, which is irreversible and destroys the record
you would want to keep if you were suspending rather than deleting.

Reversible, which is what separates it from deleted_at: the record is
untouched and only access stops.

Nobody is disabled by this migration. It adds the ability, not a decision.

Revision ID: e29d5b81c467
Revises: d18c4a7f3b95
Create Date: 2026-09-20

"""
from alembic import op
import sqlalchemy as sa


revision = "e29d5b81c467"
down_revision = "d18c4a7f3b95"
branch_labels = None
depends_on = None

COLUMNS = (
    ("disabled_at", sa.DateTime()),
    ("disabled_reason", sa.String(length=255)),
    ("disabled_by_user_id", sa.Integer()),
)


def upgrade():
    with op.batch_alter_table("users") as batch_op:
        for name, column_type in COLUMNS:
            batch_op.add_column(sa.Column(name, column_type, nullable=True))
        batch_op.create_foreign_key(
            "fk_users_disabled_by_user_id", "users", ["disabled_by_user_id"], ["id"]
        )


def downgrade():
    """Refuses while anybody is switched off, because nothing else records it.

    Dropping the columns would silently let every disabled account sign in
    again -- the opposite of what somebody asked for, and invisible until it
    had already happened.
    """
    connection = op.get_bind()
    remaining = connection.execute(
        sa.text("SELECT COUNT(*) FROM users WHERE disabled_at IS NOT NULL")
    ).scalar()
    if remaining:
        raise RuntimeError(
            f"{remaining} account(s) are switched off. Dropping these columns would "
            f"let them all sign in again. Re-enable them deliberately first if this "
            f"has to be reversed."
        )

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("fk_users_disabled_by_user_id", type_="foreignkey")
        for name, _column_type in reversed(COLUMNS):
            batch_op.drop_column(name)
