"""Records for people carried over from the old forum, who are not members.

Roughly 500-600 students from the last decade, whose posts should keep a name,
a year group and a face beside them. ``Member`` cannot hold them: it requires a
postal address, a phone number and a unique private email address, none of
which exist for somebody who left in 2016, and inventing six hundred of each
would be a lie that something later trusts.

Deliberately not a status like "alumni": such a label contradicts the fact that
these people can come back and become members again. Whether they can sign in
is answered by ``users.password_hash IS NULL``, and whether they are a member is
answered by the coverage ledger. This table adds only what neither of those
knows.

Revision ID: a7d41e8b93c5
Revises: f2b6a90c1d73
Create Date: 2026-09-19 21:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'a7d41e8b93c5'
down_revision = 'f2b6a90c1d73'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'imported_forum_profiles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('source_system', sa.String(length=40), nullable=False),
        # The old forum's own key. Re-running the import updates these rows
        # instead of creating six hundred more.
        sa.Column('source_user_id', sa.String(length=64), nullable=False),
        sa.Column('source_username', sa.String(length=255), nullable=False),
        # History, never identity: these are university addresses, disabled when
        # a student leaves and possibly reissued to a later student of the same
        # name, so they must never reach users.email.
        sa.Column('source_email', sa.String(length=255), nullable=True),
        sa.Column('display_name', sa.String(length=200), nullable=False),
        sa.Column('year_group', sa.String(length=50), nullable=True),
        sa.Column('avatar_path', sa.String(length=255), nullable=True),
        sa.Column('post_count', sa.Integer(), nullable=True),
        sa.Column('joined_on', sa.Date(), nullable=True),
        sa.Column('last_posted_on', sa.Date(), nullable=True),
        sa.Column('imported_at', sa.DateTime(), nullable=False),
        sa.Column('claimed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id'),
        sa.UniqueConstraint('source_system', 'source_user_id', name='uq_imported_forum_source'),
    )
    # The directory is browsed by year group, and the import looks rows up by
    # the old username to decide whether it has seen somebody before.
    op.create_index(
        'ix_imported_forum_profiles_year_group', 'imported_forum_profiles', ['year_group']
    )
    op.create_index(
        'ix_imported_forum_profiles_source_username',
        'imported_forum_profiles',
        ['source_username'],
    )


def downgrade():
    op.drop_index('ix_imported_forum_profiles_source_username', table_name='imported_forum_profiles')
    op.drop_index('ix_imported_forum_profiles_year_group', table_name='imported_forum_profiles')
    op.drop_table('imported_forum_profiles')
