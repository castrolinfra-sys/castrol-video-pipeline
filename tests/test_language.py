"""The spoken language, end to end short of a paid call.

Four promises, each of which breaks silently if it breaks at all:

  * Hindi does not move. Its prep and audio hashes are pinned as LITERALS,
    computed with the pre-multilingual `hashing.py`. If one of these fails,
    every open Hindi job re-runs audio on the next cycle - paid.
  * Placeholder copy never reaches a mechanic. An unapproved language fails at
    prep, terminally, before anything is spent.
  * That failure lifts by itself. Approval is in the prep hash, so the release
    that approves a language moves the hash and invariant 34 lets it go.
  * Unknown is rejected, never defaulted to Hindi.
"""

from __future__ import annotations

import dataclasses

import pytest

from castrol_pipeline.common import hashing
from castrol_pipeline.common.errors import (
    TERMINAL_STAGE_ERRORS,
    RejectCode,
    StageErrorCode,
    StageFailure,
)
from castrol_pipeline.config import Settings
from castrol_pipeline.intake.validate import Rejection, ValidRow, validate_row
from castrol_pipeline.prep import script
from castrol_pipeline.prep.script import (
    LANGUAGES,
    SUPPORTED_LANGUAGES,
    UnknownLanguage,
    current_version,
    get_language,
    resolve_language,
)
from castrol_pipeline.stages import real, stubs, vendors
from castrol_pipeline.stages.base import JobContext

from .test_intake import good_row

RAW = {"user_name": "Raju Shetty", "address": "Andheri, Mumbai"}


def _ctx(**over) -> JobContext:
    base = {
        "job_id": "j1",
        "submission_id": "s1",
        "script_version": "v2",
        "voice_id": "voice-1",
        "user_name": "Raju Shetty",
        "workshop_name": "Shetty Motors",
        "spoken_place": "Andheri",
        "phone_e164": "+919000000001",
        "raw": RAW,
    }
    return JobContext(**(base | over))


# ------------------------------------------------------------- registry --


class TestRegistry:
    def test_the_six_languages_this_release_ships(self):
        assert set(SUPPORTED_LANGUAGES) == {"hi", "ta", "te", "kn", "bn", "ml"}

    @pytest.mark.parametrize("lang", LANGUAGES, ids=lambda lang: lang.code)
    def test_every_template_fills_all_three_slots_and_nothing_else(self, lang):
        # Covers a language added later without this test being edited.
        for version, text in lang.templates.items():
            for slot in ("{name}", "{workshop}", "{locality}"):
                assert text.count(slot) == 1, (lang.code, version, slot)
            leftover = text
            for slot in ("{name}", "{workshop}", "{locality}"):
                leftover = leftover.replace(slot, "")
            assert "{" not in leftover and "}" not in leftover, (lang.code, version)

    @pytest.mark.parametrize("lang", LANGUAGES, ids=lambda lang: lang.code)
    def test_current_points_at_a_real_template(self, lang):
        assert current_version(lang.code) in lang.templates

    @pytest.mark.parametrize("lang", LANGUAGES, ids=lambda lang: lang.code)
    def test_a_code_is_the_shape_the_database_checks(self, lang):
        # 0016's CHECK is ^[a-z]{2,3}$ - a code outside it fails the INSERT.
        assert 2 <= len(lang.code) <= 3 and lang.code.isascii()
        assert lang.code.isalpha() and lang.code.islower()

    def test_only_hindi_is_approved_until_the_audio_test_passes(self):
        assert [lang.code for lang in LANGUAGES if lang.approved] == ["hi"]

    @pytest.mark.parametrize("code", ["ta", "te", "kn", "bn", "ml"])
    def test_new_jobs_get_the_clients_copy_not_our_draft(self, code):
        assert current_version(code) == "v2"
        assert "v1" in get_language(code).templates  # the draft stays on record

    def test_hindi_sends_no_language_field(self):
        assert get_language("hi").tts_language is None

    def test_hindi_follows_the_script_version_setting(self):
        assert current_version("hi") == Settings().script_version


