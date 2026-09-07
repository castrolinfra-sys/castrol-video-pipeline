"""Intake validation. Pure functions, zero I/O.

Rejection is terminal and carries a stable code. Rows are NEVER repaired here:
a repaired row is a row whose output nobody can explain.

Photo-dependent codes (BAD_MIME, IMAGE_TOO_SMALL, FETCH_FAILED) are not decided
here — they come from intake/media.py, which is the only place that has bytes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..common.errors import RejectCode
from ..prep.normalise import normalise_address, normalise_name, normalise_phone
from ..prep.plates import UnknownBackground, UnknownOutfit, resolve_combo

#: Card width limits. TBC from the final artwork (PROJECT_PLAN open issue 5).
#: These bound what the card renderer will ever be asked to fit; the renderer's
#: shrink-then-truncate fallback should therefore effectively never fire. If it
#: does, these numbers are wrong.
MAX_NAME_CHARS = 25
MAX_WORKSHOP_CHARS = 30
MAX_ADDRESS_CHARS = 34

_TEST_NAME = re.compile(r"^\s*(test|testing|abc+|xyz+|asdf|demo|dummy|qwerty)\b", re.I)
_REPEATED_DIGITS = re.compile(r"^\+91(\d)\1{9}$")


@dataclass(frozen=True)
class Rejection:
    code: RejectCode
    detail: str


@dataclass(frozen=True)
class ValidRow:
    phone_e164: str
    user_name: str
    workshop_name: str
    locality: str
    city: str
    uniform_id: str
    background_id: str
    image_url_raw: str
    is_test: bool = False
    parsed: dict[str, Any] = field(default_factory=dict)


def parse_export_timestamp(raw: str | None, fmt: str, tz: str) -> datetime | None:
    """Parse with the PINNED format. Never infers.

    Returns None rather than guessing — the raw string is stored alongside, so a
    wrong format can be reparsed later without re-pulling.
    """
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw).strip(), fmt).replace(tzinfo=ZoneInfo(tz))
    except (ValueError, KeyError):
        return None


def looks_like_test_row(name: str | None, phone_e164: str | None) -> bool:
    if name and _TEST_NAME.match(name):
        return True
    if phone_e164 and _REPEATED_DIGITS.match(phone_e164):
        return True
    return False


def validate_row(row: dict[str, Any]) -> ValidRow | Rejection:
    """Apply the row-level rules. Returns a ValidRow or the first Rejection."""

    # ---------------------------------------------------- upstream verdicts --
    status = (row.get("image_validation_status") or "").strip().upper()
    if status != "APPROVED":
        return Rejection(RejectCode.NOT_APPROVED, f"image_validation_status={status!r}")

    face_count = row.get("image_face_count")
    try:
        face_count = int(face_count)
    except (TypeError, ValueError):
        return Rejection(RejectCode.FACE_COUNT_NOT_1, f"image_face_count={face_count!r}")
    if face_count != 1:
        # A group photo passes "face detected" upstream and still breaks stage B.
        return Rejection(RejectCode.FACE_COUNT_NOT_1, f"image_face_count={face_count}")

    # ------------------------------------------------------------- identity --
    gender = (row.get("gender") or "").strip().lower()
    if gender != "male":
        return Rejection(RejectCode.GENDER_UNSUPPORTED, f"gender={gender!r}")

    phone = normalise_phone(row.get("mechanic_phone_number"))
    if phone is None:
        return Rejection(RejectCode.BAD_PHONE, f"phone={row.get('mechanic_phone_number')!r}")

    name = normalise_name(row.get("user_name"))
    if not name:
        return Rejection(RejectCode.MISSING_FIELD, "user_name is empty")
    if len(name) > MAX_NAME_CHARS:
        return Rejection(RejectCode.NAME_TOO_LONG, f"{len(name)} > {MAX_NAME_CHARS}")

    workshop = normalise_name(row.get("workshop_name"))
    if not workshop:
        return Rejection(RejectCode.MISSING_FIELD, "workshop_name is empty")
    if len(workshop) > MAX_WORKSHOP_CHARS:
        return Rejection(RejectCode.WORKSHOP_TOO_LONG, f"{len(workshop)} > {MAX_WORKSHOP_CHARS}")

    address = normalise_address(row.get("address"))
    if address is None:
        return Rejection(RejectCode.BAD_ADDRESS, f"address={row.get('address')!r}")
    locality, city = address
    if len(f"{locality}, {city}") > MAX_ADDRESS_CHARS:
        return Rejection(RejectCode.BAD_ADDRESS, f"address exceeds {MAX_ADDRESS_CHARS} chars")

    # ---------------------------------------------------------- plate combo --
    try:
        uniform_id, background_id = resolve_combo(
            outfit=row.get("outfit"), background=row.get("background")
        )
    except UnknownOutfit as exc:
        return Rejection(RejectCode.UNKNOWN_OUTFIT, str(exc))
    except UnknownBackground as exc:
        return Rejection(RejectCode.UNKNOWN_BACKGROUND, str(exc))

    # ---------------------------------------------------------------- media --
    image_url_raw = row.get("image_url")
    if not image_url_raw:
        return Rejection(RejectCode.MISSING_FIELD, "image_url is empty")

    # ----------------------------------------------------------- test rows ---
    if looks_like_test_row(name, phone):
        return Rejection(RejectCode.TEST_ROW, f"heuristic match on name={name!r}")

    return ValidRow(
        phone_e164=phone,
        user_name=name,
        workshop_name=workshop,
        locality=locality,
        city=city,
        uniform_id=uniform_id,
        background_id=background_id,
        # Stored byte-exact. Never rebuilt from parts. See intake/media.py.
        image_url_raw=str(image_url_raw),
    )
