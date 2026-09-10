"""Script template fill. Pure functions, zero I/O.

The script is fixed, one version, Hindi/Hinglish, with three inserted
variables. Only the TTS text gets speech expansion; the card is rendered from
the unexpanded values.
"""

from __future__ import annotations

from dataclasses import dataclass

from .normalise import expand_for_speech

#: The finalised Hindi/Hinglish copy, as spoken in the approved videos. Three
#: inserted variables; everything else is identical for every mechanic.
#:
#: Changing this text MUST come with a SCRIPT_VERSION bump — SCRIPT_VERSION is
#: in the prep input hash and is the only thing that forces existing jobs to
#: regenerate rather than skip.
#:
#: Two things here are load-bearing and look like typos:
#:   * "Magnatec" is mixed case, not "MAGNATEC". An all-caps token is read as
#:     an initialism and came back spelled out letter by letter.
#:   * "8 seconds" keeps the Latin digit. The voice reads it correctly in this
#:     sentence, and this is the text that produced the approved audio.
SCRIPT_TEMPLATES: dict[str, str] = {
    "v1": (
        "Mai hoon {name}, {workshop} se. Service ho, repair ho ya engine ki "
        "dikkat, mai har gaadi ka khayal rakhta hoon.\n"
        "Gaadi start hote hi pehle 8 seconds mein engine parts ka sabse zyada "
        "wear hota hai. Isi wear se bachane ke liye mai recommend karta hoon "
        "Castrol Magnatec Full Synthetic Premium Oil- jo engine start hone se "
        "pehle hi protection dena shuru karta hai.\n"
        "Aaiye {locality} par, ya screen par diye number par call kare."
    ),
}

#: Applied to the SPOKEN text only, after the fill. The card still renders the
#: brand exactly as supplied, so nothing the viewer sees changes.
#:
#: All-caps tokens are read as initialisms: "MAGNATEC" came back spelled out
#: letter by letter. An explicit table, not a regex — an earlier
#: `\b[A-Z]{2,}\b` rule was double-escaped, silently matched nothing, and the
#: bug was invisible because the output was still valid speech.
SPOKEN_OVERRIDES: dict[str, str] = {
    "MAGNATEC": "Magnatec",
    "CASTROL": "Castrol",
}


def apply_spoken_overrides(text: str) -> str:
    for src, dst in SPOKEN_OVERRIDES.items():
        text = text.replace(src, dst)
    return text

REQUIRED_PLACEHOLDERS = ("name", "workshop", "locality")


class ScriptError(ValueError):
    pass


@dataclass(frozen=True)
class FilledScript:
    version: str
    #: What the TTS voice is given. Digits and abbreviations expanded.
    spoken_text: str
    #: The same fill without speech expansion, for logging and diffing.
    display_text: str


def get_template(version: str) -> str:
    try:
        return SCRIPT_TEMPLATES[version]
    except KeyError:
        raise ScriptError(
            f"Unknown SCRIPT_VERSION {version!r}. Known: {sorted(SCRIPT_TEMPLATES)}"
        ) from None


def fill_script(
    *, version: str, name: str, workshop: str, locality: str
) -> FilledScript:
    template = get_template(version)
    values = {"name": name, "workshop": workshop, "locality": locality}

    missing = [k for k, v in values.items() if not v or not v.strip()]
    if missing:
        raise ScriptError(f"Cannot fill script, empty values: {sorted(missing)}")

    display = template.format(**values)
    spoken = apply_spoken_overrides(
        template.format(**{k: expand_for_speech(v) for k, v in values.items()})
    )
    return FilledScript(version=version, spoken_text=spoken, display_text=display)


def word_count(text: str) -> int:
    """Used by spike 0.1 to check the script against the model's max duration."""
    return len(text.split())
