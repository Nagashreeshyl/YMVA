"""Sarvam text-to-speech that never goes silent on English text.

Sarvam refuses text with no letters of the configured language ("Text must contain at least
one character from the allowed languages"): an all-English sentence in a Kannada call, or an
English stretch of a mixed reply, was simply not spoken. Such text is spoken with the same voice
in English (en-IN); the conversation's language is unchanged and comes back for the next text.
"""

from typing import Any

from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.transcriptions.language import Language

from ymva.voice.languages import has_indic_letters


class SarvamVoice(SarvamTTSService):
    _sent_language: object = None  # the language Sarvam is currently set to

    async def _send_config(self) -> None:
        await super()._send_config()  # type: ignore[no-untyped-call]
        settings: Any = self._settings
        self._sent_language = settings.language

    async def _send_text(self, text: str) -> None:
        settings: Any = self._settings
        conversation = settings.language
        # Numbers and punctuation alone count too: they have no letters of the language either.
        wanted = conversation if has_indic_letters(text) else Language.EN_IN
        if wanted != self._sent_language and self._websocket:
            settings.language = wanted
            try:
                await self._send_config()
            finally:
                settings.language = conversation
        await super()._send_text(text)
