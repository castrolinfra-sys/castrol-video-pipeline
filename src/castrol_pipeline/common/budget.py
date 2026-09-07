"""The only path to a paid vendor.

Every outbound paid call goes through `reserve()`. It calls the Postgres
function `reserve_vendor_call()` and refuses on `false`. No stage may reach a
vendor by any other route — this is the only thing between a retry bug and a
real bill.

Fails closed by construction: a vendor with no `vendor_limits` row is denied,
and so is a vendor whose row is disabled or whose cap is spent.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal

from .db import fetch_one
from .errors import BudgetExhausted
from .logging import get_logger

log = get_logger(__name__)

#: Budget keys are per logical stage, not per provider account, so one stage
#: looping cannot consume the whole day's spend.
VENDOR_TTS = "apimart_tts"
VENDOR_IMAGE = "apimart_image"
VENDOR_VIDEO = "apimart_video"
VENDOR_LIPSYNC = "apimart_lipsync"


def reserve(vendor: str, credits: float | Decimal = 0) -> bool:
    """Atomically reserve one call against today's cap. True if allowed."""
    row = fetch_one(
        "SELECT reserve_vendor_call(%(vendor)s, %(credits)s) AS allowed;",
        {"vendor": vendor, "credits": Decimal(str(credits))},
    )
    allowed = bool(row and row.get("allowed"))
    log.info(
        "budget.reserve",
        vendor=vendor,
        credits=float(credits),
        allowed=allowed,
    )
    return allowed


@contextmanager
def vendor_call(vendor: str, credits: float | Decimal = 0) -> Iterator[None]:
    """Guard a vendor call. Raises BudgetExhausted instead of calling out.

    The reservation is taken BEFORE the call and is not released if the call
    fails. That is deliberate: a failed paid call usually still costs money, and
    a budget that refunds on error is a budget a retry loop can walk straight
    through.
    """
    if not reserve(vendor, credits):
        raise BudgetExhausted(
            f"Daily budget for {vendor} is exhausted, disabled, or unconfigured. "
            "This is a hard stop for the day, not a throttle."
        )
    yield