class TestResolve:
    @pytest.mark.parametrize(
        "raw, code",
        [
            ("ta", "ta"), ("Tamil", "ta"), ("TAMIL", "ta"), ("  tamil ", "ta"),
            ("தமிழ்", "ta"), ("Telugu", "te"), ("తెలుగు", "te"),
            ("Kannada", "kn"), ("ಕನ್ನಡ", "kn"), ("Bengali", "bn"),
            ("Bangla", "bn"), ("বাংলা", "bn"), ("Malayalam", "ml"),
            ("മലയാളം", "ml"), ("Hindi", "hi"), ("Hinglish", "hi"),
            ("हिंदी", "hi"),
        ],
    )
    def test_every_spelling_resolves(self, raw, code):
        assert resolve_language(raw) == code

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_blank_is_hindi(self, raw):
        assert resolve_language(raw) == "hi"

    @pytest.mark.parametrize("raw", ["Odia", "or", "French", "tam"])
    def test_unknown_raises_rather_than_defaulting(self, raw):
        with pytest.raises(UnknownLanguage):
            resolve_language(raw)


# --------------------------------------------------------------- hashes --


class TestHindiIsPinned:
    """Literal values from the pre-multilingual hashing.py. Do not update
    these to make a failure go away - a change here re-renders Hindi."""

    PREP = "559325335bc1d28b0e4b975a893723ac4e50e5561866f4c0553829aeed0eaf5e"
    AUDIO = "b1b95e585d80f584fd1ca7f3c83a5ddf51fd64835d06cf79ab6d243daaccc837"

    def test_prep_hash(self):
        assert hashing.prep_hash(RAW, "v2", "n1") == self.PREP

    def test_prep_hash_ignores_language_args_for_hindi(self):
        assert hashing.prep_hash(
            RAW, "v2", "n1", language="hi", script_approved=False
        ) == self.PREP

    def test_audio_hash(self):
        assert hashing.audio_hash("नमस्ते", "voice-1", "sonic-3.6") == self.AUDIO

    def test_the_prep_stage_hashes_a_hindi_job_as_before(self, monkeypatch):
        s = Settings(script_version="v2", normalise_rules_version="n1")
        monkeypatch.setattr(real, "get_settings", lambda: s)
        monkeypatch.setattr(script, "get_settings", lambda: s, raising=False)
        monkeypatch.setattr("castrol_pipeline.config.get_settings", lambda: s)
        assert real.PrepStage().input_hash(_ctx()) == self.PREP


class TestVernacularHashes:
    def test_prep_moves_when_approval_flips(self):
        a = hashing.prep_hash(RAW, "v1", "n1", language="ta", script_approved=False)
        b = hashing.prep_hash(RAW, "v1", "n1", language="ta", script_approved=True)
        assert a != b

    def test_prep_differs_by_language(self):
        ta = hashing.prep_hash(RAW, "v1", "n1", language="ta", script_approved=False)
        te = hashing.prep_hash(RAW, "v1", "n1", language="te", script_approved=False)
        assert ta != te

    def test_audio_moves_when_the_field_is_toggled(self):
        without = hashing.audio_hash("text", "voice-1", "m")
        with_ = hashing.audio_hash("text", "voice-1", "m", tts_language="ta")
        assert without != with_

    def test_approving_a_language_moves_the_stage_hash(self, monkeypatch):
        # The real mechanism behind "the failure lifts by itself".
        ctx = _ctx(language="ta", script_version="v1")
        before = real.PrepStage().input_hash(ctx)
        approved = dataclasses.replace(get_language("ta"), approved=True)
        monkeypatch.setitem(script._BY_CODE, "ta", approved)
        assert real.PrepStage().input_hash(ctx) != before


