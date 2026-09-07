"""Photo fetch and landing.

=============================================================================
 THE SAS RULE — read before touching the fetch below.
=============================================================================
 The photo URL is an Azure blob URL carrying a SAS token. TREAT IT AS OPAQUE
 BYTES. Azure signs over the exact byte sequence, so ANY normalisation breaks
 the signature and returns 403 — which reads like a permissions failure rather
 than a parsing bug, and costs a day to diagnose.

 Encoding is inconsistent WITHIN A SINGLE URL: colons in `se=` are
 percent-encoded (%3A) while `sig=` carries a raw forward slash. It looks
 wrong. It is not.

 Therefore, on `image_url_raw`:
   * pass it to the HTTP client verbatim
   * never urllib.parse.quote / unquote it
   * never form-decode it (base64 signatures contain '+', which form decoding
     turns into a space)
   * never rebuild it from parsed components
   * never route it through a URL-normalising client

 httpx normalises URLs when given a string. We therefore hand it a
 pre-constructed httpx.URL, which preserves the raw bytes.
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

import httpx

from ..common.logging import get_logger

log = get_logger(__name__)

#: Minimum short edge. A sanity check for empty files, thumbnails and broken
#: uploads — NOT a quality gate. Output quality tracks input quality and that
#: has been accepted.
MIN_SHORT_EDGE_PX = 100

#: Magic-byte signatures. We validate these, never the status code: a
#: permissions failure can return HTTP 200 with an HTML body, and without this
#: check that writes login pages into S3 as .jpg.
_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"GIF87a", "image/gif", "gif"),
    (b"GIF89a", "image/gif", "gif"),
    (b"BM", "image/bmp", "bmp"),
)


class MediaError(Exception):
    """Fetch or validation failed. Carries a stable reason for the reject code."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class FetchedPhoto:
    data: bytes
    mime_type: str
    extension: str
    width: int
    height: int


def sniff_mime(data: bytes) -> tuple[str, str] | None:
    """Return (mime, extension) from magic bytes, or None if unrecognised."""
    for signature, mime, ext in _SIGNATURES:
        if data.startswith(signature):
            return mime, ext
    # WEBP is RIFF....WEBP — the size field sits between the two markers.
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    # HEIC/HEIF carry an ftyp box with a brand we recognise.
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1", b"heim"):
        return "image/heic", "heic"
    return None


def fetch_photo(image_url_raw: str, *, timeout: float = 30.0) -> FetchedPhoto:
    """Fetch, then validate magic bytes and dimensions. Never trusts the status."""
    # httpx.URL(...) preserves the raw byte sequence. Passing the plain string
    # to .get() would let the client normalise it. See the SAS rule above.
    url = httpx.URL(image_url_raw)

    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        raise MediaError(f"Photo fetch failed: {exc}", reason="FETCH_FAILED") from exc

    data = response.content
    if not data:
        raise MediaError("Photo fetch returned an empty body", reason="FETCH_FAILED")

    sniffed = sniff_mime(data)
    if sniffed is None:
        # This is the login-page-as-jpg case. The status code may well be 200.
        preview = data[:64].decode("utf-8", errors="replace")
        raise MediaError(
            f"Body is not a recognised image (status {response.status_code}, "
            f"declared {response.headers.get('content-type')!r}, starts {preview!r})",
            reason="BAD_MIME",
        )

    mime, ext = sniffed
    width, height = _dimensions(data)
    if min(width, height) < MIN_SHORT_EDGE_PX:
        raise MediaError(
            f"Short edge {min(width, height)}px is below the {MIN_SHORT_EDGE_PX}px floor",
            reason="IMAGE_TOO_SMALL",
        )

    log.info("intake.photo_fetched", mime=mime, width=width, height=height, bytes=len(data))
    return FetchedPhoto(data=data, mime_type=mime, extension=ext, width=width, height=height)


def _dimensions(data: bytes) -> tuple[int, int]:
    from PIL import Image

    try:
        with Image.open(BytesIO(data)) as img:
            return img.width, img.height
    except Exception as exc:
        raise MediaError(f"Image could not be decoded: {exc}", reason="BAD_MIME") from exc
