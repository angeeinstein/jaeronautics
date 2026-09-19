"""Add the superadmin role and give it to everyone who is already an admin.

Until now ``admin`` carried everything, including installing a new version and
rolling one back. Splitting those out raises an obvious question: who holds the
new role the first time this runs, when by definition nobody does?

**Every current administrator does.** That is not an escalation -- it is the
absence of one. Those accounts can already press the update button; granting
them the role that now guards it leaves their authority exactly where it was.
Doing nothing would instead be a silent *demotion* that locks the installation
out of its own update mechanism, which is also how the fix for it would have
been delivered.

Fresh installations have no administrators when this runs, so nothing is
granted here and the bootstrap falls to ``create-admin`` (which install.sh
calls), which takes the superadmin role when no superadmin exists yet.
``grant-superadmin`` is the recovery path if the last one ever leaves.

Revision ID: f2b6a90c1d73
Revises: e81a47c2f905
Create Date: 2026-09-19 18:20:00.000000

"""
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'f2b6a90c1d73'
down_revision = 'e81a47c2f905'
branch_labels = None
depends_on = None

ROLE_SLUG = 'superadmin'
ROLE_LABEL = 'Super Admin'
ROLE_DESCRIPTION = 'Can install updates, manage credentials and grant administrator access.'


def upgrade():
    connection = op.get_bind()
    # A Python value, not sa.func.now(): a SQL function cannot be passed as a
    # bound parameter, and matching the models' utcnow() keeps these rows looking
    # like every other row rather than like the database's idea of the time.
    created_at = datetime.now(timezone.utc)

    # The application seeds roles on start-up too, so this must be safe to run
    # against a database where the row already exists.
    role_id = connection.execute(
        sa.text("SELECT id FROM roles WHERE slug = :slug"), {"slug": ROLE_SLUG}
    ).scalar()
    if role_id is None:
        connection.execute(
            sa.text(
                "INSERT INTO roles (slug, label, description, created_at) "
                "VALUES (:slug, :label, :description, :created_at)"
            ),
            {
                "slug": ROLE_SLUG,
                "label": ROLE_LABEL,
                "description": ROLE_DESCRIPTION,
                "created_at": created_at,
            },
        )
        role_id = connection.execute(
            sa.text("SELECT id FROM roles WHERE slug = :slug"), {"slug": ROLE_SLUG}
        ).scalar()

    admin_role_id = connection.execute(
        sa.text("SELECT id FROM roles WHERE slug = 'admin'")
    ).scalar()
    if admin_role_id is None:
        return

    # Erased accounts are skipped: they cannot sign in, and handing the strongest
    # role to a row that represents nobody would be an odd thing to find later.
    rows = connection.execute(
        sa.text(
            "SELECT ur.user_id FROM user_roles ur "
            "JOIN users u ON u.id = ur.user_id "
            "WHERE ur.role_id = :admin_role_id AND u.deleted_at IS NULL"
        ),
        {"admin_role_id": admin_role_id},
    ).fetchall()

    for (user_id,) in rows:
        already = connection.execute(
            sa.text(
                "SELECT 1 FROM user_roles WHERE user_id = :user_id AND role_id = :role_id"
            ),
            {"user_id": user_id, "role_id": role_id},
        ).scalar()
        if already:
            continue
        connection.execute(
            sa.text(
                "INSERT INTO user_roles (user_id, role_id, created_at) "
                "VALUES (:user_id, :role_id, :created_at)"
            ),
            {"user_id": user_id, "role_id": role_id, "created_at": created_at},
        )


def downgrade():
    connection = op.get_bind()
    role_id = connection.execute(
        sa.text("SELECT id FROM roles WHERE slug = :slug"), {"slug": ROLE_SLUG}
    ).scalar()
    if role_id is None:
        return
    connection.execute(
        sa.text("DELETE FROM user_roles WHERE role_id = :role_id"), {"role_id": role_id}
    )
    connection.execute(sa.text("DELETE FROM roles WHERE id = :role_id"), {"role_id": role_id})
