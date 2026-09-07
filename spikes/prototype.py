"""End-to-end prototype: one mechanic photo + one plate -> one finished video.

Straight-line, no database, no orchestrator, no budget. This exists to produce
the Phase 0 exit artefact: one video a human would accept. Everything it does
by hand is what the pipeline will later do properly.

    plate.png + mechanic.jpg
            |
      [1] image   apimart gpt-image-2   person replacement on the plate
            |
      [2] audio   Cartesia sonic-3.5    Hindi script -> mp3 + probed duration
            |
      [3] video   kie kling-avatar-v2   avatar lipsync   (8-20 MINUTES)
            |
      [4] card    Pillow + ffmpeg       burn in the lower-third
            |
      out/final.mp4

Steps are resumable. Each writes its output into --out and records the state in
_state.json, so a crash during the 20-minute video step does not cost you the
image and audio again.

    uv run python spikes/prototype.py --plate spikes/in/plate.png \\
        --photo spikes/in/mechanic.jpg --out spikes/out/run1

    uv run python spikes/prototype.py --out spikes/out/run1 --only video
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import pathlib
import subprocess
import sys
import time

import httpx

# --------------------------------------------------------------- config ----

ENV: dict[str, str] = {}


def load_env(path: str = ".env") -> None:
    p = pathlib.Path(path)
    if not p.exists():
        die(f"{path} not found. Copy .env.example to .env and fill it.")
    for line in p.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            ENV[k.strip()] = v.split("  #")[0].strip()


def need(key: str) -> str:
    v = ENV.get(key, "")
    if not v:
        die(f"{key} is not set in .env")
    return v


def die(msg: str) -> None:
    print(f"\n  FAIL  {msg}\n", file=sys.stderr)
    raise SystemExit(1)


def say(step: str, msg: str) -> None:
    print(f"  [{step}] {msg}", flush=True)


# ---------------------------------------------------------------- state ----


class State:
    """Resumable step outputs, so the 20-minute video step is never redone."""

    def __init__(self, out: pathlib.Path):
        self.path = out / "_state.json"
        self.data: dict = json.loads(self.path.read_text()) if self.path.exists() else {}

    def get(self, k: str):
        return self.data.get(k)

    def set(self, k: str, v) -> None:
        self.data[k] = v
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")


# ----------------------------------------------------------- media utils ----


def ffprobe_duration(path: pathlib.Path) -> float:
    """Probe duration. Nothing upstream returns it and the avatar step bills
    per output second, so this is the only truth about how long the audio is.
    """
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    if out.returncode != 0 or not out.stdout.strip():
        die(f"ffprobe failed on {path}: {out.stderr.strip()}")
    return float(out.stdout.strip())


def to_mp3(src: pathlib.Path, dst: pathlib.Path) -> pathlib.Path:
    """Transcode to MP3.

    NOT optional. The avatar model's "Audio size is too large" is a BYTE limit,
    not a duration limit - a 37s WAV has failed while a 53s WAV succeeded.
    Cartesia's pcm_f32le is ~176 KB/s, so 40s is ~7 MB against ~640 KB as MP3.
    """
    if src.suffix.lower() == ".mp3":
        return src
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-codec:a", "libmp3lame", "-b:a", "128k", str(dst)],
        check=True,
    )
    return dst


def normalise_for_apimart(src: pathlib.Path, dst: pathlib.Path) -> pathlib.Path:
    """apimart rejects images outside [300, 6000] px on EITHER axis."""
    from PIL import Image

    with Image.open(src) as im:
        im = im.convert("RGB")
        w, h = im.size
        scale = 1.0
        if min(w, h) < 300:
            scale = 300 / min(w, h)
        elif max(w, h) > 6000:
            scale = 6000 / max(w, h)
        if scale != 1.0:
            im = im.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
            say("image", f"normalised {w}x{h} -> {im.size[0]}x{im.size[1]} for apimart")
        im.save(dst, "PNG")
    return dst


def sniff_is_image(b: bytes) -> bool:
    return b.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"RIFF"))


def download(url: str, dst: pathlib.Path, expect_image: bool = False) -> pathlib.Path:
    """Copy a provider result to local disk immediately.

    Provider result URLs have an unmeasured TTL; never depend on one lasting.
    """
    with httpx.Client(timeout=120.0, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        if expect_image and not sniff_is_image(r.content[:16]):
            die(f"{url[:80]} did not return an image (HTML error page?)")
        dst.write_bytes(r.content)
    return dst


# -------------------------------------------------------- public hosting ----


def publish(path: pathlib.Path) -> str:
    """Return a URL the providers can fetch `path` from.

    Both apimart and kie fetch inputs BY URL - not base64, not upload-then-
    reference. The URL must return bytes on the first GET; a CDN transform path
    that 202s on a cold miss makes the provider's fetcher bail.

    Uses S3 when configured. Without it the prototype cannot reach step 3.
    """
    bucket = ENV.get("S3_BUCKET", "")
    if not bucket:
        die(
            "S3_BUCKET is not set, so there is nowhere to host the audio and "
            "image for kie to fetch.\n"
            "        apimart's image result is already a public URL, but the "
            "MP3 has no home.\n"
            "        Either set up the S3 bucket, or pass --audio-url / "
            "--image-url pointing at\n"
            "        somewhere already public."
        )
    import boto3

    s3 = boto3.client(
        "s3",
        region_name=ENV.get("AWS_REGION", "ap-south-1"),
        aws_access_key_id=need("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=need("AWS_SECRET_ACCESS_KEY"),
    )
    key = f"{ENV.get('S3_PREFIX', 'castrol/')}prototype/{int(time.time())}_{path.name}"
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    s3.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": ctype})
    # 1 hour is the documented presign life elsewhere in the stack; the video
    # step can queue for 20 minutes, so give it real headroom.
    url = s3.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=6 * 3600
    )
    say("host", f"uploaded {path.name} -> s3://{bucket}/{key}")
    return url


# ------------------------------------------------------ [1] image (apimart) --

PROMPT = """\
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


