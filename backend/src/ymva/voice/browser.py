"""WebSocket protocol for browser test calls.

Browser -> server: binary frames of 16-bit little-endian mono PCM at 16 kHz.
Server -> browser: binary frames of the same format (agent audio), and JSON text frames:
  {"type": "clear"} on barge-in (drop queued audio), plus transcript/status events.
"""

import json

from pipecat.audio.utils import create_stream_resampler
from pipecat.frames.frames import (
    AudioRawFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
)
from pipecat.processors.frame_processor import FrameProcessorSetup
from pipecat.serializers.base_serializer import FrameSerializer

BROWSER_SAMPLE_RATE = 16000


class BrowserAudioSerializer(FrameSerializer):
    def __init__(self) -> None:
        super().__init__(FrameSerializer.InputParams(ignore_rtvi_messages=False))
        self._pipeline_rate = BROWSER_SAMPLE_RATE
        self._in_resampler = create_stream_resampler()
        self._out_resampler = create_stream_resampler()

    async def setup(self, setup: FrameProcessorSetup) -> None:  # type: ignore[override]
        self._pipeline_rate = setup.audio_in_sample_rate or BROWSER_SAMPLE_RATE

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, InterruptionFrame):
            return json.dumps({"type": "clear"})
        if isinstance(frame, AudioRawFrame):
            audio = frame.audio
            if frame.sample_rate != BROWSER_SAMPLE_RATE:
                audio = await self._out_resampler.resample(
                    audio, frame.sample_rate, BROWSER_SAMPLE_RATE
                )
            return audio or None
        if isinstance(frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)):
            return json.dumps(frame.message)
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if not isinstance(data, bytes) or len(data) < 2:
            return None
        audio = data[: len(data) - (len(data) % 2)]
        if self._pipeline_rate != BROWSER_SAMPLE_RATE:
            audio = await self._in_resampler.resample(
                audio, BROWSER_SAMPLE_RATE, self._pipeline_rate
            )
        return InputAudioRawFrame(audio=audio, num_channels=1, sample_rate=self._pipeline_rate)
