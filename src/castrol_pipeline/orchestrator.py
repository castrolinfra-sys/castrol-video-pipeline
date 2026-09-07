"""The orchestrator: readiness, claiming, retries, idempotency, job state.

This module owns the two things stages are forbidden to touch:
  * writes to `jobs`
  * the retry decision

Stage readiness is COMPUTED from `stage_runs`, never read from
`jobs.current_stage` — that column is a display convenience for the panel and
is never used for routing. Recomputing it is what makes a killed batch resumable
without bookkeeping.
"""

from __future__ import annotations

import os
import socket
from dataclasses import asdict
from typing import Any

from psycopg.types.json import Jsonb

from .common import db
from .common.errors import PipelineError, StageErrorCode
from .common.logging import get_logger, job_context
from .config import get_settings
from .stages.base import (
    DEFAULT_PLAN,
    STAGE_DEPENDENCIES,
    AsyncSubmission,
    JobContext,
    PipelineStage,
    StageResult,
)

log = get_logger(__name__)


def worker_identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def get_stage_registry() -> dict[PipelineStage, Any]:
    """Which implementations are in play.

    Stub mode is what makes the orchestrator provable without spending money.
    Real stages land here in build order 1.5.
    """
    from .stages.stubs import STUB_STAGES

    if get_settings().use_stub_stages:
        return STUB_STAGES
    raise NotImplementedError(
        "Real stages are not implemented yet (build order 1.5). "
        "Set USE_STUB_STAGES=true to drive the pipeline with stubs."
    )


# --------------------------------------------------------------- retry policy --

#: Exponential backoff between attempts, in seconds, capped so a stuck vendor
#: does not push a retry past the nightly window.
RETRY_BASE_S = 30
RETRY_CAP_S = 900


def backoff_seconds(attempts: int) -> int:
    return min(RETRY_BASE_S * (2 ** max(attempts - 1, 0)), RETRY_CAP_S)


def retry_delay_for(attempts: int, code: StageErrorCode) -> int | None:
    """None means terminal: do not schedule another attempt."""
    settings = get_settings()
    if code == StageErrorCode.BUDGET_EXHAUSTED:
        # A hard stop for the day, not a throttle. Retrying this is exactly the
        # bug the budget guard exists to stop.
        return None
    if attempts >= settings.stage_max_attempts:
        return None
    return backoff_seconds(attempts)


# ------------------------------------------------------------------ context --


def _result_of(run: dict[str, Any]) -> dict[str, Any]:
    params = run.get("params") or {}
    return params.get("result", {}) if isinstance(params, dict) else {}


def succeeded_runs(job_id: str) -> dict[str, dict[str, Any]]:
    """The latest succeeded run per stage, as {stage: result-payload}."""
    rows = db.fetch_all(
        """
        SELECT DISTINCT ON (stage) stage, output_key, params, finished_at
          FROM stage_runs
         WHERE job_id = %(job_id)s AND status = 'succeeded'
         ORDER BY stage, finished_at DESC;
        """,
        {"job_id": job_id},
    )
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = dict(_result_of(row))
        payload.setdefault("output_key", row["output_key"])
        out[str(row["stage"])] = payload
    return out


def load_context(job_id: str) -> JobContext:
    row = db.fetch_one(
        """
        SELECT j.id AS job_id, j.submission_id, j.script_version, j.voice_id,
               j.plate_id,
               s.raw, s.phone_e164, s.user_name, s.workshop_name,
               s.address_normalized, s.image_url_raw,
               s.background_choice, s.outfit_choice
          FROM jobs j
          JOIN submissions s ON s.id = j.submission_id
         WHERE j.id = %(job_id)s;
        """,
        {"job_id": job_id},
    )
    if row is None:
        raise LookupError(f"No job {job_id}")

    locality, _, city = (row["address_normalized"] or "").partition(", ")
    return JobContext(
        job_id=str(row["job_id"]),
        submission_id=str(row["submission_id"]),
        script_version=row["script_version"],
        voice_id=row["voice_id"],
        plate_id=str(row["plate_id"]) if row["plate_id"] else None,
        user_name=row["user_name"] or "",
        workshop_name=row["workshop_name"] or "",
        locality=locality,
        city=city,
        phone_e164=row["phone_e164"] or "",
        uniform_id=row["outfit_choice"] or "",
        background_id=row["background_choice"] or "",
        image_url_raw=row["image_url_raw"] or "",
        raw=row["raw"] or {},
        upstream=succeeded_runs(str(row["job_id"])),
    )


# ---------------------------------------------------------------- readiness --


