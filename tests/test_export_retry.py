"""The export pull survives a blip, and does not retry a real answer.

From EC2 on 2026-09-22 the client's API timed out once and cut a 270 KB body
off at 7.6 KB once, then answered in 0.3s. Without a retry, each of those threw
away a whole cycle's intake. In-memory transport only - nothing leaves the box.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from castrol_pipeline.config import get_settings
from castrol_pipeline.intake import export_client as ec
from castrol_pipeline.intake.export_client import (
    KNOWN_COLUMNS,
    RETRY_WAITS_S,
    ExportClient,
    ExportError,
    ExportWindow,
)

CSV = (",".join(KNOWN_COLUMNS) + "\n").encode("utf-8-sig")
WINDOW = ExportWindow(from_date=date(2026, 9, 15), to_date=date(2026, 9, 23))


def _client(script: list, slept: list[float]) -> tuple[ExportClient, list[int]]:
    """Each entry: an exception to raise, or an HTTP status to answer with."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        step = script[min(len(calls), len(script)) - 1]
        if isinstance(step, Exception):
            raise step
        return httpx.Response(step, content=CSV if step == 200 else b"nope")

    settings = get_settings().model_copy(
        update={"client_export_url": "https://export.test/x", "client_export_api_key": "k"}
    )
    return ExportClient(settings, transport=httpx.MockTransport(handler),
                        sleep=slept.append), calls


@pytest.mark.parametrize(
    "blip",
    [
        httpx.ReadTimeout("The read operation timed out"),
        httpx.RemoteProtocolError("peer closed connection without sending complete message body"),
        httpx.ConnectError("connection refused"),
    ],
    ids=["read-timeout", "cut-off-body", "connect"],
)
def test_a_transport_blip_is_retried_then_succeeds(blip) -> None:
    slept: list[float] = []
    client, calls = _client([blip, 200], slept)
    assert client.fetch(WINDOW).http_status == 200
    assert len(calls) == 2
    assert slept == [RETRY_WAITS_S[0]]


def test_a_server_error_is_retried() -> None:
    slept: list[float] = []
    client, calls = _client([503, 502, 200], slept)
    client.fetch(WINDOW)
    assert len(calls) == 3


@pytest.mark.parametrize("status", [401, 403, 404])
def test_a_real_answer_is_not_retried(status: int) -> None:
    """A wrong key does not become right by asking again."""
    slept: list[float] = []
    client, calls = _client([status], slept)
    with pytest.raises(ExportError, match=str(status)):
        client.fetch(WINDOW)
    assert len(calls) == 1 and slept == []


def test_gives_up_after_the_last_attempt_with_the_real_error() -> None:
    slept: list[float] = []
    client, calls = _client([httpx.ReadTimeout("timed out")], slept)
    with pytest.raises(httpx.ReadTimeout):
        client.fetch(WINDOW)
    assert len(calls) == len(RETRY_WAITS_S) + 1
    assert slept == list(RETRY_WAITS_S)


def test_stays_well_inside_the_rate_limit() -> None:
    # 100 requests per 900s; one pull may never be a meaningful share of it.
    assert len(ec.RETRY_WAITS_S) + 1 <= 5
