"""Languages YMVA agents speak, and fast detection of the language of a caller's turn."""

import unicodedata
from dataclasses import dataclass

from pipecat.transcriptions.language import Language


@dataclass(frozen=True)
class LanguageInfo:
    code: str  # Short code used across YMVA ("kn")
    name: str  # English name ("Kannada")
    native: str  # Name in the language itself, for the agent's own phrasing
    pipecat: Language  # Pipecat enum (Sarvam / Gemini mapping)


LANGUAGES: dict[str, LanguageInfo] = {
    info.code: info
    for info in (
        LanguageInfo("en", "English", "English", Language.EN_IN),
        LanguageInfo("hi", "Hindi", "हिन्दी", Language.HI_IN),
        LanguageInfo("kn", "Kannada", "ಕನ್ನಡ", Language.KN_IN),
        LanguageInfo("te", "Telugu", "తెలుగు", Language.TE_IN),
        LanguageInfo("ta", "Tamil", "தமிழ்", Language.TA_IN),
        LanguageInfo("ml", "Malayalam", "മലയാളം", Language.ML_IN),
        LanguageInfo("mr", "Marathi", "मराठी", Language.MR_IN),
        LanguageInfo("bn", "Bengali", "বাংলা", Language.BN_IN),
        LanguageInfo("gu", "Gujarati", "ગુજરાતી", Language.GU_IN),
        LanguageInfo("pa", "Punjabi", "ਪੰਜਾਬੀ", Language.PA_IN),
        LanguageInfo("od", "Odia", "ଓଡ଼ିଆ", Language.OR_IN),
    )
}

# Default greeting language by the customer's state (editable per agent).
_STATES: dict[str, tuple[str, ...]] = {
    # language: state names and the short codes apps often send instead
    "kn": ("karnataka", "ka"),
    "te": ("andhra pradesh", "ap", "telangana", "ts", "tg"),
    "ta": ("tamil nadu", "tamilnadu", "tn", "puducherry", "pondicherry", "py"),
    "ml": ("kerala", "kl", "lakshadweep"),
    "mr": ("maharashtra", "mh"),
    "bn": ("west bengal", "wb", "tripura"),
    "gu": (
        "gujarat", "gj", "dadra and nagar haveli and daman and diu", "dadra and nagar haveli",
        "daman and diu", "dn", "dd",
    ),
    "pa": ("punjab", "pb"),
    "od": ("odisha", "orissa", "od", "or"),
    "hi": (
        "uttar pradesh", "up", "bihar", "br", "madhya pradesh", "mp", "rajasthan", "rj",
        "delhi", "new delhi", "dl", "haryana", "hr", "jharkhand", "jh", "chhattisgarh",
        "cg", "uttarakhand", "uk", "himachal pradesh", "hp", "chandigarh", "ch",
        "jammu and kashmir", "jk", "ladakh", "la", "andaman and nicobar islands", "an",
    ),
}  # fmt: skip


def _key(state: str) -> str:
    """'Tamil-Nadu', 'tamil nadu ' and 'TamilNadu' all match."""
    return "".join(ch for ch in state.lower() if ch.isalpha())


STATE_LANGUAGE: dict[str, str] = {_key(s): code for code, names in _STATES.items() for s in names}

# Unicode blocks of Indic scripts. Hindi and Marathi share Devanagari; we report Hindi
# unless the agent only allows Marathi (see detect_language).
_SCRIPT_RANGES: tuple[tuple[int, int, str], ...] = (
    (0x0900, 0x097F, "hi"),
    (0x0980, 0x09FF, "bn"),
    (0x0A00, 0x0A7F, "pa"),
    (0x0A80, 0x0AFF, "gu"),
    (0x0B00, 0x0B7F, "od"),
    (0x0B80, 0x0BFF, "ta"),
    (0x0C00, 0x0C7F, "te"),
    (0x0C80, 0x0CFF, "kn"),
    (0x0D00, 0x0D7F, "ml"),
)

_PIPECAT_TO_CODE: dict[str, str] = {}
for _info in LANGUAGES.values():
    _PIPECAT_TO_CODE[_info.pipecat.value.lower()] = _info.code
    _PIPECAT_TO_CODE[_info.pipecat.value.split("-")[0].lower()] = _info.code
_PIPECAT_TO_CODE["or"] = "od"


def code_from_pipecat(language: Language | str | None) -> str | None:
    if language is None:
        return None
    value = language.value if isinstance(language, Language) else str(language)
    value = value.lower()
    return _PIPECAT_TO_CODE.get(value) or _PIPECAT_TO_CODE.get(value.split("-")[0])


def has_indic_letters(text: str) -> bool:
    return any(start <= ord(ch) <= end for ch in text for start, end, _ in _SCRIPT_RANGES)


def to_script(text: str, language: str) -> str | None:
    """Rewrite Indic letters in `language`'s script, or None if that isn't cleanly possible.

    Speech recognition sometimes writes a short Tamil reply in Kannada letters ("ಇಲ್ಲ" for
    "இல்ல"). The Indic Unicode blocks share one layout, so the same sound sits at the same
    offset in each; a letter the target script doesn't have makes the whole rewrite fail."""
    target = next((start for start, _, code in _SCRIPT_RANGES if code == language), None)
    if target is None and language == "mr":
        target = 0x0900  # Marathi is written in Devanagari
    if target is None:
        return None
    out = []
    for ch in text:
        cp = ord(ch)
        block = next((start for start, end, _ in _SCRIPT_RANGES if start <= cp <= end), None)
        if block is None or block == target:
            out.append(ch)
            continue
        moved = chr(target + cp - block)
        if unicodedata.name(moved, None) is None:
            return None
        out.append(moved)
    return "".join(out)


def detect_language(text: str, allowed: list[str] | None = None) -> str | None:
    """Detect the language of a transcript from its script.

    Indic languages each have their own script, so counting characters per Unicode block is
    fast and reliable for live calls. Latin text is treated as English. Returns None when the
    text has too few letters to tell.
    """
    counts: dict[str, int] = {}
    latin = 0
    for ch in text:
        cp = ord(ch)
        if ch.isascii():
            if ch.isalpha():
                latin += 1
            continue
        for start, end, code in _SCRIPT_RANGES:
            if start <= cp <= end:
                counts[code] = counts.get(code, 0) + 1
                break
    if counts:
        code = max(counts, key=lambda c: counts[c])
        if code == "hi" and allowed and "hi" not in allowed and "mr" in allowed:
            return "mr"
        return code
    return "en" if latin >= 3 else None


def language_for_state(state: str | None, overrides: dict[str, str] | None = None) -> str | None:
    if not state:
        return None
    key = _key(state)
    if overrides:
        lowered = {_key(k): v for k, v in overrides.items()}
        if key in lowered:
            return lowered[key]
    return STATE_LANGUAGE.get(key)
