"""Devices: their code, owner, description and board

``devices.code`` is a device's random ID ("K7MQ-2X9A"), made for those there
are; ``team_id`` its owner (none: the association); ``description`` a few
words from the owner; ``hardware_id`` the MAC address of the board in it. A
name is no longer required: the code stands in for it (docs/devices-plan.md,
"Owners").

Safe to run again after it stopped part-way.

Revision ID: c3f8a1d6e9b4
Revises: a7d3f9c1e5b2
Create Date: 2026-10-13

"""
import secrets

from alembic import op
import sqlalchemy as sa


revision = "c3f8a1d6e9b4"
down_revision = "a7d3f9c1e5b2"
branch_labels = None
depends_on = None

#: As services/devices.py makes them: no 0/O, 1/I/L, so a code read aloud is not misheard.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def _code():
    raw = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def _have():
    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("devices")}
    uniques = {unique["name"] for unique in inspector.get_unique_constraints("devices")}
    columns = {column["name"]: column for column in inspector.get_columns("devices")}
    return indexes | uniques, columns


def upgrade():
    names, columns = _have()
    with op.batch_alter_table("devices") as batch_op:
        if "code" not in columns:
            batch_op.add_column(sa.Column("code", sa.String(9), nullable=True))
        if "team_id" not in columns:
            batch_op.add_column(sa.Column("team_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key("fk_devices_team_id", "teams", ["team_id"], ["id"])
        if "description" not in columns:
            batch_op.add_column(sa.Column("description", sa.String(300), nullable=True))
        if "hardware_id" not in columns:
            batch_op.add_column(sa.Column("hardware_id", sa.String(17), nullable=True))
    bind = op.get_bind()
    devices = sa.table("devices", sa.column("id", sa.Integer), sa.column("code", sa.String))
    taken = {row.code for row in bind.execute(sa.select(devices.c.code)) if row.code}
    for row in bind.execute(sa.select(devices.c.id).where(devices.c.code.is_(None))).fetchall():
        code = _code()
        while code in taken:
            code = _code()
        taken.add(code)
        bind.execute(sa.update(devices).where(devices.c.id == row.id).values(code=code))
    names, columns = _have()
    with op.batch_alter_table("devices") as batch_op:
        batch_op.alter_column("code", existing_type=sa.String(9), nullable=False)
        batch_op.alter_column("name", existing_type=sa.String(80), nullable=True)
    names, _columns = _have()
    if "uq_devices_code" not in names:
        with op.batch_alter_table("devices") as batch_op:
            batch_op.create_unique_constraint("uq_devices_code", ["code"])
    if "ix_devices_team_id" not in names:
        op.create_index("ix_devices_team_id", "devices", ["team_id"])


def downgrade():
    names, columns = _have()
    bind = op.get_bind()
    devices = sa.table("devices", sa.column("id", sa.Integer), sa.column("name", sa.String),
                       sa.column("code", sa.String))
    # A device without a name is named after its code again, as names were required.
    bind.execute(sa.update(devices).where(devices.c.name.is_(None)).values(name=devices.c.code))
    # The foreign key first: MariaDB will not drop an index a foreign key needs.
    if "team_id" in columns:
        with op.batch_alter_table("devices") as batch_op:
            batch_op.drop_constraint("fk_devices_team_id", type_="foreignkey")
    if "ix_devices_team_id" in names:
        op.drop_index("ix_devices_team_id", table_name="devices")
    with op.batch_alter_table("devices") as batch_op:
        if "team_id" in columns:
            batch_op.drop_column("team_id")
        if "uq_devices_code" in names:
            batch_op.drop_constraint("uq_devices_code", type_="unique")
        for column in ("code", "description", "hardware_id"):
            if column in columns:
                batch_op.drop_column(column)
        batch_op.alter_column("name", existing_type=sa.String(80), nullable=False)
