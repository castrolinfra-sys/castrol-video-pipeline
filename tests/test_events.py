"""The durable event log: what it must never store, and what it must survive.

Two properties matter more than the happy path.

  * A presigned URL is a bearer credential. Writing one into a table the admin
    panel renders and support threads quote hands out a 6-hour read of a
    private object, long after the call it was minted for.
  * Recording an event must never fail a stage. The stage above it may have
    just spent a dollar; turning a logging outage into a stage failure turns it
    into a double charge on the retry.
"""

from __future__ import annotations

from castrol_pipeline.common import events


class TestScrubbing:
    def test_credentials_are_dropped(self):
        out = events._scrub(
            {"api_key": "sk-live-123", "Authorization": "Bearer x", "ok": 1}
        )
        assert out["api_key"] == "[redacted]"
        assert out["Authorization"] == "[redacted]"
        assert out["ok"] == 1

    def test_presigned_signature_is_stripped_but_the_path_survives(self):
        url = (
            "https://s3.ap-south-1.amazonaws.com/bucket/castrol/jobs/j1/audio.mp3"
            "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc123&X-Amz-Expires=21600"
        )
        got = events._scrub({"audio_url": url})["audio_url"]
        # Keep enough to know WHICH object was handed over...
        assert got.startswith(
            "https://s3.ap-south-1.amazonaws.com/bucket/castrol/jobs/j1/audio.mp3"
        )
        # ...and nothing anyone can replay.
        assert "abc123" not in got
        assert "X-Amz-Signature" not in got

    def test_plain_urls_are_left_alone(self):
        url = "https://d1dgdtphnngtpp.cloudfront.net/castrol/deliver/x/video.mp4"
        assert events._scrub({"cdn_url": url})["cdn_url"] == url

    def test_matching_is_case_insensitive(self):
        assert events._scrub({"APIKEY": "x"})["APIKEY"] == "[redacted]"
        assert events._scrub({"vendor_token": "x"})["vendor_token"] == "[redacted]"


class TestRecordingNeverRaises:
    def test_a_dead_database_does_not_propagate(self, monkeypatch, capsys):
        import castrol_pipeline.common.db as db_module

        def explode(*_args, **_kwargs):
            raise RuntimeError("connection pool is closed")

        monkeypatch.setattr(db_module, "execute", explode)

        # Must return normally. The caller may have just paid a vendor.
        events.record_event("video.submitted", job_id="j1", stage="video")

        assert "events.write_failed" in capsys.readouterr().out
