"""Uploaded images must not be able to exhaust the server.

An image file can be tiny on disk and enormous once decoded. A PNG of a single
flat colour compresses almost perfectly, so a few hundred kilobytes can describe
a picture with tens of billions of pixels -- decoding it is what kills the
process, so it has to be refused before that happens.
"""
import zlib
from io import BytesIO

import pytest

from aeronautics_members import forum_service
from aeronautics_members.forum_service import ForumProviderError

Image = pytest.importorskip("PIL.Image")


def _png_of_size(width, height):
    """A valid PNG header declaring the given dimensions."""
    buffer = BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _png_header_claiming(width, height):
    """A small PNG whose header claims enormous dimensions.

    Built by patching the IHDR width/height of a real, tiny PNG, which is what a
    decompression bomb looks like from the server's side: a small upload that
    announces a huge image.
    """
    raw = bytearray(_png_of_size(4, 4))
    # PNG layout: 8-byte signature, then chunks of [length][type][data][crc].
    # IHDR's data begins at byte 16 with width then height.
    raw[16:20] = width.to_bytes(4, "big")
    raw[20:24] = height.to_bytes(4, "big")
    # The chunk CRC covers the type and data, so it has to be recomputed --
    # otherwise Pillow rejects the file as corrupt and never reads the size,
    # which would make this test pass for the wrong reason.
    raw[29:33] = zlib.crc32(bytes(raw[12:29])).to_bytes(4, "big")
    return bytes(raw)


def test_ordinary_image_is_accepted():
    processed = forum_service._load_image_for_processing(_png_of_size(64, 64))
    assert processed.size == (64, 64)


def test_oversized_image_is_refused():
    # 30000 x 30000 is 900 million pixels: several gigabytes decoded.
    with pytest.raises(ForumProviderError) as excinfo:
        forum_service._load_image_for_processing(_png_header_claiming(30000, 30000))
    assert "too large" in str(excinfo.value).lower()


def test_refused_on_the_header_even_without_pillow_s_own_limit(monkeypatch):
    """Our own check must do the work, not Pillow's backstop.

    Pillow raises DecompressionBombError during decode, which masks whether we
    looked at the header at all. Lifting that limit isolates our check -- and
    with it lifted, the previous ordering (decode first, measure afterwards)
    would decode the bomb instead of refusing it.
    """
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", None)

    with pytest.raises(ForumProviderError) as excinfo:
        forum_service._load_image_for_processing(_png_header_claiming(40000, 40000))

    assert "too large" in str(excinfo.value).lower()


def test_pillow_is_configured_to_refuse_bombs_itself():
    # A backstop for any code path that opens an image without the check above.
    assert Image.MAX_IMAGE_PIXELS == forum_service.MAX_AVATAR_PIXELS


def test_corrupt_upload_gets_a_useful_message():
    with pytest.raises(ForumProviderError) as excinfo:
        forum_service._load_image_for_processing(b"this is not an image")
    assert "valid" in str(excinfo.value).lower()


def _image_bytes(fmt, **save_options):
    buffer = BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buffer, format=fmt, **save_options)
    return buffer.getvalue()


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_the_formats_a_phone_uploads_still_work(fmt):
    data, content_type, _extension = forum_service.normalize_avatar_image(
        _image_bytes(fmt), allowed_extensions=["jpg", "png", "webp"], max_output_bytes=512 * 1024,
    )
    assert data and content_type.startswith("image/")


@pytest.mark.parametrize("fmt", ["GIF", "BMP", "TIFF", "PPM"])
def test_other_formats_are_refused_before_any_decoder_runs(fmt):
    """Pillow would otherwise try each of its decoders in turn -- PSD and FITS
    included, where its memory-safety bugs have been found. Only the three
    formats the upload form asks for are ever handed to one."""
    with pytest.raises(ForumProviderError) as excinfo:
        forum_service.normalize_avatar_image(
            _image_bytes(fmt), allowed_extensions=["png"], max_output_bytes=512 * 1024,
        )
    assert "valid" in str(excinfo.value).lower()


def test_a_psd_is_refused(monkeypatch):
    """The format of the published advisory. A minimal header is enough to
    show it is turned away at the door: without the allowlist Pillow opens it."""
    psd = (b"8BPS" + (1).to_bytes(2, "big") + b"\0" * 6 + (3).to_bytes(2, "big")
           + (1).to_bytes(4, "big") + (1).to_bytes(4, "big") + (8).to_bytes(2, "big")
           + (3).to_bytes(2, "big") + b"\0" * 64)
    opened = []
    real_open = Image.open

    def spying_open(fp, mode="r", formats=None):
        opened.append(formats)
        return real_open(fp, mode, formats)

    monkeypatch.setattr(forum_service.Image, "open", spying_open)
    with pytest.raises(ForumProviderError):
        forum_service._load_image_for_processing(psd)
    assert opened == [["JPEG", "PNG", "WEBP"]]


def test_the_old_forum_import_may_still_read_gifs():
    """Old forum avatars were often GIFs, and those files come from the export
    an administrator supplies, not from an upload."""
    from aeronautics_members.services.forum_import import IMPORTED_AVATAR_FORMATS

    data, _content_type, _extension = forum_service.normalize_avatar_image(
        _image_bytes("GIF"), allowed_extensions=["png"], max_output_bytes=512 * 1024,
        input_formats=IMPORTED_AVATAR_FORMATS,
    )
    assert data
