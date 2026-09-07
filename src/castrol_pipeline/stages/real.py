"""The real stages. Same protocol as the stubs, real providers, real spend.

Ported from `spikes/prototype.py` once it produced videos a human accepted. The
prototype proved the calls; this puts them under the orchestrator so that every
run is claimed, budgeted, hashed, persisted and logged.

What changes versus the prototype, and why:

  * **Both paid remote stages are async.** The prototype blocked on a poll loop.
    Here they submit, record the vendor task id, and release; the poller
    reconciles. Beyond throughput, this is a correctness fix — a submitted run
    sits in `running`, and the stuck-claim reaper only touches `claimed`. A
    synchronous stage that outlived STAGE_CLAIM_TIMEOUT_S would be reaped and
    re-run while the first call was still in flight, and billed twice.

  * **Every paid call goes through `budget.vendor_call`.** Nothing here reaches
    a vendor by any other route.

  * **Our S3 copy is the system of record.** Provider result URLs have an
    unmeasured TTL and are downloaded immediately.

  * **Idempotency is by `input_hash`**, which covers model ids and prompt and
    template versions — so changing a model id regenerates, and re-running
    after a crash at video does not repay for image and audio.
"""

from __future__ import annotations

import math
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from ..common import budget, db, hashing
from ..common.errors import AssetMissing, StageErrorCode, StageFailure
from ..common.events import record_event
from ..common.logging import get_logger
from ..common.s3 import cdn_url, delivery_key, get_storage, job_key
from ..config import get_settings
from ..prep.script import fill_script
from . import media, vendors
from .base import (
    AssetKind,
    AsyncSubmission,
    JobContext,
    PipelineStage,
    StageResult,
)

log = get_logger(__name__)


# ---------------------------------------------------------------- helpers --


def _plate_row(ctx: JobContext) -> dict[str, Any]:
    """The frozen plate for this job.

    `active` is checked here as well as at selection: an unapproved plate must
    never reach a paid call, and plate approval can be revoked between the two.
    """
    if not ctx.plate_id:
        raise AssetMissing("Job has no plate_id; prep should not have passed.")
    row = db.fetch_one(
        "SELECT id, s3_key, sha256, active FROM plates WHERE id = %(id)s;",
        {"id": ctx.plate_id},
    )
    if row is None:
        raise AssetMissing(f"No plate {ctx.plate_id}")
    if not row["active"]:
        raise AssetMissing(
            f"Plate {ctx.plate_id} is not active. An unapproved plate must never "
            "reach a paid call."
        )
    return row


def _asset(ctx: JobContext, kind: AssetKind) -> dict[str, Any]:
    row = db.fetch_one(
        """
        SELECT s3_key, sha256, bytes, duration_ms, width, height
          FROM assets
         WHERE job_id = %(job_id)s AND kind = %(kind)s
         ORDER BY created_at DESC LIMIT 1;
        """,
        {"job_id": ctx.job_id, "kind": str(kind)},
    )
    if row is None:
        raise AssetMissing(f"Job {ctx.job_id} has no {kind} asset")
    return row


def _spoken_text(ctx: JobContext) -> str:
    prep = ctx.upstream.get(str(PipelineStage.PREP), {})
    text = (prep.get("meta") or {}).get("spoken_text")
    if not text:
        raise StageFailure(
            "prep produced no spoken_text — the audio stage was scheduled "
            "before prep succeeded.",
            code=StageErrorCode.ASSET_MISSING,
        )
    return str(text)


def _card_fields(ctx: JobContext) -> dict[str, str]:
    """What is printed on the card.

    The FULL address, not the spoken `Locality, City` — the card is read, so
    the landmark is useful there; the voiceover says only the area.
    Phone is card-only and never spoken.
    """
    row = db.fetch_one(
        "SELECT address_raw, phone_e164 FROM submissions WHERE id = %(id)s;",
        {"id": ctx.submission_id},
    )
    phone = (row or {}).get("phone_e164") or ctx.phone_e164
    return media.card_payload(
        name=ctx.user_name,
        workshop=ctx.workshop_name,
        address=(row or {}).get("address_raw") or ctx.locality,
        # The card shows the local 10-digit form, not E.164.
        phone=str(phone).removeprefix("+91"),
    )


