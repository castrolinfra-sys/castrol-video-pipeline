"""The only path to a paid vendor.

Every outbound paid call goes through `reserve()`. It calls the Postgres
function `reserve_vendor_call()` and refuses on `false`. No stage may reach a
vendor by any other route — this is the only thing between a retry bug and a
real bill.

Fails closed by construction: a vendor with no `vendor_limits` row is denied,
and so is a vendor whose row is disabled or whose cap is spent.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal

from .db import fetch_one
from .errors import BudgetExhausted
from .logging import get_logger

log = get_logger(__name__)

#: Budget keys are per logical stage, not per provider account, so one stage
#: looping cannot consume the whole day's spend. These MUST match the `vendor`
#: column in vendor_limits exactly — a name that is not a row there is an
#: unknown vendor, and reserve_vendor_call denies it. That is the correct
#: failure direction, but it fails the whole stage, so keep them in sync.
VENDOR_TTS = "tts"  # Cartesia, direct API — billed per 1000 chars, ceiled
VENDOR_IMAGE = "apimart_image"  # gpt-image-2-max — billed per call
VENDOR_VIDEO = "kie_video"  # kling-avatar-v2 — billed PER OUTPUT SECOND

#: Provider rates in USD, used to turn a stage's known inputs into the
#: reservation. Rates are checked into code rather than config because getting
#: one wrong under-reserves silently; a change should be a reviewed diff.
USD_PER_IMAGE_2K = Decimal("0.012")
USD_PER_VIDEO_SECOND_STANDARD = Decimal("0.04")
USD_PER_VIDEO_SECOND_PRO = Decimal("0.08")
USD_PER_TTS_KILOCHAR = Decimal("0.10")


def reserve(
    vendor: str,
    cost_usd: float | Decimal = 0,
    seconds: float | Decimal = 0,
) -> bool:
    """Atomically reserve one call against today's caps. True if allowed.

    `cost_usd` is the ESTIMATED provider cost of the call about to be made.
    For per-second vendors it must be derived from a probed audio duration —
    `vendor_limits.require_cost_estimate` makes a zero estimate a refusal,
    because a failed probe is exactly when spend runs away unnoticed.
    """
    row = fetch_one(
        "SELECT reserve_vendor_call(%(vendor)s, %(cost_usd)s, %(seconds)s) AS allowed;",
        {
            "vendor": vendor,
            "cost_usd": Decimal(str(cost_usd)),
            "seconds": Decimal(str(seconds)),
        },
    )
    allowed = bool(row and row.get("allowed"))
    log.info(
        "budget.reserve",
        vendor=vendor,
        cost_usd=float(cost_usd),
        seconds=float(seconds),
        allowed=allowed,
    )
    return allowed


@contextmanager
def vendor_call(
    vendor: str,
    cost_usd: float | Decimal = 0,
    seconds: float | Decimal = 0,
) -> Iterator[None]:
    """Guard a vendor call. Raises BudgetExhausted instead of calling out.

    The reservation is taken BEFORE the call and is not released if the call
    fails. That is deliberate: a failed paid call usually still costs money, and
    a budget that refunds on error is a budget a retry loop can walk straight
    through.
    """
    if not reserve(vendor, cost_usd, seconds):
        raise BudgetExhausted(
            f"Daily budget for {vendor} is exhausted, disabled, or unconfigured. "
            "This is a hard stop for the day, not a throttle."
        )
    yield


def video_cost_usd(duration_seconds: float, pro: bool = False) -> Decimal:
    """Estimated cost of one avatar render. Duration ceils to whole seconds.

    Never call this with a duration that was not probed from the actual audio.

    Note `math.ceil`, not the `-(-x // 1)` idiom: Decimal floor division
    TRUNCATES toward zero rather than flooring, so that idiom silently rounds
    34.2s down to 34 and under-reserves on the most expensive step in the
    pipeline. Ints floor and would have been fine; Decimals are not.
    """
    rate = USD_PER_VIDEO_SECOND_PRO if pro else USD_PER_VIDEO_SECOND_STANDARD
    return rate * Decimal(math.ceil(Decimal(str(duration_seconds))))


def tts_cost_usd(char_count: int) -> Decimal:
    """Estimated TTS cost. Billing ceils per 1000 chars, minimum 1."""
    kilochars = max(1, math.ceil(char_count / 1000))
    return USD_PER_TTS_KILOCHAR * kilochars
