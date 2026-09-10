"""The composite stage trims the silent tail off an avatar render.

kling-avatar-v2 returns a video LONGER than the audio it was given. Measured on
a real render: 27.47s of video carrying 25.57s of speech — a 1.9s tail in which
the avatar keeps moving with nothing to say, and it is the last thing a viewer
sees before the video ends.

ffmpeg cuts it rather than the prompt asking for stillness: the model takes no
duration parameter and cannot be relied on to stop on cue, while a trim is
exact. It is also free and needs no re-render, since those frames were billed
at submit either way.

That 1902ms drift is what `ChecksStage.duration_matches_audio` measures against
a 1500ms tolerance, so before the trim every job was failing that check —
silently, because checks are logged rather than blocking.
"""

from __future__ import annotations


class TestCompositeTrimsToSpeech:
    """kling-avatar-v2 returns video LONGER than the audio it was given.

    Measured: 27.47s of video carrying 25.57s of speech — a 1.9s tail in which
    the avatar keeps moving with nothing to say, and it is the last thing the
    viewer sees. `media.composite` cuts it with ffmpeg rather than asking the
    model for stillness, because the model takes no duration and cannot be
    relied on to stop on cue.

    That 1902ms drift is also what `ChecksStage.duration_matches_audio`
    measures against a 1500ms tolerance — so before the trim, every job was
    failing that check silently.
    """

    def test_it_probes_the_AUDIO_stream_not_the_container(self, monkeypatch, tmp_path):
        """The distinction is the whole bug.

        `probe_duration_seconds` asks for `format=duration`, which is the
        CONTAINER — and for an avatar render the container is as long as the
        VIDEO. Reading that would compute a trim of exactly zero.
        """
        from castrol_pipeline.stages import media

        seen = {}

        def fake_run(cmd, what):
            seen["cmd"] = cmd
            class P:
                stdout = "25.565011\n"
            return P()

        monkeypatch.setattr(media, "_run", fake_run)
        assert media.audio_stream_duration_seconds(tmp_path / "v.mp4") == 25.565011
        cmd = seen["cmd"]
        assert "-select_streams" in cmd and cmd[cmd.index("-select_streams") + 1] == "a:0"
        assert "stream=duration" in cmd, "container duration is the wrong number"

    def test_missing_audio_duration_fails_open(self, tmp_path, monkeypatch):
        """An unreadable audio stream must ship the untrimmed video.

        Failing closed would park a finished job — one that already cost a
        dollar at the avatar step — over a cosmetic tail.
        """
        from castrol_pipeline.stages import media

        calls = {}

        def fake_run(cmd, what):
            calls["cmd"] = cmd
            class P:
                stdout = ""
            return P()

        monkeypatch.setattr(media, "audio_stream_duration_seconds", lambda p: None)
        monkeypatch.setattr(media, "_run", fake_run)
        media.composite(tmp_path / "v.mp4", tmp_path / "c.png", tmp_path / "o.mp4")
        assert "-t" not in calls["cmd"], "no duration known — do not trim"

    def test_trim_is_applied_when_the_duration_is_known(self, tmp_path, monkeypatch):
        from castrol_pipeline.stages import media

        calls = {}

        def fake_run(cmd, what):
            calls["cmd"] = cmd
            class P:
                stdout = ""
            return P()

        monkeypatch.setattr(media, "audio_stream_duration_seconds", lambda p: 25.565011)
        monkeypatch.setattr(media, "_run", fake_run)
        media.composite(tmp_path / "v.mp4", tmp_path / "c.png", tmp_path / "o.mp4")
        cmd = calls["cmd"]
        assert "-t" in cmd
        assert cmd[cmd.index("-t") + 1] == "25.565"
