"""What a firmware file is, read from the file itself (docs/devices-plan.md,
"Firmware updates").

The devices run ESP-IDF, whose app image (the ``.bin`` an over-the-air update
installs) is laid out as:

- a 24-byte header: ``0xE9``, the number of segments, ..., the chip it was
  built for (at byte 12), and whether a SHA-256 is appended (byte 23);
- the segments, each an 8-byte header (load address, length) and its data.
  The first one starts with ESP-IDF's app description (``esp_app_desc_t``):
  ``0xABCD5432``, the version, the project's name, when it was built and with
  which ESP-IDF;
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
                 chip=CHIPS.get(chip_id), custom_offset=start + APP_DESC_SIZE, size=len(data),
                 sha256=hashlib.sha256(data).hexdigest())


def example(version="1.0.0", project="jae-terminal", payload=b"\x5a" * 300):
    """A small app image laid out as ESP-IDF builds one, for the tests and the
    example portal (scripts/visual/snapshot.py): nothing a device could run.
    tests/fixtures holds one esptool wrote, which :func:`read` is checked against."""
    desc = struct.pack("<IIII", APP_DESC_MAGIC, 0, 0, 0)
    desc += version.encode().ljust(32, b"\0") + project.encode().ljust(32, b"\0")
    desc += b"09:12:44".ljust(16, b"\0") + b"Oct 14 2026".ljust(16, b"\0") + b"v5.3.1".ljust(32, b"\0")
    segment = desc.ljust(APP_DESC_SIZE, b"\0") + payload
    header = bytearray(HEADER_SIZE)
    header[0], header[1] = IMAGE_MAGIC, 1
    struct.pack_into("<H", header, 12, 0x0009)  # an ESP32-S3
    header[23] = 1  # the SHA-256 appended
    data = bytes(header) + struct.pack("<II", 0x3C000020, len(segment)) + segment
    checksum = CHECKSUM_SEED
    for byte in segment:
        checksum ^= byte
    data += b"\0" * (15 - len(data) % 16) + bytes([checksum])
    return data + hashlib.sha256(data).digest()