def step_image(plate_url: str, photo_url: str, out: pathlib.Path) -> str:
    base = ENV.get("APIMART_BASE_URL") or "https://api.apimart.ai/v1"
    model = ENV.get("IMAGE_EDIT_MODEL_ID") or "gpt-image-2"
    body = {
        "model": model,
        "prompt": PROMPT,
        "image_urls": [plate_url, photo_url],
        "size": "9:16",
        "resolution": ENV.get("IMAGE_EDIT_RESOLUTION", "2K"),
        "n": 1,
        "official_fallback": False,
    }
    # 60s, not 15s: a short timeout orphans jobs - apimart accepts and starts,
    # the client raises, and the result is keyed to a task_id nobody recorded.
    with httpx.Client(timeout=60.0) as c:
        r = c.post(
            f"{base}/images/generations",
            headers={"Authorization": f"Bearer {need('APIMART_API_KEY')}"},
            json=body,
        )
        j = r.json()
        # HTTP 200 with code != 200 is an error on both gateways.
        if j.get("code") != 200:
            die(f"apimart submit rejected: {json.dumps(j)[:400]}")
        data = j["data"]
        task_id = (data[0] if isinstance(data, list) else data)["task_id"]
        say("image", f"submitted task {task_id}; avg ~83s, worst seen 644s")

        deadline = time.time() + 2700
        delay = 4.0
        while time.time() < deadline:
            time.sleep(delay)
            delay = min(delay * 1.5, 60.0)
            pr = c.get(
                f"{base}/tasks/{task_id}",
                headers={"Authorization": f"Bearer {need('APIMART_API_KEY')}"},
            ).json()
            d = pr.get("data", {})
            status = d.get("status")
            if status == "completed":
                res = d.get("result", {})
                images = res.get("images") or res.get("data") or []
                url = images[0].get("url") if isinstance(images[0], dict) else images[0]
                if isinstance(url, list):
                    url = url[0]
                say("image", "completed")
                download(url, out, expect_image=True)
                return url
            if status in {"failed", "cancelled"}:
                err = json.dumps(d.get("error", d))[:400]
                # 11 of 20 observed failures on this model were content safety.
                die(f"apimart {status}: {err}")
            say("image", f"  {status}...")
    die("apimart poll timed out after 45 minutes")
    return ""


