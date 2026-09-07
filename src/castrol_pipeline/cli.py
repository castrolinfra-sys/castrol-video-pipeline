"""CLI entrypoints: intake, work, poll, report — plus drain and doctor."""

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
def poll(limit: Annotated[int, typer.Option(help="Max in-flight tasks to check")] = 100) -> None:
    """Reconcile in-flight async vendor tasks."""
    _boot()
    from .orchestrator import poll_once, reap

    reaped = reap()
    completed = poll_once(limit=limit)
    typer.echo(json.dumps({"reaped": reaped, "completed": completed}))


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
