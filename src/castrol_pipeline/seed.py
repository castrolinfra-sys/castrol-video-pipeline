"""Create one job by hand, from local files and explicit values.

This is the manual-entry path: what you use to re-run a single mechanic, to
test a plate, or to produce a sample for the client. It does what intake does —
land the photo in S3, register a submission, create a job — without the export
API in the way.

It is not a substitute for intake. Intake pulls a window, dedupes an export
that re-returns rows, and rejects bad ones; this trusts you.

Idempotent by (photo sha256, phone): seeding the same inputs twice returns the
existing job rather than paying for a second video.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from psycopg.types.json import Jsonb

from .common import db, hashing
from .common.events import record_event
from .common.logging import get_logger
from .common.s3 import get_storage, job_key, plate_key
from .config import get_settings
from .prep.normalise import normalise_address, normalise_phone
from .stages import media

log = get_logger(__name__)


class SeedError(ValueError):
    pass


def spoken_place_from(address: str) -> str:
    """The area to SAY, derived from the address we print.

    Indian addresses run most-specific to least, so the last segment is the
    area and everything before it is doorway detail: "Beturkar Pada, Opposite
    New National Hospital, Andheri" is spoken as "Andheri". Reading the whole
    string aloud puts a hospital landmark in a 30-second ad.

    A two-part `Locality, City` address is already the spoken form and is kept
    whole. Pass --spoken-place to override either way.
    """
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if len(parts) <= 2:
        return ", ".join(parts)
    return parts[-1]


def register_plate(
    path: Path, *, uniform_id: str, background_id: str, approved_by: str
) -> str:
    """Upload a plate and mark it active. Returns the plate id.

    Activating is a deliberate act with a name attached: the real stages refuse
    an inactive plate, so this is the one door an unapproved scene could walk
    through into a paid call.
    """
    with tempfile.TemporaryDirectory() as tmp:
        norm = media.normalise_for_apimart(path, Path(tmp) / "plate.png")
        width, height = _size(norm)
        if (width, height) != (1080, 1920):
            log.warning(
                "seed.plate_not_1080x1920",
                size=f"{width}x{height}",
                note="apimart reframes non-9:16 plates by inventing new ceiling "
                     "and floor; the card position is calibrated for 1080x1920",
            )
        digest = hashing.sha256_hex(norm.read_bytes())
        key = plate_key(uniform_id, background_id, digest)
        get_storage().put_file(key, norm, content_type="image/png")

    row = db.fetch_one(
        """
        INSERT INTO plates (uniform_id, background_id, s3_key, sha256,
                            approved_by, approved_at, active, notes)
        VALUES (%(u)s, %(b)s, %(key)s, %(sha)s, %(who)s, now(), true, %(notes)s)
        ON CONFLICT (uniform_id, background_id) WHERE active DO UPDATE
           SET s3_key = EXCLUDED.s3_key,
               sha256 = EXCLUDED.sha256,
               approved_by = EXCLUDED.approved_by,
               approved_at = now()
        RETURNING id;
        """,
        {
            "u": uniform_id,
            "b": background_id,
            "key": key,
            "sha": digest,
            "who": approved_by,
            "notes": f"Seeded from {path.name}",
        },
    )
    if row is None:
        raise SeedError(f"Could not register plate for {uniform_id}/{background_id}")
    return str(row["id"])


def _size(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as im:
        return im.size


def seed_job(
    *,
    photo: Path,
    name: str,
    workshop: str,
    address: str,
    phone: str,
    plate: Path | None = None,
    plate_id: str | None = None,
    spoken_place: str | None = None,
    uniform_id: str = "polo",
    background_id: str = "bg1_white_suv",
    approved_by: str = "seed-job",
) -> dict[str, str]:
    """Create (or return) one job. Returns {job_id, submission_id, plate_id}."""
    s = get_settings()

    if not photo.exists():
        raise SeedError(f"Photo not found: {photo}")
    phone_e164 = normalise_phone(phone)
    if not phone_e164:
        raise SeedError(f"Not a valid Indian mobile number: {phone!r}")

    said = spoken_place or spoken_place_from(address)
    # address_normalized is the SPOKEN form and the orchestrator splits it on
    # ", " into locality and city. address_raw is what the card prints.
    if normalise_address(said) is None and "," in said:
        raise SeedError(
            f"Spoken place {said!r} is neither 'Locality' nor 'Locality, City'. "
            "Pass --spoken-place explicitly."
        )

    if plate_id is None:
        if plate is None:
            raise SeedError("Pass either --plate <file> or --plate-id <uuid>")
        plate_id = register_plate(
            plate, uniform_id=uniform_id, background_id=background_id,
            approved_by=approved_by,
        )

    photo_sha = hashing.sha256_hex(photo.read_bytes())
    sub_hash = hashing.submission_hash(photo_sha, phone_e164)

    raw = {
        "source": "seed-job",
        "user_name": name,
        "workshop_name": workshop,
        "address": address,
        "spoken_place": said,
        "whatsapp_number": phone,
        "outfit": uniform_id,
        "background": background_id,
        "photo_sha256": photo_sha,
    }

    submission = db.fetch_one(
        """
        INSERT INTO submissions (submission_hash, media_key, raw, phone_e164,
                                 user_name, workshop_name, address_raw,
                                 address_normalized, background_choice,
                                 outfit_choice, validation_status)
        VALUES (%(hash)s, %(media)s, %(raw)s, %(phone)s, %(name)s, %(shop)s,
                %(addr)s, %(said)s, %(bg)s, %(outfit)s, 'valid')
        ON CONFLICT (submission_hash) DO UPDATE
           SET user_name = EXCLUDED.user_name,
               workshop_name = EXCLUDED.workshop_name,
               address_raw = EXCLUDED.address_raw,
               address_normalized = EXCLUDED.address_normalized,
               raw = EXCLUDED.raw
        RETURNING id;
        """,
        {
            "hash": sub_hash,
            "media": photo_sha,
            "raw": Jsonb(raw),
            "phone": phone_e164,
            "name": name,
            "shop": workshop,
            "addr": address,
            "said": said,
            "bg": background_id,
            "outfit": uniform_id,
        },
    )
    submission_id = str(submission["id"])  # type: ignore[index]

    job = db.fetch_one(
        """
        INSERT INTO jobs (submission_id, plate_id, script_version, voice_id)
        VALUES (%(sub)s, %(plate)s, %(script)s, %(voice)s)
        ON CONFLICT (submission_id) DO UPDATE
           SET plate_id = EXCLUDED.plate_id
        RETURNING id;
        """,
        {
            "sub": submission_id,
            "plate": plate_id,
            "script": s.script_version,
            "voice": s.require("tts_voice_id"),
        },
    )
    job_id = str(job["id"])  # type: ignore[index]

    # The photo lands under the job so every artefact for one video shares a
    # prefix. Normalised on the way in: apimart rejects either axis outside
    # [300, 6000] px, and finding that out costs a paid submit.
    with tempfile.TemporaryDirectory() as tmp:
        norm = media.normalise_for_apimart(photo, Path(tmp) / "source_photo.png")
        stored = get_storage().put_file(
            job_key(job_id, "source_photo.png"), norm, content_type="image/png"
        )
        width, height = _size(norm)

    db.execute(
        """
        INSERT INTO assets (job_id, kind, s3_key, sha256, bytes, mime_type,
                            width, height, meta)
        VALUES (%(job)s, 'source_photo', %(key)s, %(sha)s, %(bytes)s, 'image/png',
                %(w)s, %(h)s, %(meta)s)
        ON CONFLICT (s3_key) DO NOTHING;
        """,
        {
            "job": job_id,
            "key": stored.key,
            "sha": stored.sha256,
            "bytes": stored.bytes,
            "w": width,
            "h": height,
            "meta": Jsonb({"original_sha256": photo_sha, "original_name": photo.name}),
        },
    )

    record_event(
        "job.seeded",
        job_id=job_id,
        name=name,
        workshop=workshop,
        spoken_place=said,
        plate_id=plate_id,
        photo_key=stored.key,
    )
    log.info(
        "seed.done",
        job_id=job_id,
        submission_id=submission_id,
        plate_id=plate_id,
    )
    return {
        "job_id": job_id,
        "submission_id": submission_id,
        "plate_id": str(plate_id),
        "photo_key": stored.key,
        "spoken_place": said,
        "card_address": address,
    }


def describe(job_id: str) -> str:
    """Everything known about one job, as JSON. The `castrol show` output."""
    job = db.fetch_one(
        """
        SELECT j.id, j.status, j.current_stage, j.failure_reason, j.created_at,
               j.completed_at, s.user_name, s.workshop_name, s.address_raw,
               s.address_normalized, s.phone_e164
          FROM jobs j JOIN submissions s ON s.id = j.submission_id
         WHERE j.id = %(id)s;
        """,
        {"id": job_id},
    )
    if job is None:
        raise SeedError(f"No job {job_id}")

    runs = db.fetch_all(
        """
        SELECT stage, status, attempts, vendor, vendor_task_id, model_id,
               cost_usd, billed_seconds, output_key, error_code, error_message,
               started_at, finished_at
          FROM stage_runs WHERE job_id = %(id)s ORDER BY created_at;
        """,
        {"id": job_id},
    )
    assets = db.fetch_all(
        """
        SELECT kind, s3_key, bytes, duration_ms, width, height, cdn_url
          FROM assets WHERE job_id = %(id)s ORDER BY created_at;
        """,
        {"id": job_id},
    )
    cost = db.fetch_one("SELECT * FROM job_costs WHERE job_id = %(id)s;", {"id": job_id})
    checks = db.fetch_all(
        "SELECT check_name, passed, score FROM checks WHERE job_id = %(id)s;",
        {"id": job_id},
    )

    return json.dumps(
        {
            "job": dict(job),
            "cost": dict(cost) if cost else None,
            "stage_runs": [dict(r) for r in runs],
            "assets": [dict(a) for a in assets],
            "checks": [dict(c) for c in checks],
        },
        indent=2,
        default=str,
    )
