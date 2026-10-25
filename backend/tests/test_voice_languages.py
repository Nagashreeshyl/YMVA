import pytest
from pipecat.frames.frames import (
    LLMMessagesAppendFrame,
    TranscriptionFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.tests.utils import run_test
from pipecat.transcriptions.language import Language
from pydantic import ValidationError

from ymva.voice.config import AgentConfig, render
from ymva.voice.guard import LanguageGuard
from ymva.voice.languages import code_from_pipecat, detect_language, language_for_state
from ymva.voice.prompts import opening_language, opening_lines, system_instruction
from ymva.voice.templates import interest_reminder


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("I will pay on the 26th", "en"),
        ("ನಾನು ನಾಳೆ ಪಾವತಿಸುತ್ತೇನೆ", "kn"),
        ("నేను రేపు చెల్లిస్తాను", "te"),
        ("நான் நாளை செலுத்துவேன்", "ta"),
        ("मैं कल भुगतान करूंगा", "hi"),
        ("ನಾನು 26th ಗೆ pay ಮಾಡುತ್ತೇನೆ", "kn"),  # code-mixed: the Indic script wins
        ("ok", None),
        ("", None),
    ],
)
def test_detect_language(text: str, expected: str | None) -> None:
    assert detect_language(text) == expected


def test_devanagari_is_marathi_when_only_marathi_allowed() -> None:
    assert detect_language("मी उद्या पैसे भरेन", ["en", "mr"]) == "mr"


def test_pipecat_language_codes() -> None:
    assert code_from_pipecat(Language.KN_IN) == "kn"
    assert code_from_pipecat("te-IN") == "te"
    assert code_from_pipecat(Language.OR_IN) == "od"
    assert code_from_pipecat(None) is None


def test_state_language_with_override() -> None:
    assert language_for_state("Karnataka") == "kn"
    assert language_for_state(" telangana ") == "te"
    assert language_for_state("Karnataka", {"karnataka": "en"}) == "en"
    assert language_for_state("Goa") is None


def test_opening_language_priority() -> None:
    config = interest_reminder()
    assert opening_language(config, {"preferred_language": "Telugu", "state": "Karnataka"}) == "te"
    assert opening_language(config, {"state": "Tamil Nadu"}) == "ta"
    assert opening_language(config, {"state": "Kerala"}) == "en"  # Malayalam not enabled
    assert opening_language(config, {}) == "en"


def test_opening_lines_and_announcement() -> None:
    config = interest_reminder()
    config.announcement.enabled = True
    config.announcement.translations = {"kn": "ಈ ಕರೆಯನ್ನು ರೆಕಾರ್ಡ್ ಮಾಡಬಹುದು."}
    announcement, greeting = opening_lines(config, {"customer_name": "Ramesh"}, "kn")
    assert announcement == "ಈ ಕರೆಯನ್ನು ರೆಕಾರ್ಡ್ ಮಾಡಬಹುದು."
    assert greeting is not None and "Ramesh" in greeting
    config.greetings.pop("te")
    announcement, greeting = opening_lines(config, {}, "te")
    assert announcement == config.announcement.text  # no Telugu version: English text
    assert greeting is None  # the model greets in Telugu itself


def test_system_instruction_has_date_and_language_rules() -> None:
    text = system_instruction(
        interest_reminder(), {"customer_name": "Ramesh"}, "kn", "Asia/Kolkata"
    )
    assert "Ramesh" in text and "{{customer_name}}" not in text
    assert "Start in Kannada" in text
    assert "Today is" in text
    assert "end_call" in text


def test_render_keeps_unknown_placeholders_visible() -> None:
    assert render("Hi {{ name }}, {{missing}}", {"name": "Asha"}) == "Hi Asha, {{missing}}"


def test_config_validation() -> None:
    with pytest.raises(ValidationError):
        AgentConfig(system_prompt="x", languages=["en"], default_language="kn")
    with pytest.raises(ValidationError):
        AgentConfig(system_prompt="x", languages=["en", "xx"])
    with pytest.raises(ValidationError):
        AgentConfig(system_prompt="x", languages=["en"], greetings={"te": "..."})


async def test_guard_switches_language_before_the_turn_reaches_the_llm() -> None:
    switches: list[tuple[str | None, str]] = []

    async def on_switch(previous: str | None, current: str) -> None:
        switches.append((previous, current))

    tts = SarvamTTSService(api_key="test")
    guard = LanguageGuard(allowed=["en", "kn", "te"], initial="en", on_switch=on_switch, tts=tts)
    down, _ = await run_test(
        guard,
        frames_to_send=[
            TranscriptionFrame("Hello, yes speaking", "user", "t1", language=Language.EN_IN),
            TranscriptionFrame("ನಾನು ನಾಳೆ ಪಾವತಿಸುತ್ತೇನೆ", "user", "t2"),
            TranscriptionFrame("ಸರಿ", "user", "t3", language=Language.KN_IN),
            TranscriptionFrame("నేను రేపు చెల్లిస్తాను", "user", "t4", language=Language.TE_IN),
        ],
        expected_down_frames=[
            TranscriptionFrame,
            LLMMessagesAppendFrame,
            TTSUpdateSettingsFrame,
            TranscriptionFrame,
            TranscriptionFrame,
            LLMMessagesAppendFrame,
            TTSUpdateSettingsFrame,
            TranscriptionFrame,
        ],
    )
    assert switches == [("en", "kn"), ("kn", "te")]
    note = next(f for f in down if isinstance(f, LLMMessagesAppendFrame))
    assert "Kannada" in note.messages[0]["content"] and note.run_llm is False
    update = next(f for f in down if isinstance(f, TTSUpdateSettingsFrame))
    assert update.service is tts and update.delta is not None
    assert update.delta.language == Language.KN_IN
    assert guard.current == "te"


async def test_guard_ignores_languages_the_agent_does_not_speak() -> None:
    async def on_switch(previous: str | None, current: str) -> None:
        raise AssertionError("should not switch")

    guard = LanguageGuard(allowed=["en", "kn"], initial="en", on_switch=on_switch)
    await run_test(
        guard,
        frames_to_send=[TranscriptionFrame("నేను రేపు చెల్లిస్తాను", "user", "t1")],
        expected_down_frames=[TranscriptionFrame],
    )
    assert guard.current == "en"
