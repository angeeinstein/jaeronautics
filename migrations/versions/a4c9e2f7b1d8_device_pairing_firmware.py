"""Devices: pairing, what the firmware can do, firmware updates, a backup code

``device_pairings``: devices asking to be paired, by a code on their screen;
``firmware_releases``: firmware files uploaded for the devices. On
``devices``: the board and the types its firmware can be used as, as it
reports them; the backup code that opens its settings when the portal cannot
be reached (made for the devices there are); the firmware it should run when
not the same as every device, and the version it could not install
(docs/devices-plan.md, "One device for everything").

Safe to run again after it stopped part-way.

Revision ID: a4c9e2f7b1d8
Revises: f1d6b3a9c8e5
Create Date: 2026-10-14

"""
import secrets

from alembic import op
import sqlalchemy as sa


revision = "a4c9e2f7b1d8"
down_revision = "f1d6b3a9c8e5"
branch_labels = None
depends_on = None


def _have():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    indexes = {index["name"] for table in tables for index in inspector.get_indexes(table)}
    columns = {column["name"] for column in inspector.get_columns("devices")}
    foreign_keys = {key["name"] for key in inspector.get_foreign_keys("devices")}
    return tables, indexes, columns, foreign_keys


def upgrade():
    tables, indexes, columns, _foreign_keys = _have()
    if "firmware_releases" not in tables:
        op.create_table(
            "firmware_releases",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("version", sa.String(32), nullable=False),
            sa.Column("project", sa.String(32), nullable=False),
            sa.Column("built", sa.String(40), nullable=True),
            sa.Column("idf_version", sa.String(32), nullable=True),
            sa.Column("chip", sa.String(20), nullable=True),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("size", sa.Integer(), nullable=False),
            sa.Column("file_name", sa.String(200), nullable=True),
            sa.Column("notes", sa.String(300), nullable=True),
            sa.Column("uploaded_at", sa.DateTime(), nullable=False),
            sa.Column("uploaded_by_user_id", sa.Integer(),
                      sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_firmware_releases_uploaded_by"),
                      nullable=True),
            sa.UniqueConstraint("version", name="uq_firmware_releases_version"),
            sa.UniqueConstraint("sha256", name="uq_firmware_releases_sha256"),
        )
    if "device_pairings" not in tables:
        op.create_table(
            "device_pairings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("code", sa.String(6), nullable=False),
            sa.Column("secret_hash", sa.String(64), nullable=False),
            sa.Column("hardware_id", sa.String(17), nullable=True),
            sa.Column("firmware", sa.String(60), nullable=True),
            sa.Column("board", sa.String(60), nullable=True),
            sa.Column("supported_types", sa.JSON(), nullable=True),
            sa.Column("address", sa.String(45), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("last_poll_at", sa.DateTime(), nullable=True),
            sa.Column("device_id", sa.Integer(),
                      sa.ForeignKey("devices.id", ondelete="CASCADE", name="fk_device_pairings_device_id"),
                      nullable=True),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("approved_by_user_id", sa.Integer(),
                      sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_device_pairings_approved_by"),
                      nullable=True),
            sa.Column("refused_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("code", name="uq_device_pairings_code"),
            sa.UniqueConstraint("secret_hash", name="uq_device_pairings_secret_hash"),
        )
    tables, indexes, columns, _foreign_keys = _have()
    if "ix_device_pairings_device_id" not in indexes:
        op.create_index("ix_device_pairings_device_id", "device_pairings", ["device_id"])
    with op.batch_alter_table("devices") as batch_op:
        if "board" not in columns:
            batch_op.add_column(sa.Column("board", sa.String(60), nullable=True))
        if "supported_types" not in columns:
            batch_op.add_column(sa.Column("supported_types", sa.JSON(), nullable=True))
        if "backup_code" not in columns:
            batch_op.add_column(sa.Column("backup_code", sa.String(12), nullable=True))
        if "firmware_release_id" not in columns:
            batch_op.add_column(sa.Column("firmware_release_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key("fk_devices_firmware_release_id", "firmware_releases",
                                        ["firmware_release_id"], ["id"], ondelete="SET NULL")
        if "firmware_failed_version" not in columns:
            batch_op.add_column(sa.Column("firmware_failed_version", sa.String(32), nullable=True))
        if "firmware_failed_detail" not in columns:
            batch_op.add_column(sa.Column("firmware_failed_detail", sa.String(255), nullable=True))
    tables, indexes, columns, _foreign_keys = _have()
    if "ix_devices_firmware_release_id" not in indexes:
        op.create_index("ix_devices_firmware_release_id", "devices", ["firmware_release_id"])
    bind = op.get_bind()
    devices = sa.table("devices", sa.column("id", sa.Integer), sa.column("backup_code", sa.String))
    for row in bind.execute(sa.select(devices.c.id).where(devices.c.backup_code.is_(None))).fetchall():
        code = "".join(secrets.choice("0123456789") for _ in range(6))
        bind.execute(sa.update(devices).where(devices.c.id == row.id).values(backup_code=code))


def downgrade():
    tables, indexes, columns, foreign_keys = _have()
    # On MariaDB a foreign key's index cannot go before the key itself.
    if "fk_devices_firmware_release_id" in foreign_keys:
        with op.batch_alter_table("devices") as batch_op:
            batch_op.drop_constraint("fk_devices_firmware_release_id", type_="foreignkey")
    tables, indexes, columns, foreign_keys = _have()
    if "ix_devices_firmware_release_id" in indexes:
        op.drop_index("ix_devices_firmware_release_id", table_name="devices")
    with op.batch_alter_table("devices") as batch_op:
        for column in ("firmware_failed_detail", "firmware_failed_version", "firmware_release_id", "backup_code",
                       "supported_types", "board"):
            if column in columns:
                batch_op.drop_column(column)
    if "device_pairings" in tables:
        op.drop_table("device_pairings")
    if "firmware_releases" in tables:
        op.drop_table("firmware_releases")
