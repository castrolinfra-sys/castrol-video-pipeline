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


def to_mp3(src: Path, dst: Path) -> Path:
    """Transcode to MP3.

    NOT optional. The avatar model's "Audio size is too large" is a BYTE limit,
    not a duration limit — a 37s WAV has failed while a 53s WAV succeeded.
    Cartesia's pcm_f32le is ~176 KB/s, so 40s is ~7 MB against ~640 KB as MP3.
    """
    if src.suffix.lower() == ".mp3":
        return src
    _run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-codec:a", "libmp3lame", "-b:a", "128k", str(dst)],
        "mp3 transcode",
    )
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
#: resolution turns out to be. Measured from the client's reference mockup; the
#: vertical position is from client review of the first real videos.
PANEL_X0, PANEL_X1 = 0.1856, 0.8144
PANEL_Y0 = 0.7000
PANEL_Y1 = PANEL_Y0 + 0.1703
ACCENT_H = 0.0077
GREEN = (1, 77, 38, 255)
RED = (210, 36, 25, 255)
WHITE = (255, 255, 255, 255)


def render_card(fields: dict[str, str], frame_w: int, frame_h: int, path: Path) -> Path:
    """Deterministic Pillow render. No generative model ever touches this text.

        panel   x 18.56% .. 81.44%   (62.9% wide, centred)
                y 70.00% down, growing to fit
        accent  red bar directly beneath, ~0.77% of frame height
        colours panel #014D26 (Castrol green), accent #D22419, text white

    The reference mockup measured 80.95% for the top edge, but that plate was
    framed waist-up. Once apimart reframes to 9:16 the subject sits higher and
    the card lands over the knees, so 70% is the reviewed position.

    Returns a FULL-FRAME transparent PNG, so the composite is a plain overlay
    at 0,0 and the position cannot drift.
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (frame_w, frame_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    x0, x1 = round(frame_w * PANEL_X0), round(frame_w * PANEL_X1)
    y0 = round(frame_h * PANEL_Y0)
    pw = x1 - x0
    ph = round(frame_h * (PANEL_Y1 - PANEL_Y0))   # nominal, for type sizing

    def font(px: int, bold: bool):
        names = (("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf") if bold
                 else ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"))
        for n in names:
            try:
                return ImageFont.truetype(n, px)
            except OSError:
                continue
        return ImageFont.load_default()

    def font_for(text, size, bold, limit):
        f = font(size, bold)
        while d.textlength(text, font=f) > limit and size > 8:
            size -= 1
            f = font(size, bold)
        return f

    inner = pw * 0.92
    body = round(ph * 0.150)

    # A long address wraps onto a second line rather than shrinking away.
    # Shrinking made a 54-char address render at ~60% the size of the line
    # above it, which is unreadable on a phone.
    addr = fields["address"]
    addr_lines = [addr]
    if d.textlength(addr, font=font(body, False)) > inner:
        f_body = font(body, False)
        cuts = [i for i, ch in enumerate(addr) if ch == ","]
        if cuts:
            # Balance by RENDERED WIDTH, not character index — the widest line
            # is what forces the shrink, so minimising it is the actual goal.
            def widest(i):
                a, b_ = addr[:i + 1].strip(), addr[i + 1:].strip()
                return max(d.textlength(a, font=f_body), d.textlength(b_, font=f_body))
            c = min(cuts, key=widest)
            addr_lines = [addr[:c + 1].strip(), addr[c + 1:].strip()]

    # Name is the hero line; everything else is one smaller regular size.
    # A wrapped address is ONE field, so both its lines share one size — the
    # largest at which every line fits. Sizing them independently left the
    # short first line large and the long second line small, which reads as a
    # rendering fault rather than a layout.
    addr_size = body
    while addr_size > 8 and any(
        d.textlength(a, font=font(addr_size, False)) > inner for a in addr_lines
    ):
        addr_size -= 1

    spec = [(fields["name"], round(ph * 0.235), True)]
    spec.append((fields["workshop"], body, False))
    spec += [(a, addr_size, False) for a in addr_lines]
    spec.append((f"Mo. {fields['phone']}", body, False))

    rendered = []
    for text, size, bold in spec:
        f = font_for(text, size, bold, inner)
        asc, desc = f.getmetrics()
        rendered.append((text, f, asc + desc))

    # Draw the panel only once the content height is known: a wrapped address
    # adds a line, and a fixed panel would push the last line onto the accent
    # bar. Top edge stays pinned at PANEL_Y0 so the card never moves up.
    gap = round(ph * 0.02)
    pad = round(ph * 0.10)
    total = sum(h for _, _, h in rendered) + gap * (len(rendered) - 1)
    panel_h = max(ph, total + 2 * pad)
    y1 = y0 + panel_h
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


def composite(video: Path, card: Path, out: Path) -> Path:
    """ffmpeg overlay, full duration, fixed geometry. Deterministic.

    The card is already frame-sized, so this is a straight 0,0 overlay — there
    is no offset to get wrong.

    -crf 16 / veryslow: the overlay is a static graphic over an already
    compressed source, so the re-encode must be visually lossless or it throws
    away quality we paid the avatar model for. ffmpeg's default crf 23 cut the
    bitrate from 4.59 to 1.19 Mbps here, which is very visible on the card
    edges and on skin gradients.
    """
    _run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(card),
         "-filter_complex", "[0:v][1:v]overlay=0:0",
         "-c:v", "libx264", "-crf", "16", "-preset", "veryslow",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart",
         "-c:a", "copy", str(out)],
        "composite overlay",
    )
    return out
