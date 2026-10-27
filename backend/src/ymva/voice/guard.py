"""Language guard: keeps the agent replying in the language of the caller's latest turn.

Pipeline mode (deterministic): sits between STT and the user aggregator. When a caller turn
arrives in a new language it (1) adds a note to the LLM context telling it to reply in that
language and (2) switches the TTS voice's language, before the turn reaches the LLM.

Gemini Live mode: Gemini hears the audio and switches language natively; the guard only
records the switch (for the call's transcript and language stats).
"""

from collections.abc import Awaitable, Callable
from typing import Any

from pipecat.frames.frames import (
    Frame,
    LLMMessagesAppendFrame,
    TranscriptionFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.sarvam.tts import SarvamTTSService

from ymva.voice.languages import LANGUAGES, code_from_pipecat, detect_language

SwitchCallback = Callable[[str | None, str], Awaitable[None]]

MIN_LETTERS_TO_SWITCH = 4
# "Hello", "okay sir" inside a Kannada call is normal code-mixing, not a language change.
MIN_WORDS_TO_SWITCH = 3


def language_note(code: str) -> str:
    info = LANGUAGES[code]
    return (
        f"[Language update: the caller is now speaking {info.name}. From now on reply only in "
        f"{info.name} ({info.native}), until the caller switches language again. Exception: "
        "if the caller explicitly asked you to use a particular language, keep using it.]"
    )


class LanguageGuard(FrameProcessor):
    def __init__(
        self,
        *,
        allowed: list[str],
        initial: str,
        on_switch: SwitchCallback,
        tts: SarvamTTSService | None = None,
        gemini_live: Any | None = None,
        auto: bool = True,
    ) -> None:
        super().__init__()
        self._allowed = allowed
        self.current = initial
        self._on_switch = on_switch
        self._tts = tts
        self._gemini_live = gemini_live
        # Off in a fixed-language call: only an explicit request (switch_to) changes language.
        self._auto = auto

    def _language_of(self, frame: TranscriptionFrame) -> str | None:
        # A word or two ("I.", "ok") is too little to judge, and STT can mislabel such
        # fragments; switching on them would flip the conversation for no reason.
        if (
            sum(ch.isalpha() for ch in frame.text) < MIN_LETTERS_TO_SWITCH
            or len(frame.text.split()) < MIN_WORDS_TO_SWITCH
        ):
            return None
        # Trust the STT's detected language when it reports an allowed one; otherwise read
        # the script of the text itself (fast, and works for Gemini transcripts too).
        code = code_from_pipecat(frame.language)
        if code in self._allowed:
            return code
        detected = detect_language(frame.text, self._allowed)
        # Gemini sometimes writes Kannada/Telugu speech in Latin letters ("Naanu Ramesh"), so
        # in Live mode Latin text is not evidence of English.
        if detected == "en" and self._gemini_live is not None:
            return None
        return detected if detected in self._allowed else None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if self._auto and isinstance(frame, TranscriptionFrame) and frame.text.strip():
            code = self._language_of(frame)
            if code and code != self.current:
                await self.switch_to(code)
        await self.push_frame(frame, direction)

    async def switch_to(self, code: str, *, steer: bool = True) -> None:
        """Make `code` the call's language. `steer=False` when the model itself asked for it."""
        if code == self.current or code not in self._allowed:
            return
        previous, self.current = self.current, code
        if steer:
            await self._apply_switch(code)
        await self._on_switch(previous, code)

    async def _apply_switch(self, code: str) -> None:
        if self._gemini_live is not None:
            # Gemini Live's native audio follows the caller's language by itself. Messages sent
            # into a live session mid-call (the old "language update" note) are known to garble
            # Gemini 3.8 Live's transcripts, so in Live mode the switch is only recorded.
            return
        info = LANGUAGES[code]
        await self.push_frame(
            LLMMessagesAppendFrame(
                messages=[{"role": "user", "content": language_note(code)}], run_llm=False
            )
        )
        if self._tts is not None:
            await self.push_frame(
                TTSUpdateSettingsFrame(
                    delta=SarvamTTSService.Settings(language=info.pipecat), service=self._tts
                )
            )
