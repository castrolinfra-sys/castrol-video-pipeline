"""Prep is pure functions and zero I/O, so it is tested directly."""

from __future__ import annotations

import pytest

from castrol_pipeline.prep.normalise import (
    expand_for_speech,
    normalise_address,
    normalise_phone,
    spoken_place_from,
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
    """Free text, confirmed by the client 2026-09-09.

    This used to require exactly `Locality, City`. A mechanic writing their own
    address normally - one word, or three clauses with a landmark - was
    rejected for it, which is not a data problem, it is an address.
    """

    @pytest.mark.parametrize(
        "raw",
        [
            "Mumbai",
            "Dombivili, Thane",
            "Beturkar Pada, Opposite New National Hospital, Andheri",
        ],
    )
    def test_any_shape_is_accepted(self, raw):
        assert normalise_address(raw) is not None

    def test_collapses_whitespace_and_drops_empty_segments(self):
        assert normalise_address("  Andheri ,   Mumbai ") == "Andheri, Mumbai"
        assert normalise_address("Andheri, , Mumbai") == "Andheri, Mumbai"

    def test_the_text_is_otherwise_left_alone(self):
        # The card prints this. Inventing punctuation for a stranger's address
        # is not ours to do.
        assert normalise_address("Shop 4 - MG Rd.") == "Shop 4 - MG Rd."

    @pytest.mark.parametrize("raw", ["", "   ", ",", None])
    def test_only_empty_is_rejected(self, raw):
        assert normalise_address(raw) is None


class TestSpokenPlace:
    def test_a_long_address_is_spoken_as_its_last_segment(self):
        # Reading the whole string aloud puts a hospital landmark in a
        # 30-second ad.
        assert (
            spoken_place_from("Beturkar Pada, Opposite New National Hospital, Andheri")
            == "Andheri"
        )

    @pytest.mark.parametrize(
        "address,said",
        [("Mumbai", "Mumbai"), ("Dombivili, Thane", "Dombivili, Thane")],
    )
    def test_one_or_two_segments_are_already_the_spoken_form(self, address, said):
        assert spoken_place_from(address) == said

    @pytest.mark.parametrize(
        "address,said",
        [
            # The real row that exposed this: no commas at all, so splitting on
            # them leaves the PIN in and the voice reads it out.
            ("Sector 3 Asian market pushp vihar south delhi 110017",
             "Sector 3 Asian market pushp vihar south delhi"),
            ("Ashram chowk opposite ashram metro station 110014",
             "Ashram chowk opposite ashram metro station"),
            ("Beturkar Pada, Opposite Hospital, Andheri 400053", "Andheri"),
        ],
    )
    def test_a_trailing_pin_code_is_not_spoken(self, address, said):
        assert spoken_place_from(address) == said

    def test_a_house_number_survives(self):
        # The PIN rule is anchored to the END and to SIX digits, so it must not
        # reach a building number - which is the only other digit run here.
        assert spoken_place_from("K 68 hari nagar ashram chowk") == (
            "K 68 hari nagar ashram chowk"
        )

    def test_an_address_that_is_only_a_pin_is_kept(self):
        # Stripping it would leave the voice with nothing to say at all, which
        # is worse than saying six digits.
        assert spoken_place_from("110017") == "110017"


class TestSpeechExpansion:
    """Short runs of digits are QUANTITIES; long runs are SEQUENCES.

    `K 68` is a house number and must be said "aṭṭhaasaṭh", not "chhah aath" -
    a mechanic heard the second one in his own address. A PIN code is the other
    way round: read as a quantity it becomes "one lakh ten thousand seventeen".
    """

    @pytest.mark.parametrize("text", ["K 68", "Shop 24", "Sector 3", "Plot 120"])
    def test_short_runs_are_left_for_the_voice_to_read_as_a_number(self, text):
        # NOT spelled into Hindi words here. Hindi numerals are irregular - 68
        # is "aṭṭhaasaṭh", not a compound of 6 and 8 - so a hand-written table
        # of them is a mispronunciation waiting to reach a client video, so the
        # digits go to the voice as digits and it reads them as a quantity.
        assert expand_for_speech(text) == text

    @pytest.mark.parametrize("run", ["110017", "9773128990", "2026"])
    def test_long_runs_are_still_spelled_out_digit_by_digit(self, run):
        said = expand_for_speech(run)
        assert run not in said
        assert said.split() == [
            {"0": "zero", "1": "ek", "2": "do", "3": "teen", "4": "chaar",
             "5": "paanch", "6": "chhah", "7": "saat", "8": "aath", "9": "nau"}[c]
            for c in run
        ]

    def test_the_boundary_is_three_digits(self):
        assert expand_for_speech("999") == "999"
        assert expand_for_speech("1000") != "1000"

    def test_expands_abbreviations(self):
        assert "Road" in expand_for_speech("MG Rd.")

    def test_is_not_applied_to_card_text(self):
        # Guard against the two paths being confused: fill_script must keep the
        # display text unexpanded while the spoken text is expanded. A long run
        # is what shows the two paths diverging now that a short one is
        # identical in both.
        filled = fill_script(
            version="v1", name="Raju", workshop="Shop 240424", locality="Andheri"
        )
        assert "Shop 240424" in filled.display_text
        assert "Shop 240424" not in filled.spoken_text
        assert "do chaar zero chaar do chaar" in filled.spoken_text


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
