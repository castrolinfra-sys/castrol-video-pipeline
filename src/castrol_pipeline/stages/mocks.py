"""Mock paid stages for the rehearsal: `STAGE_MODE=mock`.

Stub mode fakes all eight stages. This fakes only the three that cost money -
audio, image, video - and leaves prep, composite, checks, publish and deliver
REAL. That is the point of a rehearsal: the client's real export, their real
names and addresses on the real card, real ffmpeg, a real CDN upload - and no
provider call, so no spend.

Each mock subclasses the real stage and keeps its `input_hash`, so scheduling,
idempotency and the DAG behave exactly as they will in production. Only `run`
and `poll` are replaced, and neither touches `common/budget.py` or a vendor
key. What they record as `cost_usd` is what the real call WOULD have cost, so
`castrol costs` after a rehearsal is a quote for the real batch.

Failures are injected by `MOCK_FAILURES` (see `parse_failures`), picked per
(job, stage) by a hash, so re-running the rehearsal fails the same jobs.

The guards that stop a rehearsal mixing with a real run live in `dryrun.py`.
"""

from __future__ import annotations

import hashlib
import math
import subprocess
import tempfile
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..common import budget, db
from ..common.errors import (
    BudgetExhausted,
    StageErrorCode,
    StageFailure,
    VendorRejected,
    VendorTimeout,
)
from ..common.events import record_event
from ..common.s3 import get_storage, job_key
from ..config import get_settings
from . import media
from .base import AssetKind, AsyncSubmission, JobContext, PipelineStage, StageResult
from .real import (
    REAL_STAGES,
    AudioStage,
    ImageStage,
    VideoStage,
    _plate_row,
    _spoken_text,
)

#: Written to `stage_runs.vendor` by every mock submit. The launch guard looks
#: for it: a real run refuses to start while any row carries it.
MOCK_VENDOR = "mock"

#: Measured, not guessed: the real ~80-word script rendered at 27.47s.
WORDS_PER_SECOND = 2.9

MOCKABLE = (PipelineStage.AUDIO, PipelineStage.IMAGE, PipelineStage.VIDEO)
FAILURE_KINDS = ("VENDOR_REJECTED", "VENDOR_TIMEOUT", "TRANSIENT", "BUDGET_EXHAUSTED")


# ---------------------------------------------------------------- failures --


@dataclass(frozen=True)
class FailureRule:
    stage: PipelineStage
    kind: str
    percent: int


def parse_failures(spec: str) -> list[FailureRule]:
    """"image:VENDOR_REJECTED:10,video:VENDOR_TIMEOUT:5" -> rules. Pure.

    Raises on anything it does not understand. A typo that silently meant "no
    failures" would make a rehearsal look cleaner than production will be.
    """
    rules: list[FailureRule] = []
    for part in filter(None, (p.strip() for p in spec.split(","))):
        try:
            stage_s, kind, pct_s = (x.strip() for x in part.split(":"))
            stage, pct = PipelineStage(stage_s), int(pct_s)
        except ValueError:
            raise ValueError(f"MOCK_FAILURES: cannot read {part!r}") from None
        if stage not in MOCKABLE:
            raise ValueError(f"MOCK_FAILURES: {stage} is a real stage in mock mode")
        if kind not in FAILURE_KINDS:
            raise ValueError(f"MOCK_FAILURES: {kind!r} is not one of {FAILURE_KINDS}")
        if not 0 <= pct <= 100:
            raise ValueError(f"MOCK_FAILURES: {pct} is not a percentage")
        rules.append(FailureRule(stage, kind, pct))
    for stage in MOCKABLE:
        total = sum(r.percent for r in rules if r.stage == stage)
        if total > 100:
            raise ValueError(f"MOCK_FAILURES: {stage} adds up to {total}%")
    return rules


