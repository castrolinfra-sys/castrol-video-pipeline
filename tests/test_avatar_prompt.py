"""The avatar motion prompt, and the one property that must not regress.

On `kling-avatar-v2` the prompt steers expression, head movement and hand
gesture. The inherited default was literally "." — correct lipsync, natural
head motion, hands locked at rest for the whole take.

The failure that matters is not a bad prompt; it is a prompt change that does
NOT regenerate. If the text is outside the input_hash, editing it leaves every
existing job matching its old success row, and the pipeline ships the old
motion while the code says otherwise.
"""

from __future__ import annotations

from castrol_pipeline.common import hashing
from castrol_pipeline.stages.vendors import AVATAR_PROMPT, KIE_PROMPT_MAX_CHARS


class TestPromptIsInTheHash:
    def _hash(self, prompt: str, pro: bool = False) -> str:
        return hashing.video_hash(
            "image-sha", "audio-sha", "kling/ai-avatar-standard",
            {"pro": pro, "prompt": prompt},
        )

    def test_changing_the_prompt_changes_the_hash(self):
        # This is invariant 4. Without it a reworded prompt silently skips.
        assert self._hash(AVATAR_PROMPT) != self._hash(AVATAR_PROMPT + " Smile more.")

    def test_same_prompt_is_stable(self):
        assert self._hash(AVATAR_PROMPT) == self._hash(AVATAR_PROMPT)

    def test_pro_still_separates(self):
        assert self._hash(AVATAR_PROMPT, pro=False) != self._hash(AVATAR_PROMPT, pro=True)

    def test_stage_actually_puts_it_there(self):
        # Guards against the prompt being sent to the vendor but left out of
        # _params() — the exact shape of the bug this file exists for.
        from castrol_pipeline.stages.real import VideoStage

        params = VideoStage()._params()
        assert params["prompt"] == AVATAR_PROMPT


class TestPromptShape:
    def test_not_the_inherited_placeholder(self):
        assert AVATAR_PROMPT.strip() not in {"", "."}

    def test_within_kie_limit(self):
        assert len(AVATAR_PROMPT) <= KIE_PROMPT_MAX_CHARS

    def test_stays_short(self):
        # The model's own guidance: 1-3 sentences. Long or complex prompts
        # measurably degrade output, so this is a real ceiling, not style.
        assert AVATAR_PROMPT.count(".") <= 5
        assert len(AVATAR_PROMPT) < 600

    def test_asks_for_hands(self):
        low = AVATAR_PROMPT.lower()
        assert "hand" in low and "gesture" in low

    def test_keeps_gestures_clear_of_the_card(self):
        # The card is an OPAQUE overlay over 70-87% of frame height. A gesture
        # at waist level happens behind it: the viewer sees a hand enter frame
        # and vanish. If this constraint is ever dropped from the prompt, that
        # is what regresses.
        assert "chest height" in AVATAR_PROMPT.lower()

    def test_protects_the_branding(self):
        low = AVATAR_PROMPT.lower()
        assert "chest logo" in low
        assert "branding unchanged" in low or "framing" in low
