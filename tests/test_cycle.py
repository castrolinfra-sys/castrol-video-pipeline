"""The unattended cycle: window arithmetic and the wait loop's timing.

Both are pure and testable without a database, and both are the parts that go
wrong silently. A window computed in the wrong timezone drops one day's
submissions every midnight and nothing anywhere reports a problem; a sleep of
zero turns a poller into a busy loop against a paid vendor's API.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from castrol_pipeline.cycle import MAX_SLEEP_S, intake_window, next_sleep


class TestIntakeWindow:
    def test_spans_yesterday_to_tomorrow(self) -> None:
        assert intake_window(1, today=date(2026, 9, 9)) == (
            date(2026, 9, 8),
            date(2026, 9, 10),
        )

    def test_lookback_widens_only_the_start(self) -> None:
        start, end = intake_window(7, today=date(2026, 9, 9))
        assert start == date(2026, 9, 2)
        assert end == date(2026, 9, 10)

    def test_reaches_forward_a_day(self) -> None:
        """`to` is inclusive and a future date is accepted by the export API.

        We have not confirmed whether the API filters on UTC or IST. Reaching
        one day forward covers both readings; the cost is an empty extra day.
        """
        _, end = intake_window(1, today=date(2026, 9, 9))
        assert end > date(2026, 9, 9)

    def test_anchors_on_the_clients_calendar_not_the_servers(self) -> None:
        """The regression this exists for: 00:00 IST is still yesterday in UTC.

        The server runs UTC. If the anchor came from the server's own date, the
        midnight run would build its window around the previous day and the day
        that had just started would go unpulled — every single night, with no
        error anywhere.
        """
        # 00:30 on the 10th in Kolkata is 19:00 on the 9th in UTC.
        ist_midnight_in_utc = datetime(2026, 9, 9, 19, 0, tzinfo=UTC)
        server_date = ist_midnight_in_utc.date()
        client_date = date(2026, 9, 10)
        assert server_date != client_date

        _, end = intake_window(1, today=client_date)
        assert end == date(2026, 9, 11)


class TestNextSleep:
    def _left(self, *, queued: int = 0, in_flight: int = 0, due: datetime | None = None):
        return {
            "queued": queued,
            "in_flight": in_flight,
            "next_due": due,
            "total": queued + in_flight,
        }

    def test_polls_at_the_interval_while_a_vendor_task_is_in_flight(self) -> None:
        assert next_sleep(self._left(in_flight=3), 60, 3600) == 60

    def test_waits_out_a_retry_backoff_instead_of_spinning(self) -> None:
        due = datetime.now(UTC) + timedelta(seconds=200)
        assert next_sleep(self._left(queued=1, due=due), 60, 3600) == pytest.approx(200, abs=2)

    def test_a_backoff_never_parks_us_longer_than_the_cap(self) -> None:
        due = datetime.now(UTC) + timedelta(hours=3)
        assert next_sleep(self._left(queued=1, due=due), 60, 99999) == MAX_SLEEP_S

    def test_an_in_flight_task_outranks_a_far_off_retry(self) -> None:
        """Something rendering must be polled at the interval regardless.

        Waiting out a 15-minute backoff while a paid render finishes would leave
        the result uncollected for 15 minutes for no reason.
        """
        due = datetime.now(UTC) + timedelta(seconds=900)
        assert next_sleep(self._left(queued=1, in_flight=1, due=due), 60, 3600) == 60

    def test_never_sleeps_past_the_deadline(self) -> None:
        due = datetime.now(UTC) + timedelta(seconds=900)
        assert next_sleep(self._left(queued=1, due=due), 60, 12) == 12

    def test_never_sleeps_zero(self) -> None:
        """A zero would turn the wait loop into a busy loop against the vendors."""
        assert next_sleep(self._left(in_flight=1), 0, 0) == 1.0

    def test_a_due_time_already_past_does_not_shorten_the_interval(self) -> None:
        due = datetime.now(UTC) - timedelta(seconds=500)
        assert next_sleep(self._left(queued=1, due=due), 60, 3600) == 60
