"""Normalisation. Pure functions, zero I/O.

Two different jobs live here and must not be confused:

* `normalise_*` produces the values we STORE and print on the card.
* `expand_for_speech` produces the text we SEND TO TTS. It is lossy on purpose
  (digits become words) and its output must never be written to the card.
"""

from __future__ import annotations

import re

#: Bumped whenever any rule below changes. It is part of the prep input_hash,
#: so bumping it is what forces affected jobs to regenerate rather than skip.
NORMALISE_RULES_VERSION = "v1"

_WS = re.compile(r"\s+")

# Indian mobile numbers are 10 digits starting 6-9.
_MOBILE = re.compile(r"^[6-9]\d{9}$")


def collapse_whitespace(value: str) -> str:
    return _WS.sub(" ", value).strip()


def normalise_phone(raw: str | None) -> str | None:
    """Return E.164 (+91XXXXXXXXXX), or None if it is not a valid Indian mobile.

    Accepts the shapes the export has actually produced: bare 10 digits, a 91 or
    0 prefix, and separators. Anything else is a rejection, not a repair.
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if not _MOBILE.match(digits):
        return None
    return f"+91{digits}"


def normalise_address(raw: str | None) -> tuple[str, str] | None:
    """Split `Locality, City`. Returns None if it is not that shape.

    The export now supplies this pre-split, so anything else is a data problem
    worth rejecting rather than guessing at.
    """
    if not raw:
        return None
    parts = [collapse_whitespace(p) for p in str(raw).split(",")]
    parts = [p for p in parts if p]
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def normalise_name(raw: str | None) -> str | None:
    if not raw:
        return None
    return collapse_whitespace(str(raw)) or None


# ------------------------------------------------------- speech expansion ----

#: Hindi numerals in Latin script — the script is Hindi/Hinglish and the TTS
#: voice is a Hindi male. CONFIRM against the chosen voice before the first paid
#: batch; if the model reads Latin digits correctly this table can shrink.
NUMERAL_WORDS: dict[str, str] = {
    "0": "zero", "1": "ek", "2": "do", "3": "teen", "4": "chaar",
    "5": "paanch", "6": "chhah", "7": "saat", "8": "aath", "9": "nau",
}

#: Abbreviations that a TTS voice reliably mangles. Extend as real data shows up.
ABBREVIATIONS: dict[str, str] = {
    "&": " and ",
    "Mo.": "Mobile",
    "Rd.": "Road",
    "Rd": "Road",
    "St.": "Street",
    "Nr.": "Near",
    "Opp.": "Opposite",
    "Pvt.": "Private",
    "Ltd.": "Limited",
    "No.": "Number",
}


def expand_for_speech(text: str) -> str:
    """Expand abbreviations and speak digits individually.

    Digits are read one by one rather than as a quantity: '24' in a workshop
    name is far more often 'do chaar' than 'twenty-four', and a wrong reading of
    a workshop name is more jarring than a flat one.
    """
    out = text
    for abbr, full in ABBREVIATIONS.items():
        out = out.replace(abbr, full)
    out = "".join(f" {NUMERAL_WORDS[ch]} " if ch in NUMERAL_WORDS else ch for ch in out)
    return collapse_whitespace(out)
