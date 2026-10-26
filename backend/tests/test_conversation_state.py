from typing import Any

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection

from ymva.voice.live_state import ConversationState


async def _push(state: ConversationState, frame: Any) -> None:
    await state.on_push_frame(
        FramePushed(
            source=None,  # type: ignore[arg-type]
            destination=None,  # type: ignore[arg-type]
            frame=frame,
            direction=FrameDirection.DOWNSTREAM,
            timestamp=0,
        )
    )


def _tool(kind: type, name: str = "save_promise") -> Any:
    extra = {"result": {"ok": True}} if kind is FunctionCallResultFrame else {}
    return kind(function_name=name, tool_call_id="1", arguments={}, **extra)


async def test_tool_finishing_while_agent_talks_keeps_it_speaking() -> None:
    state = ConversationState(None, None)
    await _push(state, UserStartedSpeakingFrame())
    await _push(state, UserStoppedSpeakingFrame())
    assert state.state == "thinking"
    await _push(state, BotStartedSpeakingFrame())
    await _push(state, _tool(FunctionCallInProgressFrame))
    assert state.state == "using_tool"
    await _push(state, _tool(FunctionCallResultFrame))
    # Before the fix this went back to "thinking" while the agent was still talking, and the
    # stall watchdog interrupted it.
    assert state.state == "agent_speaking" and state.bot_speaking
    await _push(state, BotStoppedSpeakingFrame())
    assert state.state == "listening" and not state.bot_speaking


async def test_tool_finishing_in_silence_waits_for_reply() -> None:
    state = ConversationState(None, None)
    await _push(state, UserStoppedSpeakingFrame())
    await _push(state, _tool(FunctionCallInProgressFrame))
    await _push(state, _tool(FunctionCallResultFrame))
    assert state.state == "thinking"
    await _push(state, _tool(FunctionCallInProgressFrame, "end_call"))
    assert state.state == "thinking"  # hanging up is not "using a tool"
