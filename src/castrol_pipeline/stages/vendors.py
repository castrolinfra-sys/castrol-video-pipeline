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

#: Change / Preserve / Constrain, in that order, and the order is deliberate:
#: the model is told what the one change is, then everything that must survive
#: it, then the ways it is known to drift. Geometry preservation is invariant 6
#: - the card is burned in at a fixed fraction of frame height, so subject
#: scale drift lands it on the mechanic's hands.
_PROMPT_PRESERVE = """\
Reproduce this image EXACTLY as-is. Same camera framing, same crop, same \
subject scale, same head position, same shoulder line, same belt line, same \
pose, same hand position, same background, same lighting, same uniform \
geometry, and every Castrol and MAGNATEC logo, on the cap, the chest panel, \
the sleeve and the overhead banner, identical in placement, size and colour.

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


def _apimart_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_settings().require('apimart_api_key')}"}


def _apimart_base() -> str:
    return (get_settings().apimart_base_url or "https://api.apimart.ai/v1").rstrip("/")


def apimart_submit(
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
#: required (max 5000 chars).
#:
#: Written to the model's documented shape — subject, expression, motion, style
#: preservation, in 1-3 sentences. Long or contradictory prompts measurably
#: degrade it, and guidance that fights the source image causes drift, so this
#: describes the person already in the plate rather than inventing one.
#:
#: Phrased positively throughout. Negative instructions are unreliable on this
#: class of model and tend to surface the thing they forbid — "clear of the
#: chest logo" is how r2 put a hand on the chest logo.
#:
#: HISTORY, because three revisions were paid for and each one bought a fact:
#:
#:   * **r1** asked for "natural open-palm hand gestures" — one gesture type,
#:     so the model looped it, and it constrained WHERE the hands go but never
#:     HOW FAST, so fast hand movement smeared.
#:   * **r2** named three distinct gestures at "chest height ... clear of the
#:     chest logo", and added speed words. It fixed the loop and the smear. It
#:     also paired a position with an exclusion naming the SAME region, and the
#:     model kept the position and dropped the exclusion: both hands came back
#:     clawed across the chest panel.
#:   * **r3** stated the band bounded on both sides, "between his chest panel
#:     and his belt", and dropped the counting gesture that had asked for
#:     individuated fingers. Fingers came back clean and nothing occluded the
#:     logo — but the hands still rose to chest level, because a prompt that
#:     asks for gestures gets gestures, and a raised hand near the brand mark
#:     is a risk taken for no return.
#:
#: r4 stops asking for GESTURES — not for movement. The hands are calm and
#: natural, and they stay where the source image already has them:
#:
#:   "calm" is load-bearing and was asked for by name. Frozen hands read as a
#:   still photograph with a talking head pasted on, which is its own kind of
#:   wrong; the fix for bad gestures is not no motion but UNHURRIED motion that
#:   never leaves the resting position. "slow" stays beside it because that is
#:   what stopped r1's smear, and the two say different things: slow is about
#:   per-frame displacement, calm is about intent.
#:
#:   The card is an OPAQUE overlay across 66-82% of frame height
#:   (stages/media.py PANEL_Y0/PANEL_Y1), and in the source image the hands
#:   already rest at roughly 71-78% — BEHIND it. Hands left where they are are
#:   therefore never on screen. Every hand failure this model has shown us —
#:   warped fingers, claws, smeared motion, a palm across the Castrol mark —
#:   becomes invisible rather than merely less likely.
#:
#:   The old objection was that a waist-level gesture "happens behind the card,
#:   so the viewer sees a hand enter frame and vanish". That is an objection to
#:   hands CROSSING the boundary, not to hands resting below it. Nothing enters
#:   and nothing vanishes if nothing moves.
#:
#: What carries the video is the face: articulation, expression and head nods,
#: which is the one thing this model has always done well — the inherited "."
#: default produced correct lipsync and natural head motion, and only the hands
#: were ever the problem.
#:
#: Note what is NOT in the preservation clause: "hand position". r4 pinned it
#: there, which fought the movement clause above — a placement and a freeze
#: naming the same thing is r2's mistake in a new costume. Placement is stated
#: ONCE, positively, in the sentence that also grants the motion.
#:
#: Three sentences, three directives. The model's guidance is 3-5; additions
#: here cost something, and a request for hand motion costs the most.
#:
#: This text is part of the video input_hash, so editing it regenerates. See
#: VideoStage._params.
AVATAR_PROMPT = (
    "An Indian auto mechanic in his Castrol uniform speaking to camera in his "
    "garage, warm and confident, with clear articulation and subtle head nods. "
    "His hands stay low at waist level beside his thighs, exactly where they "
    "are in the image, moving only with calm, slow, natural motion. Keep the "
    "existing framing, pose, uniform and chest branding unchanged."
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
