"""Prep is pure functions and zero I/O, so it is tested directly."""

from __future__ import annotations

import pytest

from castrol_pipeline.prep.normalise import (
    expand_for_speech,
    normalise_address,
    normalise_phone,
)
from castrol_pipeline.prep.plates import (
    UnknownBackground,
    UnknownOutfit,
    plate_code,
    resolve_combo,
)
from castrol_pipeline.prep.script import ScriptError, fill_script


class TestPhone:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("8355837844", "+918355837844"),
            ("918355837844", "+918355837844"),
            ("08355837844", "+918355837844"),
            ("+91 83558 37844", "+918355837844"),
            ("835-583-7844", "+918355837844"),
        ],
    )
    def test_accepts_the_shapes_the_export_produces(self, raw, expected):
        assert normalise_phone(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "1234567890",  # does not start 6-9
            "835583784",  # 9 digits
            "83558378449",  # 11 digits
            "",
            None,
            "not a phone",
        ],
    )
    def test_rejects_rather_than_repairs(self, raw):
        assert normalise_phone(raw) is None


class TestAddress:
    def test_splits_locality_city(self):
        assert normalise_address("Dombivili, Thane") == ("Dombivili", "Thane")

    def test_collapses_whitespace(self):
        assert normalise_address("  Andheri ,   Mumbai ") == ("Andheri", "Mumbai")

    @pytest.mark.parametrize("raw", ["Mumbai", "A, B, C", "", None])
    def test_rejects_anything_that_is_not_two_parts(self, raw):
        assert normalise_address(raw) is None


class TestSpeechExpansion:
    def test_digits_are_read_individually(self):
        assert expand_for_speech("Shop 24") == "Shop do chaar"

    def test_expands_abbreviations(self):
        assert "Road" in expand_for_speech("MG Rd.")

    def test_is_not_applied_to_card_text(self):
        # Guard against the two paths being confused: fill_script must keep the
        # display text unexpanded while the spoken text is expanded.
        filled = fill_script(
            version="v1", name="Raju", workshop="Shop 24", locality="Andheri"
        )
        assert "Shop 24" in filled.display_text
        assert "do chaar" in filled.spoken_text


class TestScript:
    def test_unknown_version_is_an_error_not_a_default(self):
        with pytest.raises(ScriptError):
            fill_script(version="v99", name="A", workshop="B", locality="C")

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_empty_values_are_refused(self, blank):
        with pytest.raises(ScriptError):
            fill_script(version="v1", name=blank, workshop="B", locality="C")


class TestPlateMapping:
    def test_the_value_the_export_actually_sends_resolves(self):
        """The regression this pins cost every row.

        The export sends "Background 1", never "SUV". The mapping only had the
        vehicle words, and because an unmapped value REJECTS rather than
        defaulting, every real row would have failed with UNKNOWN_BACKGROUND
        while the ids it mapped to were perfectly correct.
        """
        assert resolve_combo(outfit="Castrol T-shirt", background="Background 1") == (
            "u1_tshirt",
            "bg1_white_suv",
        )
        assert resolve_combo(outfit="Castrol Uniform", background="Background 3") == (
            "u2_uniform",
            "bg3_hatchback_hood",
        )

    def test_the_clients_own_reference_words_also_resolve(self):
        # Their combination map describes the same three as SUV / Sedan /
        # Hatchback. Same fact written two ways, not a guess at a new value.
        assert resolve_combo(outfit="Uniform 1", background="SUV") == (
            "u1_tshirt",
            "bg1_white_suv",
        )
        assert resolve_combo(outfit="Uniform 2", background="Sedan") == (
            "u2_uniform",
            "bg2_dark_sedan",
        )

    def test_is_case_and_space_insensitive(self):
        assert resolve_combo(outfit="  castrol t-shirt ", background=" BACKGROUND 1 ") == (
            "u1_tshirt",
            "bg1_white_suv",
        )

    def test_every_combination_has_exactly_one_plate(self):
        """All six the resolvers can produce must name a plate.

        A KeyError here means the outfit/background tables and the plate table
        disagree, which would be a job that resolves a combination with no
        artwork behind it.
        """
        codes = {
            plate_code(*resolve_combo(outfit=o, background=b))
            for o in ("Castrol T-shirt", "Castrol Uniform")
            for b in ("Background 1", "Background 2", "Background 3")
        }
        assert codes == {f"plate_0{n}" for n in range(1, 7)}

    def test_unmapped_outfit_raises_rather_than_defaulting(self):
        # A silent default would put a mechanic in the wrong uniform.
        with pytest.raises(UnknownOutfit):
            resolve_combo(outfit="Castrol Overall", background="SUV")

    def test_unmapped_background_raises(self):
        with pytest.raises(UnknownBackground):
            resolve_combo(outfit="Castrol T-shirt", background="Motorbike")
