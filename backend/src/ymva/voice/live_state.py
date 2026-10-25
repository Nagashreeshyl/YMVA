"""Tracks who is talking, drives the typing sound while the agent thinks, and (for browser tests)
streams the conversation state and live transcript to the dashboard."""

from collections.abc import Awaitable, Callable
from typing import Any

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed

from ymva.voice.ambience import AmbienceMixer

Emit = Callable[[dict[str, Any]], Awaitable[None]]


class ConversationState(BaseObserver):
    def __init__(self, mixer: AmbienceMixer | None, emit: Emit | None) -> None:
        super().__init__()
        self._mixer = mixer
        self._emit = emit
        self._state = "listening"

    @property
    def state(self) -> str:
        return self._state

    async def _set(self, state: str) -> None:
        if self._mixer is not None:
            self._mixer.thinking = state == "thinking"
        if state != self._state:
            self._state = state
            if self._emit:
                await self._emit({"type": "state", "state": state})

    async def on_push_frame(self, data: FramePushed) -> None:
        if not data.first_push:
            return
        frame = data.frame
        if isinstance(frame, UserStartedSpeakingFrame):
            await self._set("user_speaking")
        elif isinstance(frame, UserStoppedSpeakingFrame):
            await self._set("thinking")
        elif isinstance(frame, BotStartedSpeakingFrame):
            await self._set("agent_speaking")
        elif isinstance(frame, BotStoppedSpeakingFrame):
            if self._state == "agent_speaking":
                await self._set("listening")
        elif (
            isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame))
            and self._emit
            and frame.text.strip()
        ):
            await self._emit(
                {
                    "type": "partial",
                    "content": frame.text,
                    "final": isinstance(frame, TranscriptionFrame),
                }
            )
