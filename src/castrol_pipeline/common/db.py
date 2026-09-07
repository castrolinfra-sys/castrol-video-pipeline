"""Postgres access: pool, transaction scope, and the claim/release helpers.

Connection note: use the Supabase SESSION pooler (5432). Transaction pooling
(6543) cannot hold `FOR UPDATE ... SKIP LOCKED` semantics across statements the
way the reaper expects, and prepared-statement caching breaks against it.
"""

from __future__ import annotations

import atexit
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ..config import get_settings
from .logging import get_logger

log = get_logger(__name__)

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        dsn = get_settings().require("supabase_db_url")
        _pool = ConnectionPool(
            dsn,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row, "autocommit": False},
            open=True,
        )
        # Without this every CLI invocation ends in psycopg's "couldn't stop
        # thread within 5.0 seconds" warnings, which train the reader to ignore
        # the tail of the output — where the real errors are.
        atexit.register(close_pool)
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    with get_pool().connection() as conn:
        yield conn


@contextmanager
def transaction() -> Iterator[psycopg.Cursor]:
    """A cursor inside a transaction. Commits on clean exit, rolls back on error."""
    with get_pool().connection() as conn, conn.transaction(), conn.cursor() as cur:
        yield cur


def fetch_one(sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    with transaction() as cur:
        cur.execute(sql, params or {})
        return cur.fetchone()


def fetch_all(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with transaction() as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def execute(sql: str, params: dict[str, Any] | None = None) -> int:
    with transaction() as cur:
        cur.execute(sql, params or {})
        return cur.rowcount


# ------------------------------------------------------------------ claiming --

#: One statement. `attempts` increments at CLAIM time, not at failure time: a
#: worker that dies mid-stage still burns an attempt, otherwise a crash loop
#: retries forever.
_CLAIM_SQL = """
UPDATE stage_runs
   SET status      = 'claimed',
       claimed_by  = %(worker)s,
       claimed_at  = now(),
       attempts    = attempts + 1
 WHERE id = (
       SELECT id
         FROM stage_runs
        WHERE stage = %(stage)s
          AND status = 'pending'
          AND next_attempt_at <= now()
        ORDER BY next_attempt_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
 )
RETURNING *;
"""


def claim_stage_run(stage: str, worker: str) -> dict[str, Any] | None:
    """Claim one ready run of `stage`, or return None if the queue is empty."""
    return fetch_one(_CLAIM_SQL, {"stage": stage, "worker": worker})


def enqueue_stage_run(
    job_id: str, stage: str, input_hash: str
) -> dict[str, Any] | None:
    """Create a pending run.

    Returns None when one is already in flight for this (job, stage) — the
    partial unique index is what makes double-enqueue impossible rather than
    merely unlikely.
    """
    return fetch_one(
        """
        INSERT INTO stage_runs (job_id, stage, input_hash, status)
        VALUES (%(job_id)s, %(stage)s, %(input_hash)s, 'pending')
        ON CONFLICT (job_id, stage)
          WHERE status IN ('pending', 'claimed', 'running')
          DO NOTHING
        RETURNING *;
        """,
        {"job_id": job_id, "stage": stage, "input_hash": input_hash},
    )


def find_succeeded_run(
    job_id: str, stage: str, input_hash: str
) -> dict[str, Any] | None:
    """The idempotency check: has this exact input already succeeded?"""
    return fetch_one(
        """
        SELECT * FROM stage_runs
         WHERE job_id = %(job_id)s
           AND stage = %(stage)s
           AND input_hash = %(input_hash)s
           AND status = 'succeeded'
         LIMIT 1;
        """,
        {"job_id": job_id, "stage": stage, "input_hash": input_hash},
    )


def mark_running(
    run_id: str,
    *,
    vendor: str,
    vendor_task_id: str,
    model_id: str,
    cost_usd: Any = None,
    billed_seconds: Any = None,
    billed_units: Any = None,
) -> None:
    """Async submit: record the task id, the spend, and release the worker.

    Cost lands HERE, not on completion. The submit is what incurred it, and a
    task that never completes still cost money — recording it only on success
    would make exactly the failures worth counting invisible.
    """
    execute(
        """
        UPDATE stage_runs
           SET status = 'running',
               vendor = %(vendor)s,
               vendor_task_id = %(task)s,
               model_id = %(model)s,
               cost_usd = %(cost)s,
               billed_seconds = %(seconds)s,
               billed_units = %(units)s,
               started_at = coalesce(started_at, now())
         WHERE id = %(id)s;
        """,
        {
            "id": run_id,
            "vendor": vendor,
            "task": vendor_task_id,
            "model": model_id,
            "cost": cost_usd,
            "seconds": billed_seconds,
            "units": billed_units,
        },
    )


def mark_succeeded(
    run_id: str,
    *,
    output_key: str | None,
    params: dict[str, Any] | None = None,
    cost_usd: Any = None,
    billed_seconds: Any = None,
    billed_units: Any = None,
) -> None:
    """Finish a run.

    Cost columns use COALESCE so a poller completing an async run cannot erase
    the figure the submit already recorded — the poll itself costs nothing, and
    passing None there must mean "unchanged", not "free".
    """
    execute(
        """
        UPDATE stage_runs
           SET status = 'succeeded',
               output_key = %(key)s,
               params = coalesce(%(params)s::jsonb, params),
               cost_usd = coalesce(%(cost)s, cost_usd),
               billed_seconds = coalesce(%(seconds)s, billed_seconds),
               billed_units = coalesce(%(units)s, billed_units),
               finished_at = now(),
               error_code = NULL,
               error_message = NULL
         WHERE id = %(id)s;
        """,
        {
            "id": run_id,
            "key": output_key,
            "params": psycopg.types.json.Jsonb(params) if params else None,
            "cost": cost_usd,
            "seconds": billed_seconds,
            "units": billed_units,
        },
    )


def mark_failed(
    run_id: str,
    *,
    error_code: str,
    error_message: str,
    retry_in_seconds: int | None,
) -> None:
    """Fail a run.

    `retry_in_seconds is None` means terminal: the run stays `failed` and is not
    picked up again. Otherwise it returns to `pending` behind a backoff.
    """
    if retry_in_seconds is None:
        execute(
            """
            UPDATE stage_runs
               SET status = 'failed',
                   error_code = %(code)s,
                   error_message = %(msg)s,
                   finished_at = now()
             WHERE id = %(id)s;
            """,
            {"id": run_id, "code": error_code, "msg": error_message},
        )
    else:
        execute(
            """
            UPDATE stage_runs
               SET status = 'pending',
                   error_code = %(code)s,
                   error_message = %(msg)s,
                   claimed_by = NULL,
                   claimed_at = NULL,
                   next_attempt_at = now() + make_interval(secs => %(delay)s)
             WHERE id = %(id)s;
            """,
            {
                "id": run_id,
                "code": error_code,
                "msg": error_message,
                "delay": retry_in_seconds,
            },
        )


def reap_stuck_claims(timeout_seconds: int) -> int:
    """Return rows stuck in `claimed` to `pending`.

    Without this a worker OOM silently parks a job forever.
    """
    return execute(
        """
        UPDATE stage_runs
           SET status = 'pending',
               claimed_by = NULL,
               claimed_at = NULL,
               next_attempt_at = now(),
               error_code = 'STUCK_CLAIM_REAPED'
         WHERE status = 'claimed'
           AND claimed_at < now() - make_interval(secs => %(timeout)s);
        """,
        {"timeout": timeout_seconds},
    )
