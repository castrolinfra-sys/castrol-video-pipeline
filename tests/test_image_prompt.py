"""The image-edit prompt, and the property that makes the third input safe.

Stage B replaces the person inside a frozen plate. Adding a plain-background
shot of the uniform as a THIRD reference is what stops the garment's fabric and
printed marks being reconstructed off a figure the model is simultaneously
redrawing — but it is also the most direct way to break invariant 6, because a
second image of the same garment, framed differently, is an invitation to
reframe. These tests pin the two things that keep those apart:

  * the uniform clause is present only when a uniform reference is actually
    submitted, and the ordinals it uses match the order the URLs are sent in;
  * the geometry constraints survive in BOTH prompts.
"""

from __future__ import annotations

from castrol_pipeline.stages import vendors


class TestPromptShape:
    def test_two_image_prompt_never_mentions_a_third_image(self):
        # A prompt that refers to an image which was not sent is a prompt the
        # model has to guess at.
        assert "third" not in vendors.image_prompt(with_uniform_ref=False).lower()

    def test_three_image_prompt_addresses_the_third_image(self):
        assert "third reference image" in vendors.image_prompt(with_uniform_ref=True)

    def test_both_prompts_address_the_mechanic_as_the_second_image(self):
        # Ordinals are how the prompt names its inputs, so plate first and
        # mechanic second must hold whether or not a uniform ref follows.
        for flag in (True, False):
            assert "second reference image" in vendors.image_prompt(with_uniform_ref=flag)

    def test_exported_default_is_the_two_image_prompt(self):
        assert vendors.IMAGE_PROMPT == vendors.image_prompt(with_uniform_ref=False)


class TestGeometrySurvivesTheThirdImage:
    """Invariant 6. The card is burned in at a fixed fraction of frame height,
    so subject scale or belt-line drift lands it on the mechanic's hands."""

    CONSTRAINTS = [
        "Do not reframe",
        "Do not zoom",
        "Do not move or rescale the subject",
        "same subject scale",
        "same belt line",
    ]

    def test_constraints_are_in_both_prompts(self):
        for flag in (True, False):
            prompt = vendors.image_prompt(with_uniform_ref=flag)
            for clause in self.CONSTRAINTS:
                assert clause in prompt, f"{clause!r} missing (uniform_ref={flag})"

    def test_the_uniform_clause_defers_to_the_first_image(self):
        # The whole risk of the third image is that its framing leaks in. The
        # clause must say so itself, not rely on the constraints below it.
        clause = vendors._PROMPT_UNIFORM
        assert "framing, pose, lighting and background are irrelevant" in clause
        assert "come from the first image" in clause


class TestItIsStillOneChange:
    """One submit, one generation, one edit.

    The third image buys detail for something that must NOT change. Written as
    a second instruction it contradicts "Make EXACTLY ONE change" directly
    above it, and a prompt that argues with itself measurably degrades output —
    the same failure mode invariant 29 records on the avatar prompt.
    """

    def test_exactly_one_change_survives_the_third_image(self):
        assert "Make EXACTLY ONE change" in vendors.image_prompt(with_uniform_ref=True)

    def test_the_clause_opens_by_excluding_itself_from_that_change(self):
        assert vendors._PROMPT_UNIFORM.startswith(
            "That one change does not include the uniform"
        )

    def test_the_clause_never_asks_for_a_second_edit(self):
        # "Reproduce those details" reads as a thing to DO. The uniform is a
        # thing to LEAVE ALONE, with a reference for what it already is.
        lowered = vendors._PROMPT_UNIFORM.lower()
        for verb in ("reproduce those", "redraw", "restyle", "apply the uniform"):
            assert verb not in lowered, f"{verb!r} reads as a second change"


