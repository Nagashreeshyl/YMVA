"""Near-field voice gate: keeps sounds from around the caller out of the conversation.

The caller speaks right into their phone; people talking behind them, a TV, or our own office
background leaking back from a speakerphone reach the microphone much quieter. Those quieter
sounds were starting phantom turns ("¿Qué hora es?") and cutting the agent off mid-sentence.

The gate learns how loud this caller's voice is during the call and replaces audio well below
that with silence, before voice detection and the AI hear it. Until the caller has spoken a
little, only very faint audio is gated, so a soft first "hello" always gets through.
"""

from collections import deque

import numpy as np
from pipecat.frames.frames import Frame, InputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# Audio quieter than this (int16 RMS of a frame) is silence on any phone line.
FLOOR_RMS = 120.0
# Sound this far below the caller's own voice is someone or something else: about -14 dB.
RELATIVE_LEVEL = 0.2
# The caller's voice level: a high percentile of recent frame levels (their speech is the
# loudest thing on their side), over the last minute of audio that wasn't near-silent. A
# minute, so a long reply from the agent with chatter behind the caller doesn't lower it.
LEVEL_PERCENTILE = 90
LEVEL_WINDOW_FRAMES = 3000  # 60 s of 20 ms frames
MIN_FRAMES_TO_LEARN = 50  # 1 s of speech before the gate trusts its estimate
# Stay open a moment after loud audio, so quiet word endings aren't chopped off.
HOLD_SECS = 0.25


class NearFieldGate(FrameProcessor):
    def __init__(self) -> None:
        super().__init__()
        self._levels: deque[float] = deque(maxlen=LEVEL_WINDOW_FRAMES)
        self._open_until = 0.0  # seconds of audio seen
        self._clock = 0.0

    def threshold(self) -> float:
        if len(self._levels) < MIN_FRAMES_TO_LEARN:
            return FLOOR_RMS
        voice = float(np.percentile(np.fromiter(self._levels, float), LEVEL_PERCENTILE))
        return max(FLOOR_RMS, voice * RELATIVE_LEVEL)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame) and frame.audio:
            samples = np.frombuffer(frame.audio, dtype=np.int16)
            level = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
            self._clock += samples.size / frame.sample_rate
            if level > FLOOR_RMS:
                self._levels.append(level)
            if level >= self.threshold():
                self._open_until = self._clock + HOLD_SECS
            elif self._clock > self._open_until:
                frame.audio = bytes(len(frame.audio))
        await self.push_frame(frame, direction)