# ------------------------------------------------------------ [prep] ------


class PrepStage:
    """Fill the script, resolve the spoken locality. No I/O beyond the DB."""

    name = PipelineStage.PREP
    is_async = False

    def input_hash(self, ctx: JobContext) -> str:
        s = get_settings()
        return hashing.prep_hash(ctx.raw, s.script_version, s.normalise_rules_version)

    def run(self, ctx: JobContext) -> StageResult:
        # The voice says the area, not the whole postal address. `locality` is
        # what the orchestrator split off address_normalized; city is appended
        # only when it is actually present, so we never say "Andheri, " alone.
        spoken_place = ", ".join(p for p in (ctx.locality, ctx.city) if p)
        filled = fill_script(
            version=get_settings().script_version,
            name=ctx.user_name,
            workshop=ctx.workshop_name,
            locality=spoken_place,
        )
        record_event(
            "prep.script_filled",
            job_id=ctx.job_id,
            stage=str(self.name),
            chars=len(filled.spoken_text),
            script_version=filled.version,
        )
        return StageResult(
            meta={
                "spoken_text": filled.spoken_text,
                "display_text": filled.display_text,
                "script_version": filled.version,
                "spoken_place": spoken_place,
            }
        )

    def poll(self, vendor_task_id: str, ctx: JobContext) -> StageResult | None:
        return None


# ----------------------------------------------------------- [A] audio ----


class AudioStage:
    """Cartesia TTS. Synchronous — the API returns bytes on the call.

    Safe to keep synchronous: it returns in seconds, well inside
    STAGE_CLAIM_TIMEOUT_S, so the reaper cannot double-charge it.
    """

    name = PipelineStage.AUDIO
    is_async = False

    def input_hash(self, ctx: JobContext) -> str:
        s = get_settings()
        return hashing.audio_hash(_spoken_text(ctx), ctx.voice_id, s.tts_model_id)

    def run(self, ctx: JobContext) -> StageResult:
        s = get_settings()
        text = _spoken_text(ctx)
        cost = budget.tts_cost_usd(len(text))

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            with budget.vendor_call(budget.VENDOR_TTS, cost_usd=cost):
                raw = vendors.cartesia_tts(
                    text,
                    voice_id=ctx.voice_id,
                    model_id=s.tts_model_id,
                    dst=work / "audio_raw.wav",
                )

            # MP3 is mandatory before the avatar step. "Audio size is too large"
            # is a BYTE limit, not a duration limit — a 37s WAV has failed while
            # a 53s WAV succeeded. pcm_f32le is ~176 KB/s.
            mp3 = media.to_mp3(raw, work / "audio.mp3")
            duration = media.probe_duration_seconds(mp3)
            stored = get_storage().put_file(
                job_key(ctx.job_id, "audio.mp3"), mp3, content_type="audio/mpeg"
            )

        record_event(
            "audio.synthesised",
            job_id=ctx.job_id,
            stage=str(self.name),
            chars=len(text),
            seconds=round(duration, 2),
            bytes=stored.bytes,
            cost_usd=float(cost),
        )
        if duration > 60:
            record_event(
                "audio.unusually_long",
                job_id=ctx.job_id,
                stage=str(self.name),
                level="warning",
                seconds=round(duration, 2),
                note="longest proven kie render is 39s",
            )

        return StageResult(
            output_key=stored.key,
            sha256=stored.sha256,
            asset_kind=AssetKind.AUDIO,
            bytes=stored.bytes,
            duration_ms=int(round(duration * 1000)),
            vendor=budget.VENDOR_TTS,
            model_id=s.tts_model_id,
            cost_usd=cost,
            billed_units=Decimal(len(text)),
        )

    def poll(self, vendor_task_id: str, ctx: JobContext) -> StageResult | None:
        return None


