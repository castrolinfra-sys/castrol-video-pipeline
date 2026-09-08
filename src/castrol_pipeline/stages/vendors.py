"""Thin clients for the three paid providers.

Submit and poll are separate calls on purpose. Both remote stages submit, record
the vendor task id, and release the worker; a separate poller reconciles. A
worker blocked on a 20-minute kie poll is a worker not doing the other 200 jobs,
and a claimed row that sits for 20 minutes is a row the reaper eventually
returns to the queue — which re-runs a call that is still in flight and bills
twice.

Nothing here reserves budget. `common/budget.py` is the only path to a paid
call and the stages hold that guard; putting it here too would double-meter.

Gateway quirks that are not optional to know:
  * Both gateways return HTTP 200 with `code != 200` for errors.
  * apimart's `data` is an ARRAY; kie's `resultJson` is a JSON *string*.
  * apimart has no webhooks. Polling is the only completion signal.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from ..common.errors import StageErrorCode, StageFailure, VendorRejected, VendorTimeout
from ..common.logging import get_logger
from ..config import get_settings

log = get_logger(__name__)


def _sniff_is_image(b: bytes) -> bool:
    return b.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"RIFF"))


def download(url: str, dst: Path, *, expect_image: bool = False) -> Path:
    """Copy a provider result to local disk immediately.

    Provider result URLs have an unmeasured TTL; never depend on one lasting.
    Our S3 copy is the system of record.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=180.0, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        if expect_image and not _sniff_is_image(r.content[:16]):
            raise StageFailure(
                f"{url[:80]} did not return an image (HTML error page?)",
                code=StageErrorCode.VENDOR_REJECTED,
            )
        dst.write_bytes(r.content)
    return dst


# ------------------------------------------------------- [A] Cartesia TTS --


def cartesia_tts(text: str, *, voice_id: str, model_id: str, dst: Path) -> Path:
    """Synthesise to WAV. Synchronous — Cartesia returns bytes on the call.

    Direct to api.cartesia.ai: the one deliberate exception to apimart+kie,
    taken because that intersection has no voice-cloning Hindi lane.
    """
    s = get_settings()
    base = (s.tts_base_url or "https://api.cartesia.ai").rstrip("/")
    with httpx.Client(timeout=180.0) as c:
        r = c.post(
            f"{base}/tts/bytes",
            headers={
                "Authorization": f"Bearer {s.require('cartesia_api_key')}",
                "Cartesia-Version": s.cartesia_version,
                "Content-Type": "application/json",
            },
            json={
                "model_id": model_id,
                "transcript": text,
                "voice": {"mode": "id", "id": voice_id},
                "output_format": {
                    "container": "wav",
                    "encoding": "pcm_f32le",
                    "sample_rate": 44100,
                },
            },
        )
    if r.status_code != 200:
        raise VendorRejected(f"cartesia {r.status_code}: {r.text[:400]}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(r.content)
    return dst


# --------------------------------------------------- [B] apimart image edit --

IMAGE_PROMPT = """\
Reproduce this image EXACTLY as-is. Same camera framing, same crop, same \
subject scale, same head position, same shoulder line, same belt line, same \
pose, same hand position, same background, same lighting, same uniform \
geometry, and every Castrol and MAGNATEC logo, on the cap, the chest panel, \
the sleeve and the overhead banner, identical in placement, size and colour.

Make EXACTLY ONE change: replace the person's identity with the person in the \
second reference image. Carry over their face, apparent age, skin tone on both \
the face AND the hands, body build, and facial hair. Keep their eyeglasses if \
they wear any.

Do not reframe. Do not zoom. Do not move or rescale the subject within the \
frame. Do not redesign, restyle, idealise or beautify anything. Do not invent \
new text, logos or branding.\
"""


def _apimart_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_settings().require('apimart_api_key')}"}


def _apimart_base() -> str:
    return (get_settings().apimart_base_url or "https://api.apimart.ai/v1").rstrip("/")


