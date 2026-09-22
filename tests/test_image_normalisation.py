"""EXIF orientation, and why dropping it is invisible until it ships.

A phone camera writes the sensor's own pixels and records the rotation it
should be displayed at as EXIF tag 0x0112. A photo held upright is therefore
landscape BYTES plus a "turn this" flag, and every viewer anyone would check
the file in honours that flag — so the file looks correct everywhere a human
would look at it.

`normalise_for_image_provider` re-encodes to PNG, and PNG carries no
orientation tag. Without an explicit transpose the flag is dropped, the bytes
go to the provider unturned, and the mechanic is composited lying on his side.
Nothing downstream can recover it: the bytes are all the provider gets.

Found on a real submission — one of six mechanics in the 2026-09-16 test batch
came off a phone with orientation 6.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from castrol_pipeline.stages import media

#: 6 = rotate 90° CW for display, which is a phone held upright in the common
#: orientation. 1 = already upright. 8 is the other way up.
ORIENTATION_TAG = 0x0112


def _jpeg(path, size, orientation: int | None):
    """A landscape JPEG that, per EXIF, is meant to be seen as portrait."""
    im = Image.new("RGB", size, "white")
    # A mark in one corner, so a transpose is provable rather than inferred
    # from the dimensions alone — a square image would pass on size forever.
    im.paste(Image.new("RGB", (size[0] // 4, size[1] // 4), "red"), (0, 0))
    exif = im.getexif()
    if orientation is not None:
        exif[ORIENTATION_TAG] = orientation
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif.tobytes() if orientation is not None else b"")
    path.write_bytes(buf.getvalue())
    return path


class TestExifOrientationIsApplied:
    def test_a_rotated_photo_comes_out_upright(self, tmp_path):
        src = _jpeg(tmp_path / "phone.jpg", (800, 600), orientation=6)
        out = media.normalise_for_image_provider(src, tmp_path / "out.png")
        assert Image.open(out).size == (600, 800), (
            "orientation 6 means the display size is the transpose of the "
            "stored size; PNG cannot carry the tag, so the pixels must move"
        )

    def test_the_pixels_actually_move(self, tmp_path):
        # Dimensions alone would pass if the image were merely resized. The
        # corner mark proves the content was rotated, not just reshaped.
        src = _jpeg(tmp_path / "phone.jpg", (800, 600), orientation=6)
        out = Image.open(media.normalise_for_image_provider(src, tmp_path / "o.png"))
        w, h = out.size
        assert out.getpixel((w - 5, 5))[0] > 200, "top-RIGHT should be the mark"
        assert out.getpixel((5, 5))[1] > 200, "top-left should be white"

    @pytest.mark.parametrize("orientation", [None, 1])
    def test_an_unrotated_photo_is_left_alone(self, tmp_path, orientation):
        # The overwhelming majority of submissions. A transpose applied to
        # these would be the same bug pointing the other way.
        src = _jpeg(tmp_path / "flat.jpg", (800, 600), orientation=orientation)
        out = media.normalise_for_image_provider(src, tmp_path / "out.png")
        assert Image.open(out).size == (800, 600)

    def test_orientation_is_applied_before_the_size_check(self, tmp_path):
        """Order matters: the provider's [300, 6000] bound is on the axes it
        will actually receive. Measuring the stored bytes and then rotating
        would clamp the wrong axis."""
        # 6400 wide stored, so it is over the ceiling either way — but after
        # the transpose the long axis is the one that was 6400.
        src = _jpeg(tmp_path / "big.jpg", (6400, 3000), orientation=6)
        out = Image.open(media.normalise_for_image_provider(src, tmp_path / "o.png"))
        assert max(out.size) <= 6000
        assert out.size[1] > out.size[0], "still portrait after the clamp"
