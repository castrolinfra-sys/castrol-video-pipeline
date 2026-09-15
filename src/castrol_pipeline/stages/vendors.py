"""Thin clients for the three paid providers.

Submit and poll are separate calls on purpose. Both remote stages submit, record
the vendor task id, and release the worker; a separate poller reconciles. A
worker blocked on a 20-minute video poll is a worker not doing the other 200 jobs,
and a claimed row that sits for 20 minutes is a row the reaper eventually
returns to the queue — which re-runs a call that is still in flight and bills
twice.

Nothing here reserves budget. `common/budget.py` is the only path to a paid
call and the stages hold that guard; putting it here too would double-meter.

Gateway quirks that are not optional to know:
  * Both gateways return HTTP 200 with `code != 200` for errors.
  * The image gateway's `data` is an ARRAY; the video gateway's `resultJson`
    is a JSON *string*.
  * The image gateway has no webhooks. Polling is the only completion signal.
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


# -------------------------------------------------- [A] voice provider TTS --


def voice_tts(text: str, *, voice_id: str, model_id: str, dst: Path) -> Path:
    """Synthesise to WAV. Synchronous — the provider returns bytes on the call.

    A DIRECT api, not one of the two gateways: the one deliberate exception,
    taken because that intersection has no voice-cloning Hindi lane.
    """
    s = get_settings()
    base = (s.tts_base_url or "https://api.cartesia.ai").rstrip("/")
    with httpx.Client(timeout=180.0) as c:
        r = c.post(
            f"{base}/tts/bytes",
            headers={
                "Authorization": f"Bearer {s.require('voice_api_key')}",
                "Cartesia-Version": s.voice_api_version,
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
        raise VendorRejected(f"voice provider {r.status_code}: {r.text[:400]}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(r.content)
    return dst


# ------------------------------------------------- [B] image provider edit --

#: Change / Preserve / Constrain, in that order, and the order is deliberate:
#: the model is told what the one change is, then everything that must survive
#: it, then the ways it is known to drift. Geometry preservation is invariant 6
#: - the card is burned in at a fixed fraction of frame height, so subject
#: scale drift lands it on the mechanic's hands.
_PROMPT_PRESERVE = """\
Reproduce this image EXACTLY as-is. Same camera framing, same crop, same \
subject scale, same head position, same shoulder line, same belt line, same \
pose, same hand position, same background, same lighting, same uniform \
geometry, the Castrol mark on the chest panel and the Castrol MAGNATEC \
overhead banner, identical in placement, size and colour. The chest panel's \
Castrol mark is the uniform's only branding, and the sleeves are plain.

Make EXACTLY ONE change: replace the person with the person in the second \
reference image. It is that person standing there, not the first person \
wearing their face - so carry over their face, apparent age, ethnicity, skin \
tone, facial hair, body build, proportions and posture, and keep all of it \
consistent with one another throughout the frame. Their HANDS, wrists and \
forearms are theirs too: the age, skin tone, thickness and hair of the hands \
must match that same person, not the hands in the first image. Keep their \
eyeglasses if they wear any.\
"""

#: Sent ONLY when the job's plate has a uniform reference registered against it.
#:
#: The garment in the first image is being redrawn around a different body, so
#: the model reconstructs the uniform from what it can read off a figure it is
#: simultaneously changing - and fabric, stitching, collar shape and the printed
#: marks drift. The chest logo is what the video is for.
#:
#: It is still ONE change, and the clause has to say so in its first sentence.
#: The submit is a single generation with three references, and the identity
#: swap is the only edit being asked for; this paragraph is part of the PRESERVE
#: half - it says where to read the uniform's detail from, not that the uniform
#: is a second thing to change. Phrased as an instruction ("reproduce these
#: details") it contradicts "Make EXACTLY ONE change" two paragraphs above, and
#: a prompt that argues with itself measurably degrades output.
#:
#: The rest is scope. The third image is a flat shot on a plain background: its
#: framing, pose, lighting and background must not leak in, and neither must the
#: garment's fit or position, which come from the first image. Saying so
#: explicitly is what keeps the extra reference from re-opening invariant 6.
_PROMPT_UNIFORM = """\
That one change does not include the uniform, which stays exactly as it is. \
The third reference image is that same uniform, laid out flat on a plain \
background - use it as the reference for what the uniform already looks like: \
its exact fabric, colour, panel seams, collar and cuff shape, and the exact \
shape, proportion and colour of every printed logo and text mark on it. It \
tells you nothing else. The third image's framing, pose, lighting and \
background are irrelevant, and so is the way the garment is laid out in it - \
how the uniform sits on the body, its size in the frame and its place in the \
frame all come from the first image and do not change.\
"""

