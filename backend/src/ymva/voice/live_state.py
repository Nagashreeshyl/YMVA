"""Tracks who is talking, drives the typing sound while the agent uses a tool, and (for browser
tests) streams the conversation state and live transcript to the dashboard."""

import time
from collections.abc import Awaitable, Callable
from typing import Any

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed

from ymva.voice.ambience import AmbienceMixer

Emit = Callable[[dict[str, Any]], Awaitable[None]]

# Ending the call is not "work" the caller should hear.
_SILENT_TOOLS = {"end_call"}


class ConversationState(BaseObserver):
    def __init__(self, mixer: AmbienceMixer | None, emit: Emit | None) -> None:
        super().__init__()
        self._mixer = mixer
        self._emit = emit
        self._state = "listening"
        # Whether the agent has started speaking since the caller's last turn.
        self.agent_replied = False
        self.bot_speaking = False
        # Last time anything happened (someone spoke, a tool ran); for spotting a stuck model.
        self.last_activity = time.monotonic()

    @property
    def state(self) -> str:
        return self._state

    async def _set(self, state: str) -> None:
        if self._mixer is not None:
            self._mixer.thinking = state == "using_tool"
        self.last_activity = time.monotonic()
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
            self.agent_replied = False
            await self._set("thinking")
        elif isinstance(frame, FunctionCallInProgressFrame):
            if frame.function_name not in _SILENT_TOOLS:
                await self._set("using_tool")
        elif isinstance(frame, FunctionCallResultFrame):
            if self._state == "using_tool":
                # The agent may have kept talking while the tool ran.
                await self._set("agent_speaking" if self.bot_speaking else "thinking")
        elif isinstance(frame, BotStartedSpeakingFrame):
            self.agent_replied = self.bot_speaking = True
            await self._set("agent_speaking")
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self.bot_speaking = False
            self.last_activity = time.monotonic()
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
