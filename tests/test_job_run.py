"""Running one job by any identifier, and failures that stay failed.

All pure — no database. The parts that go wrong silently are here: a phone read
as a uuid finds nothing, a terminal failure that re-enqueues itself loops on a
paid vendor, and a lock key that collides with the cycle's blocks the cycle.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from castrol_pipeline.common import db
from castrol_pipeline.common.errors import StageErrorCode
from castrol_pipeline.cycle import CYCLE_LOCK_KEY
from castrol_pipeline.jobref import parse_ref
from castrol_pipeline.jobrun import job_lock_key, paid_stages_left
from castrol_pipeline.orchestrator import failure_holds


class TestParseRef:
    def test_a_uuid_is_a_uuid_and_never_a_phone(self) -> None:
        raw = "3f1c2a9e-0b7d-4e55-9a51-8c2d1e0f4b6a"
        q = parse_ref(f"  {raw} ")
        assert q.uuid == raw
        assert q.phone is None

    def test_a_bare_mobile_normalises_to_e164(self) -> None:
        q = parse_ref("9773128990")
        assert q.uuid is None
        assert q.phone == "+919773128990"
        # Also kept raw: the same digits may be the client's own row id.
        assert q.raw == "9773128990"

    def test_a_prefixed_mobile_matches_the_same_number(self) -> None:
        assert parse_ref("+91 97731 28990").phone == parse_ref("09773128990").phone

    def test_a_short_client_id_is_not_a_phone(self) -> None:
        q = parse_ref("1482")
        assert q.phone is None and q.uuid is None and q.raw == "1482"


def _failed(code: str = "VENDOR_REJECTED", *, input_hash: str = "h1", finished=None):
    return {
        "status": "failed",
        "input_hash": input_hash,
        "error_code": code,
        "finished_at": finished or datetime(2026, 9, 16, 6, 0, tzinfo=UTC),
    }


class TestFailureHolds:
    def test_nothing_to_hold_without_a_failure(self) -> None:
        assert failure_holds(None, "h1") is False
        assert failure_holds({**_failed(), "status": "succeeded"}, "h1") is False

    def test_a_terminal_failure_at_the_same_inputs_holds(self) -> None:
        """The regression: this used to re-enqueue at attempts=0, forever."""
        assert failure_holds(_failed(), "h1") is True

    def test_changed_inputs_are_a_new_question(self) -> None:
        assert failure_holds(_failed(input_hash="old"), "new") is False

    def test_budget_stop_holds_for_the_rest_of_the_ist_day(self) -> None:
        # 06:00 UTC and 17:00 UTC on the 16th are both the 16th in IST.
        row = _failed(str(StageErrorCode.BUDGET_EXHAUSTED))
        assert failure_holds(row, "h1", now=datetime(2026, 9, 16, 17, 0, tzinfo=UTC)) is True

    def test_budget_stop_lifts_on_the_next_ist_day_not_the_next_utc_day(self) -> None:
        row = _failed(str(StageErrorCode.BUDGET_EXHAUSTED))
        # 19:00 UTC on the 16th is 00:30 on the 17th in IST — a new cap day.
        assert failure_holds(row, "h1", now=datetime(2026, 9, 16, 19, 0, tzinfo=UTC)) is False


def _table(**statuses):
    stages = ["prep", "audio", "image", "video", "composite", "checks", "publish", "deliver"]
    return [{"stage": s, "status": statuses.get(s)} for s in stages]


class TestPaidStagesLeft:
    def test_a_fresh_job_may_spend_on_all_three(self) -> None:
        assert paid_stages_left(_table(), retry_failed=False) == ["audio", "image", "video"]

    def test_succeeded_stages_do_not_spend_again(self) -> None:
        table = _table(prep="succeeded", audio="succeeded", image="succeeded")
        assert paid_stages_left(table, retry_failed=False) == ["video"]

    def test_an_in_flight_render_was_already_paid_for(self) -> None:
        table = _table(prep="succeeded", audio="succeeded", image="running")
        assert paid_stages_left(table, retry_failed=False) == ["video"]

    def test_a_failed_stage_spends_only_with_retry(self) -> None:
        table = _table(prep="succeeded", audio="succeeded", image="succeeded", video="failed")
        assert paid_stages_left(table, retry_failed=False) == []
        assert paid_stages_left(table, retry_failed=True) == ["video"]


class TestJobLock:
    def test_never_collides_with_the_cycle_and_fits_int4(self) -> None:
        for _ in range(2000):
            key = job_lock_key(str(uuid.uuid4()))
            assert key != CYCLE_LOCK_KEY
            assert CYCLE_LOCK_KEY < key < 2**31

    def test_is_stable_per_job(self) -> None:
        job = str(uuid.uuid4())
        assert job_lock_key(job) == job_lock_key(job)


def test_the_claim_can_be_narrowed_to_one_job() -> None:
    """Without this filter `castrol run` would claim, and pay for, other jobs."""
    assert "job_id = %(job_id)s::uuid" in db._CLAIM_SQL
