"""Client export API: the daily date-range pull.

The response is **CSV**, not JSON, and every value in it is a string. Parsing
and typing are validate.py's job; this module's only responsibility is turning
the bytes the client sent into rows keyed by their header names, losing
nothing.

The pull is NOT idempotent - overlapping windows re-return rows and late
submissions land in later windows. Dedupe is ours (intake/dedupe.py).

Confirmed against a real pull on 2026-09-08. The 18 columns, in order:

    id, whatsapp_number, user_name, workshop_name, address, gender,
    mechanic_id_verified, mechanic_id, mechanic_phone_number, background,
    outfit, image_url, image_mime_type, image_validation_status,
    image_rekognition_status, status, createdAt, updatedAt
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from ..common.logging import get_logger
from ..config import Settings

log = get_logger(__name__)

#: Columns we have actually seen. Used to NOTICE a schema change, never to
#: filter - an unexpected column is carried through to `export_rows.raw`
#: untouched, because the client admin reads fields the pipeline does not.
KNOWN_COLUMNS: tuple[str, ...] = (
    "id",
    "whatsapp_number",
    "user_name",
    "workshop_name",
    "address",
    "gender",
    "mechanic_id_verified",
    "mechanic_id",
    "mechanic_phone_number",
    "background",
    "outfit",
    "image_url",
    "image_mime_type",
    "image_validation_status",
    "image_rekognition_status",
    "status",
    "createdAt",
    "updatedAt",
)


class ExportError(Exception):
    pass


@dataclass(frozen=True)
class ExportWindow:
    from_date: date
    to_date: date

    def params(self) -> dict[str, str]:
        return {"from": self.from_date.isoformat(), "to": self.to_date.isoformat()}


@dataclass(frozen=True)
class ExportResult:
    """One pull, with the provenance `export_pulls` records."""

    rows: list[dict[str, str]]
    header: list[str]
    csv_sha256: str
    http_status: int


def parse_csv(body: bytes) -> ExportResult:
    """Decode and parse one export response.

    The body arrives with a UTF-8 BOM. Decoded as plain utf-8 the first header
    becomes "\ufeffid" and `row["id"]` - the client's own submission uuid, and
    the only unique identifier in the whole feed - reads as missing, while the
    other 17 columns parse perfectly. That is why this is `utf-8-sig` and why
    it is not a detail anyone may quietly "simplify".

    Values are returned exactly as they arrived: no stripping, no coercion, no
    empty-string-to-None. A row that is wrong is rejected downstream with a
    code, never repaired here (invariant 8).
    """
    text = body.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    header = list(reader.fieldnames or [])
    if not header:
        raise ExportError("Export response had no header row")
    if header[0].startswith("\ufeff"):
        # Belt and braces: if this ever fires, the decode above was changed.
        raise ExportError(
            "Export header still carries a BOM - decode the body as utf-8-sig"
        )

    rows = [{k: (v if v is not None else "") for k, v in row.items() if k is not None}
            for row in reader]

    unexpected = [c for c in header if c not in KNOWN_COLUMNS]
    missing = [c for c in KNOWN_COLUMNS if c not in header]
    if unexpected or missing:
        # Not an error. The client owns this schema and may change it without
        # telling us; the row still lands in export_rows in full. But it is
        # exactly the event worth seeing in the log before the rejects start.
        log.warning(
            "intake.export_schema_changed",
            unexpected=unexpected,
            missing=missing,
            header=header,
        )

    return ExportResult(
        rows=rows,
        header=header,
        csv_sha256=hashlib.sha256(body).hexdigest(),
        http_status=200,
    )


class ExportClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def fetch(self, window: ExportWindow, *, timeout: float = 60.0) -> ExportResult:
        url = self.settings.require("client_export_url")
        headers = {"apikey": self.settings.require("client_export_api_key")}

        log.info("intake.export_pull", **window.params())
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url, params=window.params(), headers=headers)

        if response.status_code != 200:
            raise ExportError(
                f"Export returned {response.status_code}: {response.text[:300]}"
            )

        result = parse_csv(response.content)
        log.info("intake.export_pulled", rows=len(result.rows), sha256=result.csv_sha256)
        return result

    @staticmethod
    def from_fixture(path: str | Path) -> ExportResult:
        """Parse a saved export body. Identical code path to a live pull."""
        result = parse_csv(Path(path).read_bytes())
        log.info("intake.export_fixture", path=str(path), rows=len(result.rows))
        return result


def iter_rows(rows: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    yield from rows
