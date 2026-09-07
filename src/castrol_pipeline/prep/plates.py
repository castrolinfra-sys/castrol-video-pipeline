"""Plate selection: (background, outfit) from the export -> a plate row.

An unmapped value is a REJECTED row, never a defaulted one. Silently falling
back to a default plate would put a mechanic in the wrong uniform against the
wrong scene and nobody would know why.
"""

from __future__ import annotations

#: Export `outfit` string -> uniform_id in `plates`.
#: 'Castrol T-shirt' is the one value confirmed by the client. The remaining
#: values are still open (PROJECT_PLAN open issue 3) — until they arrive, any
#: other string rejects with UNKNOWN_OUTFIT, which is the intended behaviour.
OUTFIT_TO_UNIFORM: dict[str, str] = {
    "castrol t-shirt": "polo",
}

#: Export `background` string -> background_id in `plates`.
#: 'SUV' is confirmed. The other two are inferred from the plate descriptions
#: and need client confirmation before the first real pull.
BACKGROUND_TO_ID: dict[str, str] = {
    "suv": "bg1_white_suv",
    "sedan": "bg2_dark_sedan",
    "hatchback": "bg3_hatchback_hood",
}


class UnknownOutfit(ValueError):
    pass


class UnknownBackground(ValueError):
    pass


def _key(value: str | None) -> str:
    return (value or "").strip().lower()


def resolve_uniform(outfit: str | None) -> str:
    uniform = OUTFIT_TO_UNIFORM.get(_key(outfit))
    if uniform is None:
        raise UnknownOutfit(f"Unmapped outfit {outfit!r}")
    return uniform


def resolve_background(background: str | None) -> str:
    bg = BACKGROUND_TO_ID.get(_key(background))
    if bg is None:
        raise UnknownBackground(f"Unmapped background {background!r}")
    return bg


def resolve_combo(*, outfit: str | None, background: str | None) -> tuple[str, str]:
    """Return (uniform_id, background_id). Raises on either being unmapped."""
    return resolve_uniform(outfit), resolve_background(background)
