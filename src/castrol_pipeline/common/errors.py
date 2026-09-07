"""Stage and intake error taxonomy.

Intake rejections (`RejectCode`) are terminal by design: a rejected row is
never repaired in-pipeline, because a repaired row is a row whose output nobody
can explain. Stage errors (`StageErrorCode`) are the retryable half, with two
deliberate exceptions marked below.
"""

from __future__ import annotations

from enum import StrEnum


class RejectCode(StrEnum):
    """Intake rejection reasons. Stable strings — they are stored in the DB."""

    NOT_APPROVED = "NOT_APPROVED"
    FACE_COUNT_NOT_1 = "FACE_COUNT_NOT_1"
    BAD_MIME = "BAD_MIME"
    IMAGE_TOO_SMALL = "IMAGE_TOO_SMALL"
    BAD_PHONE = "BAD_PHONE"
    NAME_TOO_LONG = "NAME_TOO_LONG"
    WORKSHOP_TOO_LONG = "WORKSHOP_TOO_LONG"
    BAD_ADDRESS = "BAD_ADDRESS"
    GENDER_UNSUPPORTED = "GENDER_UNSUPPORTED"
    UNKNOWN_BACKGROUND = "UNKNOWN_BACKGROUND"
    UNKNOWN_OUTFIT = "UNKNOWN_OUTFIT"
    TEST_ROW = "TEST_ROW"
    FETCH_FAILED = "FETCH_FAILED"
    MISSING_FIELD = "MISSING_FIELD"


class StageErrorCode(StrEnum):
    VENDOR_TIMEOUT = "VENDOR_TIMEOUT"
    VENDOR_REJECTED = "VENDOR_REJECTED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    ASSET_MISSING = "ASSET_MISSING"
    FFMPEG_FAILED = "FFMPEG_FAILED"
    CHECK_FAILED = "CHECK_FAILED"
    INTERNAL = "INTERNAL"


#: Codes that must not be retried. BUDGET_EXHAUSTED is a hard stop for the day,
#: not a throttle — retrying it is precisely the bug the budget guard exists to
#: stop.
TERMINAL_STAGE_ERRORS: frozenset[StageErrorCode] = frozenset(
    {StageErrorCode.BUDGET_EXHAUSTED}
)


class PipelineError(Exception):
    """Base for errors that carry a taxonomy code."""

    code: StageErrorCode = StageErrorCode.INTERNAL

    def __init__(self, message: str, *, code: StageErrorCode | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code

    @property
    def is_terminal(self) -> bool:
        return self.code in TERMINAL_STAGE_ERRORS


class StageFailure(PipelineError):
    """A stage failed. The orchestrator decides whether it retries."""


class BudgetExhausted(PipelineError):
    code = StageErrorCode.BUDGET_EXHAUSTED


class VendorRejected(PipelineError):
    code = StageErrorCode.VENDOR_REJECTED


class VendorTimeout(PipelineError):
    code = StageErrorCode.VENDOR_TIMEOUT


class AssetMissing(PipelineError):
    code = StageErrorCode.ASSET_MISSING
