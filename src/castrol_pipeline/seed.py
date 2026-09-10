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
from .common.s3 import get_storage, job_key, plate_key, uniform_ref_key
from .config import get_settings
from .prep.normalise import normalise_phone, spoken_place_from
from .prep.plates import plate_code
from .stages import media

log = get_logger(__name__)


class SeedError(ValueError):
    pass


def register_plate(
    path: Path,
    *,
    uniform_id: str,
    background_id: str,
    approved_by: str,
    uniform_ref: Path | None = None,
) -> str:
    """Upload a plate and mark it active. Returns the plate id.

    Activating is a deliberate act with a name attached: the real stages refuse
    an inactive plate, so this is the one door an unapproved scene could walk
    through into a paid call.

    `uniform_ref` is the optional plain-background shot of the garment, handed
    to the image edit as a third input so the uniform's fabric and printed
    marks are copied rather than reconstructed. It is stored on the plate ROW,
    which is what keeps it honest under invariant 31: this function retires and
    re-inserts rather than updating, so a job's `plate_id` keeps naming the
    exact pair of files it was built from even after the artwork is revised.

    It is not carried forward from the retired row on purpose. Re-registering a
    plate without a reference means a plate without a reference - inheriting
    would make the absence of an argument mean two different things depending
    on history, which is how you ship a combination you thought you had
    switched off.
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

        ref_key: str | None = None
        ref_digest: str | None = None
        if uniform_ref is not None:
            if not uniform_ref.exists():
                raise SeedError(f"Uniform reference not found: {uniform_ref}")
            # Same normalisation as the plate, and the sha is of the NORMALISED
            # bytes: that is what apimart is handed, and what the image stage
            # hashes. The sha of the file on disk will never equal this.
            ref_norm = media.normalise_for_apimart(
                uniform_ref, Path(tmp) / "uniform_ref.png"
            )
            ref_digest = hashing.sha256_hex(ref_norm.read_bytes())
            ref_key = uniform_ref_key(uniform_id, ref_digest)
            get_storage().put_file(ref_key, ref_norm, content_type="image/png")
            log.info(
                "seed.uniform_ref_registered",
                uniform_id=uniform_id,
                background_id=background_id,
                key=ref_key,
            )
        else:
            log.warning(
                "seed.plate_without_uniform_ref",
                uniform_id=uniform_id,
                background_id=background_id,
                note="the image edit will submit plate + photo only, and the "
                     "uniform's detail is whatever the model reconstructs",
            )

    # Append-only, deliberately. This used to UPDATE the active row in place,
    # which meant replacing the artwork for a combination silently rewrote
    # history: `jobs.plate_id` still pointed at the same row, so every job ever
    # made with the OLD plate now claimed to have used the new one. There is no
    # per-job plate asset to fall back on, so that link is the only record of
    # what a video was actually built from - and the client intends to revise
    # this artwork.
    #
    # Retiring and inserting keeps each generation of a plate addressable. The
    # partial unique index allows exactly one ACTIVE row per combination, so
    # the two statements must share a transaction.
    # The client's own number for this combination. Stamped on INSERT, not
    # backfilled: 0006 set it with UPDATEs, so every row registered after that
    # migration came out NULL. Unknown combinations are left NULL rather than
    # invented - a wrong plate_code is worse than a missing one.
    try:
        code = plate_code(uniform_id, background_id)
    except KeyError:
        code = None

    params = {
        "u": uniform_id,
        "b": background_id,
        "key": key,
        "sha": digest,
        "code": code,
        "ref_key": ref_key,
        "ref_sha": ref_digest,
        "who": approved_by,
        "notes": f"Seeded from {path.name}"
                 + (f" + uniform ref {uniform_ref.name}" if uniform_ref else ""),
    }
    with db.transaction() as cur:
        cur.execute(
            "UPDATE plates SET active = false "
            " WHERE uniform_id = %(u)s AND background_id = %(b)s AND active;",
            params,
        )
        cur.execute(
            """
            INSERT INTO plates (uniform_id, background_id, plate_code, s3_key,
                                sha256, uniform_ref_key, uniform_ref_sha256,
                                approved_by, approved_at, active, notes)
            VALUES (%(u)s, %(b)s, %(code)s, %(key)s, %(sha)s, %(ref_key)s,
                    %(ref_sha)s, %(who)s, now(), true, %(notes)s)
            RETURNING id;
            """,
            params,
        )
        row = cur.fetchone()
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
    uniform_ref: Path | None = None,
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
    # address_normalized is the SPOKEN form; address_raw is what the card
    # prints. The address is free text - any shape - so there is nothing to
    # validate beyond it not being empty.
    if not said.strip():
        raise SeedError("Address is empty, so there is nothing for the voice to say.")

    if plate_id is None:
        if plate is None:
            raise SeedError("Pass either --plate <file> or --plate-id <uuid>")
        plate_id = register_plate(
            plate, uniform_id=uniform_id, background_id=background_id,
            approved_by=approved_by, uniform_ref=uniform_ref,
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