def bucket(job_id: str, stage: PipelineStage) -> int:
    """0-99, stable per (job, stage). Pure."""
    digest = hashlib.sha256(f"{job_id}:{stage}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % 100


def failure_for(job_id: str, stage: PipelineStage, rules: list[FailureRule]) -> str | None:
    """Which failure, if any, this job gets at this stage. Pure.

    Rules for one stage stack as consecutive bands, so 10% + 5% on a stage is
    15% of jobs, not two overlapping 10% and 5% slices.
    """
    b, floor = bucket(job_id, stage), 0
    for rule in (r for r in rules if r.stage == stage):
        if floor <= b < floor + rule.percent:
            return rule.kind
        floor += rule.percent
    return None


def _failure(ctx: JobContext, stage: PipelineStage) -> str | None:
    return failure_for(ctx.job_id, stage, parse_failures(get_settings().mock_failures))


def _attempt(ctx: JobContext, stage: PipelineStage) -> int:
    row = db.fetch_one(
        """
        SELECT attempts FROM stage_runs
         WHERE job_id = %(job)s AND stage = %(stage)s
           AND status IN ('claimed', 'running')
         ORDER BY created_at DESC LIMIT 1;
        """,
        {"job": ctx.job_id, "stage": str(stage)},
    )
    return int(row["attempts"]) if row else 1


def _fail_at_submit(ctx: JobContext, stage: PipelineStage) -> None:
    """Failures that happen when the call is made."""
    kind = _failure(ctx, stage)
    if kind == "BUDGET_EXHAUSTED":
        raise BudgetExhausted(f"[mock] {stage}: daily cap reached")
    if kind == "TRANSIENT" and _attempt(ctx, stage) <= 1:
        raise StageFailure(
            f"[mock] {stage}: transient provider error, first attempt only",
            code=StageErrorCode.VENDOR_TIMEOUT,
        )


def _task_id(stage: PipelineStage, ctx: JobContext) -> str:
    # The submit time rides in the id, so a poll needs no state to know how
    # long the "render" has been going.
    return f"mock-{stage}-{int(time.time())}-{ctx.job_id}"


def _age_s(task_id: str) -> float:
    return time.time() - int(task_id.split("-")[2])


def _ffmpeg(cmd: list[str], what: str) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise StageFailure(
            f"[mock] {what} failed: {proc.stderr.strip()[:500]}",
            code=StageErrorCode.FFMPEG_FAILED,
        )


def mock_audio_seconds(text: str) -> float:
    """How long the real voice would take to say this. Pure."""
    return max(3.0, round(len(text.split()) / WORDS_PER_SECOND, 2))


# ------------------------------------------------------------------ stages --


class MockAudio(AudioStage):
    """A quiet tone as long as the real voiceover would be, as MP3."""

    def run(self, ctx: JobContext) -> StageResult:
        s = get_settings()
        text = _spoken_text(ctx)
        _fail_at_submit(ctx, self.name)
        kind = _failure(ctx, self.name)
        if kind == "VENDOR_REJECTED":
            raise VendorRejected("[mock] audio: provider refused the text")
        if kind == "VENDOR_TIMEOUT":
            raise VendorTimeout("[mock] audio: provider did not answer")

        seconds = mock_audio_seconds(text)
        with tempfile.TemporaryDirectory() as tmp:
            mp3 = Path(tmp) / "audio.mp3"
            _ffmpeg(
                ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                 "-i", f"sine=frequency=220:duration={seconds}",
                 "-af", "volume=0.08", "-codec:a", "libmp3lame", "-b:a", "128k", str(mp3)],
                "mock audio",
            )
            duration = media.probe_duration_seconds(mp3)
            stored = get_storage().put_file(
                job_key(ctx.job_id, "audio.mp3"), mp3, content_type="audio/mpeg"
            )

        record_event("audio.synthesised", job_id=ctx.job_id, stage=str(self.name),
                     mock=True, chars=len(text), seconds=round(duration, 2))
        return StageResult(
            output_key=stored.key,
            sha256=stored.sha256,
            asset_kind=AssetKind.AUDIO,
            bytes=stored.bytes,
            duration_ms=int(round(duration * 1000)),
            vendor=MOCK_VENDOR,
            model_id=f"mock:{s.tts_model_id}",
            cost_usd=budget.tts_cost_usd(len(text)),
            billed_units=Decimal(len(text)),
        )