def apimart_submit(plate_url: str, photo_url: str, *, model_id: str) -> str:
    """Submit the person-replacement edit. Returns a task id."""
    s = get_settings()
    body = {
        "model": model_id,
        "prompt": IMAGE_PROMPT,
        "image_urls": [plate_url, photo_url],
        "size": "9:16",
        "resolution": s.image_edit_resolution,
        "n": 1,
        "official_fallback": False,
    }
    # 60s, not 15s: a short timeout ORPHANS jobs — apimart accepts and starts,
    # the client raises, and the result is keyed to a task_id nobody recorded.
    # That is a paid call with no way to collect it.
    with httpx.Client(timeout=60.0) as c:
        j = c.post(f"{_apimart_base()}/images/generations",
                   headers=_apimart_headers(), json=body).json()
    if j.get("code") != 200:
        raise VendorRejected(f"apimart submit rejected: {json.dumps(j)[:400]}")
    data = j["data"]
    return str((data[0] if isinstance(data, list) else data)["task_id"])


def apimart_poll(task_id: str) -> str | None:
    """Result URL, or None while still in flight. Raises on vendor failure."""
    with httpx.Client(timeout=60.0) as c:
        j = c.get(f"{_apimart_base()}/tasks/{task_id}", headers=_apimart_headers()).json()
    d = j.get("data", {})
    status = d.get("status")

    if status == "completed":
        res = d.get("result", {})
        images = res.get("images") or res.get("data") or []
        if not images:
            raise VendorRejected(f"apimart completed with no image: {json.dumps(d)[:400]}")
        url = images[0].get("url") if isinstance(images[0], dict) else images[0]
        return url[0] if isinstance(url, list) else str(url)

    if status in {"failed", "cancelled"}:
        # 11 of 20 observed failures on this model were content safety, and
        # swapping a real person into a branded plate is exactly the trigger.
        # Not retryable: the same inputs will trip the same filter.
        raise VendorRejected(f"apimart {status}: {json.dumps(d.get('error', d))[:400]}")

    return None


# ------------------------------------------------------ [C] kie avatar video --


#: Motion direction for the avatar. NOT decorative — on kling-avatar-v2 the
#: prompt steers expression, head movement and hand gesture, and the field is
#: required (max 5000 chars). The inherited default from the other stack was
#: literally "." , which left the model to its own devices: correct lipsync,
#: natural head motion, and hands locked at rest for the whole take.
#:
#: Written to the model's documented shape — subject, expression, motion, style
#: preservation, in 1-3 sentences. Long or contradictory prompts measurably
#: degrade it, and guidance that fights the source image causes drift, so this
#: describes the person already in the plate rather than inventing one.
#:
#: Revision 2, after reviewing the first render. Two faults, one cause each:
#:
#:   * **Repetitive.** The first version said "natural open-palm hand
#:     gestures" — ONE gesture type, so the model looped it. Fixed by naming
#:     three DIFFERENT gestures mapped to the script's three beats: he
#:     introduces himself and his workshop, explains engine wear in the first
#:     8 seconds, then invites the viewer in. Naming distinct gestures is what
#:     buys variety; do not collapse them back into one description.
#:   * **Blurred.** Fast hand movement is exactly what generative video smears.
#:     The first version constrained WHERE the hands go and never HOW FAST.
#:     "slow, deliberate", "holds briefly" and "lowers before the next" all
#:     exist to reduce the per-frame displacement that causes the smear.
#:
#: Phrased positively throughout — "each one different from the last" rather
#: than "never repeat a gesture". Negative instructions are unreliable here.
#:
#: Two clauses exist for reasons outside the model:
#:
#:   * "at chest height" — the personalisation card is an OPAQUE overlay
#:     covering 66-82% of frame height (stages/media.py PANEL_Y0/PANEL_Y1).
#:     A gesture at waist level happens behind it, so the viewer sees a hand
#:     enter frame and vanish. Note the script's closing line points at the
#:     number "on screen", which invites exactly the downward gesture that
#:     would disappear — hence a welcoming open hand there, not a point.
#:   * "clear of the chest logo" — the Castrol and MAGNATEC marks on the chest
#:     panel are the point of the video. A hand parked across them for eight
#:     seconds is worse than no gesture at all.
#:
#: "fully inside the frame" stops hands leaving and re-entering, which is where
#: finger warping tends to appear.
#:
#: This text is part of the video input_hash, so editing it regenerates. See
#: VideoStage._params.
AVATAR_PROMPT = (
    "An Indian auto mechanic in his Castrol uniform speaking to camera in his "
    "garage, warm and confident, with clear articulation and subtle head nods. "
    "His hand gestures are slow, deliberate and varied: an open palm toward "
    "himself as he introduces his workshop, a measured counting gesture as he "
    "explains engine wear, then a welcoming open hand as he invites the "
    "viewer. Each gesture holds briefly and lowers before the next, each one "
    "different from the last. Hands stay at chest height, below the shoulders, "
    "clear of the chest logo, and fully inside the frame. Keep the existing "
    "framing, uniform and branding unchanged."
)


