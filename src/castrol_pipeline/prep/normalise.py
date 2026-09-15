"""Normalisation. Pure functions, zero I/O.

Two different jobs live here and must not be confused:

* `normalise_*` produces the values we STORE and print on the card.
* `expand_for_speech` produces the text we SEND TO TTS. It is lossy on purpose
  (abbreviations expand, long digit runs become words) and its output must
  never be written to the card.
"""

from __future__ import annotations

import re

#: Bumped whenever any rule below changes. It is part of the prep input_hash,
#: so bumping it is what forces affected jobs to regenerate rather than skip.
NORMALISE_RULES_VERSION = "v2"

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


#: An Indian PIN code at the very end of the spoken segment. Six digits, on its
#: own, last — anchored so it cannot eat a house number mid-string.
_TRAILING_PIN = re.compile(r"[\s,-]*\b\d{6}\b\s*$")


def normalise_address(raw: str | None) -> str | None:
    """Tidy an address. Any shape is legal; only empty is a rejection.

    This used to demand exactly `Locality, City` and reject anything else as a
    data problem. The client confirmed 2026-09-09 that the field is free text -
    a mechanic may type one word or three clauses - so a shape requirement here
    rejected real people for writing their own address normally.

    Whitespace is collapsed and empty comma segments dropped. Nothing else is
    touched: the card prints this, and inventing punctuation for a stranger's
    address is not ours to do.
    """
    if not raw:
        return None
    parts = [collapse_whitespace(p) for p in str(raw).split(",")]
    return ", ".join(p for p in parts if p) or None


def spoken_place_from(address: str) -> str:
    """The area to SAY, derived from the address we print.

    Indian addresses run most-specific to least, so the last segment is the
    area and everything before it is doorway detail: "Beturkar Pada, Opposite
    New National Hospital, Andheri" is spoken as "Andheri". Reading the whole
    string aloud puts a hospital landmark in a 30-second ad.

    One or two segments are already the spoken form and are kept whole.

    A trailing PIN code is dropped. It is postal routing, not a place: nobody
    says "come to Pushp Vihar one one zero zero one seven" out loud, and a real
    row - `Sector 3 Asian market pushp vihar south delhi 110017` - has no commas
    at all, so segment-splitting alone leaves it in. Only a SIX-digit run at the
    very end goes; a house number keeps its digits, and a PIN in the middle of
    an address is left alone because removing it could strip a building number
    that happens to be six digits long.
    """
    parts = [p.strip() for p in address.split(",") if p.strip()]
    said = ", ".join(parts) if len(parts) <= 2 else parts[-1]
    return collapse_whitespace(_TRAILING_PIN.sub("", said)) or said


def normalise_name(raw: str | None) -> str | None:
    if not raw:
        return None
    return collapse_whitespace(str(raw)) or None


# ------------------------------------------------------- speech expansion ----

#: Hindi numerals in Latin script, for digit-by-digit readings only — a short
#: number is handed to the voice as Latin digits instead, for it to read as a
#: quantity. This table is now reached only by runs of 4+ digits.
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


#: Short runs are QUANTITIES and long runs are SEQUENCES. Four is the boundary
#: because Indian PIN codes are six digits and mobile numbers ten, while house,
#: shop and sector numbers are almost never more than three.
MAX_QUANTITY_DIGITS = 3

_DIGIT_RUN = re.compile(r"\d+")


def expand_for_speech(text: str) -> str:
    """Expand abbreviations, and decide how each run of digits is read.

    A run of 1-3 digits is LEFT ALONE for the TTS to read as a quantity. `K 68`
    is a house number and is said "K aṭṭhaasaṭh", not "K chhah aath" - which is
    what this function used to produce, and what a mechanic heard in his own
    address. The digits are handed to Cartesia as Latin numerals rather than
    spelled into Hindi words here, because Hindi numerals are irregular (68 is
    `aṭṭhaasaṭh`, not a compound of 6 and 8) and a table of 99 of them typed out
    by hand is 99 chances to put a wrong word in a client's video.

    This assumes the voice reads Latin digits as Hindi quantities. If a batch
    ever comes back saying "sixty-eight" in English, or spelling them out, the
    fix is a numeral table for 1-99 here - not a change to the threshold.

    A run of 4 or more is still spelled out digit by digit, because it is an
    identifier and not an amount: a PIN code read as a quantity becomes "one
    lakh ten thousand seventeen".

    This reverses the original rule, which read every digit singly on the
    grounds that '24' in a workshop name is more often 'do chaar' than
    'twenty-four'. That holds for a name and not for an address, and addresses
    are where the digits in this script actually come from - the workshop name
    is spoken verbatim and rarely carries a number at all.
    """
    out = text
    for abbr, full in ABBREVIATIONS.items():
        out = out.replace(abbr, full)

    def read(match: re.Match[str]) -> str:
        run = match.group()
        if len(run) <= MAX_QUANTITY_DIGITS:
            return run
        return " " + " ".join(NUMERAL_WORDS[ch] for ch in run) + " "

    return collapse_whitespace(_DIGIT_RUN.sub(read, out))
