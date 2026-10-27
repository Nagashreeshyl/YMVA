"""Call atmosphere: a real call-centre murmur under the whole call, and faint keyboard typing
while the agent is using a tool (looking up or saving details), so a pause sounds like someone
working.

Both are mixed only into the agent's outgoing audio, well below speech level. The office sound
is a recording ("Office ambience - call center 3" by leonelmail on Freesound, CC0), trimmed to
the stretches without laughs or close voices and made into a seamless loop (sounds/office.wav).
"""

import wave
from functools import lru_cache
from pathlib import Path

import numpy as np
import soxr  # type: ignore[import-untyped]
from pipecat.audio.mixers.base_audio_mixer import BaseAudioMixer
from pipecat.frames.frames import MixerControlFrame, MixerEnableFrame, MixerUpdateSettingsFrame

_OFFICE_WAV = Path(__file__).parent / "sounds" / "office.wav"
_OFFICE_RMS = 400.0  # int16 units at volume 1.0 (about -38 dBFS): heard, but under the voice
_KEY_PEAK = 450.0  # int16 peak of one keystroke at volume 1.0 (faint, well under speech)
TYPING_DELAY_SECS = 0.3


@lru_cache(maxsize=4)
def _room_tone(sample_rate: int) -> np.ndarray:
    """The office recording at the call's sample rate and level; made once, shared by calls."""
    with wave.open(str(_OFFICE_WAV)) as w:
        rate = w.getframerate()
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    if rate != sample_rate:
        audio = soxr.resample(audio, rate, sample_rate).astype(np.float32)
    return audio * np.float32(_OFFICE_RMS / (np.sqrt(np.mean(audio**2)) or 1))


def _keystroke(sample_rate: int, rng: np.random.Generator) -> np.ndarray:
    """A short, soft click: a few milliseconds of bright noise with a fast decay."""
    length = int(sample_rate * rng.uniform(0.012, 0.022))
    t = np.arange(length) / sample_rate
    noise = rng.standard_normal(length)
    noise = np.diff(noise, prepend=0.0)  # brighten: removes low end, sounds like plastic
    envelope = np.exp(-t / rng.uniform(0.002, 0.004))
    click = noise * envelope
    click *= _KEY_PEAK * rng.uniform(0.6, 1.0) / (np.max(np.abs(click)) or 1)
    return click.astype(np.float32)


class AmbienceMixer(BaseAudioMixer):
    def __init__(self, *, room_tone: bool, typing: bool, volume: float, seed: int | None = None):
        self._room_tone_on = room_tone
        self._typing_on = typing
        self._volume = max(0.0, min(1.0, volume))
        self._rng = np.random.default_rng(seed)
        self._enabled = room_tone or typing
        self._sample_rate = 16000
        self._loop = np.zeros(1, dtype=np.float32)
        self._pos = 0
        self.thinking = False
        self._next_key_in = 0  # samples until the next keystroke
        self._keys_left = 0  # keystrokes left in the current burst
        self._pending: np.ndarray = np.zeros(0, dtype=np.float32)  # unfinished keystroke tail
        self._thinking_for = 0  # samples since the agent started thinking

    async def start(self, sample_rate: int) -> None:
        self._sample_rate = sample_rate
        if self._room_tone_on:
            # Made once per sample rate and shared by every call; each call starts at a random
            # point in the loop.
            self._loop = _room_tone(sample_rate)
            self._pos = int(self._rng.integers(0, self._loop.size))

    async def stop(self) -> None:
        pass

    async def process_frame(self, frame: MixerControlFrame) -> None:
        if isinstance(frame, MixerEnableFrame):
            self._enabled = frame.enable
        elif isinstance(frame, MixerUpdateSettingsFrame) and "volume" in frame.settings:
            self._volume = max(0.0, min(1.0, float(frame.settings["volume"])))

    def _room(self, n: int) -> np.ndarray:
        if not self._room_tone_on or self._loop.size <= 1:
            return np.zeros(n, dtype=np.float32)
        idx = (np.arange(n) + self._pos) % self._loop.size
        self._pos = (self._pos + n) % self._loop.size
        return self._loop[idx]

    def _typing(self, n: int) -> np.ndarray:
        out = np.zeros(n, dtype=np.float32)
        # Finish a keystroke that started in the previous chunk.
        if self._pending.size:
            take = min(n, self._pending.size)
            out[:take] += self._pending[:take]
            self._pending = self._pending[take:]
        if not (self._typing_on and self.thinking):
            self._keys_left = 0
            self._thinking_for = 0
            return out
        # Only type when the pause is long enough to notice; quick replies stay clean.
        self._thinking_for += n
        if self._thinking_for < TYPING_DELAY_SECS * self._sample_rate:
            return out
        i = 0
        while i < n:
            if self._next_key_in > n - i:
                self._next_key_in -= n - i
                break
            i += self._next_key_in
            click = _keystroke(self._sample_rate, self._rng)
            end = min(n, i + click.size)
            out[i:end] += click[: end - i]
            self._pending = click[end - i :]
            # Typing comes in short bursts of keys with small pauses between words.
            if self._keys_left <= 0:
                self._keys_left = int(self._rng.integers(3, 9))
            self._keys_left -= 1
            gap = (
                self._rng.uniform(0.07, 0.18)
                if self._keys_left > 0
                else self._rng.uniform(0.25, 0.6)
            )
            self._next_key_in = int(gap * self._sample_rate)
        return out

    async def mix(self, audio: bytes) -> bytes:
        if not self._enabled or self._volume == 0 or not audio:
            return audio
        speech = np.frombuffer(audio, dtype=np.int16).astype(np.float32)
        extra = (self._room(speech.size) + self._typing(speech.size)) * self._volume
        mixed = np.clip(speech + extra, -32768, 32767).astype(np.int16)
        return bytes(mixed.tobytes())
