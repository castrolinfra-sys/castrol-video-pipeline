"""Intake: pull a window, validate, land photos, create jobs.

Order matters here. Validation is cheap and runs first; the photo fetch is the
expensive step and only happens for rows that already passed every row-level
rule. A row rejected at intake is terminal and is never repaired.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from ..common import db, hashing
from ..common.errors import RejectCode
from ..common.logging import get_logger
from ..common.s3 import get_storage, job_key
from ..config import Settings, get_settings
from .dedupe import compute_submission_hash, media_key_from_url
from .export_client import ExportClient, ExportResult, ExportWindow
from .media import MediaError, fetch_photo
from .validate import Rejection, ValidRow, parse_export_timestamp, validate_row

log = get_logger(__name__)


class IntakeCounters:
    def __init__(self) -> None:
        self.pulled = 0
        self.new = 0
        self.duplicate = 0
        self.rejected = 0
        self.jobs_created = 0
        self.reject_codes: dict[str, int] = {}

    def reject(self, code: str) -> None:
        self.rejected += 1
        self.reject_codes[code] = self.reject_codes.get(code, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "pulled": self.pulled,
            "new": self.new,
            "duplicate": self.duplicate,
            "rejected": self.rejected,
            "jobs_created": self.jobs_created,
            "reject_codes": self.reject_codes,
        }


def record_pull(batch_id: str, window: ExportWindow, result: ExportResult) -> str:
    """Store the pull itself, before anything is interpreted.

    `submissions` records what we DID with a row. This records what we were
    HANDED, which is a different question and the only one that can settle a
    dispute about whether a field was ever sent to us. It is also the only
    place a REJECTED row's data survives in full - invariant 8 says we never
    repair one, so nothing downstream keeps it.
    """
    row = db.fetch_one(
        """
        INSERT INTO export_pulls (batch_id, from_date, to_date, http_status,
                                  row_count, csv_sha256, header)
        VALUES (%(batch)s, %(from)s, %(to)s, %(status)s, %(count)s, %(sha)s, %(header)s)
        RETURNING id;
        """,
        {
            "batch": batch_id,
            "from": window.from_date,
            "to": window.to_date,
            "status": result.http_status,
            "count": len(result.rows),
            "sha": result.csv_sha256,
            "header": result.header,
        },
    )
    assert row is not None
    return str(row["id"])


def record_row(pull_id: str, index: int, row: dict[str, str]) -> None:
    """One CSV row, verbatim, values as the strings they arrived as."""
    db.execute(
        """
        INSERT INTO export_rows (pull_id, row_index, raw, row_sha256)
        VALUES (%(pull)s, %(i)s, %(raw)s, %(sha)s)
        ON CONFLICT (pull_id, row_index) DO NOTHING;
        """,
        {
            "pull": pull_id,
            "i": index,
            "raw": Jsonb(row),
            # canonical_json is the ONE serialiser for hashing (invariant 4).
            "sha": hashing.sha256_hex(hashing.canonical_json(row)),
        },
    )


def link_row_to_submission(pull_id: str, index: int, submission_id: str) -> None:
    db.execute(
        """
        UPDATE export_rows SET submission_id = %(sub)s
         WHERE pull_id = %(pull)s AND row_index = %(i)s;
        """,
        {"sub": submission_id, "pull": pull_id, "i": index},
    )


def open_batch(window: ExportWindow) -> str:
    row = db.fetch_one(
        """
        INSERT INTO batches (kind, from_date, to_date, status)
        VALUES ('daily', %(from)s, %(to)s, 'running')
        RETURNING id;
        """,
        {"from": window.from_date, "to": window.to_date},
    )
    assert row is not None
    return str(row["id"])


def close_batch(batch_id: str, counters: IntakeCounters, *, error: str | None = None) -> None:
    db.execute(
        """
        UPDATE batches
           SET status = %(status)s,
               submissions_pulled = %(pulled)s,
               submissions_new = %(new)s,
               submissions_rejected = %(rejected)s,
               jobs_created = %(jobs)s,
               stage_failure_counts = %(codes)s,
               finished_at = now(),
               error_message = %(error)s
         WHERE id = %(id)s;
        """,
        {
            "id": batch_id,
            "status": "failed" if error else "completed",
            "pulled": counters.pulled,
            "new": counters.new,
            "rejected": counters.rejected,
            "jobs": counters.jobs_created,
            "codes": Jsonb(counters.reject_codes),
            "error": error,
        },
    )


def _already_seen(submission_hash: str, client_submission_id: str | None) -> bool:
    """Have we stored this row before? Two keys, because the strong one is theirs.

    `submission_hash` covers the blob path and the whatsapp number. The client's
    own `id` covers the one case that hash cannot see: the same submission
    re-exported with a re-issued media url, which changes the path and so
    changes the hash.

    That case only became worth handling when the pull went on a timer. The
    windows overlap by design and re-return every row twice a day, so without
    this check a re-issued url is not a duplicate row - it is a UNIQUE violation
    on submissions_client_id_idx, raised mid-loop, which fails the WHOLE batch
    and takes the rows after it down with the one that collided.
    """
    if db.fetch_one(
        "SELECT 1 AS x FROM submissions WHERE submission_hash = %(h)s;",
        {"h": submission_hash},
    ):
        return True
    if client_submission_id and db.fetch_one(
        "SELECT 1 AS x FROM submissions WHERE client_submission_id = %(c)s;",
        {"c": client_submission_id},
    ):
        return True
    return False


def _insert_submission(
    *,
    batch_id: str,
    row: dict[str, Any],
    submission_hash: str,
    settings: Settings,
    valid: ValidRow | None,
    rejection: Rejection | None,
) -> str:
    # The export sends camelCase ISO 8601, not the snake_case naive strings
    # this originally assumed.
    created = parse_export_timestamp(
        row.get("createdAt"), settings.export_timestamp_format, settings.export_timezone
    )
    updated = parse_export_timestamp(
        row.get("updatedAt"), settings.export_timestamp_format, settings.export_timezone
    )
    image_url_raw = row.get("image_url") or ""

    inserted = db.fetch_one(
        """
        INSERT INTO submissions (
            submission_hash, media_key, batch_id, raw,
            phone_e164, user_name, workshop_name, address_raw, address_normalized,
            gender, mechanic_id, has_mechanic_id, mechanic_id_verified,
            client_submission_id, card_phone_e164,
            background_choice, outfit_choice,
            image_url_raw, image_mime_type, image_validation_status,
            image_rekognition_status, image_face_count,
            client_status, created_at_ist_raw, updated_at_ist_raw,
            created_at_ist, updated_at_ist,
            validation_status, reject_reason, reject_details
        ) VALUES (
            %(hash)s, %(media_key)s, %(batch_id)s, %(raw)s,
            %(phone)s, %(name)s, %(workshop)s, %(address_raw)s, %(address_norm)s,
            %(gender)s, %(mech_id)s, %(has_mech)s, %(mech_verified)s,
            %(client_id)s, %(card_phone)s,
            %(bg)s, %(outfit)s,
            %(url)s, %(mime)s, %(val_status)s,
            %(rek)s, %(faces)s,
            %(client_status)s, %(created_raw)s, %(updated_raw)s,
            %(created)s, %(updated)s,
            %(verdict)s, %(reject)s, %(reject_details)s
        )
        RETURNING id;
        """,
        {
            "hash": submission_hash,
            "media_key": media_key_from_url(image_url_raw) if image_url_raw else "",
            "batch_id": batch_id,
            "raw": Jsonb(row),
            "phone": valid.phone_e164 if valid else None,
            "name": valid.user_name if valid else row.get("user_name"),
            "workshop": valid.workshop_name if valid else row.get("workshop_name"),
            "address_raw": row.get("address"),
            # address_normalized holds the SPOKEN form, not a tidied postal
            # address - it is what the voiceover says. address_raw is what the
            # card prints.
            "address_norm": valid.spoken_place if valid else None,
            "gender": row.get("gender"),
            "mech_id": row.get("mechanic_id"),
            # The export reports verification as a STRING, not the boolean this
            # column was built for. Both are stored: the boolean for anything
            # already reading it, the string because it is what actually
            # arrived and "NOT VERIFIED" is not the same fact as false.
            "has_mech": (row.get("mechanic_id_verified") or "").strip().upper() == "VERIFIED",
            "mech_verified": row.get("mechanic_id_verified"),
            "client_id": valid.client_submission_id if valid else (row.get("id") or None),
            "card_phone": valid.card_phone_e164 if valid else None,
            # The resolved plate ids are stored here because they are what the
            # pipeline routes on. The client's exact strings are preserved
            # verbatim in `raw`, so nothing is lost.
            "bg": valid.background_id if valid else row.get("background"),
            "outfit": valid.uniform_id if valid else row.get("outfit"),
            # Byte-exact. Never rebuilt from parts. See intake/media.py.
            "url": image_url_raw,
            "mime": row.get("image_mime_type"),
            "val_status": row.get("image_validation_status"),
            "rek": row.get("image_rekognition_status"),
            # Not in the real export at all. Kept NULL rather than removed:
            # the column is harmless and dropping it is a separate decision.
            "faces": None,
            "client_status": row.get("status"),
            "created_raw": row.get("createdAt"),
            "updated_raw": row.get("updatedAt"),
            "created": created,
            "updated": updated,
            "verdict": "valid" if valid else "rejected",
            "reject": str(rejection.code) if rejection else None,
            "reject_details": Jsonb({"detail": rejection.detail}) if rejection else None,
        },
    )
    assert inserted is not None
    return str(inserted["id"])


def _mark_rejected(submission_id: str, code: RejectCode, detail: str) -> None:
    db.execute(
        """
        UPDATE submissions
           SET validation_status = 'rejected',
               reject_reason = %(code)s,
               reject_details = %(details)s
         WHERE id = %(id)s;
        """,
        {"id": submission_id, "code": str(code), "details": Jsonb({"detail": detail})},
    )


def _resolve_plate(uniform_id: str, background_id: str) -> str | None:
    """The active, approved plate for this combo, or None.

    None is not fatal at intake — plate generation is a parallel track. It does
    mean stage B has nothing to work from, which surfaces as a stage failure
    rather than a silently wrong uniform.
    """
    row = db.fetch_one(
        """
        SELECT id FROM plates
         WHERE uniform_id = %(u)s AND background_id = %(b)s AND active
         LIMIT 1;
        """,
        {"u": uniform_id, "b": background_id},
    )
    return str(row["id"]) if row else None


def _create_job(submission_id: str, valid: ValidRow, batch_id: str, settings: Settings) -> str:
    plate_id = _resolve_plate(valid.uniform_id, valid.background_id)
    if plate_id is None:
        log.warning(
            "intake.no_active_plate",
            uniform_id=valid.uniform_id,
            background_id=valid.background_id,
        )
    row = db.fetch_one(
        """
        INSERT INTO jobs (submission_id, script_version, voice_id, plate_id, batch_id)
        VALUES (%(sub)s, %(script)s, %(voice)s, %(plate)s, %(batch)s)
        ON CONFLICT (submission_id) DO NOTHING
        RETURNING id;
        """,
        {
            "sub": submission_id,
            "script": settings.script_version,
            "voice": settings.tts_voice_id or "stub-voice",
            "plate": plate_id,
            "batch": batch_id,
        },
    )
    return str(row["id"]) if row else ""


def run_intake(
    *,
    from_date: date,
    to_date: date,
    fixture: str | Path | None = None,
    fetch_media: bool = True,
) -> IntakeCounters:
    """Pull one window and land it. Safe to re-run: dedupe is by submission_hash."""
    settings = get_settings()
    window = ExportWindow(from_date=from_date, to_date=to_date)
    counters = IntakeCounters()
    batch_id = open_batch(window)

    try:
        result = (
            ExportClient.from_fixture(fixture)
            if fixture
            else ExportClient(settings).fetch(window)
        )
        counters.pulled = len(result.rows)

        # Recorded BEFORE any row is interpreted, so a pull whose processing
        # blows up halfway still leaves evidence of exactly what arrived.
        pull_id = record_pull(batch_id, window, result)
        for index, row in enumerate(result.rows):
            record_row(pull_id, index, row)

        for index, row in enumerate(result.rows):
            image_url_raw = row.get("image_url") or ""
            # whatsapp_number, not mechanic_phone_number: the delivery key is
            # the identity this row is about. The card number is a different
            # number and hashing on it would dedupe the wrong thing.
            phone_for_hash = str(row.get("whatsapp_number") or "")
            sub_hash = compute_submission_hash(image_url_raw, phone_for_hash)

            client_id = (row.get("id") or "").strip() or None
            if _already_seen(sub_hash, client_id):
                counters.duplicate += 1
                continue

            verdict = validate_row(row)
            rejection = verdict if isinstance(verdict, Rejection) else None
            valid = verdict if isinstance(verdict, ValidRow) else None

            submission_id = _insert_submission(
                batch_id=batch_id,
                row=row,
                submission_hash=sub_hash,
                settings=settings,
                valid=valid,
                rejection=rejection,
            )
            counters.new += 1
            link_row_to_submission(pull_id, index, submission_id)

            if rejection is not None:
                counters.reject(str(rejection.code))
                log.info("intake.rejected", code=str(rejection.code), detail=rejection.detail)
                continue

            assert valid is not None

            if fetch_media:
                try:
                    photo = fetch_photo(valid.image_url_raw)
                except MediaError as exc:
                    _mark_rejected(submission_id, RejectCode(exc.reason), str(exc))
                    counters.reject(exc.reason)
                    log.info("intake.rejected", code=exc.reason, detail=str(exc))
                    continue

                # Download once, at intake, straight to storage. Our copy is the
                # system of record — nothing fetches the client's blob at job time.
                job_row_id = _create_job(submission_id, valid, batch_id, settings)
                if not job_row_id:
                    continue
                obj = get_storage().put(
                    job_key(job_row_id, f"source.{photo.extension}"),
                    photo.data,
                    content_type=photo.mime_type,
                )
                db.execute(
                    """
                    INSERT INTO assets (job_id, kind, s3_key, sha256, bytes,
                                        mime_type, width, height)
                    VALUES (%(job)s, 'source_photo', %(key)s, %(sha)s,
                            %(bytes)s, %(mime)s, %(w)s, %(h)s)
                    ON CONFLICT (s3_key) DO NOTHING;
                    """,
                    {
                        "job": job_row_id,
                        "key": obj.key,
                        "sha": obj.sha256,
                        "bytes": obj.bytes,
                        "mime": photo.mime_type,
                        "w": photo.width,
                        "h": photo.height,
                    },
                )
            else:
                job_row_id = _create_job(submission_id, valid, batch_id, settings)
                if not job_row_id:
                    continue

            counters.jobs_created += 1

        close_batch(batch_id, counters)
        log.info("intake.complete", batch_id=batch_id, **counters.as_dict())

    except Exception as exc:
        close_batch(batch_id, counters, error=str(exc)[:1000])
        raise

    return counters
