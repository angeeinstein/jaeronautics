"""What a firmware file is, read from the file itself (docs/devices-plan.md,
"Firmware updates").

The devices run ESP-IDF, whose app image (the ``.bin`` an over-the-air update
installs) is laid out as:

- a 24-byte header: ``0xE9``, the number of segments, ..., the chip it was
  built for (at byte 12), and whether a SHA-256 is appended (byte 23);
- the segments, each an 8-byte header (load address, length) and its data.
  The first one starts with ESP-IDF's app description (``esp_app_desc_t``):
  ``0xABCD5432``, the version, the project's name, when it was built and with
  which ESP-IDF, and (at 144) the SHA-256 of the program file (ELF) it was
  made from -- what a crash report names its build by;
- a checksum byte (every segment's data XORed, from ``0xEF``), placed so it
  ends a 16-byte block;
- with ``hash_appended``, the SHA-256 of everything before it;
- after that, a signature block for signed images, which is left alone here.

So a file can be told to be a whole, unchanged ESP-IDF app before any device
gets it, and what it is shown without anyone typing it in.
"""

import hashlib
import struct
from dataclasses import dataclass

IMAGE_MAGIC = 0xE9
APP_DESC_MAGIC = 0xABCD5432
HEADER_SIZE = 24
SEGMENT_HEADER_SIZE = 8
APP_DESC_SIZE = 256
CHECKSUM_SEED = 0xEF
MAX_SEGMENTS = 16

#: ``esp_chip_id_t``: the chip an image was built for.
CHIPS = {0x0000: "esp32", 0x0002: "esp32s2", 0x0005: "esp32c3", 0x0009: "esp32s3", 0x000C: "esp32c2",
         0x000D: "esp32c6", 0x0010: "esp32h2", 0x0012: "esp32p4", 0x0014: "esp32c61", 0x0017: "esp32c5"}


class NotAnImage(ValueError):
    """The file is not a whole ESP-IDF app image; the message says what is wrong, in words."""


@dataclass(frozen=True)
class Image:
    version: str
    project: str
    built: str
    idf_version: str
    chip: str | None
    #: The SHA-256 of the program file (ELF), hex: what a crash report's ``build`` starts with.
    elf_sha256: str
    #: Where the app description ends: what a firmware adds of its own
    #: (``.rodata_custom_desc``) starts here, in the first segment.
    custom_offset: int
    size: int
    sha256: str


def _text(raw):
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace").strip()


def read(data):
    """The :class:`Image` ``data`` holds; :class:`NotAnImage` if it is not one."""
    if len(data) < HEADER_SIZE + SEGMENT_HEADER_SIZE + APP_DESC_SIZE:
        raise NotAnImage("This file is too small to be firmware.")
    if data[0] != IMAGE_MAGIC:
        raise NotAnImage("This is not an ESP-IDF firmware file (a .bin built for the device's app).")
    segment_count = data[1]
    if not 1 <= segment_count <= MAX_SEGMENTS:
        raise NotAnImage("This firmware file is damaged: it has no readable parts.")
    chip_id = struct.unpack_from("<H", data, 12)[0]
    hash_appended = data[23] == 1

    offset = HEADER_SIZE
    checksum = CHECKSUM_SEED
    first_segment = None
    for _ in range(segment_count):
        if offset + SEGMENT_HEADER_SIZE > len(data):
            raise NotAnImage("This firmware file is cut off.")
        _address, length = struct.unpack_from("<II", data, offset)
        offset += SEGMENT_HEADER_SIZE
        if offset + length > len(data):
            raise NotAnImage("This firmware file is cut off.")
        segment = data[offset:offset + length]
        if first_segment is None:
            first_segment = (offset, segment)
        for byte in segment:
            checksum ^= byte
        offset += length
    # The checksum ends a 16-byte block.
    offset += 15 - (offset % 16)
    if offset >= len(data):
        raise NotAnImage("This firmware file is cut off.")
    if data[offset] != checksum:
        raise NotAnImage("This firmware file is damaged: its checksum does not match.")
    offset += 1
    if hash_appended:
        if offset + 32 > len(data):
            raise NotAnImage("This firmware file is cut off.")
        if hashlib.sha256(data[:offset]).digest() != data[offset:offset + 32]:
            raise NotAnImage("This firmware file is damaged: its SHA-256 does not match.")

    start, segment = first_segment
    if len(segment) < APP_DESC_SIZE or struct.unpack_from("<I", segment, 0)[0] != APP_DESC_MAGIC:
        raise NotAnImage("This is not an app: it has no app description (a bootloader or a partition table?).")
    version = _text(segment[16:48])
    project = _text(segment[48:80])
    built = " ".join(part for part in (_text(segment[96:112]), _text(segment[80:96])) if part)
    if not version or not project:
        raise NotAnImage("This firmware file names no version or project.")
    return Image(version=version, project=project, built=built, idf_version=_text(segment[112:144]),
                 elf_sha256=segment[144:176].hex(), chip=CHIPS.get(chip_id), custom_offset=start + APP_DESC_SIZE,
                 size=len(data),
                 sha256=hashlib.sha256(data).hexdigest())


