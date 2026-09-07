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
    """Cartesia bills 1 credit per CHARACTER, 100K credits per $5.

    Per character, with no block rounding. An earlier model here ceiled to
    $0.10 per 1000 chars — that figure came from another stack's internal
    credit conversion and overstated TTS by 2x on a real ~450 char script.
    """

    def test_per_character(self):
        assert tts_cost_usd(1000) == Decimal("0.05")
        assert tts_cost_usd(500) == Decimal("0.025")

    def test_does_not_round_to_a_block(self):
        # 1001 chars costs one character more than 1000, not twice as much.
        assert tts_cost_usd(1001) > tts_cost_usd(1000)
        assert tts_cost_usd(1001) < tts_cost_usd(1000) * 2

    def test_empty_is_free(self):
        assert tts_cost_usd(0) == Decimal("0")

    def test_matches_the_measured_per_second_rate(self):
        # The README quotes audio at $0.000886/s of runtime, derived from a
        # 443-char script that produced 25.0s. Keep the two in step.
        assert tts_cost_usd(443) / Decimal("25.0") == Decimal("0.000886")

    def test_is_a_rounding_error_next_to_video(self):
        # A ~450 char script against 25s of avatar: TTS must stay ~2%, not the
        # ~7% the old model implied. If this flips, the cost docs are wrong.
        share = tts_cost_usd(450) / (tts_cost_usd(450) + video_cost_usd(25))
        assert share < Decimal("0.03")