# ------------------------------------------------------ [2] audio (Cartesia) --


def step_audio(script_text: str, out_dir: pathlib.Path) -> tuple[pathlib.Path, float]:
    base = ENV.get("TTS_BASE_URL") or "https://api.cartesia.ai"
    raw = out_dir / "audio_raw.wav"
    with httpx.Client(timeout=180.0) as c:
        r = c.post(
            f"{base}/tts/bytes",
            headers={
                "Authorization": f"Bearer {need('TTS_API_KEY')}",
                "Cartesia-Version": ENV.get("CARTESIA_VERSION", "2026-05-11"),
                "Content-Type": "application/json",
            },
            json={
                "model_id": ENV.get("TTS_MODEL_ID", "sonic-3.5"),
                "transcript": script_text,
                "voice": {"mode": "id", "id": need("TTS_VOICE_ID")},
                "output_format": {
                    "container": "wav",
                    "encoding": "pcm_f32le",
                    "sample_rate": 44100,
                },
            },
        )
        if r.status_code != 200:
            die(f"cartesia {r.status_code}: {r.text[:400]}")
        raw.write_bytes(r.content)

    mp3 = to_mp3(raw, out_dir / "audio.mp3")
    dur = ffprobe_duration(mp3)
    say("audio", f"{len(script_text)} chars -> {dur:.1f}s, "
                 f"{raw.stat().st_size // 1024}KB wav -> {mp3.stat().st_size // 1024}KB mp3")
    if dur > 60:
        say("audio", f"WARNING: {dur:.0f}s exceeds the longest proven kie render (39s)")
    return mp3, dur


# --------------------------------------------------------- [3] video (kie) --


def step_video(image_url: str, audio_url: str, out: pathlib.Path) -> str:
    base = ENV.get("KIE_BASE_URL") or "https://api.kie.ai"
    model = ENV.get("VIDEO_MODEL_ID") or "kling/ai-avatar-standard"
    hdr = {"Authorization": f"Bearer {need('KIE_API_KEY')}",
           "Content-Type": "application/json"}
    with httpx.Client(timeout=120.0) as c:
        r = c.post(
            f"{base}/api/v1/jobs/createTask",
            headers=hdr,
            json={"model": model,
                  "input": {"image_url": image_url,
                            "audio_url": audio_url,
                            "prompt": "."}},
        )
        j = r.json()
        if j.get("code") != 200:
            die(f"kie submit rejected: {json.dumps(j)[:400]}")
        task_id = j["data"]["taskId"]
        say("video", f"submitted task {task_id}. THIS TAKES 8-20 MINUTES.")

        deadline = time.time() + 3600
        while time.time() < deadline:
            time.sleep(20)
            pr = c.get(f"{base}/api/v1/jobs/recordInfo",
                       params={"taskId": task_id}, headers=hdr).json()
            d = pr.get("data", {})
            state = d.get("state")
            if state == "success":
                # resultJson is a JSON *string*, not an object.
                result = json.loads(d["resultJson"])
                url = result["resultUrls"][0]
                say("video", "completed")
                download(url, out)
                return url
            if state in {"fail", "FAILED", "failed", "error", "ERROR"}:
                msg = d.get("failMsg") or d.get("errorReason") or d.get("msg")
                die(f"kie {state}: {d.get('failCode', '')} {msg}")
            say("video", f"  {state}... ({int(time.time() - deadline + 3600)}s elapsed)")
    die("kie poll timed out after 60 minutes")
    return ""


# ------------------------------------------------------ [4] card + composite --


