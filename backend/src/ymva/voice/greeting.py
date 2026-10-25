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
from ymva.voice.languages import LANGUAGES
from ymva.voice.prompts import opening_language, opening_lines

log = get_logger(__name__)

_TTL_SECONDS = 600
_SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"


@dataclass
class GreetingAudio:
    text: str
    language: str
    pcm: bytes  # 16-bit mono
    sample_rate: int


_cache: dict[uuid.UUID, tuple[float, asyncio.Task[GreetingAudio | None]]] = {}
# Finished greeting audio by (text, voice, pace, sample rate), reused across calls.
_audio: dict[tuple[str, str, float, int], GreetingAudio] = {}
_AUDIO_CACHE_LIMIT = 200


def _prune() -> None:
    now = time.monotonic()
    for key in [k for k, (t, _) in _cache.items() if now - t > _TTL_SECONDS]:
        _cache.pop(key, None)


async def _synthesize(
    config: AgentConfig, variables: dict[str, str], api_key: str, sample_rate: int
) -> GreetingAudio | None:
    language = opening_language(config, variables)
    announcement, greeting = opening_lines(config, variables, language)
    text = " ".join(part for part in (announcement, greeting) if part)
    if not text:
        return None  # the model writes the greeting, so there is nothing to prepare
    p = config.pipeline
    cache_key = (text, p.voice, p.speaking_pace, sample_rate)
    if cache_key in _audio:
        return _audio[cache_key]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0)) as client:
            response = await client.post(
                _SARVAM_TTS_URL,
                headers={"api-subscription-key": api_key},
                json={
                    "text": text,
                    "target_language_code": LANGUAGES[language].pipecat.value,
                    "model": "bulbul:v3",
                    "speaker": p.voice,
                    "pace": p.speaking_pace,
                    "speech_sample_rate": sample_rate,
                },
            )
        response.raise_for_status()
        wav = base64.b64decode("".join(response.json()["audios"]))
        with wave.open(io.BytesIO(wav)) as w:
            pcm = w.readframes(w.getnframes())
            rate = w.getframerate()
        audio = GreetingAudio(text=text, language=language, pcm=pcm, sample_rate=rate)
        if len(_audio) >= _AUDIO_CACHE_LIMIT:
            _audio.pop(next(iter(_audio)))
        _audio[cache_key] = audio
        return audio
    except Exception as exc:
        log.warning("greeting prefetch failed; it will be spoken normally", error=str(exc))
        return None


def prefetch(
    call_id: uuid.UUID,
    config: AgentConfig,
    variables: dict[str, str],
    api_key: str | None,
    sample_rate: int,
) -> None:
    """Start preparing the greeting audio in the background (Standard mode only)."""
    if config.mode != "pipeline" or not api_key:
        return
    _prune()
    task = asyncio.create_task(_synthesize(config, variables, api_key, sample_rate))
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