# ----------------------------------------------------------------- prep --


@pytest.fixture
def quiet(monkeypatch):
    """PrepStage records an event; keep it off the database."""
    monkeypatch.setattr(real, "record_event", lambda *a, **kw: None)


def _settings(monkeypatch, **kw):
    s = Settings(**kw)
    for mod in (real, stubs):
        monkeypatch.setattr(mod, "get_settings", lambda s=s: s)
    return s


class TestPrepStage:
    def test_script_not_approved_is_terminal(self):
        assert StageErrorCode.SCRIPT_NOT_APPROVED in TERMINAL_STAGE_ERRORS

    @pytest.mark.parametrize("stage", [real.PrepStage(), stubs.StubPrep()])
    def test_an_unapproved_language_fails_before_anything_is_spent(
        self, stage, monkeypatch, quiet
    ):
        _settings(monkeypatch, allow_placeholder_scripts=False)
        with pytest.raises(StageFailure) as exc:
            stage.run(_ctx(language="ta", script_version="v1"))
        assert exc.value.code == StageErrorCode.SCRIPT_NOT_APPROVED

    @pytest.mark.parametrize("stage", [real.PrepStage(), stubs.StubPrep()])
    def test_the_flag_lets_a_test_render_through(self, stage, monkeypatch, quiet):
        _settings(monkeypatch, allow_placeholder_scripts=True)
        out = stage.run(_ctx(language="ta", script_version="v1"))
        assert out.meta["language"] == "ta"
        assert out.meta["script_approved"] is False
        assert "Raju Shetty" in out.meta["spoken_text"]
        assert "நான்" in out.meta["spoken_text"]

    def test_hindi_runs_without_the_flag(self, monkeypatch, quiet):
        _settings(monkeypatch, allow_placeholder_scripts=False)
        out = real.PrepStage().run(_ctx())
        assert out.meta["language"] == "hi"
        assert out.meta["spoken_text"]


# ---------------------------------------------------------------- voice --


class TestVoiceRequest:
    def _capture(self, monkeypatch):
        sent: dict = {}

        class _Resp:
            status_code = 200
            content = b"RIFF"
            text = ""

        class _Client:
            def __init__(self, *a, **kw): ...
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def post(self, url, headers=None, json=None):
                sent.update(json)
                return _Resp()

        monkeypatch.setattr(vendors.httpx, "Client", _Client)
        monkeypatch.setattr(
            vendors, "get_settings", lambda: Settings(CARTESIA_API_KEY="k")
        )
        return sent

    def test_none_sends_no_language_key(self, monkeypatch, tmp_path):
        sent = self._capture(monkeypatch)
        vendors.voice_tts("t", voice_id="v", model_id="m", dst=tmp_path / "a.wav")
        assert "language" not in sent

    def test_a_code_is_sent_as_given(self, monkeypatch, tmp_path):
        sent = self._capture(monkeypatch)
        vendors.voice_tts(
            "t", voice_id="v", model_id="m", dst=tmp_path / "a.wav", language="ta"
        )
        assert sent["language"] == "ta"


# --------------------------------------------------------------- intake --


class TestIntake:
    def test_an_absent_column_is_hindi(self):
        result = validate_row(good_row())
        assert isinstance(result, ValidRow)
        assert result.language == "hi"

    def test_the_column_resolves(self):
        result = validate_row(good_row(language="Tamil"))
        assert isinstance(result, ValidRow)
        assert result.language == "ta"

    def test_an_unknown_language_is_rejected(self):
        result = validate_row(good_row(language="Odia"))
        assert isinstance(result, Rejection)
        assert result.code == RejectCode.LANGUAGE_UNSUPPORTED

    def test_a_custom_column_name_is_read(self):
        result = validate_row(
            good_row(preferred_language="Kannada"),
            language_column="preferred_language",
        )
        assert isinstance(result, ValidRow)
        assert result.language == "kn"
