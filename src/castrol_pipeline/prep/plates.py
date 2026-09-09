"""Plate selection: (outfit, background) from the export -> a plate row.

An unmapped value is a REJECTED row, never a defaulted one. Silently falling
back to a default plate would put a mechanic in the wrong uniform against the
wrong scene and nobody would know why.

Vocabulary confirmed by the client's own combination map, 2026-09-09:

    Uniform 1 = Castrol T-Shirt      Background 1 = SUV
    Uniform 2 = Castrol Uniform      Background 2 = Sedan
                                     Background 3 = Hatchback

Two things that map cleanly and one trap. The background ids were always right
- Background 1 really is the SUV - so the descriptions stay. What was wrong was
the KEY side: the export sends "Background 1", never "SUV", so every real row
rejected with UNKNOWN_BACKGROUND while the values it mapped to were correct.

The uniform ids were the opposite problem: `polo` and `half_shirt` were our
invented garment names, and one of the two values is literally a t-shirt. A
reader deciding whether "Castrol T-shirt" meant `polo` or `half_shirt` had a
coin flip with no way to check. They now carry the client's own number and the
client's own word.
"""

from __future__ import annotations

#: Export `outfit` string -> uniform_id in `plates`.
#: Keys are lowercased on lookup. The client's reference sheet writes these as
#: "Uniform 1" / "Uniform 2" while the export sends the garment name, so both
#: spellings are accepted - they are the same fact written two ways, not a
#: guess at an unknown value.
OUTFIT_TO_UNIFORM: dict[str, str] = {
    "castrol t-shirt": "u1_tshirt",
    "castrol t shirt": "u1_tshirt",
    "castrol tshirt": "u1_tshirt",
    "uniform 1": "u1_tshirt",
    "castrol uniform": "u2_uniform",
    "uniform 2": "u2_uniform",
}

#: Export `background` string -> background_id in `plates`.
#: The export sends "Background N"; the client's sheet describes the same three
#: as SUV / Sedan / Hatchback. Both are accepted for the same reason.
BACKGROUND_TO_ID: dict[str, str] = {
    "background 1": "bg1_white_suv",
    "suv": "bg1_white_suv",
    "background 2": "bg2_dark_sedan",
    "sedan": "bg2_dark_sedan",
    "background 3": "bg3_hatchback_hood",
    "hatchback": "bg3_hatchback_hood",
}

#: (uniform_id, background_id) -> the approved plate artwork.
#: The client numbers these 1..6 in this exact order: the three t-shirt
#: combinations first, then the three uniform ones.
PLATE_CODE: dict[tuple[str, str], str] = {
    ("u1_tshirt", "bg1_white_suv"): "plate_01",
    ("u1_tshirt", "bg2_dark_sedan"): "plate_02",
    ("u1_tshirt", "bg3_hatchback_hood"): "plate_03",
    ("u2_uniform", "bg1_white_suv"): "plate_04",
    ("u2_uniform", "bg2_dark_sedan"): "plate_05",
    ("u2_uniform", "bg3_hatchback_hood"): "plate_06",
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


def plate_code(uniform_id: str, background_id: str) -> str:
    """The client's plate number for a combination.

    Every (uniform, background) the resolvers can produce has one, so a
    KeyError here means the two tables above disagree - a bug, not bad data.
    """
    return PLATE_CODE[(uniform_id, background_id)]
