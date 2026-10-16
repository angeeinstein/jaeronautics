"""Factory images: what a fresh board is flashed with from the browser

Bootloader, partition table and app in one file, one per chip
(services/device_factory.py, docs/devices-plan.md). A new table; safe to run
again.

Revision ID: b7e3d1f5a8c2
Revises: a4c7e2f9b1d6
Create Date: 2026-10-16

"""
from alembic import op
import sqlalchemy as sa


revision = "b7e3d1f5a8c2"
down_revision = "a4c7e2f9b1d6"
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("firmware_factory_images"):
        return
    op.create_table(
        "firmware_factory_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chip", sa.String(length=20), nullable=False, unique=True),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("project", sa.String(length=32), nullable=False),
        sa.Column("built", sa.String(length=40), nullable=True),
        sa.Column("idf_version", sa.String(length=32), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("file_name", sa.String(length=200), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
    )


def downgrade():
    if sa.inspect(op.get_bind()).has_table("firmware_factory_images"):
        op.drop_table("firmware_factory_images")
