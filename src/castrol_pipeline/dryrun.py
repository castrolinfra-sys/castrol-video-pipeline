"""Keeping a rehearsal and a real run apart when they share one database.

A mock rehearsal (`STAGE_MODE=mock`) pulls the client's REAL export into the
REAL tables. That is what makes it a rehearsal, and it is also the hazard:
intake dedupes on the client's row id and finished jobs are never rescheduled,
so every mechanic the rehearsal touched looks already done to a real run. If the
wipe before launch is forgotten, those mechanics never get a video, and nothing
anywhere reports it.

So the forgetting is made loud instead of relied on:

  * mock mode refuses to start unless its S3 prefix is `castrol-dryrun/` and
    delivery is off - its files land where one `aws s3 rm` removes them, and
    nothing it makes can reach the client;
  * real mode refuses to start while ANY rehearsal row is in the database;
  * `reset_for_launch` clears the job tables and keeps what is not job data.
"""

from __future__ import annotations

from typing import Any

from .common import db
from .common.errors import PipelineError
from .config import get_settings

DRYRUN_S3_PREFIX = "castrol-dryrun/"

#: Everything a run writes. Wiped together, in one statement, so no FK is left
#: dangling and a failure leaves the database exactly as it was.
JOB_TABLES: tuple[str, ...] = (
    "job_events",
    "job_reports",
    "checks",
    "deliveries",
    "assets",
    "stage_runs",
    "jobs",
    "export_rows",
    "submissions",
    "export_pulls",
    "batches",
    "vendor_usage",
)

#: Kept, and why, so nobody "tidies" one into the list above:
#:   plates         - the artwork, its history and the uniform references (inv. 31)
#:   vendor_limits  - the daily caps from 0011/0012
#:   auth.users     - the panel logins; not in `public`, never touched here
#:   supabase_migrations.schema_migrations - the ledger apply_migration.py keeps
KEPT_TABLES: tuple[str, ...] = ("plates", "vendor_limits")


class DryRunGuard(PipelineError):
    """A run was refused because it would mix rehearsal and real work."""


def rehearsal_residue() -> dict[str, int]:
    """How much rehearsal data is in the database."""
    row = db.fetch_one(
        """
        SELECT (SELECT count(*) FROM stage_runs WHERE vendor = 'mock')         AS mock_runs,
               (SELECT count(*) FROM assets WHERE s3_key LIKE %(prefix)s)      AS dryrun_assets;
        """,
        {"prefix": DRYRUN_S3_PREFIX + "%"},
    )
    return {"mock_runs": int(row["mock_runs"]), "dryrun_assets": int(row["dryrun_assets"])} \
        if row else {"mock_runs": 0, "dryrun_assets": 0}


def check_mode_is_safe() -> str:
    """Refuse a run that would mix rehearsal and real work. Returns the mode."""
    s = get_settings()
    mode = s.effective_stage_mode

    if mode == "mock":
        problems = []
        if s.s3_prefix != DRYRUN_S3_PREFIX:
            problems.append(f"S3_PREFIX must be {DRYRUN_S3_PREFIX} (is {s.s3_prefix})")
        if s.delivery_enabled:
            problems.append("DELIVERY_ENABLED must be false")
        if problems:
            raise DryRunGuard("Mock mode refused: " + "; ".join(problems))
        from .stages.mocks import parse_failures

        parse_failures(s.mock_failures)  # a typo fails here, not mid-batch
        return mode

    if mode == "real":
        left = rehearsal_residue()
        if any(left.values()):
            raise DryRunGuard(
                f"Rehearsal data is still in this database ({left['mock_runs']} mock "
                f"runs, {left['dryrun_assets']} files under {DRYRUN_S3_PREFIX}). A real "
                "run now would treat those mechanics as already done and never render "
                "them. Run `castrol reset-for-launch` first."
            )
    return mode


def table_counts() -> dict[str, int]:
    return {
        t: int(db.fetch_one(f"SELECT count(*) AS n FROM {t};")["n"])  # noqa: S608 - fixed list
        for t in JOB_TABLES
    }


def reset_for_launch() -> dict[str, Any]:
    """Empty every job table in one transaction. Keeps KEPT_TABLES and auth."""
    before = table_counts()
    with db.transaction() as cur:
        cur.execute(f"TRUNCATE {', '.join(JOB_TABLES)} RESTART IDENTITY;")  # noqa: S608
    return {"wiped": before, "kept": list(KEPT_TABLES)}