# --- A factory image: everything a fresh board needs, from address 0 ---------------------
#
# ``idf.py merge-bin`` (``esptool.py merge_bin``) writes one file to flash at
# 0x0: the bootloader -- at 0x0, or after 0xFF padding where the chip starts it
# later --, the partition table (by default at 0x8000), the OTA data, and the
# app in its partition. Its partition table is a list of 32-byte entries:
# ``0xAA 0x50``, type, subtype, offset, size, a 16-byte label, flags; an MD5
# entry (``0xEB 0xEB``) and 0xFF after the last.

#: Where each chip's ROM looks for the bootloader; 0x0 for the rest.
BOOTLOADER_OFFSETS = {"esp32": 0x1000, "esp32s2": 0x1000, "esp32p4": 0x2000, "esp32c5": 0x2000}
PARTITION_MAGIC = b"\xaa\x50"
PARTITION_ENTRY_SIZE = 32
#: Where a partition table may start: ESP-IDF's default 0x8000, moved on in 4 KB steps for a larger bootloader.
PARTITION_TABLE_OFFSETS = range(0x8000, 0x10000 + 1, 0x1000)
APP_TYPE = 0x00
FACTORY_SUBTYPE, OTA_0_SUBTYPE = 0x00, 0x10


@dataclass(frozen=True)
class FactoryImage:
    version: str
    project: str
    built: str
    idf_version: str
    chip: str
    elf_sha256: str
    #: Where the app sits in it: its partition's offset.
    app_offset: int
    size: int
    sha256: str


def _partitions(data, offset):
    """``[(type, subtype, offset, size, label)]`` of the table at ``offset``; empty if none is there."""
    entries = []
    for at in range(offset, min(offset + 0xC00, len(data) - PARTITION_ENTRY_SIZE + 1), PARTITION_ENTRY_SIZE):
        raw = data[at:at + PARTITION_ENTRY_SIZE]
        if raw[:2] != PARTITION_MAGIC:
            break
        kind, subtype, start, size = struct.unpack_from("<BBII", raw, 2)
        entries.append((kind, subtype, start, size, _text(raw[12:28])))
    return entries


