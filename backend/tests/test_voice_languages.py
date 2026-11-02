import asyncio
from typing import Any

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
    # Short codes and loose spellings from other apps.
    assert language_for_state("KA") == "kn" and language_for_state("TN") == "ta"
    assert language_for_state("Tamil-Nadu") == "ta" and language_for_state("tamilnadu") == "ta"
    assert language_for_state("Uttar Pradesh") == "hi" and language_for_state("Delhi") == "hi"
    assert language_for_state("Orissa") == "od"


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
            TranscriptionFrame("ನಾನು ನಾಳೆ ಪಾವತಿ ಮಾಡ್ತೀನಿ", "user", "t2"),
            TranscriptionFrame("ಸರಿ ಸರಿ ಆಯ್ತು", "user", "t3", language=Language.KN_IN),
            TranscriptionFrame("Hello sir.", "user", "t3a", language=Language.EN_IN),  # code-mixing
            TranscriptionFrame("I.", "user", "t3b", language=Language.EN_IN),  # too short to switch
            TranscriptionFrame("నేను రేపు డబ్బు కడతాను", "user", "t4", language=Language.TE_IN),
        ],
        expected_down_frames=[
            TranscriptionFrame,
            LLMMessagesAppendFrame,
            TTSUpdateSettingsFrame,
            TranscriptionFrame,
            TranscriptionFrame,
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
        frames_to_send=[TranscriptionFrame("నేను రేపు డబ్బు కడతాను", "user", "t1")],
        expected_down_frames=[TranscriptionFrame],
    )
    assert guard.current == "en"


def test_missing_details_are_never_left_for_the_model_to_guess() -> None:
    config = interest_reminder()
    text = system_instruction(config, {"customer_name": "Ramesh"}, "en", "Asia/Kolkata")
    assert "{{due_amount}}" not in text and "[not provided] rupees" in text
    assert "Never invent amounts" in text
    # Greeting needs customer_name: without it, the model greets instead of reading a gap.
    _, greeting = opening_lines(config, {}, "en")
    assert greeting is None
    _, greeting = opening_lines(config, {"customer_name": "Asha"}, "en")
    assert greeting is not None and "Asha" in greeting


async def test_live_mode_does_not_read_romanised_indic_as_english() -> None:
    async def on_switch(previous: str | None, current: str) -> None:
        raise AssertionError("should not switch")

    guard = LanguageGuard(
        allowed=["en", "kn"], initial="kn", on_switch=on_switch, gemini_live=object()
    )
    await run_test(
        guard,
        frames_to_send=[TranscriptionFrame("Naanu Ramesh, naanu hana kattuttene", "user", "t1")],
        expected_down_frames=[TranscriptionFrame],
    )
    assert guard.current == "kn"