class MockImage(ImageStage):
    """Hands back the job's own plate artwork, so the card sits on a real frame."""

    def run(self, ctx: JobContext) -> AsyncSubmission:
        _plate_row(ctx)  # same refusal as the real stage on an inactive plate
        _fail_at_submit(ctx, self.name)
        task_id = _task_id(self.name, ctx)
        record_event("image.submitted", job_id=ctx.job_id, stage=str(self.name),
                     mock=True, vendor_task_id=task_id)
        return AsyncSubmission(
            vendor=MOCK_VENDOR,
            vendor_task_id=task_id,
            model_id=f"mock:{get_settings().image_edit_model_id}",
            params={"mock": True},
            cost_usd=budget.image_cost_usd(),
            billed_units=Decimal(1),
        )

    def poll(self, vendor_task_id: str, ctx: JobContext) -> StageResult | None:
        kind = _failure(ctx, self.name)
        if kind == "VENDOR_TIMEOUT" or _age_s(vendor_task_id) < get_settings().mock_image_seconds:
            return None  # a timeout never answers; the poller's own limit fails it
        if kind == "VENDOR_REJECTED":
            raise VendorRejected("[mock] image: content safety rejected the edit")

        store = get_storage()
        plate = _plate_row(ctx)
        with tempfile.TemporaryDirectory() as tmp:
            local = store.download(str(plate["s3_key"]), Path(tmp) / "image_edit.png")
            width, height = media.probe_dimensions(local)
            stored = store.put_file(
                job_key(ctx.job_id, "image_edit.png"), local, content_type="image/png"
            )
        record_event("image.completed", job_id=ctx.job_id, stage=str(self.name),
                     mock=True, size=f"{width}x{height}")
        return StageResult(
            output_key=stored.key,
            sha256=stored.sha256,
            asset_kind=AssetKind.IMAGE_EDIT,
            bytes=stored.bytes,
            width=width,
            height=height,
            vendor=MOCK_VENDOR,
            model_id=f"mock:{get_settings().image_edit_model_id}",
        )


class MockVideo(VideoStage):
    """The edited frame held still over the audio, at the tier's real size."""

    def run(self, ctx: JobContext) -> AsyncSubmission:
        s = get_settings()
        duration_ms = ctx.upstream[str(PipelineStage.AUDIO)].get("duration_ms")
        if not duration_ms:
            raise StageFailure("[mock] audio duration missing",
                               code=StageErrorCode.ASSET_MISSING)
        _fail_at_submit(ctx, self.name)
        seconds = Decimal(str(duration_ms)) / 1000
        billed = Decimal(math.ceil(seconds))
        task_id = _task_id(self.name, ctx)
        record_event("video.submitted", job_id=ctx.job_id, stage=str(self.name),
                     mock=True, vendor_task_id=task_id, billed_seconds=float(billed))
        return AsyncSubmission(
            vendor=MOCK_VENDOR,
            vendor_task_id=task_id,
            model_id=f"mock:{s.video_model_id}",
            params={**self._params(), "mock": True},
            cost_usd=budget.video_cost_usd(float(seconds), pro=s.video_is_pro),
            billed_seconds=billed,
        )

    def poll(self, vendor_task_id: str, ctx: JobContext) -> StageResult | None:
        s = get_settings()
        kind = _failure(ctx, self.name)
        if kind == "VENDOR_TIMEOUT" or _age_s(vendor_task_id) < s.mock_video_seconds:
            return None
        if kind == "VENDOR_REJECTED":
            raise VendorRejected("[mock] video: provider rejected the render")

        # 720x1280 on standard, 1072x1920 on pro: what the real tiers return.
        w, h = (1072, 1920) if s.video_is_pro else (720, 1280)
        store = get_storage()
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            image = store.download(ctx.upstream_key(PipelineStage.IMAGE), work / "frame.png")
            audio = store.download(ctx.upstream_key(PipelineStage.AUDIO), work / "audio.mp3")
            out = work / "video_raw.mp4"
            # -t, not just -shortest: a looped still overshoots the audio by
            # about a second under -shortest alone, and the real model's output
            # is exactly as long as the audio that drives it.
            seconds = media.probe_duration_seconds(audio)
            _ffmpeg(
                ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(image),
                 "-i", str(audio), "-t", f"{seconds:.3f}",
                 "-vf", f"scale={w}:{h},format=yuv420p", "-r", "25",
                 "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
                 "-c:a", "aac", "-shortest", str(out)],
                "mock video",
            )
            width, height = media.probe_dimensions(out)
            duration = media.probe_duration_seconds(out)
            stored = store.put_file(
                job_key(ctx.job_id, "video_raw.mp4"), out, content_type="video/mp4"
            )
        record_event("video.completed", job_id=ctx.job_id, stage=str(self.name),
                     mock=True, seconds=round(duration, 2), size=f"{width}x{height}")
        return StageResult(
            output_key=stored.key,
            sha256=stored.sha256,
            asset_kind=AssetKind.VIDEO_RAW,
            bytes=stored.bytes,
            duration_ms=int(round(duration * 1000)),
            width=width,
            height=height,
            vendor=MOCK_VENDOR,
            model_id=f"mock:{s.video_model_id}",
        )


MOCK_STAGES: dict[PipelineStage, Any] = {
    **REAL_STAGES,
    PipelineStage.AUDIO: MockAudio(),
    PipelineStage.IMAGE: MockImage(),
    PipelineStage.VIDEO: MockVideo(),
}
