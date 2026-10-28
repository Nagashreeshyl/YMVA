import time
from types import SimpleNamespace
from typing import Any

from ymva.voice.playback import MAX_LAG_SECS, PlaybackGuard


async def test_a_phone_that_falls_behind_is_caught_up() -> None:
    serializer: Any = SimpleNamespace(_stream_id="s1", played=None)
    lags: list[float] = []

    async def resynced(lag: float) -> None:
        lags.append(lag)

    guard = PlaybackGuard(serializer, resynced)
    sent: list[dict[str, str]] = []

    async def send(message: dict[str, str]) -> None:
        sent.append(message)

    guard._send = send  # type: ignore[method-assign]
    # In step: the phone confirmed a checkpoint sent half a second ago. Nothing to do.
    await serializer.played(f"{time.monotonic() - 0.5:.3f}")
    assert sent == [] and lags == []
    # 20 s behind (the real Vobiz call): the stale audio is cleared and the event recorded.
    await serializer.played(f"{time.monotonic() - 20:.3f}")
    assert sent == [{"event": "clearAudio", "streamId": "s1"}]
    assert lags and lags[0] > MAX_LAG_SECS


async def test_a_burst_of_late_confirmations_clears_only_once() -> None:
    serializer: Any = SimpleNamespace(_stream_id="s1", played=None)

    async def resynced(_: float) -> None:
        return None

    guard = PlaybackGuard(serializer, resynced)
    sent: list[dict[str, str]] = []

    async def send(message: dict[str, str]) -> None:
        sent.append(message)

    guard._send = send  # type: ignore[method-assign]
    now = time.monotonic()
    for age in (11, 9, 7, 5, 3):  # a stalled connection releases queued checkpoints together
        await serializer.played(f"{now - age:.3f}")
    assert len(sent) == 1  # fresh speech isn't cut again and again