def ready_stages(job_id: str, done: dict[str, dict[str, Any]] | None = None) -> list[PipelineStage]:
    """Stages whose dependencies have all succeeded and which are not done."""
    done = succeeded_runs(job_id) if done is None else done
    ready: list[PipelineStage] = []
    for stage in DEFAULT_PLAN:
        if str(stage) in done:
            continue
        if all(str(dep) in done for dep in STAGE_DEPENDENCIES[stage]):
            ready.append(stage)
    return ready


def schedule_ready(job_id: str) -> list[PipelineStage]:
    """Enqueue a pending run for every ready stage. Idempotent.

    Two guards make double-work impossible rather than merely unlikely:
      * a stage whose exact input_hash already succeeded is skipped outright
      * the partial unique index rejects a second in-flight run per (job, stage)
    """
    registry = get_stage_registry()
    ctx = load_context(job_id)
    enqueued: list[PipelineStage] = []

    for stage in ready_stages(job_id, ctx.upstream):
        impl = registry.get(stage)
        if impl is None:
            continue
        input_hash = impl.input_hash(ctx)

        if db.find_succeeded_run(job_id, str(stage), input_hash):
            # Already done at this exact input. Nothing to do — this is what
            # makes a re-run after a code change redo only what actually moved.
            continue

        if db.enqueue_stage_run(job_id, str(stage), input_hash):
            enqueued.append(stage)

    if enqueued:
        log.info("orchestrator.scheduled", job_id=job_id, stages=[str(s) for s in enqueued])
    return enqueued


# ------------------------------------------------------------- persistence --


def _persist_result(ctx: JobContext, run: dict[str, Any], result: StageResult) -> None:
    payload = {k: v for k, v in asdict(result).items() if v is not None}
    payload.pop("asset_kind", None)

    if result.asset_kind and result.output_key and result.sha256:
        db.execute(
            """
            INSERT INTO assets (job_id, kind, s3_key, sha256, bytes,
                                duration_ms, width, height, meta)
            VALUES (%(job_id)s, %(kind)s, %(key)s, %(sha)s, %(bytes)s,
                    %(dur)s, %(w)s, %(h)s, %(meta)s)
            ON CONFLICT (s3_key) DO NOTHING;
            """,
            {
                "job_id": ctx.job_id,
                "kind": str(result.asset_kind),
                "key": result.output_key,
                "sha": result.sha256,
                "bytes": result.bytes,
                "dur": result.duration_ms,
                "w": result.width,
                "h": result.height,
                "meta": Jsonb(result.meta or {}),
            },
        )

    # Checks are written regardless of outcome, and are non-blocking this release.
    for check in (result.meta or {}).get("checks", []):
        db.execute(
            """
            INSERT INTO checks (job_id, check_name, passed, score, details)
            VALUES (%(job_id)s, %(name)s, %(passed)s, %(score)s, %(details)s);
            """,
            {
                "job_id": ctx.job_id,
                "name": check.get("check_name", "unnamed"),
                "passed": bool(check.get("passed")),
                "score": check.get("score"),
                "details": Jsonb(check.get("details", {})),
            },
        )

    db.mark_succeeded(
        str(run["id"]),
        output_key=result.output_key,
        params={"result": payload, "vendor_params": result.params or {}},
    )


def _record_delivery(ctx: JobContext, result: StageResult) -> None:
    cdn = (result.meta or {}).get("stub_cdn_url") or (result.meta or {}).get("cdn_url")
    if not cdn:
        return
    db.execute(
        """
        INSERT INTO deliveries (job_id, phone_e164, cdn_url, attempts, posted_at, response_code)
        VALUES (%(job_id)s, %(phone)s, %(cdn)s, %(attempts)s, %(posted)s, %(code)s)
        ON CONFLICT (job_id) DO UPDATE
           SET cdn_url = EXCLUDED.cdn_url,
               attempts = deliveries.attempts + 1,
               posted_at = EXCLUDED.posted_at,
               response_code = EXCLUDED.response_code;
        """,
        {
            "job_id": ctx.job_id,
            "phone": ctx.phone_e164,
            "cdn": cdn,
            "attempts": 1,
            "posted": (result.meta or {}).get("posted_at"),
            "code": (result.meta or {}).get("response_code"),
        },
    )


