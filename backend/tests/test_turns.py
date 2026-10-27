import numpy as np
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)

from ymva.voice.turns import GuardedSpeechStart

RATE = 16000


def _audio(rms: float, seconds: float) -> list[InputAudioRawFrame]:
    t = np.arange(int(RATE * 0.02)) / RATE
    tone = (np.sin(2 * np.pi * 220 * t) * rms * np.sqrt(2)).astype(np.int16).tobytes()
    return [InputAudioRawFrame(audio=tone, sample_rate=RATE, num_channels=1)
            for _ in range(int(seconds / 0.02))]  # fmt: skip


class Recorder(GuardedSpeechStart):
    def __init__(self) -> None:
        super().__init__()
        self.turns = 0

    async def trigger_user_turn_started(self, *_: object, **__: object) -> None:  # type: ignore[override]
        self.turns += 1


async def _feed(s: Recorder, *frames: object) -> None:
    for f in frames:
        await s.process_frame(f)  # type: ignore[arg-type]


async def test_caller_gets_the_turn_at_once_when_the_agent_is_quiet() -> None:
    s = Recorder()
    await _feed(s, VADUserStartedSpeakingFrame())
    assert s.turns == 1


async def test_noise_around_the_caller_does_not_cut_the_agent_off() -> None:
    s = Recorder()
    # The caller answers once while the agent is quiet: their voice level is learned.
    await _feed(s, VADUserStartedSpeakingFrame(), *_audio(3000, 1.0), VADUserStoppedSpeakingFrame())
    s.turns = 0
    await _feed(s, BotStartedSpeakingFrame())
    # A short word or cough: under half a second, the agent keeps talking.
    await _feed(s, VADUserStartedSpeakingFrame(), *_audio(3000, 0.3), VADUserStoppedSpeakingFrame())
    # Voices behind the caller: long but far quieter than the caller.
    await _feed(s, VADUserStartedSpeakingFrame(), *_audio(500, 2.0), VADUserStoppedSpeakingFrame())
    assert s.turns == 0
    # The caller really talks over the agent: it stops for them.
    await _feed(s, VADUserStartedSpeakingFrame(), *_audio(2500, 0.6))
    assert s.turns == 1


async def test_caller_still_talking_when_the_agent_finishes_gets_the_turn() -> None:
    s = Recorder()
    await _feed(s, BotStartedSpeakingFrame(), VADUserStartedSpeakingFrame(), *_audio(3000, 0.2))
    assert s.turns == 0
    await _feed(s, BotStoppedSpeakingFrame())
    assert s.turns == 1
