"""Script template fill. Pure functions, zero I/O.

The script is fixed per language, with three inserted variables. Hindi/Hinglish
is the default and the only language in production; the vernaculars are drafts
for the audio test. Only the TTS text gets speech expansion; the card is
rendered from the unexpanded values, and it is English in every language.
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
    #: v2 (2026-09-16). SAME WORDS AS v1 — only punctuation differs.
    #:
    #: The middle block was two sentences, the second of them ~130 characters
    #: with no internal break, and the voice ran it as one continuous push. A
    #: long unbroken clause is where the lipsync visibly degrades: the avatar
    #: model has no rest to land on, so the mouth never closes and the take
    #: reads as gabbling.
    #:
    #: Sonic 3.6 adapts pause length to context rather than to SSML — there is
    #: no markup on this endpoint — so punctuation IS the pause control. Commas
    #: at the clause boundaries, and em-dashes around the product name, which
    #: give a longer rest than a comma and set the brand off on both sides.
    #: The v1 hyphen in "Oil- jo" is gone; it was tight against the word and
    #: bought no rest at all.
    #:
    #: Not a speed change. `generation_config.speed` slows every word evenly,
    #: including the ones that were already clear, and measured on this script
    #: it buys 6% more duration for a 20% speed cut — the model re-paces rather
    #: than stretches, trimming pauses as it slows. Punctuation puts the time
    #: where the take actually needs it.
    #:
    #: Approved 2026-09-22 off an A/B on one image: v1 and v2 audio rendered
    #: against the same face and prompt, the only variable. v2 runs 28.7s
    #: against v1's 25.9s, so it costs ~+3 billed seconds (~$0.10) a video.
    #: A heavier version (ellipses, the brand line split into its own sentence)
    #: was tried and declined as over-paused.
    "v2": (
        "Mai hoon {name}, {workshop} se. Service ho, repair ho ya engine ki "
        "dikkat, mai har gaadi ka khayal rakhta hoon.\n"
        "Gaadi start hote hi, pehle 8 seconds mein, engine parts ka sabse "
        "zyada wear hota hai. Isi wear se bachane ke liye, mai recommend "
        "karta hoon — Castrol Magnatec Full Synthetic Premium Oil — "
        "jo engine start hone se pehle hi, protection dena shuru karta hai.\n"
        "Aaiye {locality} par, ya screen par diye number par call kare."
    ),
}

#: The language a job speaks when nothing says otherwise - including every row
#: from before the export carried a language at all. Hindi keeps its templates
#: in SCRIPT_TEMPLATES, untouched, so nothing about a Hindi job's text or hash
#: moves now that the others exist.
DEFAULT_LANGUAGE = "hi"


@dataclass(frozen=True)
class Language:
    """One spoken language, and everything the pipeline needs to speak it.

    ADDING A LANGUAGE IS ONE `Language(...)` ENTRY IN `LANGUAGES` BELOW, and
    nothing else: intake resolves it from the export, the job stores its code,
    prep fills its template and the voice is told its code. No migration - the
    database checks the SHAPE of a code, never a list of them.
    """

    #: Stored on `jobs.language` and `submissions.language`. ISO 639-1, which
    #: is also what the voice provider calls it.
    code: str
    name: str
    #: What the export may send, matched case-insensitively alongside `code`
    #: and `name`. The client's own words and the language's own name for
    #: itself, so neither a "Tamil" nor a "தமிழ்" is rejected.
    aliases: tuple[str, ...]
    #: Every version of this language's script, by version id.
    templates: dict[str, str]
    #: The version a new job gets. None means SCRIPT_VERSION from settings,
    #: which is Hindi's, and stays the env override it has always been.
    current: str | None
    #: False while the copy is ours, not the client's. A job in an unapproved
    #: language fails at prep (`SCRIPT_NOT_APPROVED`) instead of rendering a
    #: draft to a real mechanic - see PrepStage. Approval is in the prep hash,
    #: so flipping this to True reopens exactly those jobs, with nothing to do
    #: by hand.
    approved: bool
    #: Sent as the voice provider's `language` field. None sends no field,
    #: which is what Hindi has always done (TECH_DESIGN: test before adding it).
    tts_language: str | None


#: Every language except Hindi has TWO scripts. "v1" is our draft of
#: 2026-10-28, kept on record. "v2" is the CLIENT's copy of 2026-10-30,
#: verbatim but for the slot markers, and is what a new job gets. All five
#: were APPROVED on 2026-10-30, after one audio take per language was heard
#: and accepted. That makes them live: the export column defaults to
#: `language`, so the first row the client sends in one renders for a real
#: mechanic.
#:
#: The client's copy glues case endings onto the slots ("{workshop}-லிருந்து",
#: "{locality}ನಲ್ಲಿ"), which the drafts below were written to avoid. That is
#: their call and is kept as sent, and the voice read those endings acceptably
#: in the audio test.
#:
#: What follows describes the v1 DRAFTS:
#:
#: Each is v2's meaning, not a word-for-word copy, and written the way v2 was:
#:
#:   * Native script, so the voice reads each language as itself rather than
#:     guessing at a romanisation. The three slots stay ENGLISH, because the
#:     export is English and the card prints it - so every name, workshop and
#:     place is Latin inside native text, and whether the voice reads that
#:     cleanly is the thing the audio test exists to answer. The brand is
#:     Latin for the same reason, and as "Magnatec", never all caps.
#:   * Every slot stands alone as a word. Tamil, Telugu, Kannada and Malayalam
#:     fuse their case endings onto the noun, and no template can glue an
#:     ending onto an arbitrary English place name correctly - so where Hindi
#:     says "come to {locality}", these say "where we are is {locality}". The
#:     workshop gets its own sentence ("my garage is {workshop}") for the same
#:     reason. Bengali's "theke" is a separate word like Hindi's "se", so its
#:     workshop stays inline.
#:   * "8" stays a digit, as in v2. Whether each language reads it correctly
#:     is a test item, not an assumption.
#:   * Punctuation is the pause control, carried from v2: commas at the same
#:     clause boundaries, em-dashes around the brand. Bengali ends sentences
#:     with the danda (।), its own full stop.
LANGUAGES: tuple[Language, ...] = (
    Language(
        code="hi", name="Hindi", aliases=("hinglish", "हिंदी", "हिन्दी"),
        current=None, approved=True, tts_language=None,
        templates=SCRIPT_TEMPLATES,
    ),
    Language(
        code="ta", name="Tamil", aliases=("tamil", "தமிழ்"),
        current="v2", approved=True, tts_language="ta",
        templates={
            "v1": (
                "நான் {name}. என் கேரேஜ், {workshop}. சர்வீஸ் ஆகட்டும், ரிப்பேர் "
                "ஆகட்டும், இல்ல எஞ்சின் பிரச்சனை ஆகட்டும், ஒவ்வொரு காரையும் நான் "
                "நல்லா கவனிச்சுக்குவேன்.\n"
                "கார் ஸ்டார்ட் ஆனதும், முதல் 8 செகண்ட்ஸ்ல, எஞ்சின் பார்ட்ஸ்க்கு "
                "தான் அதிக தேய்மானம் ஏற்படுது. இந்த தேய்மானத்துல இருந்து "
                "பாதுகாக்க, நான் ரெகமெண்ட் பண்றது — Castrol Magnatec Full "
                "Synthetic Premium Oil — இது எஞ்சின் ஸ்டார்ட் ஆகறதுக்கு "
                "முன்னாடியே, பாதுகாப்பு கொடுக்க ஆரம்பிச்சுடும்.\n"
                "நாங்க இருக்கறது {locality}. நேர்ல வாங்க, இல்லன்னா ஸ்க்ரீன்ல "
                "இருக்கற நம்பருக்கு கால் பண்ணுங்க."
            ),
            # The client's copy, 2026-10-30, verbatim but for the slot
            # markers.
            "v2": (
                "நான் {name}, {workshop}-லிருந்து. சர்வீஸ் ஆனாலும் ரிப்பேர் "
                "ஆனாலும், ஒவ்வொரு வண்டியையும் கவனமா பார்த்துப்பேன். வண்டி "
                "ஸ்டார்ட் ஆன முதல் 8 விநாடிகளில்தான் எஞ்சின் தேய்மானம் அதிகம். "
                "இதிலிருந்து பாதுகாக்க, நான் பரிந்துரைப்பது Castrol MAGNATEC "
                "Full Synthetic Premium Oil.\n"
                "சர்வீஸுக்கு {locality}-ல வாங்க, இல்லனா ஸ்கிரீன்ல இருக்கிற "
                "நம்பருக்கு கால் பண்ணுங்க."
            ),
        },
    ),
    Language(
        code="te", name="Telugu", aliases=("telugu", "తెలుగు"),
        current="v2", approved=True, tts_language="te",
        templates={
            "v1": (
                "నేను {name}. నా గ్యారేజ్, {workshop}. సర్వీస్ అయినా, రిపేర్ "
                "అయినా, లేదా ఇంజిన్ ప్రాబ్లమ్ అయినా, ప్రతి కారునీ నేను "
                "జాగ్రత్తగా చూసుకుంటాను.\n"
                "కార్ స్టార్ట్ అవ్వగానే, మొదటి 8 సెకండ్స్‌లో, ఇంజిన్ పార్ట్స్‌కి "
                "ఎక్కువగా అరుగుదల జరుగుతుంది. ఈ అరుగుదల నుంచి కాపాడటానికి, నేను "
                "రికమెండ్ చేసేది — Castrol Magnatec Full Synthetic Premium Oil — "
                "ఇది ఇంజిన్ స్టార్ట్ అవ్వకముందే, ప్రొటెక్షన్ ఇవ్వడం "
                "మొదలుపెడుతుంది.\n"
                "మేము ఉండేది {locality}. నేరుగా రండి, లేదా స్క్రీన్ మీద ఉన్న "
                "నంబర్‌కి కాల్ చేయండి."
            ),
            # The client's copy, 2026-10-30, verbatim but for the slot
            # markers.
            "v2": (
                "నేను {name}, {workshop} నుంచి. సర్వీస్ అయినా, రిపేర్ అయినా "
                "ప్రతి వాహనాన్ని జాగ్రత్తగా చూసుకుంటాను. వాహనం స్టార్ట్ అయిన "
                "మొదటి 8 సెకన్లలోనే ఇంజిన్ వేర్ ఎక్కువగా ఉంటుంది. దీని నుంచి "
                "రక్షణ కోసం నేను సిఫార్సు చేసేది Castrol MAGNATEC Full "
                "Synthetic Premium Oil.\n"
                "సర్వీస్ కోసం {locality}లో సంప్రదించండి లేదా స్క్రీన్‌పై ఉన్న "
                "నంబర్‌కు కాల్ చేయండి."
            ),
        },
    ),
    Language(
        code="kn", name="Kannada", aliases=("kannada", "ಕನ್ನಡ"),
        current="v2", approved=True, tts_language="kn",
        templates={
            "v1": (
                "ನಾನು {name}. ನನ್ನ ಗ್ಯಾರೇಜ್, {workshop}. ಸರ್ವೀಸ್ ಆಗಲಿ, ರಿಪೇರಿ "
                "ಆಗಲಿ, ಅಥವಾ ಇಂಜಿನ್ ಸಮಸ್ಯೆ ಆಗಲಿ, ಪ್ರತಿಯೊಂದು ಕಾರನ್ನೂ ನಾನು "
                "ಜೋಪಾನವಾಗಿ ನೋಡಿಕೊಳ್ಳುತ್ತೇನೆ.\n"
                "ಕಾರ್ ಸ್ಟಾರ್ಟ್ ಆದ ತಕ್ಷಣ, ಮೊದಲ 8 ಸೆಕೆಂಡ್‌ಗಳಲ್ಲಿ, ಇಂಜಿನ್ "
                "ಪಾರ್ಟ್ಸ್‌ಗೆ ಅತಿ ಹೆಚ್ಚು ಸವೆತ ಆಗುತ್ತದೆ. ಈ ಸವೆತದಿಂದ ರಕ್ಷಿಸಲು, "
                "ನಾನು ರೆಕಮೆಂಡ್ ಮಾಡುವುದು — Castrol Magnatec Full Synthetic "
                "Premium Oil — ಇದು ಇಂಜಿನ್ ಸ್ಟಾರ್ಟ್ ಆಗುವ ಮುನ್ನವೇ, ರಕ್ಷಣೆ ನೀಡಲು "
                "ಶುರು ಮಾಡುತ್ತದೆ.\n"
                "ನಾವು ಇರುವುದು {locality}. ನೇರವಾಗಿ ಬನ್ನಿ, ಅಥವಾ ಸ್ಕ್ರೀನ್ ಮೇಲೆ "
                "ಇರುವ ನಂಬರ್‌ಗೆ ಕಾಲ್ ಮಾಡಿ."
            ),
            # The client's copy, 2026-10-30, verbatim but for the slot
            # markers.
            "v2": (
                "ನಾನು {name}, {workshop}ನಿಂದ. ಸರ್ವಿಸ್ ಆಗಲಿ, ರಿಪೇರಿ ಆಗಲಿ, ಪ್ರತಿ "
                "ವಾಹನದ ಕಾಳಜಿ ವಹಿಸುತ್ತೇನೆ. ವಾಹನ ಸ್ಟಾರ್ಟ್ ಆದ ಮೊದಲ 8 ಸೆಕೆಂಡುಗಳಲ್ಲೇ "
                "ಎಂಜಿನ್ ವೇರ್ ಹೆಚ್ಚಾಗುತ್ತದೆ. ಇದರಿಂದ ರಕ್ಷಣೆಗಾಗಿ ನಾನು ಶಿಫಾರಸು "
                "ಮಾಡುವುದು Castrol MAGNATEC Full Synthetic Premium Oil.\n"
                "ಸರ್ವಿಸ್‌ಗಾಗಿ {locality}ನಲ್ಲಿ ಸಂಪರ್ಕಿಸಿ ಅಥವಾ ಸ್ಕ್ರೀನ್ ಮೇಲಿರುವ "
                "ನಂಬರ್‌ಗೆ ಕರೆ ಮಾಡಿ."
            ),
        },
    ),
    Language(
        code="bn", name="Bengali", aliases=("bengali", "bangla", "বাংলা"),
        current="v2", approved=True, tts_language="bn",
        templates={
            "v1": (
                "আমি {name}, {workshop} থেকে। সার্ভিস হোক, রিপেয়ার হোক বা "
                "ইঞ্জিনের সমস্যা, আমি প্রতিটা গাড়ির যত্ন নিই।\n"
                "গাড়ি স্টার্ট হওয়ার সঙ্গে সঙ্গেই, প্রথম 8 সেকেন্ডে, ইঞ্জিনের "
                "পার্টসে সবচেয়ে বেশি ক্ষয় হয়। এই ক্ষয় থেকে বাঁচাতে, আমি রেকমেন্ড "
                "করি — Castrol Magnatec Full Synthetic Premium Oil — যা ইঞ্জিন "
                "স্টার্ট হওয়ার আগে থেকেই, প্রোটেকশন দেওয়া শুরু করে।\n"
                "আমাদের ঠিকানা {locality}। চলে আসুন, অথবা স্ক্রিনে দেওয়া নম্বরে "
                "কল করুন।"
            ),
            # The client's copy, 2026-10-30, verbatim but for the slot
            # markers.
            "v2": (
                "আমি {name},{workshop} থেকে। সার্ভিস হোক বা রিপেয়ার, প্রতিটি "
                "গাড়ির যত্ন নিই। গাড়ি স্টার্ট হওয়ার প্রথম ৮ সেকেন্ডেই "
                "ইঞ্জিনের ক্ষয় সবচেয়ে বেশি হয়। এই ক্ষয় থেকে সুরক্ষার জন্য "
                "আমি পরামর্শ দিই Castrol MAGNATEC Full Synthetic Premium Oil।\n"
                "সার্ভিসের জন্য {locality}-এ যোগাযোগ করুন অথবা স্ক্রিনে দেওয়া "
                "নম্বরে ফোন করুন।"
            ),
        },
    ),
    Language(
        code="ml", name="Malayalam", aliases=("malayalam", "മലയാളം"),
        current="v2", approved=True, tts_language="ml",
        templates={
            "v1": (
                "ഞാൻ {name}. എന്റെ ഗാരേജ്, {workshop}. സർവീസ് ആകട്ടെ, റിപ്പയർ "
                "ആകട്ടെ, അല്ലെങ്കിൽ എഞ്ചിൻ പ്രശ്നം ആകട്ടെ, ഓരോ വണ്ടിയും ഞാൻ "
                "ശ്രദ്ധയോടെ നോക്കും.\n"
                "കാർ സ്റ്റാർട്ട് ആകുമ്പോൾ തന്നെ, ആദ്യത്തെ 8 സെക്കൻഡിൽ, എഞ്ചിൻ "
                "പാർട്സിനാണ് ഏറ്റവും കൂടുതൽ തേയ്മാനം ഉണ്ടാകുന്നത്. ഈ "
                "തേയ്മാനത്തിൽ നിന്ന് സംരക്ഷിക്കാൻ, ഞാൻ റെക്കമെൻഡ് ചെയ്യുന്നത് — "
                "Castrol Magnatec Full Synthetic Premium Oil — ഇത് എഞ്ചിൻ "
                "സ്റ്റാർട്ട് ആകുന്നതിന് മുമ്പേ, സംരക്ഷണം നൽകി തുടങ്ങുന്നു.\n"
                "ഞങ്ങൾ ഉള്ളത് {locality}. നേരിട്ട് വരൂ, അല്ലെങ്കിൽ സ്ക്രീനിൽ "
                "കാണുന്ന നമ്പറിൽ വിളിക്കൂ."
            ),
            # The client's copy, 2026-10-30, verbatim but for the slot
            # markers.
            "v2": (
                "ഞാൻ {name}, {workshop}-ൽ നിന്നാണ്. സർവീസായാലും റിപ്പയറായാലും, "
                "ഓരോ വണ്ടിയും ഞാൻ ശ്രദ്ധയോടെ നോക്കാറുണ്ട്. വണ്ടി സ്റ്റാർട്ട് "
                "ചെയ്യുന്ന ആദ്യത്തെ 8 സെക്കൻഡിലാണ് എൻജിനിൽ ഏറ്റവും കൂടുതൽ "
                "തേയ്മാനം സംഭവിക്കുന്നത്. ഇതിൽ നിന്ന് എൻജിനെ സംരക്ഷിക്കാൻ ഞാൻ "
                "ശുപാർശ ചെയ്യുന്നത് Castrol MAGNATEC Full Synthetic Premium Oil "
                "ആണ്.\n"
                "സർവീസിനായി {locality}-ൽ ബന്ധപ്പെടുക. അല്ലെങ്കിൽ സ്ക്രീനിൽ "
                "കാണുന്ന നമ്പറിൽ വിളിക്കൂ."
            ),
        },
    ),
)

_BY_CODE: dict[str, Language] = {lang.code: lang for lang in LANGUAGES}

SUPPORTED_LANGUAGES: tuple[str, ...] = tuple(_BY_CODE)

#: Every spelling the export may send -> code. Built, not written, so a new
#: entry above is resolvable the moment it exists.
_ALIASES: dict[str, str] = {
    spelling.casefold(): lang.code
    for lang in LANGUAGES
    for spelling in (lang.code, lang.name, *lang.aliases)
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
    language: str = DEFAULT_LANGUAGE


class UnknownLanguage(ScriptError):
    pass


def get_language(code: str) -> Language:
    """The registry entry for a stored code. Unknown is an error, not Hindi."""
    try:
        return _BY_CODE[code]
    except KeyError:
        raise UnknownLanguage(
            f"Unknown script language {code!r}. Known: {list(SUPPORTED_LANGUAGES)}"
        ) from None


def resolve_language(raw: str | None) -> str:
    """What the export sent -> a language code.

    Blank means Hindi, because every row from before the export carried a
    language is Hindi and a blank on a new row is the same fact. A value that
    is present but unknown RAISES rather than defaulting, for the same reason
    an unknown outfit does: a silent default would put a Tamil mechanic's video
    in Hindi, and nothing downstream would notice.
    """
    text = " ".join(str(raw or "").split())
    if not text:
        return DEFAULT_LANGUAGE
    try:
        return _ALIASES[text.casefold()]
    except KeyError:
        raise UnknownLanguage(
            f"language={raw!r} is not one of {list(SUPPORTED_LANGUAGES)} "
            "(or their names)"
        ) from None


def current_version(code: str) -> str:
    """The script version a new job in this language gets."""
    lang = get_language(code)
    if lang.current is not None:
        return lang.current
    from ..config import get_settings

    return get_settings().script_version


def templates_for(language: str) -> dict[str, str]:
    """Every version of one language's script."""
    return get_language(language).templates


def get_template(version: str, language: str = DEFAULT_LANGUAGE) -> str:
    templates = templates_for(language)
    try:
        return templates[version]
    except KeyError:
        raise ScriptError(
            f"Unknown SCRIPT_VERSION {version!r} for {language!r}. "
            f"Known: {sorted(templates)}"
        ) from None


def fill_script(
    *,
    version: str,
    name: str,
    workshop: str,
    locality: str,
    language: str = DEFAULT_LANGUAGE,
) -> FilledScript:
    template = get_template(version, language)
    values = {"name": name, "workshop": workshop, "locality": locality}

    missing = [k for k, v in values.items() if not v or not v.strip()]
    if missing:
        raise ScriptError(f"Cannot fill script, empty values: {sorted(missing)}")

    display = template.format(**values)
    spoken = apply_spoken_overrides(
        template.format(
            **{k: expand_for_speech(v, language=language) for k, v in values.items()}
        )
    )
    return FilledScript(
        version=version, spoken_text=spoken, display_text=display, language=language
    )


def word_count(text: str) -> int:
    """Used by spike 0.1 to check the script against the model's max duration."""
    return len(text.split())
