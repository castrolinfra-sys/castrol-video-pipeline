"""Local media work: probing, transcoding, the card, the composite.

Everything here is deterministic and free. No vendor, no network, no spend —
which is why the card can be re-rendered and re-composited as many times as a
review needs without regenerating anything.

This is the single implementation. `spikes/prototype.py` imports from here
rather than keeping its own copy: the card geometry was tuned against real
client review, and two copies of it would drift on the first change.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ..common.errors import StageErrorCode, StageFailure
from ..common.logging import get_logger

log = get_logger(__name__)


def _run(cmd: list[str], what: str) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise StageFailure(
            f"{what} failed: {proc.stderr.strip()[:500]}",
            code=StageErrorCode.FFMPEG_FAILED,
        )
    return proc


# ---------------------------------------------------------------- probing --


def probe_duration_seconds(path: Path) -> float:
    """Probe duration.

    Nothing upstream returns it and the avatar step bills per output second, so
    this is the only truth about how long the audio is. A failed probe must
    raise rather than default: `vendor_limits.require_cost_estimate` refuses a
    zero reservation precisely because a missed probe is when spend runs away.
    """
    out = _run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        f"ffprobe duration on {path.name}",
    )
    text = out.stdout.strip()
    if not text:
        raise StageFailure(
            f"ffprobe returned no duration for {path.name}",
            code=StageErrorCode.FFMPEG_FAILED,
        )
    return float(text)


def probe_dimensions(path: Path) -> tuple[int, int]:
    out = _run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
        f"ffprobe dimensions on {path.name}",
    )
    w, h = (int(v) for v in out.stdout.strip().split("x")[:2])
    return w, h


# ----------------------------------------------------------- transcoding --


def to_mp3(src: Path, dst: Path, *, seconds: float | None = None) -> Path:
    """Transcode to MP3, optionally keeping only the first `seconds`.

    NOT optional. The avatar model's "Audio size is too large" is a BYTE limit,
    not a duration limit — a 37s WAV has failed while a 53s WAV succeeded.
    Cartesia's pcm_f32le is ~176 KB/s, so 40s is ~7 MB against ~640 KB as MP3.

    `seconds` exists for prompt work, and only the spike passes it. The avatar
    model bills per OUTPUT second and the output is as long as the audio, so a
    10s clip is a ~$0.40 render against ~$1.06 for the full take — and hand
    placement, finger shape and logo survival are all visible in the first few
    seconds. The pipeline never trims: a mechanic gets the whole script.

    A trim always re-encodes, because the short-circuit below returns the
    source untouched and would silently hand back the full-length file — a
    "cheap" probe that quietly bills the full duration.
    """
    if src.suffix.lower() == ".mp3" and seconds is None:
        return src
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src)]
    if seconds is not None:
        cmd += ["-t", f"{seconds:g}"]
    cmd += ["-codec:a", "libmp3lame", "-b:a", "128k", str(dst)]
    _run(cmd, "mp3 transcode")
    return dst


def normalise_for_apimart(src: Path, dst: Path) -> Path:
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
            log.info("media.normalised", frm=f"{w}x{h}", to=f"{im.size[0]}x{im.size[1]}")
        im.save(dst, "PNG")
    return dst


# ----------------------------------------------------------------- card ----

#: Geometry as fractions of the frame, so it scales to whatever the plate
#: resolution turns out to be.
#:
#: v2 is FULL WIDTH — the client wanted the contact details on one full-width
#: line — but the vertical rect is v1's, which is the position that survived
#: client review: top edge just below the belt, clear of the hands. The v1
#: reference mockup measured 80.95% for the top edge, but that plate was framed
#: waist-up; once apimart reframes to 9:16 the subject sits higher and that
#: lands the card over the knees.
PANEL_X0, PANEL_X1 = 0.0, 1.0
PANEL_Y0 = 0.6640
PANEL_Y1 = 0.8113
#: Red accent, directly beneath the panel.
ACCENT_H = 0.0077
#: Text inset from each frame edge. The panel is full-bleed; the TYPE is not.
SIDE_MARGIN = 0.030

#: Nominal type and spacing, as fractions of frame HEIGHT. These are a ceiling,
#: not a promise: the panel rect above is FIXED, so a card that needs more
#: lines is scaled down to fit rather than being allowed to grow.
NAME_H = 0.0300
BODY_H = 0.0195
LINE_GAP = 0.0030
PANEL_PAD = 0.0100
#: Floor on that scaling. Below this the card is unreadable on a phone and the
#: right fix is the intake length limits, not a smaller font.
MIN_SCALE = 0.55

#: Address and phone share one line while they fit. Wide separator: at this
#: size a bare "|" reads as part of the address.
CONTACT_SEP = "   |   "

GREEN = (1, 77, 38, 255)
RED = (210, 36, 25, 255)
WHITE = (255, 255, 255, 255)


def render_card(fields: dict[str, str], frame_w: int, frame_h: int, path: Path) -> Path:
    """Deterministic Pillow render. No generative model ever touches this text.

        panel   full frame width, y 66.40% .. 81.13% — a FIXED rect
        accent  red bar directly beneath, ~0.77% of frame height
        colours panel #014D26 (Castrol green), accent #D22419, text white

        Raju Shetty                        bold, hero
        Shetty Motors                      bold
        Andheri, Mumbai | Mo. 9898989898   regular, one line while it fits

    The rect is fixed and the TYPE adapts, which is the opposite of v1. A band
    that grew with its content changed size from job to job, and at full width
    that reads as a different template rather than as a longer address. Content
    that does not fit is scaled down as a block — every size and gap by the
    same factor — so the proportions never change either.

    Returns a FULL-FRAME transparent PNG, so the composite is a plain overlay
    at 0,0 and the position cannot drift.
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (frame_w, frame_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    x0, x1 = round(frame_w * PANEL_X0), round(frame_w * PANEL_X1)
    y0, y1 = round(frame_h * PANEL_Y0), round(frame_h * PANEL_Y1)
    panel_h = y1 - y0
    inner = (x1 - x0) * (1 - 2 * SIDE_MARGIN)

    def font(px: int, bold: bool):
        names = (("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf") if bold
                 else ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"))
        for n in names:
            try:
                return ImageFont.truetype(n, px)
            except OSError:
                continue
        return ImageFont.load_default()

    def fits(text: str, size: int, bold: bool) -> bool:
        return d.textlength(text, font=font(size, bold)) <= inner

    def shrink_to_fit(text: str, size: int, bold: bool) -> int:
        while size > 8 and not fits(text, size, bold):
            size -= 1
        return size

    # The contact block: address and phone on one line while that fits at the
    # nominal body size. It is one field to the reader, so it degrades as a
    # unit — first by splitting the phone onto its own line, then by wrapping
    # the address at a comma. This decision is made ONCE, at nominal size,
    # before any vertical scaling: wrapping and then scaling the block keeps
    # the card in proportion, where letting a shrunk font pull the address back
    # onto one line would give a full-width line of tiny type.
    body = round(frame_h * BODY_H)
    phone_line = f"Mo. {fields['phone']}"
    address = fields["address"]
    contact = f"{address}{CONTACT_SEP}{phone_line}"
    if fits(contact, body, False):
        contact_lines = [contact]
    else:
        contact_lines = [address, phone_line]
        if not fits(address, body, False):
            f_body = font(body, False)
            cuts = [i for i, ch in enumerate(address) if ch == ","]
            if cuts:
                # Balance by RENDERED WIDTH, not character index — the widest
                # line is what forces the shrink, so minimising it is the goal.
                def widest(i: int) -> float:
                    a, b_ = address[:i + 1].strip(), address[i + 1:].strip()
                    return max(d.textlength(a, font=f_body),
                               d.textlength(b_, font=f_body))
                c = min(cuts, key=widest)
                contact_lines = [address[:c + 1].strip(),
                                 address[c + 1:].strip(), phone_line]

    def layout(scale: float):
        """Every line the card draws at one scale factor, and the height it needs.

        Widths are re-fitted at each scale, and heights come from the fonts
        actually chosen rather than from the nominal sizes.
        """
        name_size = round(frame_h * NAME_H * scale)
        body_size = round(frame_h * BODY_H * scale)
        gap = round(frame_h * LINE_GAP * scale)
        pad = round(frame_h * PANEL_PAD * scale)

        spec = [
            (fields["name"], shrink_to_fit(fields["name"], name_size, True), True),
            (fields["workshop"], shrink_to_fit(fields["workshop"], body_size, True), True),
        ]
        # Every contact line shares ONE size — the largest at which all of them
        # fit. Sizing them independently left a short line large next to a long
        # line small, which reads as a rendering fault rather than a layout.
        contact_size = body_size
        while contact_size > 8 and any(
            not fits(t, contact_size, False) for t in contact_lines
        ):
            contact_size -= 1
        spec += [(t, contact_size, False) for t in contact_lines]

        rendered = []
        for text, size, bold in spec:
            f = font(size, bold)
            asc, desc = f.getmetrics()
            rendered.append((text, f, asc + desc))
        total = sum(h for _, _, h in rendered) + gap * (len(rendered) - 1)
        return rendered, gap, total, total + 2 * pad

    scale = 1.0
    rendered, gap, total, needed = layout(scale)
    while needed > panel_h and scale > MIN_SCALE:
        scale = round(scale - 0.02, 2)
        rendered, gap, total, needed = layout(scale)
    if needed > panel_h:
        # Not a failure — a squeezed card still delivers. But it means intake
        # let through something longer than the template was sized for, which
        # is worth seeing in the logs before the client sees it in a video.
        log.warning("media.card_overflows", needed=needed, panel_h=panel_h,
                    scale=scale, lines=len(rendered))

    d.rectangle([x0, y0, x1, y1], fill=GREEN)
    d.rectangle([x0, y1, x1, y1 + max(2, round(frame_h * ACCENT_H))], fill=RED)

    y = y0 + (panel_h - total) // 2     # vertically centre the block in the panel
    cx = (x0 + x1) / 2
    for text, f, h in rendered:
        d.text((cx, y), text, font=f, fill=WHITE, anchor="ma")   # centre-aligned
        y += h + gap

    img.save(path, "PNG")
    return path


def card_payload(
    *, name: str, workshop: str, address: str, phone: str
) -> dict[str, Any]:
    """The exact dict the card is rendered from.

    Also what goes into the composite input hash — so a corrected spelling
    regenerates the card, and nothing else.
    """
    return {"name": name, "workshop": workshop, "address": address, "phone": phone}


# ------------------------------------------------------------ composite ----


def audio_stream_duration_seconds(path: Path) -> float | None:
    """Duration of the AUDIO stream specifically, not the container.

    `probe_duration_seconds` reports the container, which for an avatar render
    is the VIDEO length. These differ, and the difference is the point — see
    `composite`.
    """
    out = _run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        "probe audio stream duration",
    )
    try:
        return float(out.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


def composite(video: Path, card: Path, out: Path) -> Path:
    """ffmpeg overlay, fixed geometry, trimmed to the speech. Deterministic.

    The card is already frame-sized, so this is a straight 0,0 overlay — there
    is no offset to get wrong.

    TRIMMED TO THE AUDIO, and that is not a detail. kling-avatar-v2 returns a
    video LONGER than the audio it was given — measured at 27.47s of video
    carrying 25.57s of speech, a 1.9s tail in which the avatar keeps moving
    with nothing to say. It reads as the mechanic fidgeting after his line, and
    it is the last thing the viewer sees.

    Cutting it here rather than asking the model for stillness is deliberate:
    the model does not take a duration and cannot be relied on to stop on cue,
    while ffmpeg cuts exactly. It is also FREE and needs no re-render — the
    trailing frames were already paid for at submit either way.

    Note this drift is what `ChecksStage.duration_matches_audio` measures with a
    1500ms tolerance. At 1902ms every job was failing that check silently,
    because checks are logged rather than blocking. Trimming makes the check
    mean what it says.

    -crf 16 / veryslow: the overlay is a static graphic over an already
    compressed source, so the re-encode must be visually lossless or it throws
    away quality we paid the avatar model for. ffmpeg's default crf 23 cut the
    bitrate from 4.59 to 1.19 Mbps here, which is very visible on the card
    edges and on skin gradients.
    """
    speech = audio_stream_duration_seconds(video)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(card),
           "-filter_complex", "[0:v][1:v]overlay=0:0"]
    if speech:
        # Fail OPEN, not closed: an unreadable audio stream means we ship the
        # untrimmed video, which is the behaviour we had. Refusing to composite
        # would park a finished job over a cosmetic tail.
        cmd += ["-t", f"{speech:.3f}"]
    else:
        log.warning(
            "media.composite_no_audio_duration",
            note="could not read the audio stream; shipping the full video "
                 "including any silent tail",
        )
    cmd += ["-c:v", "libx264", "-crf", "16", "-preset", "veryslow",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-c:a", "copy", str(out)]
    _run(cmd, "composite overlay")
    return out
