"""The fabricated panel history is the right SHAPE before it reaches a database.

No database here. `plan()` is pure given its seed, which is the whole reason it
is separate from the insert: a panel you are comparing numbers against must not
move under you between two runs, and the properties that make the data worth
seeding at all - boundary-length names, fractional durations, an unknown
failure code - are decided here rather than discovered on screen.
"""

from __future__ import annotations

import math
from datetime import date

from castrol_pipeline.common.errors import StageErrorCode
from castrol_pipeline.intake.validate import MAX_NAME_CHARS, MAX_WORKSHOP_CHARS
from castrol_pipeline.panelseed import (
    DURATIONS_S,
    FAILURE_REASONS,
    NAMES,
    WORKSHOPS,
    Expected,
    plan,
)

TODAY = date(2026, 9, 23)


class TestPlan:
    def test_the_same_seed_gives_the_same_history(self):
        a = plan(7, 20, seed=42, today=TODAY)
        b = plan(7, 20, seed=42, today=TODAY)
        assert [r["job_id"] for r in a] != [r["job_id"] for r in b], (
            "job ids must be fresh uuids - two seeds sharing one would collide "
            "on a second run"
        )
        for x, y in zip(a, b, strict=True):
            assert (x["day"], x["status"], x["seconds"], x["user_name"]) == (
                y["day"], y["status"], y["seconds"], y["user_name"]
            )

    def test_a_different_seed_gives_different_history(self):
        a = plan(7, 20, seed=1, today=TODAY)
        b = plan(7, 20, seed=2, today=TODAY)
        assert [r["status"] for r in a] != [r["status"] for r in b]

    def test_it_covers_every_requested_day_and_ends_today(self):
        rows = plan(14, 20, seed=5, today=TODAY)
        days = sorted({r["day"] for r in rows})
        assert len(days) == 14
        assert days[-1] == TODAY
        assert (days[-1] - days[0]).days == 13

    def test_today_is_deliberately_partial(self):
        """"Today, so far" is where an off-by-one in a date filter shows up."""
        rows = plan(14, 40, seed=5, today=TODAY)
        per_day = {}
        for r in rows:
            per_day[r["day"]] = per_day.get(r["day"], 0) + 1
        earlier = [n for d, n in per_day.items() if d != TODAY]
        assert per_day[TODAY] < min(earlier)

    def test_volume_varies_so_the_chart_has_a_shape(self):
        rows = plan(14, 40, seed=5, today=TODAY)
        counts = {}
        for r in rows:
            counts[r["day"]] = counts.get(r["day"], 0) + 1
        assert len(set(counts.values())) > 3, "a flat chart proves nothing"

    def test_every_status_the_panel_renders_is_present(self):
        rows = plan(21, 45, seed=9, today=TODAY)
        assert {r["status"] for r in rows} == {"completed", "failed", "running"}

    def test_timestamps_land_inside_their_own_day(self):
        # daily_usage buckets on the IST calendar date; a row that drifts into
        # the next day would make the seeded totals wrong and look like a view
        # bug.
        for r in plan(10, 20, seed=3, today=TODAY):
            assert r["at"].date() == r["day"]


class TestTheDataExercisesTheThingsThatBreak:
    def test_a_duration_exists_whose_ceiling_crosses_an_integer(self):
        """Migration 0014's case: round(27.04, 1) is 27.0, which ceils to 27
        where the vendor billed 28. A duration set of whole and .5 numbers
        would pass a broken view."""
        assert any(0 < s - int(s) < 0.05 for s in DURATIONS_S)

    def test_a_whole_second_is_not_ceiled_upward(self):
        whole = [s for s in DURATIONS_S if s == int(s)]
        assert whole, "need at least one whole duration"
        assert all(math.ceil(s) == int(s) for s in whole)

    def test_a_failure_code_the_panel_does_not_know_is_included(self):
        """`lib/reasons.ts` falls back to one generic line rather than leaking
        the raw string. A fallback nobody exercises is one nobody has seen."""
        codes = {r.split(":")[-1].strip() for r in FAILURE_REASONS}
        known = {c.value for c in StageErrorCode}
        assert codes - known, "no unknown code - the generic fallback is untested"

    def test_the_delivery_code_is_among_them(self):
        # It is the one that used to render as "the photo was rejected by
        # automated content checks", which is about a different failure.
        assert any("DELIVERY_NOT_ACCEPTED" in r for r in FAILURE_REASONS)

    def test_names_and_workshops_reach_their_intake_limits(self):
        assert max(len(n) for n in NAMES) == MAX_NAME_CHARS
        assert max(len(w) for w in WORKSHOPS) == MAX_WORKSHOP_CHARS

    def test_nothing_seeded_would_be_rejected_by_intake(self):
        # Seeding a row intake would have thrown away is seeding a row the
        # panel can never actually receive.
        assert all(len(n) <= MAX_NAME_CHARS for n in NAMES)
        assert all(len(w) <= MAX_WORKSHOP_CHARS for w in WORKSHOPS)

    def test_some_jobs_have_no_mechanic_id(self):
        # The Jobs page searches that column; a blank one is a case the search
        # has to survive rather than match.
        rows = plan(14, 40, seed=11, today=TODAY)
        assert any(not r["mechanic_id"] for r in rows)
        assert any(r["mechanic_id"] for r in rows)


class TestExpectedAccounting:
    def test_only_completed_jobs_contribute_seconds(self):
        exp = Expected()
        exp.add(TODAY, "completed", 25)
        exp.add(TODAY, "failed", None)
        exp.add(TODAY, "running", None)
        assert exp.seconds == 25
        assert exp.per_day[TODAY] == {
            "jobs": 3, "completed": 1, "failed": 1, "seconds": 25
        }
        assert (exp.jobs, exp.completed, exp.failed, exp.running) == (3, 1, 1, 1)

    def test_days_are_kept_apart(self):
        exp = Expected()
        exp.add(date(2026, 9, 1), "completed", 20)
        exp.add(date(2026, 9, 2), "completed", 30)
        assert exp.per_day[date(2026, 9, 1)]["seconds"] == 20
        assert exp.per_day[date(2026, 9, 2)]["seconds"] == 30
        assert exp.seconds == 50