def advance_job(job_id: str) -> None:
    """Recompute job status from stage_runs. The only writer of `jobs`."""
    done = succeeded_runs(job_id)

    if str(PipelineStage.DELIVER) in done:
        db.execute(
            """
            UPDATE jobs SET status = 'completed', current_stage = 'deliver',
                            completed_at = coalesce(completed_at, now())
             WHERE id = %(id)s;
            """,
            {"id": job_id},
        )
        return

    dead = db.fetch_one(
        """
        SELECT stage, error_code, error_message FROM stage_runs
         WHERE job_id = %(id)s AND status = 'failed'
         ORDER BY finished_at DESC LIMIT 1;
        """,
        {"id": job_id},
    )
    if dead:
        # No partial delivery, ever: a job that did not complete every stage is
        # never POSTed. It sits here and in the panel, and nowhere else.
        db.execute(
            """
            UPDATE jobs SET status = 'failed',
                            failure_reason = %(reason)s
             WHERE id = %(id)s;
            """,
            {"id": job_id, "reason": f"{dead['stage']}: {dead['error_code']}"},
        )
        return

    nxt = ready_stages(job_id, done)
    db.execute(
        """
        UPDATE jobs SET status = 'running', current_stage = %(stage)s
         WHERE id = %(id)s AND status <> 'completed';
        """,
        {"id": job_id, "stage": str(nxt[0]) if nxt else str(PipelineStage.DELIVER)},
    )


# ------------------------------------------------------------- worker loop --


def execute_one(stage: PipelineStage, worker: str | None = None) -> bool:
    """Claim and execute a single run of `stage`. False if the queue was empty."""
    worker = worker or worker_identity()
    run = db.claim_stage_run(str(stage), worker)
    if run is None:
        return False

    job_id = str(run["job_id"])
    run_id = str(run["id"])
    impl = get_stage_registry()[stage]

    with job_context(job_id, stage=str(stage), run_id=run_id):
        log.info("stage.claimed", attempts=run["attempts"])
        try:
            ctx = load_context(job_id)
            outcome = impl.run(ctx)

            if isinstance(outcome, AsyncSubmission):
                # Submit and release. The poller reconciles. A worker blocked on
                # a 4-minute vendor poll is a worker not doing the other 200 jobs.
                db.mark_running(
                    run_id,
                    vendor=outcome.vendor,
                    vendor_task_id=outcome.vendor_task_id,
                    model_id=outcome.model_id,
                )
                log.info("stage.submitted", vendor_task_id=outcome.vendor_task_id)
                return True

            _persist_result(ctx, run, outcome)
            if stage == PipelineStage.DELIVER:
                _record_delivery(ctx, outcome)
            log.info("stage.succeeded", output_key=outcome.output_key)

        except Exception as exc:  # noqa: BLE001 - the orchestrator is the boundary
            code = exc.code if isinstance(exc, PipelineError) else StageErrorCode.INTERNAL
            delay = retry_delay_for(int(run["attempts"]), code)
            db.mark_failed(
                run_id,
                error_code=str(code),
                error_message=str(exc)[:2000],
                retry_in_seconds=delay,
            )
            log.error(
                "stage.failed",
                error_code=str(code),
                attempts=run["attempts"],
                retry_in_seconds=delay,
                error=str(exc)[:500],
            )
        finally:
            schedule_ready(job_id)
            advance_job(job_id)

    return True


def drain_stage(stage: PipelineStage, *, limit: int = 1000) -> int:
    processed = 0
    while processed < limit and execute_one(stage):
        processed += 1
    return processed


# ------------------------------------------------------------------ poller --


def poll_once(*, limit: int = 100) -> int:
    """Reconcile in-flight async vendor tasks. Returns how many completed."""
    registry = get_stage_registry()
    rows = db.fetch_all(
        """
        SELECT * FROM stage_runs
         WHERE status = 'running' AND vendor_task_id IS NOT NULL
         ORDER BY started_at
         LIMIT %(limit)s;
        """,
        {"limit": limit},
    )

    completed = 0
    for run in rows:
        stage = PipelineStage(str(run["stage"]))
        impl = registry.get(stage)
        if impl is None or not getattr(impl, "is_async", False):
            continue

        job_id = str(run["job_id"])
        with job_context(job_id, stage=str(stage), run_id=str(run["id"])):
            try:
                ctx = load_context(job_id)
                result = impl.poll(str(run["vendor_task_id"]), ctx)
                if result is None:
                    continue  # still in flight
                _persist_result(ctx, run, result)
                completed += 1
                log.info("stage.polled_complete", output_key=result.output_key)
            except Exception as exc:  # noqa: BLE001
                code = exc.code if isinstance(exc, PipelineError) else StageErrorCode.INTERNAL
                delay = retry_delay_for(int(run["attempts"]), code)
                db.mark_failed(
                    str(run["id"]),
                    error_code=str(code),
                    error_message=str(exc)[:2000],
                    retry_in_seconds=delay,
                )
                log.error("stage.poll_failed", error_code=str(code), error=str(exc)[:500])
            finally:
                schedule_ready(job_id)
                advance_job(job_id)

    return completed


def reap() -> int:
    """Return stuck claims to the queue. Without this a worker OOM parks a job."""
    n = db.reap_stuck_claims(get_settings().stage_claim_timeout_s)
    if n:
        log.warning("orchestrator.reaped", rows=n)
    return n
