"""Retry policy and DAG shape. No database — these are the decisions, not the I/O."""

from __future__ import annotations

from castrol_pipeline.common.errors import StageErrorCode
from castrol_pipeline.orchestrator import backoff_seconds, retry_delay_for
from castrol_pipeline.stages.base import (
    DEFAULT_PLAN,
    STAGE_DEPENDENCIES,
    PipelineStage,
)


class TestBackoff:
    def test_grows_exponentially(self):
        assert backoff_seconds(1) < backoff_seconds(2) < backoff_seconds(3)

    def test_is_capped(self):
        assert backoff_seconds(50) == backoff_seconds(60)


class TestRetryPolicy:
    def test_budget_exhausted_is_never_retried(self):
        # A hard stop for the day, not a throttle. Retrying it is the exact bug
        # the budget guard exists to stop.
        assert retry_delay_for(1, StageErrorCode.BUDGET_EXHAUSTED) is None

    def test_a_transient_error_is_retried_while_attempts_remain(self):
        assert retry_delay_for(1, StageErrorCode.VENDOR_TIMEOUT) is not None

    def test_exhausted_attempts_are_terminal(self):
        from castrol_pipeline.config import get_settings

        cap = get_settings().stage_max_attempts
        assert retry_delay_for(cap, StageErrorCode.VENDOR_TIMEOUT) is None


class TestDag:
    def test_every_planned_stage_has_dependencies_declared(self):
        for stage in DEFAULT_PLAN:
            assert stage in STAGE_DEPENDENCIES

    def test_dependencies_come_earlier_in_the_plan(self):
        seen: set[PipelineStage] = set()
        for stage in DEFAULT_PLAN:
            for dep in STAGE_DEPENDENCIES[stage]:
                assert dep in seen, f"{stage} depends on {dep}, which is not scheduled first"
            seen.add(stage)

    def test_audio_and_image_are_independent(self):
        # They must run ahead in parallel so video workers never wait upstream.
        assert PipelineStage.IMAGE not in STAGE_DEPENDENCIES[PipelineStage.AUDIO]
        assert PipelineStage.AUDIO not in STAGE_DEPENDENCIES[PipelineStage.IMAGE]

    def test_video_waits_for_both(self):
        assert set(STAGE_DEPENDENCIES[PipelineStage.VIDEO]) == {
            PipelineStage.AUDIO,
            PipelineStage.IMAGE,
        }

    def test_repair_is_wired_but_not_in_the_default_plan(self):
        # Conditional until spike 0.1 sets a lipsync threshold.
        assert PipelineStage.REPAIR in STAGE_DEPENDENCIES
        assert PipelineStage.REPAIR not in DEFAULT_PLAN

    def test_deliver_is_last(self):
        assert DEFAULT_PLAN[-1] == PipelineStage.DELIVER
