"""Give every old forum picture its address token

The portal now shows the old forum's picture of somebody who has none of their
own here (services/pictures.py). The address token was minted only when a
profile was published to the forum, so a profile not published yet had a
picture and no way to show it. Data only.

Revision ID: d4a7c2e9f1b3
Revises: c3f9a1d7e5b4
Create Date: 2026-10-08

"""
import secrets

from alembic import op
import sqlalchemy as sa


revision = "d4a7c2e9f1b3"
down_revision = "c3f9a1d7e5b4"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    profiles = sa.table(
        "imported_forum_profiles",
        sa.column("id", sa.Integer),
        sa.column("avatar_path", sa.String),
        sa.column("avatar_public_token", sa.String),
    )
    missing = connection.execute(
        sa.select(profiles.c.id).where(
            profiles.c.avatar_path.is_not(None), profiles.c.avatar_public_token.is_(None)
        )
    ).scalars().all()
    for profile_id in missing:
        connection.execute(
            profiles.update().where(profiles.c.id == profile_id).values(avatar_public_token=secrets.token_hex(32))
        )


def downgrade():
    # The tokens are harmless, and some were minted by publishing to the forum,
    # which this cannot tell apart; nothing to undo.
    pass
