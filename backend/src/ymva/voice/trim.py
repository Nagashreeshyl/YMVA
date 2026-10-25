"""Removes the silence a TTS service puts before each reply, so the agent is heard sooner."""

import numpy as np
from pipecat.frames.frames import Frame, InterruptionFrame, TTSAudioRawFrame, TTSStartedFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# Samples quieter than this (int16) count as silence.
_SILENCE_PEAK = 300
# Never trim more than this, in case a reply genuinely starts softly.
_MAX_TRIM_SECS = 0.5


class LeadingSilenceTrimmer(FrameProcessor):
    def __init__(self) -> None:
        super().__init__()
        self._trimming = False
        self._trimmed = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, (TTSStartedFrame, InterruptionFrame)):
            self._trimming = isinstance(frame, TTSStartedFrame)
            self._trimmed = 0
        elif isinstance(frame, TTSAudioRawFrame) and self._trimming:
            samples = np.frombuffer(frame.audio, dtype=np.int16)
            loud = np.flatnonzero(np.abs(samples) > _SILENCE_PEAK)
            budget = int(_MAX_TRIM_SECS * frame.sample_rate) - self._trimmed
            if loud.size == 0 and samples.size <= budget:
                self._trimmed += samples.size
                return  # all silence: drop it
            start = min(int(loud[0]) if loud.size else samples.size, max(budget, 0))
            # Back off a few milliseconds so the first sound isn't clipped.
            start = max(0, start - frame.sample_rate // 200)
            self._trimming = False
            if start:
                frame.audio = samples[start:].tobytes()
        await self.push_frame(frame, direction)
