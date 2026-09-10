"""The input_hash contract.

Invariant 4: `input_hash` covers model ids and prompt/template versions. That is
what makes changing a model id regenerate instead of skip. These tests are the
guard on it — if one fails, a re-run will silently reuse stale output.
"""

from __future__ import annotations

from castrol_pipeline.common import hashing


class TestCanonicalJson:
    def test_key_order_does_not_change_the_hash(self):
        assert hashing.canonical_hash({"a": 1, "b": 2}) == hashing.canonical_hash(
            {"b": 2, "a": 1}
        )

    def test_no_whitespace(self):
        assert hashing.canonical_json({"a": 1, "b": [1, 2]}) == b'{"a":1,"b":[1,2]}'

    def test_unicode_is_preserved_not_escaped(self):
        assert "मुंबई" in hashing.canonical_json({"city": "मुंबई"}).decode("utf-8")

    def test_nested_key_order_also_normalises(self):
        assert hashing.canonical_hash({"x": {"a": 1, "b": 2}}) == hashing.canonical_hash(
            {"x": {"b": 2, "a": 1}}
        )


class TestModelIdIsInTheHash:
    """Each of these is a regenerate-vs-skip decision."""

    def test_audio_hash_moves_with_model_id(self):
        a = hashing.audio_hash("text", "voice-1", "tts-v1")
        b = hashing.audio_hash("text", "voice-1", "tts-v2")
        assert a != b

    def test_audio_hash_moves_with_voice_id(self):
        assert hashing.audio_hash("t", "voice-1", "m") != hashing.audio_hash(
            "t", "voice-2", "m"
        )

    def test_image_hash_moves_with_prompt_version(self):
        assert hashing.image_hash("p", "ph", "v1", "m") != hashing.image_hash(
            "p", "ph", "v2", "m"
        )

    def test_image_hash_moves_with_model_id(self):
        assert hashing.image_hash("p", "ph", "v1", "m1") != hashing.image_hash(
            "p", "ph", "v1", "m2"
        )

    def test_image_hash_moves_when_a_uniform_reference_is_added(self):
        # Registering the uniform reference against a plate must REGENERATE.
        # It is a third input to the edit and it swaps the prompt for the
        # three-image one, so a job that skipped here would ship a video made
        # the old way while the plate row says otherwise.
        assert hashing.image_hash("p", "ph", "v1", "m") != hashing.image_hash(
            "p", "ph", "v1", "m", uniform_ref_sha256="u"
        )

    def test_image_hash_moves_when_the_uniform_reference_is_replaced(self):
        assert hashing.image_hash(
            "p", "ph", "v1", "m", uniform_ref_sha256="u1"
        ) != hashing.image_hash("p", "ph", "v1", "m", uniform_ref_sha256="u2")

    def test_no_uniform_reference_is_the_same_as_the_empty_one(self):
        # The default and an explicit "" are the same fact - a plate with no
        # reference - and must not be two different hashes.
        assert hashing.image_hash("p", "ph", "v1", "m") == hashing.image_hash(
            "p", "ph", "v1", "m", uniform_ref_sha256=""
        )

    def test_video_hash_moves_with_params(self):
        assert hashing.video_hash("i", "a", "m", {"fps": 25}) != hashing.video_hash(
            "i", "a", "m", {"fps": 30}
        )

    def test_composite_hash_moves_with_card_template_version(self):
        payload = {"name": "A"}
        assert hashing.composite_hash("v", payload, "v1") != hashing.composite_hash(
            "v", payload, "v2"
        )

    def test_composite_hash_moves_with_card_text(self):
        assert hashing.composite_hash("v", {"name": "A"}, "v1") != hashing.composite_hash(
            "v", {"name": "B"}, "v1"
        )

    def test_prep_hash_moves_with_script_version(self):
        assert hashing.prep_hash({"a": 1}, "v1", "n1") != hashing.prep_hash(
            {"a": 1}, "v2", "n1"
        )

    def test_prep_hash_moves_with_normalise_rules_version(self):
        assert hashing.prep_hash({"a": 1}, "v1", "n1") != hashing.prep_hash(
            {"a": 1}, "v1", "n2"
        )


class TestStageSeparation:
    def test_stages_with_identical_inputs_do_not_collide(self):
        # publish and deliver both take one string; without the stage name in
        # the hash they could land on one another's success row.
        assert hashing.publish_hash("abc") != hashing.deliver_hash("abc", "abc")


class TestSubmissionHash:
    def test_is_stable(self):
        assert hashing.submission_hash("/a/b.jpg", "+919000000000") == (
            hashing.submission_hash("/a/b.jpg", "+919000000000")
        )

    def test_separator_prevents_field_boundary_collision(self):
        # Without a separator, ("ab","c") and ("a","bc") would hash identically.
        assert hashing.submission_hash("ab", "c") != hashing.submission_hash("a", "bc")
