"""One unattended run, start to finish: pull the window, then drive every job
to a terminal state.

This is what the twice-daily timer invokes, and the only entry point expected
to run with nobody watching. It differs from `drain` in three ways that only
matter when there is no human in the room:

  * **It takes a lock.** The noon run must not start on top of a midnight run
    that is still rendering. Two cycles at once would not corrupt anything —
    claiming is `SKIP LOCKED` and readiness is computed — but they would double
    the concurrent load on a paid vendor for no gain, and make the logs
    unreadable at exactly the moment someone is trying to work out what broke.

  * **It waits.** `drain` stops the moment a sweep moves nothing, which for an
    async stage means "the video is still rendering". Under a timer that would
    submit every paid render and exit before collecting any of them, leaving
    the money spent and the results uncollected until the next cycle.

  * **It stops at a deadline** instead of running until the next timer fires.

Throughput is not what the deadline limits, and the arithmetic is worth writing
down because it looks otherwise. A render takes ~8.6 minutes, but renders do not
queue behind each other - image and video submit and release, so a hundred of
them are in flight at the vendor at once and the wall clock is the LONGEST one,
not the sum. What is serial is only our own work, measured end to end on a real
job at **~32 seconds per video**, nearly all of it the ffmpeg composite:

    prep 1s | audio <1s | image submit <1s | video submit <1s
    | composite 28s | checks 1s | publish 1s | deliver 1s

At 8 hours that is roughly 900 videos per cycle. The binding limit at a hundred
a day is `vendor_limits.daily_cost_cap_usd` - $50 on kie is about 44 videos -
and that is a deliberate guard, not an accident to route around.

Nothing here is a new source of truth. Readiness is still computed from
`stage_runs`, so a cycle killed at any point — deadline, deploy, instance
reboot — resumes from the next one with no bookkeeping to repair. That is the
property that makes it safe to run this unattended at all.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .common import db
from .common.events import record_event
from .common.logging import get_logger
from .config import get_settings
from .stages.base import DEFAULT_PLAN

log = get_logger(__name__)

#: Lock key within `db.ADVISORY_LOCK_NAMESPACE`. One cycle at a time, across
#: every host pointed at this database — not just this machine, which is what a
#: pidfile or a flock would have given us.
CYCLE_LOCK_KEY = 1

#: Longest we will sit idle waiting for a retry that is not due yet. A backed-off
#: run has a known due time and could be slept to exactly, but waking up
#: periodically keeps the log showing a live process.
MAX_SLEEP_S = 300


def intake_window(lookback_days: int, *, today: date | None = None) -> tuple[date, date]:
    """The window to pull, on the client's calendar.

    Anchored in the client's timezone, not the server's: EC2 runs UTC, and at
    00:00 IST it is still yesterday there — a window built from the server's own
    date would miss the day that has just started, every single midnight run.

    Both ends are deliberately loose. `from` reaches back because a submission
    can land after its window was already pulled, and `to` reaches forward one
    day because we have not confirmed which timezone the API filters on; a
    future `to` is accepted and simply returns nothing extra. Overlap is free —
    intake dedupes on the client's own row id — and a missed row is not
    recoverable by anything downstream, so the trade only runs one way.
    """
    tz = ZoneInfo(get_settings().export_timezone)
    anchor = today or datetime.now(tz).date()
    return anchor - timedelta(days=lookback_days), anchor + timedelta(days=1)


def outstanding_work() -> dict[str, Any]:
    """What is still owed: queued runs, in-flight vendor tasks, next retry due.

    Deliberately not filtered by job status. A job can be marked `failed` on one
    stage while another is still running at a vendor, and that task still has to
    be collected — the submit was already billed (invariant 24), so walking away
    from it loses the money AND the evidence of what it cost.
    """
    row = db.fetch_one(
        """
        SELECT count(*) FILTER (WHERE status = 'running')              AS in_flight,
               count(*) FILTER (WHERE status IN ('pending', 'claimed')) AS queued,
               min(next_attempt_at) FILTER (WHERE status = 'pending')   AS next_due
          FROM stage_runs
         WHERE status IN ('pending', 'claimed', 'running');
        """
    )
    queued = int(row["queued"]) if row else 0
    in_flight = int(row["in_flight"]) if row else 0
    return {
        "queued": queued,
        "in_flight": in_flight,
        "next_due": row["next_due"] if row else None,
        "total": queued + in_flight,
    }


def next_sleep(left: dict[str, Any], interval_s: int, remaining_s: float) -> float:
    """How long to wait before the next pass.

    Anything in flight at a vendor is polled at the interval. If the only thing
    left is a run behind a retry backoff, its due time is known, so waiting for
    it beats waking every interval to find it still not due.
    """
    wait = float(interval_s)
    due = left.get("next_due")
    if left["in_flight"] == 0 and due is not None:
        delta = (due - datetime.now(UTC)).total_seconds()
        wait = max(wait, min(delta, MAX_SLEEP_S))
    return max(1.0, min(wait, remaining_s))


def _schedule_all() -> int:
    """Enqueue every ready stage across every unfinished job.

    Intake creates jobs but does not schedule them, and a previous cycle may
    have stopped at its deadline with work left. This is the one place that
    catches up, and it is why the cycle needs no memory of what the last one did.
    """
    from .orchestrator import advance_job, schedule_ready

    enqueued = 0
    for row in db.fetch_all(
        "SELECT id FROM jobs WHERE status NOT IN ('completed', 'cancelled');"
    ):
        enqueued += len(schedule_ready(str(row["id"])))
        advance_job(str(row["id"]))
    return enqueued


def _reopen_suppressed_deliveries() -> list[str]:
    """Re-deliver jobs that finished while DELIVERY_ENABLED was false.

    A suppressed delivery SUCCEEDS the stage. It has to - the video is made and
    published, and failing the stage would park a finished job as `failed`
    forever. It records `dry_run` and posts nothing.

    Which means the day delivery is switched on, every video made before that
    day is already marked delivered and will never be sent. The job is
    `completed`, so nothing schedules it; the input hash covers the CDN url and
    the phone, and neither changed. Without this the backlog is silently
    stranded, and the failure looks like nothing at all.

    Only runs when delivery is enabled. The stage it re-runs is free, and the
    client stores {phone, videoLink} idempotently (invariant 14) - so a repeat
    post is harmless where a missed one is a video nobody ever gets.
    """
    if not get_settings().delivery_enabled:
        return []

    from .common.errors import PipelineError
    from .orchestrator import redo_stage
    from .stages.base import PipelineStage

    rows = db.fetch_all(
        """
        SELECT DISTINCT ON (job_id) job_id
          FROM stage_runs
         WHERE stage = 'deliver' AND status = 'succeeded'
           AND params -> 'result' -> 'meta' ->> 'dry_run' = 'true'
         ORDER BY job_id, finished_at DESC;
        """
    )
    reopened: list[str] = []
    for row in rows:
        job_id = str(row["job_id"])
        try:
            redo_stage(job_id, PipelineStage.DELIVER)
        except PipelineError as exc:
            # A deliver run is already in flight for this job. Nothing to fix -
            # the next cycle sees it either delivered or failed.
            log.warning("cycle.redeliver_skipped", job_id=job_id, reason=str(exc)[:200])
            continue
        reopened.append(job_id)

    if reopened:
        log.warning("cycle.redelivering", jobs=len(reopened))
        record_event("cycle.redelivering", level="warning", jobs=len(reopened))
    return reopened


def run_cycle(
    *,
    lookback_days: int = 1,
    deadline_minutes: int = 480,
    interval_s: int = 60,
    fetch: bool = True,
) -> dict[str, Any]:
    """Pull, then work until everything is terminal or the deadline is reached."""
    from .intake.runner import run_intake
    from .orchestrator import drain_stage, poll_once, reap

    started_at = datetime.now(UTC)
    clock_start = time.monotonic()
    deadline = clock_start + deadline_minutes * 60

    summary: dict[str, Any] = {
        "started_at": started_at,
        "skipped": False,
        "deadline_hit": False,
        "passes": 0,
        "stage_runs": {},
    }

    with db.advisory_lock(CYCLE_LOCK_KEY) as acquired:
        if not acquired:
            # Not an error. The previous cycle is still rendering, which on a
            # large batch is the expected state, and the work it is doing is
            # the same work this one would have picked up.
            log.warning("cycle.already_running")
            record_event("cycle.skipped", level="warning", reason="lock_held")
            return {**summary, "skipped": True, "reason": "another cycle holds the lock"}

        log.info("cycle.start", deadline_minutes=deadline_minutes, fetch=fetch)

        if fetch:
            from_date, to_date = intake_window(lookback_days)
            summary["window"] = [str(from_date), str(to_date)]
            try:
                counters = run_intake(from_date=from_date, to_date=to_date)
                summary["intake"] = counters.as_dict()
            except Exception as exc:  # noqa: BLE001 - the cycle is the boundary
                # A dead export API is not a reason to abandon the jobs already
                # in the database. Yesterday's renders still need finishing;
                # the non-zero exit is what makes the failed pull visible.
                summary["intake_error"] = str(exc)[:500]
                log.error("cycle.intake_failed", error=str(exc))
                record_event("cycle.intake_failed", level="error", error=str(exc)[:500])

        summary["redelivering"] = len(_reopen_suppressed_deliveries())
        summary["scheduled"] = _schedule_all()

        totals: dict[str, int] = summary["stage_runs"]
        while True:
            summary["passes"] += 1
            reap()

            for stage in DEFAULT_PLAN:
                n = drain_stage(stage)
                if n:
                    totals[str(stage)] = totals.get(str(stage), 0) + n

            completed = poll_once()
            if completed:
                totals["poll"] = totals.get("poll", 0) + completed

            left = outstanding_work()
            log.info("cycle.pass", pass_no=summary["passes"], **{
                k: v for k, v in left.items() if k != "next_due"
            })
            if left["total"] == 0:
                break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Not a failure of any job — everything is still queued and the
                # next cycle resumes it. It IS a signal that a batch is taking
                # longer than the gap between runs, which is worth waking up for.
                summary["deadline_hit"] = True
                summary["outstanding"] = {k: left[k] for k in ("queued", "in_flight")}
                log.warning("cycle.deadline", **summary["outstanding"])
                record_event("cycle.deadline", level="warning", **summary["outstanding"])
                break

            time.sleep(next_sleep(left, interval_s, remaining))

        summary["elapsed_s"] = round(time.monotonic() - clock_start, 1)
        summary["jobs"] = {
            r["status"]: r["n"]
            for r in db.fetch_all("SELECT status, count(*) AS n FROM jobs GROUP BY status;")
        }
        log.info("cycle.done", **{k: summary[k] for k in ("passes", "elapsed_s", "jobs")})
        record_event(
            "cycle.done",
            passes=summary["passes"],
            elapsed_s=summary["elapsed_s"],
            deadline_hit=summary["deadline_hit"],
        )

    return summary