class TestTheFaceIsRenderedAsAPhotograph:
    """v3, 2026-09-16. Bearded mechanics came back messy.

    The preserve clause already said "carry over their facial hair", and that
    is what made the gap easy to miss: naming a feature tells the model the
    feature is there, not that its STRUCTURE has to be read off the reference.
    Given only the noun, this model draws a beard-shaped mass — soft at the
    jawline, smeared into the lips, its density and grey invented.

    So the prompt asks for the structure by name, and the constrain half blocks
    the two ways "beautify" shows up on a face: smoothed skin and tidied hair.
    """

    def test_the_structure_of_the_facial_hair_is_named(self):
        # Each of these is a property the model was inventing rather than
        # copying. The noun alone is what v2 had, and it was not enough.
        for flag in (True, False):
            prompt = vendors.image_prompt(with_uniform_ref=flag).lower()
            for detail in ("outline", "length", "density", "patchiness", "grey"):
                assert detail in prompt, f"{detail!r} missing (uniform_ref={flag})"

    def test_the_hair_is_sourced_from_the_mechanic_not_invented(self):
        # Same ordinal discipline as everything else in this prompt: the beard
        # comes from image two, the same place the face does.
        prompt = vendors.image_prompt(with_uniform_ref=True)
        assert "from the second reference image exactly as they already are" in prompt

    def test_a_clean_shaven_man_is_protected(self):
        # A paragraph about beards is an invitation to add one. This is the
        # sentence that stops "render his facial hair as real hair" being read
        # as a reason to put hair on a face that has none.
        for flag in (True, False):
            assert "clean-shaven man stays clean-shaven" in vendors.image_prompt(
                with_uniform_ref=flag
            )

    def test_retouching_is_blocked_in_both_prompts(self):
        # Invariant-6-adjacent: these live in CONSTRAIN, the half that lists
        # the ways this model is known to drift, so they must survive the
        # third image exactly as the geometry clauses do.
        for flag in (True, False):
            prompt = vendors.image_prompt(with_uniform_ref=flag)
            assert "Do not smooth, airbrush or even out the skin" in prompt
            assert "Do not tidy, trim, thin, reshape or fill in the facial hair" in prompt

    def test_it_is_still_exactly_one_change(self):
        """The naturalness paragraph describes the replacement, it does not add
        a second edit — the failure mode invariant 29 records, and the same one
        TestItIsStillOneChange pins for the uniform clause."""
        prompt = vendors.image_prompt(with_uniform_ref=True)
        assert prompt.index("Make EXACTLY ONE change") < prompt.index(
            "ordinary, unretouched photograph"
        ), "the paragraph must sit inside the change half, not after Constrain"
        lowered = prompt.lower()
        for verb in ("regrow", "add a beard", "groom", "restyle his hair"):
            assert verb not in lowered, f"{verb!r} reads as a second change"


class TestVersionGatesTheRewrite:
    def test_the_prompt_version_was_bumped(self):
        """This prompt is hashed by VERSION, not by its own text (unlike
        AVATAR_PROMPT, invariant 30). Editing the words without bumping leaves
        every open job matching its old image row, so the rewrite ships
        nothing while the code says otherwise."""
        from castrol_pipeline.config import Settings

        assert Settings().image_prompt_version == "v3"


class TestSubmitOrdering:
    """The URLs must go out in the order the prompt names them."""

    def _capture(self, monkeypatch):
        sent: dict = {}

        class _Resp:
            @staticmethod
            def json():
                return {"code": 200, "data": {"task_id": "t-1"}}

        class _Client:
            def __init__(self, *a, **kw): ...
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def post(self, url, headers=None, json=None):
                sent.update(json)
                return _Resp()

        monkeypatch.setattr(vendors.httpx, "Client", _Client)
        monkeypatch.setattr(vendors, "_image_headers", lambda: {})
        monkeypatch.setattr(vendors, "_image_base", lambda: "https://x")
        return sent

    def test_uniform_reference_is_third(self, monkeypatch):
        sent = self._capture(monkeypatch)
        vendors.image_submit(
            "PLATE", "PHOTO", model_id="m", uniform_ref_url="UNIFORM"
        )
        assert sent["image_urls"] == ["PLATE", "PHOTO", "UNIFORM"]
        assert "third reference image" in sent["prompt"]

    def test_without_one_only_two_urls_go_out(self, monkeypatch):
        sent = self._capture(monkeypatch)
        vendors.image_submit("PLATE", "PHOTO", model_id="m")
        assert sent["image_urls"] == ["PLATE", "PHOTO"]
        assert "third" not in sent["prompt"].lower()
