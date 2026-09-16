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
from castrol_pipeline.stages.vendors import AVATAR_PROMPT, VIDEO_PROMPT_MAX_CHARS


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

    def test_within_video_provider_limit(self):
        assert len(AVATAR_PROMPT) <= VIDEO_PROMPT_MAX_CHARS

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

    The current text stops asking for gestures. It names the rest position
    positively — low, one hand each side — and bounds the motion rather than
    forbidding it, because frozen hands are their own defect: a still
    photograph with a talking head pasted on.

    It also carries the one thing the client asked for by name after seeing a
    batch: the hands must never overlap each other. Measured on the nine
    renders of 2026-09-14, seven of nine held them apart for the whole take.

    The card no longer hides this. It used to sit across 66-82% of frame
    height, which is where the hands are; it now starts at 72.27%
    (stages/media.py PANEL_Y0), BELOW them. So the prompt is the only thing
    keeping the hands presentable — they are on screen either way.
    """

    def test_hands_are_told_to_stay_low(self):
        low = AVATAR_PROMPT.lower()
        assert "hands stay low" in low, "name where the hands rest"

    def test_hands_are_kept_apart(self):
        # The client's words after reviewing a batch: reduce the hand motion
        # and make sure no overlap occurs. Hands that meet are where this model
        # renders fingers worst - it has to invent an occlusion.
        low = AVATAR_PROMPT.lower()
        assert "one on each side" in low
        assert "apart from each other" in low
        assert "clear of one another" in low

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


class TestHeadMotionIsReduced:
    """r5, 2026-09-16, and the client asked for it by name.

    r4 asked for "subtle head nods". A nod is a repeating movement, and this
    model performs a requested movement for the whole take rather than
    occasionally — the same mechanism that looped r1's hand gesture, pointed at
    the head. What ships is a mechanic bobbing continuously for 25 seconds.

    The head is now told where to be (level, facing camera) and how much it may
    move (slightly), which is exactly the shape that worked for the hands: name
    the rest position positively, then bound the motion.
    """

    def test_no_nodding_is_requested(self):
        # Any word naming a repeatable head movement gets that movement on a
        # loop. "nod" is the one that shipped; the rest are its neighbours.
        low = AVATAR_PROMPT.lower()
        for asks_for_motion in ("nod", "tilt", "shake", "sway", "turns his head"):
            assert asks_for_motion not in low, (
                f"{asks_for_motion!r} asks for head motion; r5 exists because "
                "this model repeats a requested movement for the whole take"
            )

    def test_the_head_is_placed_and_bounded(self):
        low = AVATAR_PROMPT.lower()
        assert "head that stays level" in low, "name the rest position"
        assert "facing camera" in low
        assert "only slight natural movement" in low, "bounded, not frozen"

    def test_the_head_is_not_frozen(self):
        """Same argument as the hands, and it is not a nicety.

        A motionless head over a moving mouth reads as a photograph with a
        talking head pasted on — which is the defect the inherited "." default
        produced. Reducing motion to zero trades one visible failure for
        another, so the bound has to permit movement while limiting it.
        """
        low = AVATAR_PROMPT.lower()
        for freezes in ("head still", "motionless", "perfectly still", "rigid"):
            assert freezes not in low, f"{freezes!r} freezes the head"

    def test_something_else_carries_the_expression(self):
        # With the hands low and the head steady, the eyes and mouth are all
        # the life left in the frame, so the prompt has to ask for them.
        low = AVATAR_PROMPT.lower()
        assert "engaged face" in low
