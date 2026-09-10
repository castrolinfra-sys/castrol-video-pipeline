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
        # real discipline. r4 is three sentences; the ceiling leaves room to
        # add one, not four.
        assert AVATAR_PROMPT.count(".") <= 4
        assert len(AVATAR_PROMPT) < 700

    def test_directs_calm_slow_motion(self):
        """Both words, and they are not synonyms.

        "slow" is what stopped r1's smear — it bounds per-frame displacement,
        which is the mechanical cause. "calm" bounds intent, and was asked for
        by name: hands that are merely slow can still be doing something
        elaborate, and frozen hands read as a still photograph with a talking
        head pasted on.
        """
        low = AVATAR_PROMPT.lower()
        assert "slow" in low, "the smear guard from r1"
        assert "calm" in low, "unhurried intent, not just low velocity"
        assert "natural" in low

    def test_phrased_positively(self):
        # Negative instructions are unreliable on this class of model — asking
        # it not to do something tends to surface the thing. Every constraint
        # here is written as what TO do. "clear of the chest logo" is how r2
        # put a hand on the chest logo.
        low = AVATAR_PROMPT.lower()
        for banned in ("never repeat", "do not ", "don't ", "avoid "):
            assert banned not in low, f"negative phrasing: {banned!r}"

    def test_protects_the_branding(self):
        low = AVATAR_PROMPT.lower()
        assert "branding unchanged" in low or "framing" in low


class TestHandsStayDown:
    """Revision 4, and the reason it exists.

    Three paid revisions moved the hands around the frame and each one found a
    new way for this model to render them badly: r1 looped one gesture and
    smeared it, r2 clawed both hands across the chest panel, r3 produced clean
    open palms that still rose to chest level for no return.

    r4 stops asking for gestures. In the source image the hands already rest at
    roughly 71-78% of frame height, and the card is an opaque overlay across
    66-82% (stages/media.py PANEL_Y0/PANEL_Y1) — so hands left where they are
    are BEHIND it and never on screen. Every hand failure this model has shown
    us becomes invisible rather than merely less likely.

    The old objection — a waist-level gesture "happens behind the card, so the
    viewer sees a hand enter frame and vanish" — is an objection to hands
    CROSSING that boundary. Nothing enters or vanishes if nothing moves.
    """

    def test_hands_are_told_to_stay_low(self):
        low = AVATAR_PROMPT.lower()
        assert "waist" in low, "name where the hands are: at waist level"
        assert "stay low" in low

    def test_hand_position_defers_to_the_source_image(self):
        # The strongest version of this instruction is "where they already
        # are" — it defers to the source image instead of describing a pose the
        # model then has to construct and could construct differently.
        assert "exactly where they are in the image" in AVATAR_PROMPT.lower()

    def test_placement_is_stated_once(self):
        """Placement lives in the movement sentence, NOT also in the
        preservation clause.

        r4 pinned "hand position" in the keep-unchanged list while the sentence
        above granted movement — a placement and a freeze naming the same
        thing, which is r2's contradiction wearing a different costume. The
        model resolves those by picking one, and we do not get to choose which.
        """
        low = AVATAR_PROMPT.lower()
        keep_clause = low[low.index("keep the existing"):]
        assert "hand" not in keep_clause, (
            "the preservation clause must not re-state hand placement — the "
            "movement sentence already owns it"
        )

    def test_does_not_ask_for_gestures(self):
        """The whole point of r4. A prompt that asks for gestures gets them.

        Every word here is one this model has already been observed acting on,
        so any of them reintroduces raised hands near the brand mark.
        """
        low = AVATAR_PROMPT.lower()
        for asks_for_motion in (
            "gesture", "open palm", "open hand", "counting",
            "pointing", "chest height", "raises", "lifts",
        ):
            assert asks_for_motion not in low, (
                f"{asks_for_motion!r} asks for hand motion; r4 exists because "
                "hand motion is where this model fails and the card hides the "
                "hands anyway"
            )

    def test_no_position_that_contradicts_an_exclusion(self):
        # The r2 failure in general form: never pair a placement with an
        # exclusion that names the same place. "chest height" + "clear of the
        # chest logo" is the instance that cost a render.
        low = AVATAR_PROMPT.lower()
        assert not ("chest height" in low and "chest logo" in low), (
            "a position and an exclusion naming the same region — the model "
            "keeps the position and drops the exclusion"
        )

    def test_the_face_still_carries_the_video(self):
        # Hands stop moving, so everything the viewer reads as life comes from
        # the head. The inherited "." default proved this model does that part
        # well; only the hands were ever the problem.
        low = AVATAR_PROMPT.lower()
        assert "articulation" in low
        assert "head nods" in low or "head" in low
