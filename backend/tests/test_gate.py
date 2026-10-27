import numpy as np
from pipecat.frames.frames import InputAudioRawFrame
from pipecat.tests.utils import run_test

from ymva.voice.gate import NearFieldGate

RATE = 16000


def _frames(rms: float, seconds: float) -> list[InputAudioRawFrame]:
    t = np.arange(int(RATE * 0.02)) / RATE
    tone = (np.sin(2 * np.pi * 220 * t) * rms * np.sqrt(2)).astype(np.int16).tobytes()
    return [InputAudioRawFrame(audio=tone, sample_rate=RATE, num_channels=1)
            for _ in range(int(seconds / 0.02))]  # fmt: skip


def _loud(frame: InputAudioRawFrame) -> bool:
    return bool(np.abs(np.frombuffer(frame.audio, np.int16)).max() > 0)


async def test_gate_keeps_the_caller_and_drops_quieter_sounds_around_them() -> None:
    soft_hello = _frames(300, 0.5)  # before the gate knows the caller: passes
    caller = _frames(3000, 2.0)
    background = _frames(400, 1.0)  # people talking behind the caller, ~-17 dB
    caller_again = _frames(2500, 0.5)
    sent = soft_hello + caller + background + caller_again
    received, _ = await run_test(NearFieldGate(), frames_to_send=sent)
    audio = [f for f in received if isinstance(f, InputAudioRawFrame)]
    passed = [_loud(f) for f in audio]
    n1, n2, n3 = len(soft_hello), len(caller), len(background)
    assert all(passed[:n1]) and all(passed[n1 : n1 + n2])
    hold = int(0.25 / 0.02) + 1  # word endings right after speech are kept
    assert not any(passed[n1 + n2 + hold : n1 + n2 + n3])
    assert all(passed[n1 + n2 + n3 :])
