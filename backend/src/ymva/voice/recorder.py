"""Saves a call's timeline (turns, language switches, tool calls) and its metrics."""

import asyncio
import statistics
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    MetricsFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import (
    LLMUsageMetricsData,
    STTUsageMetricsData,
    TTFBMetricsData,
    TTSUsageMetricsData,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed

from ymva.db import get_sessionmaker
from ymva.logging import get_logger
from ymva.models import Call, CallEvent, CallEventType, CallStatus

log = get_logger(__name__)

LiveEmitter = Callable[[dict[str, Any]], Awaitable[None]]


def _summary(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
    return {
        "p50_ms": round(statistics.median(ordered) * 1000),
        "p95_ms": round(p95 * 1000),
        "count": len(ordered),
    }


class MetricsObserver(BaseObserver):
    """Measures response latency (caller stops talking -> agent audio starts) and usage."""

    def __init__(self) -> None:
        super().__init__()
        self._user_stopped_at: float | None = None
        self.response_latency: list[float] = []
        self.ttfb: dict[str, list[float]] = {}
        self.usage: dict[str, float] = {
            "llm_prompt_tokens": 0,
            "llm_completion_tokens": 0,
            "tts_characters": 0,
            "stt_seconds": 0,
        }

    async def on_push_frame(self, data: FramePushed) -> None:
        if not data.first_push:
            return
        frame = data.frame
        now = data.timestamp / 1e9
        if isinstance(frame, VADUserStoppedSpeakingFrame):
            self._user_stopped_at = now - (getattr(frame, "stop_secs", 0) or 0)
        elif isinstance(frame, BotStartedSpeakingFrame) and self._user_stopped_at is not None:
            self.response_latency.append(max(0.0, now - self._user_stopped_at))
            self._user_stopped_at = None
        elif isinstance(frame, MetricsFrame):
            for item in frame.data:
                if isinstance(item, TTFBMetricsData) and item.value > 0:
                    kind = _service_kind(item.processor)
                    self.ttfb.setdefault(kind, []).append(item.value)
                elif isinstance(item, LLMUsageMetricsData):
                    self.usage["llm_prompt_tokens"] += item.value.prompt_tokens or 0
                    self.usage["llm_completion_tokens"] += item.value.completion_tokens or 0
                elif isinstance(item, TTSUsageMetricsData):
                    self.usage["tts_characters"] += item.value
                elif isinstance(item, STTUsageMetricsData):
                    self.usage["stt_seconds"] += item.value.audio_seconds

    def latency_report(self) -> dict[str, Any]:
        report: dict[str, Any] = {"response": _summary(self.response_latency)}
        for kind, values in self.ttfb.items():
            report[f"{kind}_ttfb"] = _summary(values)
        return report


def _service_kind(processor: str) -> str:
    name = processor.lower()
    for kind in ("stt", "tts", "llm"):
        if kind in name:
            return kind
    return "other"


class CallRecorder:
    def __init__(self, call_id: uuid.UUID, emit: LiveEmitter | None = None) -> None:
        self.call_id = call_id
        self._emit = emit
        self._seq = 0
        self._lock = asyncio.Lock()
        self.languages: list[str] = []

    async def event(
        self,
        type_: CallEventType,
        content: str = "",
        *,
        language: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        if language and language not in self.languages:
            self.languages.append(language)
        async with self._lock:
            self._seq += 1
            try:
                async with get_sessionmaker()() as session:
                    session.add(
                        CallEvent(
                            call_id=self.call_id,
                            seq=self._seq,
                            type=type_,
                            content=content,
                            language=language,
                            data=data or {},
                        )
                    )
                    await session.commit()
            except Exception:
                log.exception("failed to save call event", call_id=str(self.call_id))
        if self._emit:
            try:
                await self._emit(
                    {"type": type_.value, "content": content, "language": language, **(data or {})}
                )
            except Exception:
                log.debug("live event not delivered", call_id=str(self.call_id))

    async def mark_started(self, provider_call_id: str | None = None) -> None:
        now = datetime.now(UTC)
        async with get_sessionmaker()() as session:
            call = await session.get(Call, self.call_id)
            if call is None:
                return
            call.status = CallStatus.IN_PROGRESS
            call.started_at = call.started_at or now
            call.answered_at = call.answered_at or now
            if provider_call_id and not call.provider_call_id:
                call.provider_call_id = provider_call_id
            await session.commit()

    async def finish(
        self,
        *,
        status: CallStatus,
        end_reason: str,
        metrics: MetricsObserver | None,
        error: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        async with get_sessionmaker()() as session:
            call = await session.get(Call, self.call_id)
            if call is None:
                return
            call.status = status
            call.ended_at = now
            call.end_reason = end_reason
            if call.answered_at:
                call.duration_seconds = max(0, round((now - call.answered_at).total_seconds()))
            call.languages = self.languages
            if metrics:
                call.latency = metrics.latency_report()
                call.cost_breakdown = {"usage": metrics.usage}
            if error:
                call.error = error[:2000]
            await session.commit()
