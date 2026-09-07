"""structlog setup. JSON to stdout, `job_id` bound inside any job context."""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager

import structlog

_configured = False


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    global _configured
    if _configured:
        return

    logging.basicConfig(
        format="%(message)s", stream=sys.stdout, level=getattr(logging, level.upper(), logging.INFO)
    )

    renderer = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


@contextmanager
def job_context(job_id: str, **extra: object) -> Iterator[None]:
    """Bind job_id (and anything else) to every log line inside the block.

    Personal data does not go in here. job_id and phone_e164 are the only
    identifiers permitted in logs (TECH_DESIGN section 17).
    """
    structlog.contextvars.bind_contextvars(job_id=job_id, **extra)
    try:
        yield
    finally:
        structlog.contextvars.unbind_contextvars("job_id", *extra.keys())
