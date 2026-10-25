"""Writes a call's audio (caller and agent mixed) to a WAV file while the call runs."""

import uuid
import wave
from pathlib import Path
from typing import Any

from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor

from ymva.config import get_settings
from ymva.logging import get_logger

log = get_logger(__name__)

RECORDING_SAMPLE_RATE = 16000
# Flush to disk every ~5 seconds of audio, so long calls never sit in memory.
_CHUNK_BYTES = RECORDING_SAMPLE_RATE * 2 * 5


def recordings_dir() -> Path:
    path = Path(get_settings().recordings_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def recording_path(key: str) -> Path:
    """Resolve a stored recording key, refusing anything outside the recordings folder."""
    base = recordings_dir().resolve()
    path = (base / key).resolve()
    if base not in path.parents:
        raise ValueError("Invalid recording key")
    return path


class CallRecording:
    def __init__(self, call_id: uuid.UUID) -> None:
        self.key = f"{call_id}.wav"
        self.processor = AudioBufferProcessor(
            sample_rate=RECORDING_SAMPLE_RATE,
            num_channels=1,
            buffer_size=_CHUNK_BYTES,
            auto_start_recording=True,
        )
        self._file: wave.Wave_write | None = None
        self._bytes = 0
        self.processor.add_event_handler("on_audio_data", self._on_audio)

    async def _on_audio(self, _: Any, audio: bytes, sample_rate: int, num_channels: int) -> None:
        if not audio:
            return
        try:
            if self._file is None:
                self._file = wave.open(str(recording_path(self.key)), "wb")  # noqa: SIM115
                self._file.setnchannels(num_channels)
                self._file.setsampwidth(2)
                self._file.setframerate(sample_rate)
            self._file.writeframes(audio)
            self._bytes += len(audio)
        except OSError:
            log.exception("couldn't write call recording", key=self.key)

    @property
    def duration_seconds(self) -> float:
        return self._bytes / (RECORDING_SAMPLE_RATE * 2)

    def close(self) -> str | None:
        """Finish the WAV file; returns its key, or None when nothing was recorded."""
        if self._file is None:
            return None
        self._file.close()
        self._file = None
        return self.key if self._bytes else None
