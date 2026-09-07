"""Stub stages: deterministic fixtures, no vendor, no spend.

These exist so the orchestrator can be proved end-to-end before any paid call —
build order 1.4, "full batch drained with stub stages". They implement the same
Stage protocol as the real ones, hash the same inputs, and write real (if
trivial) media to storage, so swapping in a real stage is a config change and
not a rewrite.

They deliberately do NOT call common/budget.py: they reach no vendor, so
reserving against a daily cap would spend budget the pipeline never used.
"""

from __future__ import annotations

import struct
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

from ..common import hashing
from ..common.logging import get_logger
from ..common.s3 import get_storage, job_key
from ..config import get_settings
from .base import (
    AssetKind,
    AsyncSubmission,
    JobContext,
    PipelineStage,
    StageResult,
)

log = get_logger(__name__)

#: Simulated vendor latency, so concurrency and the claim/release path get
#: exercised rather than every stage returning instantly.
STUB_LATENCY_S = 0.05

STUB_VOICE_DURATION_MS = 35_000  # ~80 words, the figure spike 0.1 has to confirm
STUB_WIDTH, STUB_HEIGHT = 1080, 1920


def _sleep() -> None:
    time.sleep(STUB_LATENCY_S)


def _silent_wav(duration_ms: int, *, sample_rate: int = 24_000) -> bytes:
    frames = int(sample_rate * duration_ms / 1000)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "a.wav"
        with wave.open(str(path), "wb") as fh:
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(sample_rate)
            fh.writeframes(struct.pack("<h", 0) * frames)
        return path.read_bytes()


def _flat_png(width: int, height: int, colour: tuple[int, int, int]) -> bytes:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (width, height), colour).save(buf, format="PNG")
    return buf.getvalue()


def _stub_mp4(duration_ms: int, width: int, height: int) -> bytes:
    """A real, playable mp4 so downstream ffmpeg work has valid input."""
    seconds = max(duration_ms / 1000, 0.5)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "v.mp4"
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=darkslategray:s={width}x{height}:d={seconds}",
            "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono:d={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
            "-c:a", "aac", "-shortest", str(out),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return out.read_bytes()


def card_payload(ctx: JobContext) -> dict[str, Any]:
    """Exactly what the card renders. Part of the composite input hash."""
    return {
        "name": ctx.user_name,
        "workshop": ctx.workshop_name,
        "address": f"{ctx.locality}, {ctx.city}",
        "phone": ctx.phone_e164,
    }


# ------------------------------------------------------------------ stages --


class StubPrep:
    name = PipelineStage.PREP
    is_async = False

    def input_hash(self, ctx: JobContext) -> str:
        s = get_settings()
        return hashing.prep_hash(ctx.raw, s.script_version, s.normalise_rules_version)

    def run(self, ctx: JobContext) -> StageResult:
        from ..prep.script import fill_script

        filled = fill_script(
            version=ctx.script_version,
            name=ctx.user_name,
            workshop=ctx.workshop_name,
            locality=ctx.locality,
        )
        return StageResult(
            meta={
                "spoken_text": filled.spoken_text,
                "display_text": filled.display_text,
                "script_version": filled.version,
            }
        )


class StubAudio:
    name = PipelineStage.AUDIO
    is_async = False

    def input_hash(self, ctx: JobContext) -> str:
        s = get_settings()
        text = ctx.upstream.get("prep", {}).get("meta", {}).get("spoken_text", "")
        return hashing.audio_hash(
            text, s.tts_voice_id or "stub-voice", s.tts_model_id or "stub-tts"
        )

    def run(self, ctx: JobContext) -> StageResult:
        _sleep()
        data = _silent_wav(STUB_VOICE_DURATION_MS)
        obj = get_storage().put(
            job_key(ctx.job_id, "audio.wav"), data, content_type="audio/wav"
        )
        return StageResult(
            output_key=obj.key,
            sha256=obj.sha256,
            bytes=obj.bytes,
            asset_kind=AssetKind.AUDIO,
            duration_ms=STUB_VOICE_DURATION_MS,
            vendor="stub",
            model_id="stub-tts",
        )


class StubImage:
    name = PipelineStage.IMAGE
    is_async = False

    def input_hash(self, ctx: JobContext) -> str:
        s = get_settings()
        return hashing.image_hash(
            plate_sha256=ctx.upstream.get("plate", {}).get("sha256", "stub-plate"),
            photo_sha256=ctx.upstream.get("source_photo", {}).get("sha256", "stub-photo"),
            prompt_version=s.image_prompt_version,
            model_id=s.image_edit_model_id or "stub-image",
        )

    def run(self, ctx: JobContext) -> StageResult:
        _sleep()
        data = _flat_png(STUB_WIDTH, STUB_HEIGHT, (40, 60, 70))
        obj = get_storage().put(
            job_key(ctx.job_id, "image_edit.png"), data, content_type="image/png"
        )
        return StageResult(
            output_key=obj.key,
            sha256=obj.sha256,
            bytes=obj.bytes,
            asset_kind=AssetKind.IMAGE_EDIT,
            width=STUB_WIDTH,
            height=STUB_HEIGHT,
            vendor="stub",
            model_id="stub-image",
        )


