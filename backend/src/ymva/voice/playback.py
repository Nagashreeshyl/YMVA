"""Keeps the phone's playback in step with the call (Plivo-protocol providers: Vobiz, Plivo).

The provider plays our audio from a queue. With the office background we send audio
continuously at real time, so a queue that started late never catches up: on a real Vobiz call
the agent was heard ~20-30 s late and broken up, while our own recording sounded fine.

Every couple of seconds we send a checkpoint; the provider answers "playedStream" once its
playback reaches it. If that takes too long, the queue holds stale audio, so we clear it and the
caller is back in real time.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    OutputTransportMessageUrgentFrame,
    StartFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from ymva.telephony.plivo_family import CheckpointSerializer

CHECK_EVERY_SECS = 2.0
# Further behind than this, the caller hears the agent late enough to notice: clear the queue.
MAX_LAG_SECS = 2.0


class PlaybackGuard(FrameProcessor):
    def __init__(
        self, serializer: CheckpointSerializer, on_resync: Callable[[float], Awaitable[None]]
    ) -> None:
        super().__init__()
        self._serializer = serializer
        self._on_resync = on_resync
        self._cleared_at = 0.0  # checkpoints sent before the last clear say nothing new
        self._task: asyncio.Task[None] | None = None
        serializer.played = self._played

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self._task = self.create_task(self._checkpoints())
        elif isinstance(frame, (EndFrame, CancelFrame)) and self._task:
            await self.cancel_task(self._task)
            self._task = None
        await self.push_frame(frame, direction)

    async def _send(self, message: dict[str, str]) -> None:
        # Urgent: goes out at once, so a checkpoint measures only the provider's queue.
        await self.push_frame(OutputTransportMessageUrgentFrame(message=message))

    async def _checkpoints(self) -> None:
        stream = str(self._serializer._stream_id)
        while True:
            await asyncio.sleep(CHECK_EVERY_SECS)
            await self._send(
                {"event": "checkpoint", "streamId": stream, "name": f"{time.monotonic():.3f}"}
            )

    async def _played(self, name: str) -> None:
        try:
            sent = float(name)
        except ValueError:
            return
        lag = time.monotonic() - sent
        # After a stalled connection releases, every queued checkpoint arrives late at once;
        # one clear is enough, further clears would only cut the agent's fresh speech.
        if lag > MAX_LAG_SECS and sent > self._cleared_at:
            self._cleared_at = time.monotonic()
            await self._send({"event": "clearAudio", "streamId": str(self._serializer._stream_id)})
            await self._on_resync(lag)
