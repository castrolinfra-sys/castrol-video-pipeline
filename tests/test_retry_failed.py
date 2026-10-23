"""`castrol retry-failed`: putting terminal failures back in the queue in bulk.

No database. What matters here is the decision per job: re-enqueue with the
failure hold lifted, recompute the job's status, and count only the jobs that
actually went back in - never claim a job was re-queued when nothing was.
"""

from __future__ import annotations

from castrol_pipeline import orchestrator
from castrol_pipeline.stages.base import PipelineStage


def _patch(monkeypatch, enqueues: dict[str, list[PipelineStage]]):
    calls: dict[str, list] = {"schedule": [], "advance": [], "events": []}

    def schedule_ready(job_id, *, retry_failed=False):
        calls["schedule"].append((job_id, retry_failed))
        return enqueues.get(job_id, [])

    monkeypatch.setattr(orchestrator, "schedule_ready", schedule_ready)
    monkeypatch.setattr(orchestrator, "advance_job", lambda j: calls["advance"].append(j))
    monkeypatch.setattr(
        orchestrator, "record_event", lambda name, **kw: calls["events"].append((name, kw))
    )
    return calls


def test_every_job_is_scheduled_with_the_failure_hold_lifted(monkeypatch):
    calls = _patch(monkeypatch, {"a": [PipelineStage.VIDEO], "b": [PipelineStage.VIDEO]})
    orchestrator.retry_failed_jobs(["a", "b"])
    # Without retry_failed=True, failure_holds keeps every one of them failed
    # and the command would silently do nothing (invariant 34).
    assert calls["schedule"] == [("a", True), ("b", True)]


def test_only_jobs_that_went_back_in_are_counted_and_advanced(monkeypatch):
    # "b" enqueued nothing - its run is already in flight, or it was fixed in
    # between. It must not be reported as re-queued, or marked running.
    calls = _patch(monkeypatch, {"a": [PipelineStage.VIDEO], "b": []})
    out = orchestrator.retry_failed_jobs(["a", "b"])
    assert out == {"requested": 2, "requeued": 1, "not_requeued": ["b"]}
    assert calls["advance"] == ["a"]
    assert [kw["job_id"] for _, kw in calls["events"]] == ["a"]


def test_nothing_requested_is_nothing_done(monkeypatch):
    calls = _patch(monkeypatch, {})
    assert orchestrator.retry_failed_jobs([]) == {
        "requested": 0, "requeued": 0, "not_requeued": [],
    }
    assert calls["schedule"] == []
