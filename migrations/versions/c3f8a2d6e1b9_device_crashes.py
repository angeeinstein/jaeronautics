"""Device crash reports, and the program file's SHA-256 of each firmware

A device that crashed sends what ESP-IDF read from its core dump, and the
dump (services/device_crashes.py, docs/devices-plan.md, "Crash reports"); a
crash names its build by the program file's SHA-256, which every firmware's
app description holds: kept now with each update file and factory image,
filled in from the files themselves for those uploaded before. Safe to run
again.

Revision ID: c3f8a2d6e1b9
Revises: b7e3d1f5a8c2
Create Date: 2026-10-16

"""
from alembic import op
import sqlalchemy as sa


revision = "c3f8a2d6e1b9"
down_revision = "b7e3d1f5a8c2"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    for table in ("firmware_releases", "firmware_factory_images"):
        if "elf_sha256" not in _columns(table):
            with op.batch_alter_table(table) as batch:
                batch.add_column(sa.Column("elf_sha256", sa.String(length=64), nullable=True))
    if sa.inspect(op.get_bind()).has_table("device_crashes"):
        return
    op.create_table(
        "device_crashes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("crash_id", sa.String(length=64), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("reported_by", sa.String(length=32), nullable=True),
        sa.Column("build", sa.String(length=16), nullable=False),
        sa.Column("task", sa.String(length=32), nullable=False),
        sa.Column("pc", sa.String(length=10), nullable=False),
        sa.Column("backtrace", sa.JSON(), nullable=False),
        sa.Column("backtrace_corrupted", sa.Boolean(), nullable=False),
        sa.Column("reset_reason", sa.String(length=24), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("file_received_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("device_id", "crash_id", name="uq_device_crashes_device_crash"),
    )
    op.create_index("ix_device_crashes_device_id", "device_crashes", ["device_id"])
    op.create_index("ix_device_crashes_received_at", "device_crashes", ["received_at"])


def downgrade():
    if sa.inspect(op.get_bind()).has_table("device_crashes"):
        op.drop_table("device_crashes")
    for table in ("firmware_releases", "firmware_factory_images"):
        if "elf_sha256" in _columns(table):
            with op.batch_alter_table(table) as batch:
                batch.drop_column("elf_sha256")
