"""Cost estimation is a billing input, so the rounding is worth pinning.

The avatar step is 93-96% of per-video spend and bills per output second, so
an estimate that rounds the wrong way under-reserves against the daily cap on
exactly the call that matters.
"""

from decimal import Decimal

from castrol_pipeline.common.budget import tts_cost_usd, video_cost_usd


class TestVideoCost:
    def test_whole_seconds(self):
        assert video_cost_usd(35) == Decimal("1.40")

    def test_fractional_seconds_ceil(self):
        # Regression: Decimal floor division truncates toward zero, so the
        # -(-x // 1) idiom returned 34 here and under-reserved by a second.
        assert video_cost_usd(34.2) == Decimal("1.40")
        assert video_cost_usd(34.0001) == Decimal("1.40")

    def test_pro_is_double(self):
        assert video_cost_usd(35, pro=True) == video_cost_usd(35) * 2

    def test_never_under_estimates(self):
        for tenths in range(1, 600):
            seconds = tenths / 10
            assert video_cost_usd(seconds) >= Decimal("0.04") * Decimal(str(seconds))


class TestTtsCost:
    def test_short_script_bills_one_kilochar(self):
        assert tts_cost_usd(550) == Decimal("0.10")

    def test_minimum_one(self):
        assert tts_cost_usd(0) == Decimal("0.10")
        assert tts_cost_usd(1) == Decimal("0.10")

    def test_ceils(self):
        assert tts_cost_usd(1000) == Decimal("0.10")
        assert tts_cost_usd(1001) == Decimal("0.20")
        assert tts_cost_usd(1200) == Decimal("0.20")
