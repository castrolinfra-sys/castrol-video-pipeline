"""Losing OUR connection while collecting a result must never cost a render.

`poll_once` used to catch every exception and mark the run failed with a
retry delay — and the retry path re-SUBMITS. So a render the vendor had
already finished and billed was discarded and paid for again whenever the
fetch blipped. It happened twice on one job on 2026-09-22 (WinError 10054).

A transport error is not a verdict: the vendor task is untouched, so the run
stays `running` and the next pass polls the same task id. A real rejection
from the vendor is still a failure.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from castrol_pipeline import orchestrator
from castrol_pipeline.common.errors import VendorRejected
from castrol_pipeline.stages.base import PipelineStage


def _drive(monkeypatch, raise_exc: BaseException) -> list[dict]:
    failed: list[dict] = []

    class _Stage:
        is_async = True

        def poll(self, task_id, ctx):
            raise raise_exc

    run = {
        "id": "run-1",
        "job_id": "00000000-0000-0000-0000-000000000001",
        "stage": str(PipelineStage.VIDEO),
        "vendor_task_id": "task-1",
        "attempts": 1,
        "started_at": datetime.now(UTC),   # well inside the overdue limit
    }
    monkeypatch.setattr(orchestrator, "get_stage_registry",
                        lambda: {PipelineStage.VIDEO: _Stage()})
    monkeypatch.setattr(orchestrator.db, "fetch_all", lambda *a, **k: [run])
    monkeypatch.setattr(orchestrator.db, "mark_failed",
                        lambda run_id, **kw: failed.append({"id": run_id, **kw}))
    monkeypatch.setattr(orchestrator, "load_context", lambda job_id: object())
    monkeypatch.setattr(orchestrator, "record_event", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "schedule_ready", lambda *a, **k: [])
    monkeypatch.setattr(orchestrator, "advance_job", lambda *a, **k: None)

    orchestrator.poll_once()
    return failed


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadError("connection reset"),
        httpx.ConnectTimeout("timed out"),
        httpx.RemoteProtocolError("peer closed"),
        ConnectionResetError(10054, "An existing connection was forcibly closed"),
        TimeoutError("read timed out"),
    ],
    ids=["read", "connect-timeout", "protocol", "winerror-10054", "timeout"],
)
def test_a_dropped_connection_leaves_the_run_in_flight(monkeypatch, exc):
    assert _drive(monkeypatch, exc) == [], (
        "a transport error marked the run failed - the retry would resubmit "
        "and pay for a render the vendor may already have finished"
    )


def test_a_vendor_rejection_still_fails_the_run(monkeypatch):
    failed = _drive(monkeypatch, VendorRejected("video provider fail: 500"))
    assert len(failed) == 1
