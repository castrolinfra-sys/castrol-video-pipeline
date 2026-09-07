"""Durable job events, written to `job_events` alongside the stdout log.

structlog to stdout is for the operator watching a run. This is for everyone
who arrives later: the person asking why a video that shipped five months ago
looks wrong, and the admin panel's per-job timeline.

Two rules.

**Logging never fails a stage.** Every write here is wrapped. A stage that
reached a paid vendor and succeeded must not be marked failed because the event
insert timed out — that turns a logging outage into a double charge on the
retry. Failures to record are themselves logged, to stdout, and swallowed.

**Events are not log lines.** Only what an incident is reconstructed from:
stage transitions, vendor task ids, spend, published URLs, failures. Writing
every debug line here would make the table useless and the timeline unreadable.
"""

from __future__ import annotations

import os
import socket
from typing import Any

from psycopg.types.json import Jsonb

from .logging import get_logger

log = get_logger(__name__)


class EventLevel:
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


def worker_identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def record_event(
    event: str,
    *,
    job_id: str | None = None,
    stage: str | None = None,
    stage_run_id: str | None = None,
    level: str = EventLevel.INFO,
    **fields: Any,
) -> None:
    """Write one durable event. Also emits to stdout at the matching level.

    `fields` is written by us, never by a vendor — a provider error body goes
    in as a truncated string under a key we choose, not as a spread dict, so a
    vendor cannot inject keys into our audit trail.
    """
    emit = {
        EventLevel.ERROR: log.error,
        EventLevel.WARNING: log.warning,
    }.get(level, log.info)
    # structlog binds the message to the keyword `event`, so a field of that
    # name is a TypeError rather than a log line. Rename instead of dropping —
    # a swallowed field in an audit trail is worse than an ugly key.
    safe = {(f"field_{k}" if k == "event" else k): v for k, v in fields.items()}
    emit(event, job_id=job_id, stage=stage, **safe)

    try:
        # Imported here rather than at module scope: recording an event must
        # not be the thing that opens a connection pool in a process that has
        # no database (the prototype, tests, an offline card re-render).
        from . import db

        db.execute(
            """
            INSERT INTO job_events (job_id, stage_run_id, stage, level, event,
                                    fields, worker)
            VALUES (%(job_id)s, %(run_id)s, %(stage)s, %(level)s, %(event)s,
                    %(fields)s, %(worker)s);
            """,
            {
                "job_id": job_id,
                "run_id": stage_run_id,
                "stage": stage,
                "level": level,
                "event": event,
                "fields": Jsonb(_scrub(fields)),
                "worker": worker_identity(),
            },
        )
    except Exception as exc:  # noqa: BLE001 - see module docstring
        # `failed_event`, not `event`: structlog reserves that keyword, and a
        # TypeError raised HERE would propagate out of the handler whose whole
        # purpose is to swallow — turning a logging outage into a stage failure,
        # and on a paid stage into a double charge on the retry.
        log.warning("events.write_failed", failed_event=event, error=str(exc)[:200])


#: Keys that must never reach the events table. The audit trail is read by the
#: admin panel and exported in support threads; a credential or a signed URL in
#: there outlives every place it was supposed to be scoped to.
_FORBIDDEN = ("api_key", "apikey", "authorization", "token", "secret", "password")


def _scrub(fields: dict[str, Any]) -> dict[str, Any]:
    """Drop credentials, and redact the query string off presigned URLs.

    A presigned URL is a bearer credential for one object. Storing the whole
    thing in a 180-day audit table hands out a 6-hour read that nobody expected
    to have written down, so keep the path and drop the signature.
    """
    out: dict[str, Any] = {}
    for key, value in fields.items():
        low = key.lower()
        if any(bad in low for bad in _FORBIDDEN):
            out[key] = "[redacted]"
            continue
        if isinstance(value, str) and "X-Amz-Signature=" in value:
            out[key] = value.split("?", 1)[0] + "?[signature redacted]"
            continue
        out[key] = value
    return out


def job_timeline(job_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """Newest-first events for one job. What the panel and `castrol events` read."""
    from . import db

    return db.fetch_all(
        """
        SELECT created_at, level, stage, event, fields, worker
          FROM job_events
         WHERE job_id = %(job_id)s
         ORDER BY created_at DESC
         LIMIT %(limit)s;
        """,
        {"job_id": job_id, "limit": limit},
    )
