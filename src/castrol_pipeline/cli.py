"""CLI entrypoints. Every `castrol <cmd>` in the docs lands in this file.

    doctor      config + database reachable
    seed-job    create one job by hand from local files   -> seed.py
    intake      pull an export window                     -> intake/runner.py
    schedule    enqueue ready stages                      -> orchestrator.py
    work        drain one stage                           -> orchestrator.py
    poll        reconcile in-flight vendor tasks          -> orchestrator.py
    redo        re-run a stage on a finished job (SPENDS) -> orchestrator.py
    drain       sweep every stage until nothing moves     -> orchestrator.py
    show        one job: runs, cost, assets, checks       -> seed.py:describe
    events      one job's durable timeline                -> common/events.py
    costs       per-generation spend + today's caps       -> job_costs view
    report      the morning number for a batch
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Annotated

import typer

from .common.logging import configure_logging, get_logger
from .config import get_settings
from .stages.base import DEFAULT_PLAN, PipelineStage

app = typer.Typer(add_completion=False, help="Castrol MAGNATEC video pipeline")
log = get_logger("cli")


def _boot() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise typer.BadParameter(f"Expected YYYY-MM-DD, got {value!r}") from None


@app.command()
def intake(
    from_: Annotated[str, typer.Option("--from", help="Window start, YYYY-MM-DD")],
    to: Annotated[str, typer.Option("--to", help="Window end, YYYY-MM-DD")],
    fixture: Annotated[
        str | None,
        typer.Option("--fixture", help="Read a saved export JSON instead of calling the API"),
    ] = None,
    no_media: Annotated[
        bool, typer.Option("--no-media", help="Skip the photo fetch (row logic only)")
    ] = False,
) -> None:
    """Pull one export window, validate, land photos, create jobs."""
    _boot()
    from .intake.runner import run_intake

    counters = run_intake(
        from_date=_parse_date(from_),
        to_date=_parse_date(to),
        fixture=fixture,
        fetch_media=not no_media,
    )
    typer.echo(json.dumps(counters.as_dict(), indent=2))


@app.command("seed-job")
def seed_job_cmd(
    photo: Annotated[str, typer.Option(help="Mechanic photo (local file)")],
    name: Annotated[str, typer.Option(help="Mechanic name — spoken and on the card")],
    workshop: Annotated[str, typer.Option(help="Workshop name — spoken and on the card")],
    address: Annotated[str, typer.Option(help="Full address, as printed on the card")],
    phone: Annotated[str, typer.Option(help="Indian mobile — card only, never spoken")],
    plate: Annotated[
        str | None, typer.Option(help="Plate image to upload and activate")
    ] = None,
    plate_id: Annotated[
        str | None, typer.Option("--plate-id", help="Use an already-registered plate")
    ] = None,
    spoken_place: Annotated[
        str | None,
        typer.Option(
            "--spoken-place",
            help="What the voice says. Defaults to the last segment of --address, "
                 "so landmarks are not read aloud.",
        ),
    ] = None,
    uniform: Annotated[str, typer.Option(help="uniform_id for the plate")] = "polo",
    background: Annotated[
        str, typer.Option(help="background_id for the plate")
    ] = "bg1_white_suv",
) -> None:
    """Create one job by hand from local files. Idempotent on (photo, phone).

    The manual-entry path — a single mechanic, a plate test, a client sample —
    without the export API in the way. It creates the rows and lands the photo;
    it does not run anything. Follow it with `drain`.
    """
    _boot()
    from pathlib import Path

    from .seed import seed_job

    out = seed_job(
        photo=Path(photo),
        plate=Path(plate) if plate else None,
        plate_id=plate_id,
        name=name,
        workshop=workshop,
        address=address,
        phone=phone,
        spoken_place=spoken_place,
        uniform_id=uniform,
        background_id=background,
    )
    typer.echo(json.dumps(out, indent=2))


@app.command()
def show(job_id: Annotated[str, typer.Argument(help="Job UUID")]) -> None:
    """Everything known about one job: runs, cost, assets, checks."""
    _boot()
    from .seed import describe

    typer.echo(describe(job_id))


@app.command()
def events(
    job_id: Annotated[str, typer.Argument(help="Job UUID")],
    limit: Annotated[int, typer.Option(help="Max events")] = 200,
) -> None:
    """The durable timeline for one job, oldest first.

    Queried newest-first (that is the index), then reversed for display —
    a run reads forwards.
    """
    _boot()
    from .common.events import job_timeline

    rows = job_timeline(job_id, limit)
    for row in reversed(rows):
        stamp = row["created_at"].strftime("%H:%M:%S")
        stage = row["stage"] or "-"
        extra = json.dumps(row["fields"], default=str) if row["fields"] else ""
        typer.echo(f"{stamp}  {row['level']:<7} {stage:<10} {row['event']:<24} {extra}")


@app.command()
def costs(
    since: Annotated[str | None, typer.Option(help="YYYY-MM-DD")] = None,
    limit: Annotated[int, typer.Option(help="Max jobs listed")] = 50,
) -> None:
    """Per-generation spend, and today's usage against the caps."""
    _boot()
    from .common import db

    jobs = db.fetch_all(
        """
        SELECT job_id, status, cost_usd, video_seconds, paid_calls, failed_runs,
               created_at
          FROM job_costs
         WHERE (%(since)s::date IS NULL OR created_at >= %(since)s::date)
         ORDER BY created_at DESC
         LIMIT %(limit)s;
        """,
        {"since": since, "limit": limit},
    )
    usage = db.fetch_all(
        """
        SELECT u.vendor, u.usage_date, u.calls, u.cost_usd, u.seconds,
               l.daily_cost_cap_usd, l.daily_call_cap
          FROM vendor_usage u
          JOIN vendor_limits l ON l.vendor = u.vendor
         WHERE u.usage_date = (now() at time zone 'Asia/Kolkata')::date
         ORDER BY u.vendor;
        """
    )
    total = sum(float(j["cost_usd"] or 0) for j in jobs)
    typer.echo(
        json.dumps(
            {
                "jobs": [dict(j) for j in jobs],
                "job_count": len(jobs),
                "total_usd": round(total, 4),
                "mean_usd": round(total / len(jobs), 4) if jobs else None,
                "today_by_vendor": [dict(u) for u in usage],
            },
            indent=2,
            default=str,
        )
    )