def _kie_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_settings().require('kie_api_key')}",
        "Content-Type": "application/json",
    }


def _kie_base() -> str:
    return (get_settings().kie_base_url or "https://api.kie.ai").rstrip("/")


#: kie's documented ceiling on the prompt field.
KIE_PROMPT_MAX_CHARS = 5000


def kie_submit(
    image_url: str, audio_url: str, *, model_id: str, prompt: str = AVATAR_PROMPT
) -> str:
    """Submit the avatar render. Returns a task id. THIS TAKES 8-20 MINUTES."""
    if len(prompt) > KIE_PROMPT_MAX_CHARS:
        raise VendorRejected(
            f"Avatar prompt is {len(prompt)} chars, over kie's {KIE_PROMPT_MAX_CHARS} "
            "limit. Caught before submit: a rejected submit on this model is a "
            "20-minute round trip to discover a typo."
        )
    with httpx.Client(timeout=120.0) as c:
        j = c.post(
            f"{_kie_base()}/api/v1/jobs/createTask",
            headers=_kie_headers(),
            json={"model": model_id,
                  "input": {"image_url": image_url,
                            "audio_url": audio_url,
                            "prompt": prompt}},
        ).json()
    if j.get("code") != 200:
        raise VendorRejected(f"kie submit rejected: {json.dumps(j)[:400]}")
    return str(j["data"]["taskId"])


def kie_poll(task_id: str) -> str | None:
    """Result URL, or None while still in flight. Raises on vendor failure."""
    with httpx.Client(timeout=60.0) as c:
        j = c.get(f"{_kie_base()}/api/v1/jobs/recordInfo",
                  params={"taskId": task_id}, headers=_kie_headers()).json()
    d = j.get("data", {})
    state = d.get("state")

    if state == "success":
        # resultJson is a JSON *string*, not an object.
        result = json.loads(d["resultJson"])
        urls = result.get("resultUrls") or []
        if not urls:
            raise VendorRejected(f"kie success with no result url: {json.dumps(d)[:400]}")
        return str(urls[0])

    if state in {"fail", "FAILED", "failed", "error", "ERROR"}:
        msg = d.get("failMsg") or d.get("errorReason") or d.get("msg")
        # "Audio size is too large" is a BYTE limit, not a duration limit. If
        # this appears, the mp3 transcode did not happen.
        raise VendorRejected(f"kie {state}: {d.get('failCode', '')} {msg}")

    return None


def poll_timeout(started_at_seconds: float, limit_seconds: float, vendor: str) -> None:
    """Raise once an in-flight task has outlived its ceiling.

    The poller is stateless per pass, so something has to notice a task the
    vendor will never finish. Without this a lost task sits `running` forever
    and the job never fails, never completes, and never appears in a report.
    """
    if started_at_seconds > limit_seconds:
        raise VendorTimeout(
            f"{vendor} task exceeded {limit_seconds:.0f}s without completing"
        )