def render_card(fields: dict, width: int, path: pathlib.Path) -> pathlib.Path:
    """Deterministic Pillow render. No generative model ever touches this text."""
    from PIL import Image, ImageDraw, ImageFont

    w, h = width, int(width * 0.30)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w, h], radius=int(h * 0.10), fill=(0, 0, 0, 175))

    def font(px: int):
        for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
            try:
                return ImageFont.truetype(name, px)
            except OSError:
                continue
        return ImageFont.load_default()

    lines = [
        (fields["name"], int(h * 0.26), (255, 255, 255, 255)),
        (fields["workshop"], int(h * 0.20), (255, 210, 0, 255)),
        (fields["address"], int(h * 0.16), (235, 235, 235, 255)),
        (f"Mo. {fields['phone']}", int(h * 0.16), (235, 235, 235, 255)),
    ]
    y, pad = int(h * 0.10), int(w * 0.04)
    for text, size, colour in lines:
        f = font(size)
        # Shrink to fit rather than wrap or overflow. Intake length rules are
        # what should keep this from ever firing.
        while d.textlength(text, font=f) > w - 2 * pad and size > 10:
            size -= 2
            f = font(size)
        d.text((pad, y), text, font=f, fill=colour)
        y += int(size * 1.35)

    img.save(path, "PNG")
    return path


def step_composite(video: pathlib.Path, card: pathlib.Path,
                   out: pathlib.Path, y_frac: float) -> pathlib.Path:
    """ffmpeg overlay, full duration, fixed geometry. Deterministic."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(card),
         "-filter_complex", f"[0:v][1:v]overlay=x=(W-w)/2:y=H*{y_frac}",
         "-c:a", "copy", str(out)],
        check=True,
    )
    return out


# ----------------------------------------------------------------- main ----


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plate", help="frozen garage plate png")
    ap.add_argument("--photo", help="mechanic source photo")
    ap.add_argument("--out", default="spikes/out/run1")
    ap.add_argument("--script", help="file holding the Hindi script text")
    ap.add_argument("--name", default="Raju Shetty")
    ap.add_argument("--workshop", default="Shetty Motors")
    ap.add_argument("--address", default="Andheri, Mumbai")
    ap.add_argument("--phone", default="9898989898")
    ap.add_argument("--card-y", type=float, default=0.70,
                    help="card top as a fraction of frame height")
    ap.add_argument("--plate-url", help="skip hosting; plate is already public")
    ap.add_argument("--photo-url", help="skip hosting; photo is already public")
    ap.add_argument("--audio-url", help="skip hosting; mp3 is already public")
    ap.add_argument("--only", choices=["image", "audio", "video", "composite"],
                    help="run one step and stop")
    args = ap.parse_args()

    load_env()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    st = State(out)
    only = args.only

    # [1] image
    if only in (None, "image") and not st.get("image_url"):
        if not (args.plate and args.photo):
            die("--plate and --photo are required for the image step")
        plate_url = args.plate_url or publish(
            normalise_for_apimart(pathlib.Path(args.plate), out / "plate_norm.png"))
        photo_url = args.photo_url or publish(
            normalise_for_apimart(pathlib.Path(args.photo), out / "photo_norm.png"))
        st.set("image_url", step_image(plate_url, photo_url, out / "image_edit.png"))
    if only == "image":
        return 0

    # [2] audio
    if only in (None, "audio") and not st.get("audio_seconds"):
        if not args.script:
            die("--script <file> is required for the audio step")
        text = pathlib.Path(args.script).read_text(encoding="utf-8").strip()
        mp3, dur = step_audio(text, out)
        st.set("audio_seconds", dur)
        st.set("audio_path", str(mp3))
    if only == "audio":
        return 0

    # [3] video
    if only in (None, "video") and not st.get("video_path"):
        audio_url = args.audio_url or publish(pathlib.Path(st.get("audio_path")))
        step_video(st.get("image_url"), audio_url, out / "video_raw.mp4")
        st.set("video_path", str(out / "video_raw.mp4"))
    if only == "video":
        return 0

    # [4] card + composite
    from PIL import Image

    with Image.open(out / "image_edit.png") as im:
        frame_w = im.size[0]
    card = render_card(
        {"name": args.name, "workshop": args.workshop,
         "address": args.address, "phone": args.phone},
        int(frame_w * 0.84), out / "card.png",
    )
    final = step_composite(pathlib.Path(st.get("video_path")), card,
                           out / "final.mp4", args.card_y)
    say("done", f"{final}  ({final.stat().st_size // 1024}KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
