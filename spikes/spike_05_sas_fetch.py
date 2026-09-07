"""Spike 0.5 - fetch Azure SAS photo URLs verbatim.

Kills the assumption that nothing in the HTTP stack re-encodes the signature.

Azure signs over the exact bytes of the URL. Encoding is inconsistent within a
single URL - colons in `se=` arrive percent-encoded while `sig=` carries a raw
forward slash - so any normalisation breaks the signature and returns 403,
which reads like a permissions failure rather than a parsing bug.

httpx does not re-encode an already-encoded URL when it is passed through
httpx.URL(). This spike proves that against the real client, and also proves
the magic-byte check, because a permissions failure can return HTTP 200 with an
HTML body - which without the check writes login pages into S3 as .jpg.

Usage:
    uv run python spikes/spike_05_sas_fetch.py spikes/in/sas_urls.txt

Input file: one URL per line, copied from the export byte-for-byte. Do not let
an editor "clean up" the lines.
"""

import hashlib
import pathlib
import sys
from urllib.parse import urlsplit

import httpx

MAGIC = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"RIFF": "image/webp",  # bytes 8..12 are 'WEBP'; good enough for a spike
}


def sniff(head: bytes) -> str | None:
    for prefix, mime in MAGIC.items():
        if head.startswith(prefix):
            return mime
    return None


def media_key(url: str) -> str:
    """Blob path with the query string stripped.

    These path segments are unique per upload, which makes this the strongest
    available dedupe key - stronger than anything derived from created_at_ist,
    which has no seconds.
    """
    return urlsplit(url).path.lstrip("/")


def main(path: str) -> int:
    urls = [
        line.strip()
        for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not urls:
        print(f"no URLs in {path}")
        return 1

    failures = 0
    # follow_redirects stays OFF: a redirect is itself a signal something is
    # wrong, and following one would hide it.
    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        for i, url in enumerate(urls, 1):
            print(f"\n[{i}] {url[:110]}{'...' if len(url) > 110 else ''}")

            # The single most important line in this file: the URL goes to the
            # client verbatim. No quote(), no unquote(), no rebuild from parts.
            try:
                r = client.get(url)
            except Exception as exc:  # noqa: BLE001 - spike
                print(f"    TRANSPORT FAIL: {type(exc).__name__}: {exc}")
                failures += 1
                continue

            # Did the client mangle the URL on the way out?
            sent = str(r.request.url)
            if sent != url:
                print("    !! URL WAS REWRITTEN BY THE CLIENT")
                print(f"       sent: {sent[:110]}")
                failures += 1

            declared = r.headers.get("content-type", "")
            actual = sniff(r.content[:16])
            print(f"    status      {r.status_code}")
            print(f"    declared    {declared}")
            print(f"    magic bytes {actual or 'NOT AN IMAGE'}")
            print(f"    bytes       {len(r.content)}")
            print(f"    sha256      {hashlib.sha256(r.content).hexdigest()[:16]}...")
            print(f"    media_key   {media_key(url)}")

            # Status code is not the test. Magic bytes are.
            if actual is None:
                print("    FAIL: body is not an image (HTML error page?)")
                print(f"       first 120 bytes: {r.content[:120]!r}")
                failures += 1
            elif r.status_code != 200:
                print(f"    FAIL: status {r.status_code}")
                failures += 1
            else:
                print("    OK")

    print(f"\n{len(urls) - failures}/{len(urls)} fetched cleanly")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