# ----------------------------------------------------------- [B] image ----


class ImageStage:
    """apimart person replacement on the plate. Async: submit and release."""

    name = PipelineStage.IMAGE
    is_async = True

    def input_hash(self, ctx: JobContext) -> str:
        s = get_settings()
        plate = _plate_row(ctx)
        photo = _asset(ctx, AssetKind.SOURCE_PHOTO)
        return hashing.image_hash(
            str(plate["sha256"] or ""),
            str(photo["sha256"]),
            s.image_prompt_version,
            s.image_edit_model_id,
        )

    def run(self, ctx: JobContext) -> AsyncSubmission:
        s = get_settings()
        store = get_storage()
        plate = _plate_row(ctx)
        photo = _asset(ctx, AssetKind.SOURCE_PHOTO)

        # apimart fetches inputs BY URL. Presigned, not CDN: these are working
        # artefacts and must stay private and short-lived.
        plate_url = store.presigned_get_url(str(plate["s3_key"]))
        photo_url = store.presigned_get_url(str(photo["s3_key"]))

        cost = budget.image_cost_usd()
        with budget.vendor_call(budget.VENDOR_IMAGE, cost_usd=cost):
            task_id = vendors.apimart_submit(
                plate_url, photo_url, model_id=s.image_edit_model_id
            )

        record_event(
            "image.submitted",
            job_id=ctx.job_id,
            stage=str(self.name),
            vendor_task_id=task_id,
            model_id=s.image_edit_model_id,
            cost_usd=float(cost),
            note="avg ~83s, worst observed 644s",
        )
        return AsyncSubmission(
            vendor=budget.VENDOR_IMAGE,
            vendor_task_id=task_id,
            model_id=s.image_edit_model_id,
            params={"resolution": s.image_edit_resolution, "size": "9:16"},
            cost_usd=cost,
            billed_units=Decimal(1),
        )

    def poll(self, vendor_task_id: str, ctx: JobContext) -> StageResult | None:
        url = vendors.apimart_poll(vendor_task_id)
        if url is None:
            return None

        with tempfile.TemporaryDirectory() as tmp:
            local = vendors.download(
                url, Path(tmp) / "image_edit.png", expect_image=True
            )
            width, height = _image_size(local)
            stored = get_storage().put_file(
                job_key(ctx.job_id, "image_edit.png"), local, content_type="image/png"
            )

        record_event(
            "image.completed",
            job_id=ctx.job_id,
            stage=str(self.name),
            vendor_task_id=vendor_task_id,
            bytes=stored.bytes,
            size=f"{width}x{height}",
        )
        return StageResult(
            output_key=stored.key,
            sha256=stored.sha256,
            asset_kind=AssetKind.IMAGE_EDIT,
            bytes=stored.bytes,
            width=width,
            height=height,
            vendor=budget.VENDOR_IMAGE,
            model_id=get_settings().image_edit_model_id,
        )