class StubVideo:
    """Async on purpose: submits, returns, and lets the poller reconcile.

    This is the stage whose shape matters most to prove — a worker that blocks
    on a 4-minute vendor poll is a worker not doing the other 200 jobs.
    """

    name = PipelineStage.VIDEO
    is_async = True

    def input_hash(self, ctx: JobContext) -> str:
        s = get_settings()
        return hashing.video_hash(
            image_edit_sha256=ctx.upstream_sha(PipelineStage.IMAGE),
            audio_sha256=ctx.upstream_sha(PipelineStage.AUDIO),
            model_id=s.video_model_id or "stub-video",
            params={"fps": 25},
        )

    def run(self, ctx: JobContext) -> AsyncSubmission:
        _sleep()
        # A real vendor returns a task id here. The stub derives one that is
        # stable per job so a re-poll is deterministic.
        return AsyncSubmission(
            vendor="stub",
            vendor_task_id=f"stub-task-{ctx.job_id}",
            model_id="stub-video",
            params={"fps": 25},
        )

    def poll(self, vendor_task_id: str, ctx: JobContext) -> StageResult | None:
        duration = ctx.upstream.get("audio", {}).get("duration_ms") or STUB_VOICE_DURATION_MS
        data = _stub_mp4(int(duration), STUB_WIDTH, STUB_HEIGHT)
        obj = get_storage().put(
            job_key(ctx.job_id, "video_raw.mp4"), data, content_type="video/mp4"
        )
        return StageResult(
            output_key=obj.key,
            sha256=obj.sha256,
            bytes=obj.bytes,
            asset_kind=AssetKind.VIDEO_RAW,
            duration_ms=int(duration),
            width=STUB_WIDTH,
            height=STUB_HEIGHT,
            vendor="stub",
            model_id="stub-video",
        )


class StubComposite:
    name = PipelineStage.COMPOSITE
    is_async = False

    def input_hash(self, ctx: JobContext) -> str:
        s = get_settings()
        return hashing.composite_hash(
            video_in_sha256=ctx.upstream_sha(PipelineStage.VIDEO),
            card_payload=card_payload(ctx),
            card_template_version=s.card_template_version,
        )

    def run(self, ctx: JobContext) -> StageResult:
        _sleep()
        storage = get_storage()
        data = storage.get(ctx.upstream_key(PipelineStage.VIDEO))
        obj = storage.put(
            job_key(ctx.job_id, "video_final.mp4"), data, content_type="video/mp4"
        )
        return StageResult(
            output_key=obj.key,
            sha256=obj.sha256,
            bytes=obj.bytes,
            asset_kind=AssetKind.VIDEO_FINAL,
            duration_ms=ctx.upstream.get("video", {}).get("duration_ms"),
            width=STUB_WIDTH,
            height=STUB_HEIGHT,
            meta={"card_payload": card_payload(ctx), "stub": "card not burned in"},
        )


class StubChecks:
    name = PipelineStage.CHECKS
    is_async = False

    def input_hash(self, ctx: JobContext) -> str:
        return hashing.canonical_hash(
            {"stage": "checks", "video": ctx.upstream_sha(PipelineStage.COMPOSITE)}
        )

    def run(self, ctx: JobContext) -> StageResult:
        _sleep()
        # Checks are logged, not blocking, this release.
        return StageResult(
            meta={"checks": [{"check_name": "stub_file_integrity", "passed": True}]}
        )


class StubPublish:
    name = PipelineStage.PUBLISH
    is_async = False

    def input_hash(self, ctx: JobContext) -> str:
        return hashing.publish_hash(ctx.upstream_sha(PipelineStage.COMPOSITE))

    def run(self, ctx: JobContext) -> StageResult:
        from ..common.s3 import delivery_key

        _sleep()
        storage = get_storage()
        data = storage.get(ctx.upstream_key(PipelineStage.COMPOSITE))
        obj = storage.put(delivery_key(), data, content_type="video/mp4")
        return StageResult(output_key=obj.key, sha256=obj.sha256, bytes=obj.bytes)


class StubDeliver:
    name = PipelineStage.DELIVER
    is_async = False

    def input_hash(self, ctx: JobContext) -> str:
        key = ctx.upstream_key(PipelineStage.PUBLISH)
        return hashing.deliver_hash(key, ctx.phone_e164)

    def run(self, ctx: JobContext) -> StageResult:
        _sleep()
        # The stub records the delivery without POSTing anything. Nothing in
        # stub mode may reach the client's webhook.
        key = ctx.upstream_key(PipelineStage.PUBLISH)
        return StageResult(
            meta={
                "stub_cdn_url": f"stub://{key}",
                "phone_e164": ctx.phone_e164,
                "posted": False,
            }
        )


STUB_STAGES: dict[PipelineStage, Any] = {
    PipelineStage.PREP: StubPrep(),
    PipelineStage.AUDIO: StubAudio(),
    PipelineStage.IMAGE: StubImage(),
    PipelineStage.VIDEO: StubVideo(),
    PipelineStage.COMPOSITE: StubComposite(),
    PipelineStage.CHECKS: StubChecks(),
    PipelineStage.PUBLISH: StubPublish(),
    PipelineStage.DELIVER: StubDeliver(),
}