_PROMPT_CONSTRAIN = """\
Do not reframe. Do not zoom. Do not move or rescale the subject within the \
frame. Do not redesign, restyle, idealise or beautify anything. Do not invent \
new text, logos or branding.\
"""


def image_prompt(*, with_uniform_ref: bool) -> str:
    """The edit prompt for a two- or three-image submit.

    Two prompts rather than one that mentions an image which may not be there:
    a prompt referring to a third reference image on a two-image submit is a
    prompt the model has to guess at. Which one a job got is recoverable after
    the fact from its plate row - a plate either has a reference or it does
    not, and that fact is in the image stage's input_hash.
    """
    parts = [_PROMPT_PRESERVE]
    if with_uniform_ref:
        parts.append(_PROMPT_UNIFORM)
    parts.append(_PROMPT_CONSTRAIN)
    return "\n\n".join(parts)


#: The two-image prompt, word for word the one the prototype proved.
IMAGE_PROMPT = image_prompt(with_uniform_ref=False)


def _image_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_settings().require('image_api_key')}"}


def _image_base() -> str:
    return (get_settings().image_base_url or "https://api.apimart.ai/v1").rstrip("/")


def image_submit(
    plate_url: str,
    photo_url: str,
    *,
    model_id: str,
    uniform_ref_url: str | None = None,
) -> str:
    """Submit the person-replacement edit. Returns a task id.

    Order is load-bearing: the prompt addresses its inputs by ordinal, so the
    plate is first, the mechanic second, and the uniform reference - when the
    plate has one - third. Three references is well inside the ~4 cap
    (invariant 17), and none of them is generated: the uniform reference is
    client artwork, exactly like the plate.
    """
    s = get_settings()
    image_urls = [plate_url, photo_url]
    if uniform_ref_url:
        image_urls.append(uniform_ref_url)
    body = {
        "model": model_id,
        "prompt": image_prompt(with_uniform_ref=bool(uniform_ref_url)),
        "image_urls": image_urls,
        "size": "9:16",
        "resolution": s.image_edit_resolution,
        "n": 1,
        "official_fallback": False,
    }
    # 60s, not 15s: a short timeout ORPHANS jobs — the provider accepts and starts,
    # the client raises, and the result is keyed to a task_id nobody recorded.
    # That is a paid call with no way to collect it.
    with httpx.Client(timeout=60.0) as c:
        j = c.post(f"{_image_base()}/images/generations",
                   headers=_image_headers(), json=body).json()
    if j.get("code") != 200:
        raise VendorRejected(f"image provider submit rejected: {json.dumps(j)[:400]}")
    data = j["data"]
    return str((data[0] if isinstance(data, list) else data)["task_id"])


def image_poll(task_id: str) -> str | None:
    """Result URL, or None while still in flight. Raises on vendor failure."""
    with httpx.Client(timeout=60.0) as c:
        j = c.get(f"{_image_base()}/tasks/{task_id}", headers=_image_headers()).json()
    d = j.get("data", {})
    status = d.get("status")

    if status == "completed":
        res = d.get("result", {})
        images = res.get("images") or res.get("data") or []
        if not images:
            raise VendorRejected(f"image provider completed with no image: {json.dumps(d)[:400]}")
        url = images[0].get("url") if isinstance(images[0], dict) else images[0]
        return url[0] if isinstance(url, list) else str(url)

    if status in {"failed", "cancelled"}:
        # 11 of 20 observed failures on this model were content safety, and
        # swapping a real person into a branded plate is exactly the trigger.
        # Not retryable: the same inputs will trip the same filter.
        raise VendorRejected(f"image provider {status}: {json.dumps(d.get('error', d))[:400]}")

    return None


# ----------------------------------------------- [C] video provider avatar --


