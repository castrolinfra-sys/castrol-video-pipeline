"""Key construction and URL building.

These look trivial and are not. A prefix applied in two places produced a
CloudFront 403 that reads exactly like a permissions failure, and cost a real
debugging session before the cause turned out to be a doubled `castrol/`.

The rule under test: a key carries its prefix from the moment it is built, and
nothing downstream adds or removes one.
"""

from __future__ import annotations

import pytest

from castrol_pipeline.common import s3
from castrol_pipeline.config import get_settings


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("S3_PREFIX", "castrol/")
    monkeypatch.setenv("CDN_BASE_URL", "https://d1dgdtphnngtpp.cloudfront.net")
    get_settings.cache_clear()
    s3.reset_storage()
    yield
    get_settings.cache_clear()
    s3.reset_storage()


class TestKeysCarryTheirPrefix:
    def test_job_key(self):
        assert s3.job_key("abc", "audio.mp3") == "castrol/jobs/abc/audio.mp3"

    def test_plate_key(self):
        assert s3.plate_key("polo", "bg1", "deadbeef") == (
            "castrol/plates/polo_bg1/deadbeef.png"
        )

    def test_delivery_key_is_random_and_not_derived(self):
        a, b = s3.delivery_key(), s3.delivery_key()
        assert a != b, "two deliveries must not collide"
        assert a.startswith("castrol/deliver/")
        assert a.endswith("/video.mp4")

    def test_missing_trailing_slash_is_tolerated(self, monkeypatch):
        # A prefix typed without its slash must not concatenate into
        # `castroljobs/...`.
        monkeypatch.setenv("S3_PREFIX", "castrol")
        get_settings.cache_clear()
        assert s3.job_key("abc", "x.mp4") == "castrol/jobs/abc/x.mp4"

    def test_empty_prefix_yields_no_leading_slash(self, monkeypatch):
        monkeypatch.setenv("S3_PREFIX", "")
        get_settings.cache_clear()
        assert s3.job_key("abc", "x.mp4") == "jobs/abc/x.mp4"


class TestCdnUrl:
    def test_key_goes_in_verbatim(self):
        # The distribution has NO Origin Path, so the full key including the
        # prefix must appear. Dropping it is the 403.
        key = s3.job_key("abc", "final.mp4")
        assert s3.cdn_url(key) == (
            "https://d1dgdtphnngtpp.cloudfront.net/castrol/jobs/abc/final.mp4"
        )

    def test_does_not_double_the_prefix(self):
        url = s3.cdn_url(s3.delivery_key())
        assert url.count("castrol/") == 1

    def test_tolerates_a_trailing_slash_on_the_base(self, monkeypatch):
        monkeypatch.setenv("CDN_BASE_URL", "https://d1dgdtphnngtpp.cloudfront.net/")
        get_settings.cache_clear()
        assert "net//" not in s3.cdn_url("castrol/x.mp4")


class TestLocalBackendRefusesToPretend:
    def test_presign_is_not_faked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STORAGE_BACKEND", "local")
        monkeypatch.setenv("LOCAL_STORAGE_DIR", str(tmp_path))
        get_settings.cache_clear()
        s3.reset_storage()
        store = s3.get_storage()
        store.put("castrol/jobs/a/x.txt", b"hello")

        # A vendor fetches inputs over HTTP. Returning a file:// path or a
        # plausible-looking string here would fail inside the provider, hours
        # later, as an unexplained render failure.
        with pytest.raises(NotImplementedError, match="STORAGE_BACKEND=s3"):
            store.presigned_get_url("castrol/jobs/a/x.txt")

    def test_round_trip_and_copy(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STORAGE_BACKEND", "local")
        monkeypatch.setenv("LOCAL_STORAGE_DIR", str(tmp_path))
        get_settings.cache_clear()
        s3.reset_storage()
        store = s3.get_storage()

        src = s3.job_key("j1", "final.mp4")
        stored = store.put(src, b"video-bytes")
        assert stored.key == src

        dst = s3.delivery_key()
        copied = store.copy(src, dst)
        # Publishing COPIES: the working artefact must survive, because the
        # delivered object is deleted at 180 days and the evidence is not.
        assert store.exists(src)
        assert store.get(dst) == b"video-bytes"
        assert copied.bytes == stored.bytes