@app.command()
def redo(
    job_id: Annotated[str, typer.Argument(help="Job UUID")],
    stage: Annotated[str, typer.Option("--stage", help="Stage to re-run")],
    yes: Annotated[
        bool, typer.Option("--yes", help="Skip the confirmation prompt")
    ] = False,
) -> None:
    """Make one stage runnable again for a finished job, and everything after it.

    For when an input the hash cannot see has changed — a reworded avatar
    prompt, a corrected model id, a plate reissued under the same key.

    This SPENDS MONEY on the next `work`/`drain`. It clears the way and
    schedules; it does not run anything itself.
    """
    _boot()
    from .common import db
    from .orchestrator import redo_stage

    try:
        target = PipelineStage(stage)
    except ValueError:
        raise typer.BadParameter(
            f"Unknown stage {stage!r}. Known: {[str(s) for s in PipelineStage]}"
        ) from None

    # Show the bill before touching anything. The video stage is 96% of spend
    # and re-running it by accident is the expensive mistake this guards.
    prior = db.fetch_one(
        """
        SELECT cost_usd, billed_seconds FROM stage_runs
         WHERE job_id = %(job)s AND stage = %(stage)s AND status = 'succeeded'
         ORDER BY finished_at DESC LIMIT 1;
        """,
        {"job": job_id, "stage": str(target)},
    )
    if prior is None:
        typer.echo(f"No succeeded {target} run for this job — nothing to redo.")
        raise typer.Exit(1)

    cost = float(prior["cost_usd"] or 0)
    typer.echo(
        f"Re-running {target} for {job_id}.\n"
        f"  last attempt cost ${cost:.4f} (~Rs {cost * 100:.2f}) "
        f"for {prior['billed_seconds'] or '?'}s\n"
        f"  the next `castrol work --stage {target}` will spend about that again"
    )
    if not yes and not typer.confirm("Proceed?"):
        raise typer.Abort()

    typer.echo(json.dumps(redo_stage(job_id, target), indent=2))


@app.command()
def work(
    stage: Annotated[str, typer.Option("--stage", help="Stage to drain")],
    limit: Annotated[int, typer.Option(help="Max runs to process")] = 1000,
) -> None:
    """Claim and execute pending runs of one stage."""
    _boot()
    from .orchestrator import drain_stage

    try:
        target = PipelineStage(stage)
    except ValueError:
        raise typer.BadParameter(
            f"Unknown stage {stage!r}. Known: {[str(s) for s in PipelineStage]}"
        ) from None

    processed = drain_stage(target, limit=limit)
    typer.echo(json.dumps({"stage": str(target), "processed": processed}))


@app.command()
def poll(
    limit: Annotated[int, typer.Option(help="Max in-flight tasks to check")] = 100,
    watch: Annotated[
        bool, typer.Option("--watch", help="Keep polling until nothing is in flight")
    ] = False,
    interval: Annotated[int, typer.Option(help="Seconds between passes with --watch")] = 30,
) -> None:
    """Reconcile in-flight async vendor tasks.

    One pass by default — that is what a cron-driven poller wants. `--watch`
    blocks until the queue is empty, which is what a person waiting on a
    20-minute avatar render wants. Ctrl-C is safe: the task keeps running at
    the vendor and the next poll picks it up.
    """
    _boot()
    import time as _time

    from .common import db
    from .orchestrator import poll_once, reap

    def _inflight() -> int:
        row = db.fetch_one(
            "SELECT count(*) AS n FROM stage_runs "
            "WHERE status = 'running' AND vendor_task_id IS NOT NULL;"
        )
        return int(row["n"]) if row else 0

    total = 0
    while True:
        reaped = reap()
        completed = poll_once(limit=limit)
        total += completed
        remaining = _inflight()
        typer.echo(json.dumps(
            {"reaped": reaped, "completed": completed, "in_flight": remaining}
        ))
        if not watch or remaining == 0:
            break
        _time.sleep(interval)

    if watch:
        typer.echo(json.dumps({"watched": True, "completed_total": total}))