def read_factory(data):
    """The :class:`FactoryImage` ``data`` holds; :class:`NotAnImage` if it is not one."""
    try:
        read(data)
    except NotAnImage:
        pass
    else:
        raise NotAnImage("This is an update file (the app alone), not a factory image: build that with "
                         "idf.py merge-bin.")
    found = next((offset for offset in (0x0, 0x1000, 0x2000)
                  if len(data) > offset + HEADER_SIZE and data[offset] == IMAGE_MAGIC), None)
    if found is None:
        raise NotAnImage("This is not a factory image: it has no bootloader. Build it with idf.py merge-bin.")
    chip = CHIPS.get(struct.unpack_from("<H", data, found + 12)[0])
    if chip is None:
        raise NotAnImage("This factory image is for a chip the portal does not know.")
    if BOOTLOADER_OFFSETS.get(chip, 0x0) != found:
        raise NotAnImage(f"The bootloader is not where the {chip} looks for it: build the image with "
                         "idf.py merge-bin, to be written from address 0.")
    table = next((entries for offset in PARTITION_TABLE_OFFSETS if (entries := _partitions(data, offset))), None)
    if not table:
        raise NotAnImage("This factory image has no partition table.")
    apps = {subtype: (start, size) for kind, subtype, start, size, _label in table if kind == APP_TYPE}
    # What the bootloader starts on a fresh board: the factory app, else the first OTA slot.
    place = apps.get(FACTORY_SUBTYPE) or apps.get(OTA_0_SUBTYPE)
    if place is None:
        raise NotAnImage("The partition table of this factory image has no app partition.")
    start, size = place
    if start >= len(data):
        raise NotAnImage("This factory image has no app in it: build it with idf.py merge-bin after the app.")
    try:
        app = read(data[start:start + size])
    except NotAnImage as exc:
        raise NotAnImage(f"The app in this factory image: {str(exc)[0].lower()}{str(exc)[1:]}") from exc
    if app.chip != chip:
        raise NotAnImage(f"The app in this factory image is for the {app.chip or 'unknown chip'}, "
                         f"its bootloader for the {chip}.")
    return FactoryImage(version=app.version, project=app.project, built=app.built, idf_version=app.idf_version,
                        elf_sha256=app.elf_sha256, chip=chip, app_offset=start, size=len(data),
                        sha256=hashlib.sha256(data).hexdigest())


def factory_example(version="1.0.0", project="jae-terminal", chip_id=0x0009, app=None):
    """A factory image laid out as ``idf.py merge-bin`` writes one, around
    :func:`example` -- for the tests and the example portal: nothing a board could run."""
    chip = CHIPS[chip_id]
    bootloader_at = BOOTLOADER_OFFSETS.get(chip, 0x0)
    bootloader = bytearray(HEADER_SIZE)
    bootloader[0], bootloader[1] = IMAGE_MAGIC, 0
    struct.pack_into("<H", bootloader, 12, chip_id)
    app_at, app_size = 0x10000, 0x100000

    def entry(kind, subtype, start, size, label):
        return PARTITION_MAGIC + struct.pack("<BBII", kind, subtype, start, size) + label.ljust(16, b"\0") + b"\0" * 4

    table = (entry(0x01, 0x02, 0x9000, 0x6000, b"nvs") + entry(0x01, 0x00, 0xF000, 0x2000, b"otadata")
             + entry(APP_TYPE, OTA_0_SUBTYPE, app_at, app_size, b"ota_0")
             + entry(APP_TYPE, OTA_0_SUBTYPE + 1, app_at + app_size, app_size, b"ota_1"))
    data = bytearray(b"\xff" * app_at)
    data[bootloader_at:bootloader_at + len(bootloader)] = bootloader
    data[0x8000:0x8000 + len(table)] = table
    return bytes(data) + (app if app is not None else example(version=version, project=project, chip_id=chip_id))


def example(version="1.0.0", project="jae-terminal", payload=b"\x5a" * 300, chip_id=0x0009):
    """A small app image laid out as ESP-IDF builds one, for the tests and the
    example portal (scripts/visual/snapshot.py): nothing a device could run.
    tests/fixtures holds one esptool wrote, which :func:`read` is checked against."""
    desc = struct.pack("<IIII", APP_DESC_MAGIC, 0, 0, 0)
    desc += version.encode().ljust(32, b"\0") + project.encode().ljust(32, b"\0")
    desc += b"09:12:44".ljust(16, b"\0") + b"Oct 14 2026".ljust(16, b"\0") + b"v5.3.1".ljust(32, b"\0")
    desc += hashlib.sha256(f"{project} {version}".encode()).digest()
    segment = desc.ljust(APP_DESC_SIZE, b"\0") + payload
    header = bytearray(HEADER_SIZE)
    header[0], header[1] = IMAGE_MAGIC, 1
    struct.pack_into("<H", header, 12, chip_id)  # 0x0009: an ESP32-S3
    header[23] = 1  # the SHA-256 appended
    data = bytes(header) + struct.pack("<II", 0x3C000020, len(segment)) + segment
    checksum = CHECKSUM_SEED
    for byte in segment:
        checksum ^= byte
    data += b"\0" * (15 - len(data) % 16) + bytes([checksum])
    return data + hashlib.sha256(data).digest()
