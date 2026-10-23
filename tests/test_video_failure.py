"""A failed video task: the provider's outage versus a verdict on our inputs.

Batch 1 (2026-10-23) lost 188 of 297 renders to the provider's own "500
internal error, please try again later". They were raised as VENDOR_REJECTED -
terminal, and shown to the client as a rejected PHOTO. A 5xx is the provider
failing on its side and is retried; everything else stays a rejection.
"""

from __future__ import annotations

import pytest

from castrol_pipeline.common.errors import (
    StageErrorCode,
    VendorRejected,
    VendorUnavailable,
)
from castrol_pipeline.stages.vendors import video_task_failure


@pytest.mark.parametrize("code", ["500", 500, "502", 503, " 504 "])
def test_a_5xx_is_the_provider_being_down(code):
    err = video_task_failure(
        "fail", {"failCode": code, "failMsg": "internal error, please try again later."}
    )
    assert isinstance(err, VendorUnavailable)
    assert err.code is StageErrorCode.VENDOR_UNAVAILABLE
    assert not err.is_terminal


@pytest.mark.parametrize(
    "data",
    [
        # The one non-5xx Batch 1 failure. Not retried automatically: a person
        # can still bring it back with `castrol retry-failed`.
        {"failCode": "422", "failMsg": "generate playground failed, task id is blank"},
        {"failCode": "400", "failMsg": "Audio size is too large"},
        {"failMsg": "no code at all"},
        {"failCode": "", "errorReason": "blank code"},
        {"failCode": "E_SAFETY", "msg": "a code that is not a number"},
    ],
)
def test_anything_else_is_a_rejection(data):
    err = video_task_failure("fail", data)
    assert isinstance(err, VendorRejected)
    assert err.is_terminal


def test_the_message_keeps_what_the_provider_said():
    # The text is what `castrol find --status failed` and the events show; it
    # must still carry the code and the provider's own words.
    err = video_task_failure(
        "fail", {"failCode": "500", "failMsg": "internal error, please try again later."}
    )
    assert str(err) == "video provider fail: 500 internal error, please try again later."