@app.command()
def drain(
    passes: Annotated[int, typer.Option(help="Max sweeps over the whole plan")] = 20,
) -> None:
    """Run every stage and the poller repeatedly until nothing moves.

    This is the 1.4 acceptance path: a full batch drained end-to-end. It is a
    single-process convenience for local runs — production uses N workers and a
    separate poller.
    """
    _boot()
    from .orchestrator import drain_stage, poll_once, reap

    totals: dict[str, int] = {}
    for sweep in range(passes):
        moved = 0
        for stage in DEFAULT_PLAN:
            n = drain_stage(stage)
            if n:
                totals[str(stage)] = totals.get(str(stage), 0) + n
                moved += n
        reap()
        completed = poll_once()
        if completed:
            totals["poll"] = totals.get("poll", 0) + completed
            moved += completed
        if moved == 0:
            log.info("drain.quiet", sweeps=sweep + 1)
            break
    typer.echo(json.dumps(totals, indent=2))


@app.command()
def schedule() -> None:
    """Enqueue ready stages for every job that is not finished.

    Normally the orchestrator does this after each run. This exists to recover a
    batch whose scheduling was interrupted.
    """
    _boot()
    from .common import db
    from .orchestrator import advance_job, schedule_ready

    jobs = db.fetch_all("SELECT id FROM jobs WHERE status NOT IN ('completed', 'cancelled');")
    enqueued = 0
    for row in jobs:
        enqueued += len(schedule_ready(str(row["id"])))
        advance_job(str(row["id"]))
    typer.echo(json.dumps({"jobs": len(jobs), "enqueued": enqueued}))


@app.command()
def report(batch_id: Annotated[str | None, typer.Option(help="Defaults to latest")] = None) -> None:
    """The morning number: pulled / new / rejected / created / completed / failed."""
    _boot()
    from .common import db

    batch = db.fetch_one(
        "SELECT * FROM batches WHERE id = %(id)s;" if batch_id
        else "SELECT * FROM batches ORDER BY started_at DESC LIMIT 1;",
        {"id": batch_id} if batch_id else {},
    )
    if batch is None:
        typer.echo(json.dumps({"error": "no batches"}))
        raise typer.Exit(1)

    stages = db.fetch_all(
        """
        SELECT stage, status, count(*) AS n
          FROM stage_runs sr
          JOIN jobs j ON j.id = sr.job_id
         WHERE j.batch_id = %(id)s
         GROUP BY stage, status
         ORDER BY stage, status;
        """,
        {"id": str(batch["id"])},
    )
    jobs = db.fetch_all(
        "SELECT status, count(*) AS n FROM jobs WHERE batch_id = %(id)s GROUP BY status;",
        {"id": str(batch["id"])},
    )

    typer.echo(
        json.dumps(
            {
                "batch_id": str(batch["id"]),
                "window": [str(batch["from_date"]), str(batch["to_date"])],
                "status": batch["status"],
                "pulled": batch["submissions_pulled"],
                "new": batch["submissions_new"],
                "rejected": batch["submissions_rejected"],
                "jobs_created": batch["jobs_created"],
                "reject_codes": batch["stage_failure_counts"],
                "jobs_by_status": {r["status"]: r["n"] for r in jobs},
                "stage_runs": [
                    {"stage": r["stage"], "status": r["status"], "n": r["n"]} for r in stages
                ],
            },
            indent=2,
            default=str,
        )
    )


@app.command()
def doctor() -> None:
    """Check that config and the database are actually usable."""
    _boot()
    settings = get_settings()
    out: dict[str, object] = {
        "environment": settings.environment,
        "storage_backend": settings.storage_backend,
        "use_stub_stages": settings.use_stub_stages,
        "script_version": settings.script_version,
    }

    try:
        from .common import db

        row = db.fetch_one("SELECT count(*) AS n FROM jobs;")
        out["db"] = "ok"
        out["jobs"] = row["n"] if row else 0
        caps = db.fetch_all("SELECT vendor, daily_call_cap, enabled FROM vendor_limits;")
        out["vendor_limits"] = [dict(c) for c in caps]
        if not caps:
            out["vendor_warning"] = (
                "vendor_limits is empty: reserve_vendor_call() denies every paid "
                "call. Expected in stub mode; apply 0002_seed before stage 1.5."
            )
    except Exception as exc:  # noqa: BLE001
        out["db"] = f"FAILED: {exc}"

    typer.echo(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    app()
