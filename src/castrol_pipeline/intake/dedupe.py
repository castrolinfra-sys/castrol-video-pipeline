"""Dedupe keys for a non-idempotent export.

Overlapping date windows re-return rows and late submissions land in later
windows, so dedupe is ours. The key is sha256(media_key + phone_e164) where
media_key is the blob path with the query string stripped: those path segments
are unique per upload, which makes them a stronger key than anything
timestamp-derived (`created_at_ist` has no seconds).
"""

from __future__ import annotations

from urllib.parse import urlsplit

from ..common.hashing import submission_hash


def media_key_from_url(image_url_raw: str) -> str:
    """Extract the blob path, query string stripped.

    Parsing here is safe and does NOT violate the SAS rule: the result is used
    only as a dedupe key. It is never used to rebuild a URL for fetching — the
    fetch always uses `image_url_raw` verbatim. See intake/media.py.
    """
    return urlsplit(image_url_raw).path


def compute_submission_hash(image_url_raw: str, phone_e164: str) -> str:
    return submission_hash(media_key_from_url(image_url_raw), phone_e164)
