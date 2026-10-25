"""Gemini Live service that keeps a call going when Google drops the connection mid-call."""

from collections.abc import Callable
from typing import Any

from loguru import logger
from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService

RESUME_NOTE = (
    "[The phone line dropped for a second while you were talking. Say briefly that the line "
    "cut for a moment, in the language of the conversation, then continue where you left off.]"
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
        self._continue_after_reconnect: bool | None = None  # None: not reconnecting

    async def _reconnect(self) -> None:
        self._continue_after_reconnect = self._was_replying()
        self._session_resumption_handle = None
        await super()._reconnect()  # type: ignore[no-untyped-call]

    async def _handle_session_ready(self, session: Any) -> None:
        await super()._handle_session_ready(session)
        resume, self._continue_after_reconnect = self._continue_after_reconnect, None
        if not resume:
            return
        logger.info(f"{self}: reconnected mid-reply; asking the agent to continue")
        try:
            from google.genai.types import Content, Part

            await session.send_client_content(
                turns=Content(role="user", parts=[Part(text=RESUME_NOTE)]), turn_complete=True
            )
            # Gemini 3 only starts a reply once it also gets realtime input.
            await session.send_realtime_input(text=" ")
        except Exception as exc:
            logger.warning(f"{self}: couldn't ask the agent to continue: {exc}")
