"""Drive ONE job to a terminal state: `castrol run <ref>`.

The cycle works everything; this works a single mechanic, for the moments a
person is holding one identifier - a client asking after one number, a failure
somebody wants to retry now rather than at midnight, a sample to prove a fix.

It is the cycle's wait loop with every claim and poll narrowed to one job
(`claim_stage_run(job_id=...)`, `poll_once(job_id=...)`), so it never picks up,
and never pays for, anybody else's queued work.

Safe to kill and safe to repeat, for the same reason the cycle is: nothing here
is state. Readiness comes from `stage_runs`, a submitted render keeps going at
the vendor, and running the same command again - after Ctrl-C, a dropped SSM
session or an instance reboot - carries on from wherever the rows say.

It does NOT take the cycle's lock, and running alongside a cycle is fine:
claiming is SKIP LOCKED, so a run is executed by exactly one of them. It takes
a per-job lock instead, so two people running the same job do not both poll it.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

from .common import db
from .common.events import record_event
from .common.logging import get_logger
from .config import get_settings
from .cycle import CYCLE_LOCK_KEY, next_sleep
from .stages.base import DEFAULT_PLAN, PipelineStage

log = get_logger(__name__)

#: The stages that reach a paid vendor (invariant 3). Used only to warn before
#: a run - the budget guard, not this set, is what actually stops spend.
PAID_STAGES: frozenset[PipelineStage] = frozenset(
    {PipelineStage.AUDIO, PipelineStage.IMAGE, PipelineStage.VIDEO}
)


def job_lock_key(job_id: str) -> int:
    """A per-job advisory lock key in int4, kept clear of the cycle's own key."""
    return (uuid.UUID(job_id).int % (2**31 - 1 - (CYCLE_LOCK_KEY + 1))) + CYCLE_LOCK_KEY + 1


def stage_table(job_id: str) -> list[dict[str, Any]]:
    """The newest run per planned stage - what `run` shows before it starts."""
    rows = db.fetch_all(
        """
        SELECT DISTINCT ON (stage) stage, status, attempts, error_code,
               cost_usd, billed_seconds, next_attempt_at, finished_at
          FROM stage_runs
         WHERE job_id = %(job)s AND status <> 'skipped'
         ORDER BY stage, created_at DESC;
        """,
        {"job": job_id},
    )
    by_stage = {str(r["stage"]): dict(r) for r in rows}
    return [by_stage.get(str(s), {"stage": str(s), "status": None}) for s in DEFAULT_PLAN]


def paid_stages_left(table: list[dict[str, Any]], *, retry_failed: bool) -> list[str]:
    """Paid stages this run may submit. Pure.

    A stage with no succeeded run will run once its inputs are ready - unless
    its last word is a failure and we are not retrying, in which case it is
    blocked and cannot spend. A `running` stage was already submitted and
    billed at submit (invariant 24); polling it only collects.
    """
    out = []
    for row in table:
        if PipelineStage(row["stage"]) not in PAID_STAGES:
            continue
        if row["status"] in ("succeeded", "running"):
            continue
        if row["status"] == "failed" and not retry_failed:
            continue
        out.append(row["stage"])
    return out


def job_outstanding(job_id: str) -> dict[str, Any]:
    """`cycle.outstanding_work`, for one job."""
    row = db.fetch_one(
        """
        SELECT count(*) FILTER (WHERE status = 'running')              AS in_flight,
               count(*) FILTER (WHERE status IN ('pending', 'claimed')) AS queued,
               min(next_attempt_at) FILTER (WHERE status = 'pending')   AS next_due
          FROM stage_runs
         WHERE job_id = %(job)s AND status IN ('pending', 'claimed', 'running');
        """,
        {"job": job_id},
    )
    queued = int(row["queued"]) if row else 0
    in_flight = int(row["in_flight"]) if row else 0
    return {
        "queued": queued,
        "in_flight": in_flight,
        "next_due": row["next_due"] if row else None,
        "total": queued + in_flight,
    }


def run_job(
    job_id: str,
    *,
    retry_failed: bool = False,
    timeout_minutes: int = 150,
    interval_s: int = 30,
) -> dict[str, Any]:
    """Schedule, work and poll one job until nothing of it is owed, or timeout.

    The default timeout sits past `vendor_task_timeout_s` (2h), so an unattended
    `run` ends on a terminal status rather than on "still rendering". Timing out
    loses nothing either way: in-flight renders keep going at the vendor, and
    the next `run` or cycle collects them.
    """
    from .orchestrator import (
        advance_job,
        drain_stage,
        poll_once,
        reap,
        schedule_ready,
    )

    clock_start = time.monotonic()
    deadline = clock_start + timeout_minutes * 60
    summary: dict[str, Any] = {
        "job_id": job_id,
        "started_at": datetime.now(UTC),
        "retry_failed": retry_failed,
        "stage_mode": get_settings().effective_stage_mode,
        "passes": 0,
        "stage_runs": {},
        "timed_out": False,
    }

    with db.advisory_lock(job_lock_key(job_id)) as acquired:
        if not acquired:
            log.warning("job.run_already_running", job_id=job_id)
            return {**summary, "skipped": True, "reason": "another `run` holds this job"}

        record_event(
            "job.manual_run",
            job_id=job_id,
            level="warning" if retry_failed else "info",
            retry_failed=retry_failed,
        )
        summary["enqueued"] = [str(x) for x in schedule_ready(job_id, retry_failed=retry_failed)]
        advance_job(job_id)

        totals: dict[str, int] = summary["stage_runs"]
        while True:
            summary["passes"] += 1
            reap()
            for stage in DEFAULT_PLAN:
                n = drain_stage(stage, job_id=job_id)
                if n:
                    totals[str(stage)] = totals.get(str(stage), 0) + n
            completed = poll_once(job_id=job_id)
            if completed:
                totals["poll"] = totals.get("poll", 0) + completed

            left = job_outstanding(job_id)
            log.info("job.run_pass", job_id=job_id, pass_no=summary["passes"],
                     queued=left["queued"], in_flight=left["in_flight"])
            if left["total"] == 0:
                break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                summary["timed_out"] = True
                summary["outstanding"] = {k: left[k] for k in ("queued", "in_flight")}
                log.warning("job.run_timeout", job_id=job_id, **summary["outstanding"])
                break
            time.sleep(next_sleep(left, interval_s, remaining))

        job = db.fetch_one(
            "SELECT status, current_stage, failure_reason FROM jobs WHERE id = %(id)s;",
            {"id": job_id},
        )
        summary["status"] = job["status"] if job else None
        summary["failure_reason"] = job["failure_reason"] if job else None
        summary["elapsed_s"] = round(time.monotonic() - clock_start, 1)
        record_event(
            "job.manual_run_done",
            job_id=job_id,
            level="error" if summary["status"] == "failed" else "info",
            status=summary["status"],
            passes=summary["passes"],
            elapsed_s=summary["elapsed_s"],
            timed_out=summary["timed_out"],
        )

    return summary
