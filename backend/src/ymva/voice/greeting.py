"""Prepares the agent's first words before the call connects, so the greeting plays instantly.

The greeting is synthesized with Sarvam while the phone rings (outbound) or while a browser
test call is connecting, and kept in memory for a few minutes. The voice session plays it the
moment audio starts; if it isn't ready in time, the session simply speaks it the normal way.
"""

import asyncio
import base64
import io
import time
import uuid
import wave
from dataclasses import dataclass

import httpx

from ymva.logging import get_logger
from ymva.voice.config import AgentConfig
from ymva.voice.languages import LANGUAGES, has_indic_letters
from ymva.voice.prompts import opening_language, opening_lines

log = get_logger(__name__)

_TTL_SECONDS = 600
_SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
# Speak with the same named voices as Gemini Live, so a prepared greeting matches the call.
# Tried in order: each has its own quota, and the fastest one runs out first on small plans.
_GEMINI_TTS_MODELS = (
    "gemini-3.8-flash-lite-tts",
    "gemini-3.1-flash-tts-preview",
    "gemini-2.5-flash-preview-tts",
)
_GEMINI_TTS_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


@dataclass
class GreetingAudio:
    text: str
    language: str
    pcm: bytes  # 16-bit mono
    sample_rate: int


_cache: dict[uuid.UUID, tuple[float, asyncio.Task[GreetingAudio | None]]] = {}
# Finished greeting audio by (engine, text, voice, pace, sample rate), reused across calls.
_audio: dict[tuple[str, str, str, float, int], GreetingAudio] = {}
_AUDIO_CACHE_LIMIT = 200


def _prune() -> None:
    now = time.monotonic()
    for key in [k for k, (t, _) in _cache.items() if now - t > _TTL_SECONDS]:
        _cache.pop(key, None)


def _wav_to_pcm(wav: bytes) -> tuple[bytes, int]:
    with wave.open(io.BytesIO(wav)) as w:
        return w.readframes(w.getnframes()), w.getframerate()


async def synthesize_sarvam(
    text: str, language: str, voice: str, pace: float, sample_rate: int, api_key: str
) -> tuple[bytes, int]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as client:
        response = await client.post(
            _SARVAM_TTS_URL,
            headers={"api-subscription-key": api_key},
            json={
                "text": text,
                # Sarvam refuses text with no letters of the language (an English greeting).
                "target_language_code": (
                    LANGUAGES[language] if has_indic_letters(text) else LANGUAGES["en"]
                ).pipecat.value,
                "model": "bulbul:v3",
                "speaker": voice,
                "pace": pace,
                "speech_sample_rate": sample_rate,
            },
        )
    response.raise_for_status()
    return _wav_to_pcm(base64.b64decode("".join(response.json()["audios"])))


async def synthesize_gemini(text: str, voice: str, api_key: str) -> tuple[bytes, int]:
    body = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=3.0)) as client:
        for model in _GEMINI_TTS_MODELS:
            response = await client.post(
                _GEMINI_TTS_URL.format(model=model),
                headers={"x-goog-api-key": api_key},
                json=body,
            )
            # Out of quota or overloaded: the next model may still have room.
            if response.status_code not in (429, 500, 503):
                break
    response.raise_for_status()
    part = response.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
    data = base64.b64decode(part["data"])
    mime = str(part.get("mimeType", ""))
    if "wav" in mime:
        return _wav_to_pcm(data)
    rate = int(mime.split("rate=")[1].split(";")[0]) if "rate=" in mime else 24000
    return data, rate


async def _synthesize(
    config: AgentConfig, variables: dict[str, str], keys: dict[str, str], sample_rate: int
) -> GreetingAudio | None:
    language = opening_language(config, variables)
    announcement, greeting = opening_lines(config, variables, language)
    text = " ".join(part for part in (announcement, greeting) if part)
    if not text:
        return None  # the model writes the greeting, so there is nothing to prepare
    live = config.mode == "gemini_live"
    voice = config.gemini_live.voice if live else config.pipeline.voice
    pace = 1.0 if live else config.pipeline.speaking_pace
    cache_key = ("gemini" if live else "sarvam", text, voice, pace, sample_rate)
    if cache_key in _audio:
        return _audio[cache_key]
    try:
        if live:
            pcm, rate = await synthesize_gemini(text, voice, keys["google"])
        else:
            pcm, rate = await synthesize_sarvam(
                text, language, voice, pace, sample_rate, keys["sarvam"]
            )
    except Exception as exc:
        log.warning("greeting prefetch failed; it will be spoken normally", error=str(exc))
        return None
    audio = GreetingAudio(text=text, language=language, pcm=pcm, sample_rate=rate)
    if len(_audio) >= _AUDIO_CACHE_LIMIT:
        _audio.pop(next(iter(_audio)))
    _audio[cache_key] = audio
    return audio


def prefetch(
    call_id: uuid.UUID,
    config: AgentConfig,
    variables: dict[str, str],
    keys: dict[str, str],
    sample_rate: int,
) -> None:
    """Start preparing the greeting audio in the background."""
    if (config.mode == "gemini_live" and "google" not in keys) or (
        config.mode == "pipeline" and "sarvam" not in keys
    ):
        return
    _prune()
    task = asyncio.create_task(_synthesize(config, variables, keys, sample_rate))
    _cache[call_id] = (time.monotonic(), task)


def has(call_id: uuid.UUID) -> bool:
    return call_id in _cache


async def take(call_id: uuid.UUID, wait_seconds: float) -> GreetingAudio | None:
    """The prepared greeting, waiting briefly if it's still being made."""
    entry = _cache.pop(call_id, None)
    if entry is None:
        return None
    try:
        return await asyncio.wait_for(asyncio.shield(entry[1]), wait_seconds)
    except TimeoutError:
        return None
