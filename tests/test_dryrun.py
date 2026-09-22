"""The rehearsal: mock failure injection, and the guards that keep it apart
from a real run.

No database. A typo in MOCK_FAILURES that silently meant "no failures" would
make the rehearsal look cleaner than production; a mock run writing under the
real prefix, or with delivery on, is exactly what the guard exists to refuse.
"""

from __future__ import annotations

import uuid

import pytest

from castrol_pipeline.config import get_settings
from castrol_pipeline.dryrun import DRYRUN_S3_PREFIX, JOB_TABLES, KEPT_TABLES, DryRunGuard
from castrol_pipeline.stages.base import PipelineStage
from castrol_pipeline.stages.mocks import (
    MOCK_STAGES,
    bucket,
    failure_for,
    mock_audio_seconds,
    parse_failures,
)
from castrol_pipeline.stages.real import REAL_STAGES

AUDIO, IMAGE, VIDEO = PipelineStage.AUDIO, PipelineStage.IMAGE, PipelineStage.VIDEO


class TestParseFailures:
    def test_reads_the_documented_shape(self) -> None:
        rules = parse_failures(
            "image:VENDOR_REJECTED:10, video:VENDOR_TIMEOUT:5,audio:TRANSIENT:10"
        )
        assert [(r.stage, r.kind, r.percent) for r in rules] == [
            (IMAGE, "VENDOR_REJECTED", 10),
            (VIDEO, "VENDOR_TIMEOUT", 5),
            (AUDIO, "TRANSIENT", 10),
        ]

    def test_empty_means_no_failures(self) -> None:
        assert parse_failures("") == []

    @pytest.mark.parametrize(
        "spec",
        [
            "image:VENDOR_REJECTED",          # no percent
            "image:CONTENT_SAFETY:10",        # not a kind we inject
            "composite:VENDOR_REJECTED:10",   # a real stage in mock mode
            "imag:VENDOR_REJECTED:10",        # typo'd stage
            "image:VENDOR_REJECTED:110",
            "image:VENDOR_REJECTED:60,image:TRANSIENT:50",  # over 100% on one stage
        ],
    )
    def test_anything_unreadable_raises(self, spec: str) -> None:
        with pytest.raises(ValueError):
            parse_failures(spec)


class TestFailureFor:
    def test_is_stable_for_a_job(self) -> None:
        rules = parse_failures("image:VENDOR_REJECTED:50")
        job = str(uuid.uuid4())
        assert failure_for(job, IMAGE, rules) == failure_for(job, IMAGE, rules)

    def test_only_hits_the_named_stage(self) -> None:
        rules = parse_failures("image:VENDOR_REJECTED:100")
        job = str(uuid.uuid4())
        assert failure_for(job, IMAGE, rules) == "VENDOR_REJECTED"
        assert failure_for(job, VIDEO, rules) is None

    def test_rates_land_near_what_was_asked(self) -> None:
        rules = parse_failures("image:VENDOR_REJECTED:10,image:TRANSIENT:20")
        jobs = [str(uuid.UUID(int=i)) for i in range(5000)]
        hits = [failure_for(j, IMAGE, rules) for j in jobs]
        assert 0.08 < hits.count("VENDOR_REJECTED") / 5000 < 0.12
        assert 0.17 < hits.count("TRANSIENT") / 5000 < 0.23

    def test_bucket_is_a_percentile(self) -> None:
        assert all(0 <= bucket(str(uuid.uuid4()), VIDEO) < 100 for _ in range(500))


def test_mock_audio_is_as_long_as_the_real_voiceover() -> None:
    # The real ~80-word script rendered at 27.47s.
    assert 26 < mock_audio_seconds(" ".join(["word"] * 80)) < 29
    assert mock_audio_seconds("") >= 3


def test_only_the_three_paid_stages_are_mocked() -> None:
    """Everything free must run the REAL code - that is the rehearsal."""
    for stage, impl in MOCK_STAGES.items():
        if stage in (AUDIO, IMAGE, VIDEO):
            assert impl is not REAL_STAGES[stage]
            assert type(impl).__module__.endswith("mocks")
        else:
            assert impl is REAL_STAGES[stage]


class TestModeGuard:
    @pytest.fixture
    def mock_settings(self, monkeypatch: pytest.MonkeyPatch):
        s = get_settings()
        monkeypatch.setattr(s, "use_stub_stages", False)
        monkeypatch.setattr(s, "stage_mode", "mock")
        monkeypatch.setattr(s, "s3_prefix", DRYRUN_S3_PREFIX)
        monkeypatch.setattr(s, "delivery_enabled", False)
        monkeypatch.setattr(s, "mock_failures", "")
        return s

    def test_a_correct_mock_setup_passes(self, mock_settings) -> None:
        from castrol_pipeline.dryrun import check_mode_is_safe

        assert check_mode_is_safe() == "mock"

    def test_mock_mode_refuses_the_real_prefix(self, mock_settings, monkeypatch) -> None:
        from castrol_pipeline.dryrun import check_mode_is_safe

        monkeypatch.setattr(mock_settings, "s3_prefix", "castrol/")
        with pytest.raises(DryRunGuard, match="S3_PREFIX"):
            check_mode_is_safe()

    def test_mock_mode_refuses_delivery(self, mock_settings, monkeypatch) -> None:
        from castrol_pipeline.dryrun import check_mode_is_safe

        monkeypatch.setattr(mock_settings, "delivery_enabled", True)
        with pytest.raises(DryRunGuard, match="DELIVERY_ENABLED"):
            check_mode_is_safe()

    def test_a_bad_failure_spec_stops_the_run_before_it_starts(
        self, mock_settings, monkeypatch
    ) -> None:
        from castrol_pipeline.dryrun import check_mode_is_safe

        monkeypatch.setattr(mock_settings, "mock_failures", "image:OOPS:10")
        with pytest.raises(ValueError):
            check_mode_is_safe()


def test_the_wipe_never_touches_what_is_not_job_data() -> None:
    assert not set(JOB_TABLES) & set(KEPT_TABLES)
    assert "plates" in KEPT_TABLES and "vendor_limits" in KEPT_TABLES
