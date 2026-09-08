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
        # The model's guidance is about COMPLEXITY, not raw characters: long or
        # convoluted prompts measurably degrade output. Sentence count is the
        # real discipline, so that stays tight while the char ceiling has
        # headroom for naming the three gestures.
        assert AVATAR_PROMPT.count(".") <= 5
        assert len(AVATAR_PROMPT) < 700

    def test_asks_for_hands(self):
        low = AVATAR_PROMPT.lower()
        assert "hand" in low and "gesture" in low

    def test_directs_slow_motion(self):
        # Revision 2. The first render came back BLURRED: fast hand movement is
        # what generative video smears, and v1 constrained where the hands go
        # but never how fast. If these words are dropped, the smear returns.
        low = AVATAR_PROMPT.lower()
        assert "slow" in low and "deliberate" in low
        assert "holds briefly" in low or "hold" in low

    def test_asks_for_varied_gestures(self):
        # Revision 2. The first render came back REPETITIVE because v1 named a
        # single gesture type ("natural open-palm hand gestures") and the model
        # looped it. Variety comes from naming DISTINCT gestures, so require
        # more than one to be described.
        low = AVATAR_PROMPT.lower()
        assert "varied" in low
        named = sum(w in low for w in ("open palm", "counting", "welcoming open hand"))
        assert named >= 3, "name three distinct gestures, one per script beat"
        assert "different from the last" in low

    def test_phrased_positively(self):
        # Negative instructions are unreliable on this class of model — asking
        # it not to do something tends to surface the thing. Every constraint
        # here is written as what TO do.
        low = AVATAR_PROMPT.lower()
        for banned in ("never repeat", "do not ", "don't ", "avoid "):
            assert banned not in low, f"negative phrasing: {banned!r}"

    def test_keeps_gestures_clear_of_the_card(self):
        # The card is an OPAQUE overlay over 66-82% of frame height. A gesture
        # at waist level happens behind it: the viewer sees a hand enter frame
        # and vanish. If this constraint is ever dropped from the prompt, that
        # is what regresses.
        assert "chest height" in AVATAR_PROMPT.lower()

    def test_protects_the_branding(self):
        low = AVATAR_PROMPT.lower()
        assert "chest logo" in low
        assert "branding unchanged" in low or "framing" in low