#: Motion direction for the avatar. NOT decorative — on kling-avatar-v2 the
#: prompt steers expression, head movement and hand gesture, and the field is
#: required (max 5000 chars).
#:
#: THIS IS r1, RESTORED 2026-09-10, and the restore is deliberate. r1 is the
#: text that was live when the render the client approved was shared to them on
#: Tuesday 08 Sep. Later revisions r2-r5 each chased a fault seen in a later
#: render; the client's own verdict outranks all of them, so we are back here.
#:
#: What the evidence actually says about that approved render, recorded because
#: it is easy to misread and expensive to relearn:
#:
#:   The mechanic's arms are FOLDED for the entire take - 4s, 10s, 16s, 22s,
#:   identical. r1 asks for "natural open-palm hand gestures" and the model
#:   performed none of them. That render used the OLD plate artwork, in which
#:   the mechanic stands with his arms crossed, and a locked pose in the source
#:   image beats prompt text. The stillness came from the plate, not from here.
#:
#:   The final plates pose him with his hands free at his thighs, so nothing
#:   holds them down any more. On those plates this family of prompt has been
#:   observed producing a looped, smeared gesture (r1) and both hands clawed
#:   across the chest panel (r2).
#:
#: Two known hazards live in the text below. They are left in place because
#: this is a verbatim restore, not a new revision - do not "fix" them without a
#: render to justify it, and do not re-derive them from scratch:
#:
#:   * "at chest height" and "without covering the chest logo" name the SAME
#:     region. Given a position and an exclusion pointing at one place, this
#:     model keeps the position and drops the exclusion. That is how r2 - which
#:     carried the same pairing - put both hands on the chest panel.
#:   * Nothing here constrains SPEED. Fast hand movement is what generative
#:     video smears, and r1's first render came back blurred for exactly that
#:     reason. "slow, deliberate", added in r2, is what fixed it.
#:
#: This text is part of the video input_hash, so editing it regenerates. See
#: VideoStage._params.
AVATAR_PROMPT = (
    "An Indian auto mechanic in his Castrol work uniform, speaking directly to "
    "camera in his garage. Calm, natural and slow, with clear articulation and "
    "subtle head nods. His hands stay low and mostly still, one on each side "
    "of his body, apart from each other and clear of one another at all times, "
    "with only small slow movements that settle back to rest. Keep the "
    "existing framing, uniform and branding unchanged."
)


def _video_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_settings().require('video_api_key')}",
        "Content-Type": "application/json",
    }


def _video_base() -> str:
    return (get_settings().video_base_url or "https://api.kie.ai").rstrip("/")


#: The video provider's documented ceiling on the prompt field.
VIDEO_PROMPT_MAX_CHARS = 5000


def video_submit(
    image_url: str, audio_url: str, *, model_id: str, prompt: str = AVATAR_PROMPT
) -> str:
    """Submit the avatar render. Returns a task id. THIS TAKES 8-20 MINUTES."""
    if len(prompt) > VIDEO_PROMPT_MAX_CHARS:
        raise VendorRejected(
            f"Avatar prompt is {len(prompt)} chars, over the video "
            f"provider's {VIDEO_PROMPT_MAX_CHARS} "
            "limit. Caught before submit: a rejected submit on this model is a "
            "20-minute round trip to discover a typo."
        )
    with httpx.Client(timeout=120.0) as c:
        j = c.post(
            f"{_video_base()}/api/v1/jobs/createTask",
            headers=_video_headers(),
            json={"model": model_id,
                  "input": {"image_url": image_url,
                            "audio_url": audio_url,
                            "prompt": prompt}},
        ).json()
    if j.get("code") != 200:
        raise VendorRejected(f"video provider submit rejected: {json.dumps(j)[:400]}")
    return str(j["data"]["taskId"])


def video_poll(task_id: str) -> str | None:
    """Result URL, or None while still in flight. Raises on vendor failure."""
    with httpx.Client(timeout=60.0) as c:
        j = c.get(f"{_video_base()}/api/v1/jobs/recordInfo",
                  params={"taskId": task_id}, headers=_video_headers()).json()
    d = j.get("data", {})
    state = d.get("state")

    if state == "success":
        # resultJson is a JSON *string*, not an object.
        result = json.loads(d["resultJson"])
        urls = result.get("resultUrls") or []
        if not urls:
            raise VendorRejected(
                f"video provider success with no result url: {json.dumps(d)[:400]}"
            )
        return str(urls[0])

    if state in {"fail", "FAILED", "failed", "error", "ERROR"}:
        msg = d.get("failMsg") or d.get("errorReason") or d.get("msg")
        # "Audio size is too large" is a BYTE limit, not a duration limit. If
        # this appears, the mp3 transcode did not happen.
        raise VendorRejected(f"video provider {state}: {d.get('failCode', '')} {msg}")

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