async def test_leading_silence_is_trimmed() -> None:
    import numpy as np
    from pipecat.frames.frames import TTSAudioRawFrame, TTSStartedFrame

    from ymva.voice.trim import LeadingSilenceTrimmer

    rate = 16000
    silence = np.zeros(rate // 50, dtype=np.int16).tobytes()  # 20 ms
    speech = (np.ones(rate // 50, dtype=np.int16) * 4000).tobytes()
    down, _ = await run_test(
        LeadingSilenceTrimmer(),
        frames_to_send=[
            TTSStartedFrame(),
            *[TTSAudioRawFrame(audio=silence, sample_rate=rate, num_channels=1) for _ in range(15)],
            TTSAudioRawFrame(
                audio=silence[: len(silence) // 2] + speech[: len(speech) // 2],
                sample_rate=rate,
                num_channels=1,
            ),
            TTSAudioRawFrame(
                audio=silence, sample_rate=rate, num_channels=1
            ),  # pause inside speech is kept
        ],
    )
    audio = [f for f in down if isinstance(f, TTSAudioRawFrame)]
    assert len(audio) == 2  # 300 ms of leading silence dropped
    assert audio[1].audio == silence


def test_everyday_mix_covers_every_language() -> None:
    from ymva.voice.languages import LANGUAGES
    from ymva.voice.prompts import EVERYDAY_MIX
    from ymva.voice.templates import VOICE_PREVIEW_LINES

    for info in LANGUAGES.values():
        assert info.code in VOICE_PREVIEW_LINES, info.name
        if info.code != "en":
            assert f"{info.name}:" in EVERYDAY_MIX, info.name
    config = interest_reminder()
    assert "Kanglish" in system_instruction(config, {}, "kn", "Asia/Kolkata")
    config.speaking_style = "pure"
    assert "Kanglish" not in system_instruction(config, {}, "kn", "Asia/Kolkata")


def _session(config: AgentConfig, variables: dict[str, str] | None = None) -> object:
    import uuid

    from pipecat.processors.frame_processor import FrameProcessor

    from ymva.voice.session import CallSession, CallSetup

    class Transport:
        def __init__(self) -> None:
            self._in, self._out = FrameProcessor(), FrameProcessor()

        def input(self) -> FrameProcessor:
            return self._in

        def output(self) -> FrameProcessor:
            return self._out

    setup = CallSetup(
        call_id=uuid.uuid4(),
        config=config,
        variables=variables or {},
        keys={"google": "x", "sarvam": "x"},
    )
    return CallSession(setup, Transport())  # type: ignore[arg-type]


def test_live_mode_guard_hears_the_caller() -> None:
    from ymva.voice.gemini_live import ResilientGeminiLive

    config = interest_reminder().model_copy(update={"mode": "gemini_live"})
    session = _session(config)
    pipeline, _, _, guard, _ = session._build()  # type: ignore[attr-defined]
    order = [type(p) for p in pipeline.processors]
    # Gemini sends the caller's words upstream: the guard must sit before the model.
    assert order.index(LanguageGuard) < order.index(ResilientGeminiLive)
    assert guard._allowed == config.languages


def test_agent_can_keep_to_one_language() -> None:
    config = interest_reminder().model_copy(update={"follow_caller_language": False})
    session = _session(config)
    _, _, _, guard, _ = session._build()  # type: ignore[attr-defined]
    assert guard._allowed == [session.opening]  # type: ignore[attr-defined]
    text = system_instruction(config, {}, "kn", "Asia/Kolkata")
    assert "Speak only Kannada for the whole call" in text
    follow = system_instruction(interest_reminder(), {}, "kn", "Asia/Kolkata")
    assert "explicitly asks you to speak a particular language" in follow


async def test_preferred_language_opens_then_the_caller_is_followed() -> None:
    from pipecat.frames.frames import TranscriptionFrame

    config = interest_reminder()
    session = _session(config, {"preferred_language": "kn"})
    _, _, _, guard, _ = session._build()  # type: ignore[attr-defined]
    assert session.opening == "kn"  # type: ignore[attr-defined]
    await run_test(guard, frames_to_send=[TranscriptionFrame("हाँ जी बोलिए अभी", "", "")])
    assert guard.current == "hi"  # the caller spoke Hindi: the call follows them
    text = system_instruction(config, {"preferred_language": "kn"}, "kn", "Asia/Kolkata")
    assert "Start in Kannada" in text and "call-screening assistant" in text
    assert "answer it" in text  # a caller's question is answered first


async def test_slow_gemini_connect_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A handshake that hangs (once took 13.5 s on a real call) is dropped and tried again."""
    from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService

    from ymva.voice import gemini_live

    config = interest_reminder().model_copy(update={"mode": "gemini_live"})
    pipeline, _, _, _, _ = _session(config)._build()  # type: ignore[attr-defined]
    llm = next(p for p in pipeline.processors if isinstance(p, gemini_live.ResilientGeminiLive))
    attempts: list[int] = []

    async def connect(self: Any, cfg: Any) -> None:
        attempts.append(1)
        if len(attempts) == 1:
            await asyncio.sleep(60)  # stuck handshake
        self._ready.set()  # what _handle_session_ready does once connected

    monkeypatch.setattr(GeminiLiveLLMService, "_connection_task_handler", connect)
    monkeypatch.setattr(gemini_live, "CONNECT_TIMEOUT_SECS", 0.05)
    await asyncio.wait_for(llm._connect_in_time(None), timeout=2)
    assert len(attempts) == 2 and llm._ready.is_set()


async def test_silent_opening_is_asked_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gemini sometimes connects but never greets; the caller must not wait in silence."""
    from ymva.voice import session as session_module

    monkeypatch.setattr(session_module, "OPENING_REPLY_SECS", 0.05)
    config = interest_reminder().model_copy(update={"mode": "gemini_live"})
    call = _session(config)
    call._build()  # type: ignore[attr-defined]
    llm = call._live_llm  # type: ignore[attr-defined]
    asked: list[int] = []

    async def recover_opening() -> None:
        asked.append(1)
        call.conversation.agent_replied = True  # greets this time

    monkeypatch.setattr(llm, "recover_opening", recover_opening)
    llm._ready.set()
    await asyncio.wait_for(call._ensure_opening(), timeout=2)  # type: ignore[attr-defined]
    assert asked == [1]

    asked.clear()
    await asyncio.wait_for(call._ensure_opening(), timeout=2)  # type: ignore[attr-defined]
    assert asked == []  # it greeted: nothing to do
