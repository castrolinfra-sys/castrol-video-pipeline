"""Client export API: the daily date-range pull.

The pull is NOT idempotent — overlapping windows re-return rows and late
submissions land in later windows. Dedupe is ours (intake/dedupe.py).

A fixture source is supported because the export API key is still pending from
the client. It lets 1.2 be built and tested against a saved response, and it is
the same code path from `rows()` onward.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from ..common.logging import get_logger
from ..config import Settings

log = get_logger(__name__)


class ExportError(Exception):
    pass


@dataclass(frozen=True)
class ExportWindow:
    from_date: date
    to_date: date

    def params(self) -> dict[str, str]:
        return {"from": self.from_date.isoformat(), "to": self.to_date.isoformat()}


def _unwrap(payload: Any) -> list[dict[str, Any]]:
    """Pull the row list out of whatever envelope the API uses.

    The exact envelope is unconfirmed, so accept the plausible shapes rather
    than guessing one and failing opaquely on the first real pull.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "submissions", "results", "rows", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    raise ExportError(
        f"Could not find a row list in the export response (top level: "
        f"{type(payload).__name__}, keys: "
        f"{sorted(payload)[:10] if isinstance(payload, dict) else 'n/a'})"
    )


class ExportClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def fetch(self, window: ExportWindow, *, timeout: float = 60.0) -> list[dict[str, Any]]:
        url = self.settings.require("client_export_url")
        # Auth scheme is still pending from the client (PROJECT_PLAN open issue
        # 3). `apikey` mirrors the delivery webhook, which is the best available
        # guess; confirm before the first real pull.
        headers = {"apikey": self.settings.require("client_export_api_key")}

        log.info("intake.export_pull", **window.params())
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url, params=window.params(), headers=headers)

        if response.status_code != 200:
            raise ExportError(
                f"Export returned {response.status_code}: {response.text[:300]}"
            )
        rows = _unwrap(response.json())
        log.info("intake.export_pulled", rows=len(rows))
        return rows

    @staticmethod
    def from_fixture(path: str | Path) -> list[dict[str, Any]]:
        """Load a saved export response. Same shape handling as a live pull."""
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = _unwrap(payload)
        log.info("intake.export_fixture", path=str(path), rows=len(rows))
        return rows


def iter_rows(rows: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    yield from rows
