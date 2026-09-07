"""Script template fill. Pure functions, zero I/O.

The script is fixed, one version, Hindi/Hinglish, with three inserted
variables. Only the TTS text gets speech expansion; the card is rendered from
the unexpanded values.
"""

from __future__ import annotations

from dataclasses import dataclass

from .normalise import expand_for_speech

#: Placeholder. The finalised ~80-word Hindi/Hinglish copy is not in the repo
#: yet — paste it here and keep the three {placeholders}. Changing this text
#: MUST come with a SCRIPT_VERSION bump, because SCRIPT_VERSION is in the prep
#: input hash and is the only thing that forces existing jobs to regenerate.
SCRIPT_TEMPLATES: dict[str, str] = {
    "v1": (
        "PLACEHOLDER SCRIPT — replace with the finalised Hindi/Hinglish copy. "
        "Namaste, main {name} hoon, {workshop} se. "
        "Hum {locality} mein Castrol MAGNATEC use karte hain."
    ),
}

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
    spoken = template.format(**{k: expand_for_speech(v) for k, v in values.items()})
    return FilledScript(version=version, spoken_text=spoken, display_text=display)


def word_count(text: str) -> int:
    """Used by spike 0.1 to check the script against the model's max duration."""
    return len(text.split())