def _image_size(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as im:
        return im.size


# ----------------------------------------------------------- [C] video ----


class VideoStage:
    """kie avatar lipsync. Async, 8-20 minutes, and 96% of the bill."""

    name = PipelineStage.VIDEO
    is_async = True

    def _params(self) -> dict[str, Any]:
        return {"pro": get_settings().video_use_pro}

    def input_hash(self, ctx: JobContext) -> str:
        s = get_settings()
        return hashing.video_hash(
            ctx.upstream_sha(PipelineStage.IMAGE),
            ctx.upstream_sha(PipelineStage.AUDIO),
            s.video_model_id,
            self._params(),
        )

    def run(self, ctx: JobContext) -> AsyncSubmission:
        s = get_settings()
        store = get_storage()

        duration_ms = ctx.upstream[str(PipelineStage.AUDIO)].get("duration_ms")
        if not duration_ms:
            # Never reserve without a probed duration. kie's fallback_duration
            # is 5s, so a missed probe bills 5s for a 35s video and the daily
            # cap never notices. vendor_limits.require_cost_estimate refuses a
            # zero reservation for exactly this reason; fail here, louder.
            raise StageFailure(
                "Audio duration is missing, so the video cost cannot be "
                "estimated. Refusing to submit — an unpriced call on the only "
                "expensive step is how a budget cap gets bypassed.",
                code=StageErrorCode.ASSET_MISSING,
            )

        seconds = Decimal(str(duration_ms)) / 1000
        billed = Decimal(math.ceil(seconds))
        cost = budget.video_cost_usd(float(seconds), pro=s.video_use_pro)

        image_url = store.presigned_get_url(ctx.upstream_key(PipelineStage.IMAGE))
        audio_url = store.presigned_get_url(ctx.upstream_key(PipelineStage.AUDIO))

        with budget.vendor_call(
            budget.VENDOR_VIDEO, cost_usd=cost, seconds=billed
        ):
            task_id = vendors.kie_submit(
                image_url, audio_url, model_id=s.video_model_id
            )

        record_event(
            "video.submitted",
            job_id=ctx.job_id,
            stage=str(self.name),
            vendor_task_id=task_id,
            model_id=s.video_model_id,
            billed_seconds=float(billed),
            cost_usd=float(cost),
            note="8-20 minutes",
        )
        return AsyncSubmission(
            vendor=budget.VENDOR_VIDEO,
            vendor_task_id=task_id,
            model_id=s.video_model_id,
            params=self._params(),
            cost_usd=cost,
            billed_seconds=billed,
        )

    def poll(self, vendor_task_id: str, ctx: JobContext) -> StageResult | None:
        url = vendors.kie_poll(vendor_task_id)
        if url is None:
            return None

        with tempfile.TemporaryDirectory() as tmp:
            local = vendors.download(url, Path(tmp) / "video_raw.mp4")
            width, height = media.probe_dimensions(local)
            duration = media.probe_duration_seconds(local)
            stored = get_storage().put_file(
                job_key(ctx.job_id, "video_raw.mp4"), local, content_type="video/mp4"
            )

        record_event(
            "video.completed",
            job_id=ctx.job_id,
            stage=str(self.name),
            vendor_task_id=vendor_task_id,
            seconds=round(duration, 2),
            size=f"{width}x{height}",
            bytes=stored.bytes,
        )
        return StageResult(
            output_key=stored.key,
            sha256=stored.sha256,
            asset_kind=AssetKind.VIDEO_RAW,
            bytes=stored.bytes,
            duration_ms=int(round(duration * 1000)),
            width=width,
            height=height,
            vendor=budget.VENDOR_VIDEO,
            model_id=get_settings().video_model_id,
        )


# ------------------------------------------------------- [D] composite ----


class CompositeStage:
    """Render the card and burn it in. Local, deterministic, free.

    Free is the point: a card revision is an ffmpeg re-encode of an artefact we
    already have, so the client can iterate on the lower-third without
    regenerating a single paid second.
    """

    name = PipelineStage.COMPOSITE
    is_async = False

    def input_hash(self, ctx: JobContext) -> str:
        return hashing.composite_hash(
            ctx.upstream_sha(PipelineStage.VIDEO),
            _card_fields(ctx),
            get_settings().card_template_version,
        )

    def run(self, ctx: JobContext) -> StageResult:
        store = get_storage()
        fields = _card_fields(ctx)

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            source = store.download(
                ctx.upstream_key(PipelineStage.VIDEO), work / "video_raw.mp4"
            )
            width, height = media.probe_dimensions(source)
            card = media.render_card(fields, width, height, work / "card.png")
            final = media.composite(source, card, work / "final.mp4")
            duration = media.probe_duration_seconds(final)

            card_stored = store.put_file(
                job_key(ctx.job_id, "card.png"), card, content_type="image/png"
            )
            stored = store.put_file(
                job_key(ctx.job_id, "final.mp4"), final, content_type="video/mp4"
            )

        record_event(
            "composite.rendered",
            job_id=ctx.job_id,
            stage=str(self.name),
            card_key=card_stored.key,
            seconds=round(duration, 2),
            bytes=stored.bytes,
            template_version=get_settings().card_template_version,
        )
        return StageResult(
            output_key=stored.key,
            sha256=stored.sha256,
            asset_kind=AssetKind.VIDEO_FINAL,
            bytes=stored.bytes,
            duration_ms=int(round(duration * 1000)),
            width=width,
            height=height,
            meta={"card_key": card_stored.key, "card_sha256": card_stored.sha256},
        )

    def poll(self, vendor_task_id: str, ctx: JobContext) -> StageResult | None:
        return None


# ---------------------------------------------------------- [checks] -----


class ChecksStage:
    """Machine validation. Written regardless of outcome, non-blocking.

    Non-blocking is a deliberate release decision, not an oversight: we do not
    yet have enough real outputs to set a threshold that would not reject good
    videos. The results are recorded so that threshold can be set from data.
    """

    name = PipelineStage.CHECKS
    is_async = False

    def input_hash(self, ctx: JobContext) -> str:
        return hashing.canonical_hash(
            {"stage": "checks", "final": ctx.upstream_sha(PipelineStage.COMPOSITE)}
        )

    def run(self, ctx: JobContext) -> StageResult:
        final = ctx.upstream[str(PipelineStage.COMPOSITE)]
        audio_ms = ctx.upstream[str(PipelineStage.AUDIO)].get("duration_ms") or 0
        final_ms = final.get("duration_ms") or 0
        drift_ms = abs(final_ms - audio_ms)

        checks = [
            {
                # The avatar model is driven by the audio, so a final video that
                # is not the length of the audio means a truncated render.
                "check_name": "duration_matches_audio",
                "passed": audio_ms > 0 and drift_ms <= 1500,
                "score": drift_ms / 1000 if audio_ms else None,
                "details": {"audio_ms": audio_ms, "final_ms": final_ms},
            },
            {
                "check_name": "is_vertical_9x16",
                "passed": bool(final.get("height")) and
                          final.get("height", 0) > final.get("width", 0),
                "details": {"width": final.get("width"), "height": final.get("height")},
            },
            {
                # A composite that lost most of its bitrate means the re-encode
                # fell back to defaults. 200 KB/s is well under any real output.
                "check_name": "bitrate_plausible",
                "passed": final_ms > 0
                          and (final.get("bytes", 0) / (final_ms / 1000)) > 200_000,
                "score": round(final.get("bytes", 0) / max(final_ms / 1000, 1)),
                "details": {"bytes": final.get("bytes"), "ms": final_ms},
            },
        ]

        failed = [c["check_name"] for c in checks if not c["passed"]]
        if failed:
            record_event(
                "checks.failed",
                job_id=ctx.job_id,
                stage=str(self.name),
                level="warning",
                failed=failed,
                note="non-blocking this release; delivery proceeds",
            )
        else:
            record_event("checks.passed", job_id=ctx.job_id, stage=str(self.name))

        return StageResult(meta={"checks": checks, "failed": failed})

    def poll(self, vendor_task_id: str, ctx: JobContext) -> StageResult | None:
        return None


# --------------------------------------------------------- [publish] -----


class PublishStage:
    """Copy the final video to its delivery key and mint the CDN URL.

    A copy, not a move. The two live under different lifetimes on purpose: the
    delivered object expires at 180 days to satisfy the client's 6-month link,
    while the working artefact under `jobs/` never expires. Expiring the link
    must not destroy the evidence of how the video was made.

    The URL is plain CloudFront over an unguessable key, never presigned —
    SigV4 caps expiry at 7 days and the link must live 6 months. It stops
    working because the object is DELETED on schedule.
    """

    name = PipelineStage.PUBLISH
    is_async = False

    def input_hash(self, ctx: JobContext) -> str:
        return hashing.publish_hash(ctx.upstream_sha(PipelineStage.COMPOSITE))

    def run(self, ctx: JobContext) -> StageResult:
        source_key = ctx.upstream_key(PipelineStage.COMPOSITE)
        source_sha = ctx.upstream_sha(PipelineStage.COMPOSITE)

        key = delivery_key()
        stored = get_storage().copy(source_key, key)
        url = cdn_url(key)

        record_event(
            "publish.copied",
            job_id=ctx.job_id,
            stage=str(self.name),
            source_key=source_key,
            delivery_key=key,
            cdn_url=url,
            bytes=stored.bytes,
        )
        return StageResult(
            output_key=key,
            # The bytes are identical to the composite output, so carry its
            # digest rather than the empty one a server-side copy returns.
            sha256=source_sha,
            asset_kind=AssetKind.VIDEO_FINAL,
            bytes=stored.bytes,
            cdn_url=url,
            meta={"cdn_url": url, "source_key": source_key},
        )

    def poll(self, vendor_task_id: str, ctx: JobContext) -> StageResult | None:
        return None


# --------------------------------------------------------- [deliver] -----


class DeliverStage:
    """POST {phone, videoLink} to the client webhook.

    The one irreversible, outward-facing action in the pipeline: the client
    relays it to a real mechanic over WhatsApp. It is OFF by default and logs
    what it would have sent — DELIVERY_ENABLED=true is a deliberate act.
    """

    name = PipelineStage.DELIVER
    is_async = False

    def _url(self, ctx: JobContext) -> str:
        published = ctx.upstream.get(str(PipelineStage.PUBLISH), {})
        url = published.get("cdn_url") or (published.get("meta") or {}).get("cdn_url")
        if not url:
            raise StageFailure(
                "No CDN URL from publish; refusing to deliver.",
                code=StageErrorCode.ASSET_MISSING,
            )
        return str(url)

    def input_hash(self, ctx: JobContext) -> str:
        return hashing.deliver_hash(self._url(ctx), ctx.phone_e164)

    def run(self, ctx: JobContext) -> StageResult:
        s = get_settings()
        url = self._url(ctx)
        payload = {"phone": ctx.phone_e164, "videoLink": url}

        if not s.delivery_enabled:
            record_event(
                "deliver.suppressed",
                job_id=ctx.job_id,
                stage=str(self.name),
                level="warning",
                cdn_url=url,
                phone=ctx.phone_e164,
                note="DELIVERY_ENABLED is false; nothing was sent to the client",
            )
            return StageResult(
                cdn_url=url,
                meta={"cdn_url": url, "dry_run": True, "payload": payload},
            )

        headers = {"Content-Type": "application/json"}
        if s.client_webhook_api_key:
            headers["apikey"] = s.client_webhook_api_key

        with httpx.Client(timeout=30.0) as c:
            r = c.post(s.require("client_webhook_url"), json=payload, headers=headers)

        record_event(
            "deliver.posted",
            job_id=ctx.job_id,
            stage=str(self.name),
            level="info" if r.status_code < 400 else "error",
            status_code=r.status_code,
            cdn_url=url,
            phone=ctx.phone_e164,
        )
        if r.status_code >= 400:
            raise StageFailure(
                f"client webhook {r.status_code}: {r.text[:300]}",
                code=StageErrorCode.VENDOR_REJECTED,
            )

        return StageResult(
            cdn_url=url,
            meta={
                "cdn_url": url,
                "response_code": r.status_code,
                "response_body": r.text[:1000],
            },
        )

    def poll(self, vendor_task_id: str, ctx: JobContext) -> StageResult | None:
        return None


REAL_STAGES: dict[PipelineStage, Any] = {
    PipelineStage.PREP: PrepStage(),
    PipelineStage.AUDIO: AudioStage(),
    PipelineStage.IMAGE: ImageStage(),
    PipelineStage.VIDEO: VideoStage(),
    PipelineStage.COMPOSITE: CompositeStage(),
    PipelineStage.CHECKS: ChecksStage(),
    PipelineStage.PUBLISH: PublishStage(),
    PipelineStage.DELIVER: DeliverStage(),
}
