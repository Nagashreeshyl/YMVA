"""End-of-turn detection tuned for Sarvam's one-final-per-utterance transcripts."""

from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.frames.frames import TranscriptionFrame
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
