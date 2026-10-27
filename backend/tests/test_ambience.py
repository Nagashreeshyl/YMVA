import numpy as np

from ymva.voice.ambience import _OFFICE_RMS, _room_tone


def test_office_sound_loads_at_the_calls_rate_and_level() -> None:
    for rate in (8000, 16000, 24000):
        loop = _room_tone(rate)
        assert abs(loop.size / rate - 132.5) < 0.1  # the whole loop, resampled
        assert abs(np.sqrt(np.mean(loop**2)) - _OFFICE_RMS) < 1
