"""Turn-taking: when the caller has started and finished speaking."""

from collections import deque

import numpy as np
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start import BaseUserTurnStartStrategy
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy

# If the end-of-turn model thinks the caller is mid-sentence, wait at most this long for them to
# continue (Pipecat's default is 3 s, which felt like the agent had gone away).
TURN_END_MAX_WAIT_SECS = 1.0


class SegmentAwareTurnStop(TurnAnalyzerUserTurnStopStrategy):  # type: ignore[no-untyped-call]
    """Ignores "final" transcripts that arrive while the caller is still talking.

    When the caller pauses briefly mid-sentence, Sarvam finalises the first part; that result can
    land just after they start speaking again. The stock strategy then treats the whole turn as
    transcribed, replies to half a sentence, and replies again a second later when the rest
    arrives. Here a final that lands mid-speech only updates the text; the turn ends on the final
    for the last part (sent right after the caller stops) or on the usual safety timeout.
    """

    async def _handle_transcription(self, frame: TranscriptionFrame) -> None:
        if frame.finalized and self._vad_user_speaking:
            self._text = frame.text
            return
        await super()._handle_transcription(frame)


def turn_stop_strategy() -> SegmentAwareTurnStop:
    return SegmentAwareTurnStop(
        turn_analyzer=LocalSmartTurnAnalyzerV3(
            params=SmartTurnParams(stop_secs=TURN_END_MAX_WAIT_SECS)
        )
    )


# While the agent speaks, the caller must keep talking this long before it stops for them.
BARGE_IN_SECS = 0.5
# ...at no less than this share of their own speaking level (about -10 dB): quieter is someone
# or something around them, or our office sound coming back from a speakerphone.
BARGE_IN_LEVEL = 0.3


class GuardedSpeechStart(BaseUserTurnStartStrategy):  # type: ignore[no-untyped-call]
    """A turn starts when voice detection hears the caller, as usual, except while the agent is
    speaking: then the caller must keep talking for a moment at close to their own voice level.

    Background voices and stray noises were cutting the agent off mid-sentence. The audio
    itself is never changed, so the AI still hears everything the caller says.
    """

    def __init__(self) -> None:
        super().__init__()
        self._agent_speaking = False
        self._pending = False  # caller speech heard during the agent's turn, not yet decided
        self._loud_secs = 0.0
        self._speech_levels: deque[float] = deque(maxlen=1500)  # 30 s of the caller's speech
        self._caller_speaking = False

    def _voice_level(self) -> float | None:
        if len(self._speech_levels) < 25:  # half a second of their speech
            return None
        return float(np.median(np.fromiter(self._speech_levels, float)))

    async def _start(self) -> ProcessFrameResult:
        self._pending, self._loud_secs = False, 0.0
        await self.trigger_user_turn_started()
        return ProcessFrameResult.STOP

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if isinstance(frame, BotStartedSpeakingFrame):
            self._agent_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._agent_speaking = False
            if self._pending:  # the caller is still talking now the agent has finished
                return await self._start()
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self._caller_speaking = True
            if not self._agent_speaking:
                return await self._start()
            self._pending, self._loud_secs = True, 0.0
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._caller_speaking = False
            self._pending = False
        elif isinstance(frame, InputAudioRawFrame) and self._caller_speaking and frame.audio:
            samples = np.frombuffer(frame.audio, dtype=np.int16).astype(np.float64)
            level = float(np.sqrt(np.mean(samples**2)))
            if not self._agent_speaking:
                self._speech_levels.append(level)  # learn their voice when it's clearly them
            elif self._pending:
                voice = self._voice_level()
                if voice is None or level >= voice * BARGE_IN_LEVEL:
                    self._loud_secs += samples.size / frame.sample_rate
                if self._loud_secs >= BARGE_IN_SECS:
                    return await self._start()
        return ProcessFrameResult.CONTINUE
