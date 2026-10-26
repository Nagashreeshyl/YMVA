"""Gemini Live service that keeps a call going when Google drops the connection or stalls."""

from collections.abc import Callable
from typing import Any

from loguru import logger
from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService

RESUME_NOTE = (
    "[The phone line dropped for a second while you were talking. Say briefly that the line "
    "cut for a moment, in the language of the conversation, then continue where you left off.]"
)
REPEAT_NOTE = (
    "[The line was unclear for a moment and you didn't catch what the caller just said. "
    "Politely ask them to say it again, briefly, in the language of the conversation.]"
)


class ResilientGeminiLive(GeminiLiveLLMService):
    """Reconnects with a fresh session seeded with the conversation so far.

    Pipecat resumes the old session by default; in tests and a real call a resumed session
    heard the caller but never answered again. If the agent was mid-reply when the line
    dropped, it is asked to pick up again so the caller isn't left in silence.
    """

    def __init__(self, *, was_replying: Callable[[], bool], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._was_replying = was_replying
        self._note_after_reconnect: str | None = None
        # Tool calls Gemini dropped (the caller spoke over them). Answering one anyway left
        # Gemini silent for the rest of the call, so their results are not sent.
        self._cancelled_calls: set[str] = set()

    async def _reconnect(self) -> None:
        if self._note_after_reconnect is None and self._was_replying():
            self._note_after_reconnect = RESUME_NOTE
        self._session_resumption_handle = None
        await super()._reconnect()  # type: ignore[no-untyped-call]

    async def recover_stall(self) -> None:
        """Gemini sometimes stops hearing the caller (seen after tool calls); a fresh session
        with the conversation so far, asking the caller to repeat, gets the call going again."""
        logger.warning(f"{self}: no reply to the caller; reconnecting")
        self._note_after_reconnect = REPEAT_NOTE
        await self._reconnect()

    async def _handle_server_message(self, message: Any) -> None:
        cancellation = getattr(message, "tool_call_cancellation", None)
        if cancellation and cancellation.ids:
            logger.info(f"{self}: Gemini cancelled tool calls {cancellation.ids}")
            self._cancelled_calls.update(cancellation.ids)
        await super()._handle_server_message(message)

    async def _tool_result(
        self, tool_call_id: str, tool_name: str, tool_result_message: dict[str, Any]
    ) -> None:
        if tool_call_id in self._cancelled_calls:
            logger.info(f"{self}: not answering cancelled tool call {tool_name} ({tool_call_id})")
            return
        await super()._tool_result(tool_call_id, tool_name, tool_result_message)

    async def _handle_session_ready(self, session: Any) -> None:
        await super()._handle_session_ready(session)
        note, self._note_after_reconnect = self._note_after_reconnect, None
        if note is None:
            return
        logger.info(f"{self}: reconnected; asking the agent to carry on")
        try:
            from google.genai.types import Content, Part

            await session.send_client_content(
                turns=Content(role="user", parts=[Part(text=note)]), turn_complete=True
            )
            # Gemini 3 only starts a reply once it also gets realtime input.
            await session.send_realtime_input(text=" ")
        except Exception as exc:
            logger.warning(f"{self}: couldn't ask the agent to continue: {exc}")
