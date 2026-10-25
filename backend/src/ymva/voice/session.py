"""Runs one call: builds the Pipecat pipeline for the agent's mode and drives it to the end."""

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    EndWorkerFrame,
    FunctionCallResultProperties,
    LLMMessagesAppendFrame,
    LLMRunFrame,
    OutputTransportMessageUrgentFrame,
    TTSSpeakFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.service_switcher import ServiceSwitcher, ServiceSwitcherStrategyFailover
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.transports.base_transport import BaseTransport

from ymva.config import get_settings
from ymva.logging import get_logger
from ymva.models import CallEventType, CallStatus
from ymva.voice.config import AgentConfig
from ymva.voice.guard import LanguageGuard
from ymva.voice.languages import LANGUAGES
from ymva.voice.prompts import opening_language, opening_lines, system_instruction
from ymva.voice.recorder import CallRecorder, MetricsObserver
from ymva.voice.recording import CallRecording

log = get_logger(__name__)

PIPELINE_SAMPLE_RATE = 16000
GEMINI_OUTPUT_SAMPLE_RATE = 24000

END_CALL = FunctionSchema(
    name="end_call",
    description=(
        "Hang up the phone call. Call this only after you have said goodbye, when the "
        "conversation is complete or the caller asks to end the call."
    ),
    properties={
        "reason": {
            "type": "string",
            "description": "Short reason, e.g. 'payment date confirmed' or 'caller busy'.",
        }
    },
    required=[],
)


class MissingCredentialError(Exception):
    pass


@dataclass
class CallSetup:
    call_id: uuid.UUID
    config: AgentConfig
    variables: dict[str, str]
    keys: dict[str, str]  # provider -> API key
    send_live_events: bool = False  # browser test calls show the transcript live
    # Agent audio sample rate leaving the pipeline: 24 kHz for browsers, 16 kHz for phone lines.
    output_sample_rate: int = PIPELINE_SAMPLE_RATE
    provider_call_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def required_providers(config: AgentConfig) -> set[str]:
    if config.mode == "gemini_live":
        return {"google"}
    return {
        config.pipeline.stt_provider,
        config.pipeline.llm_provider,
        config.pipeline.tts_provider,
    }


class CallSession:
    def __init__(self, setup: CallSetup, transport: BaseTransport) -> None:
        self.setup = setup
        self.transport = transport
        self.metrics = MetricsObserver()
        self.recorder = CallRecorder(
            setup.call_id, emit=self._emit if setup.send_live_events else None
        )
        self.recording = CallRecording(setup.call_id)
        self.task: PipelineTask | None = None
        self._ended_by: str = "caller_hung_up"
        self._idle_prompts = 0

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.task is not None:
            await self.task.queue_frame(OutputTransportMessageUrgentFrame(message=event))

    def _key(self, provider: str) -> str:
        key = self.setup.keys.get(provider)
        if not key:
            raise MissingCredentialError(
                f"No API key saved for {provider}. Add it in Integrations."
            )
        return key

    # ---- tools -------------------------------------------------------------------------

    async def _end_call(self, params: FunctionCallParams) -> None:
        reason = str(params.arguments.get("reason") or "agent ended the call")
        self._ended_by = "agent_ended"
        await self.recorder.event(
            CallEventType.TOOL_CALL,
            "end_call",
            data={"tool": "end_call", "arguments": params.arguments},
        )
        await params.result_callback(
            {"status": "ending call"}, properties=FunctionCallResultProperties(run_llm=False)
        )
        await params.llm.push_frame(EndWorkerFrame(reason=reason))

    # ---- pipeline construction ---------------------------------------------------------

    def _build(self) -> tuple[Pipeline, LLMContext, int, LanguageGuard, Any]:
        config = self.setup.config
        variables = self.setup.variables
        self.opening = opening_language(config, variables)
        instruction = system_instruction(config, variables, self.opening, get_settings().timezone)
        tools = ToolsSchema(standard_tools=[END_CALL])

        async def on_switch(previous: str | None, current: str) -> None:
            await self.recorder.event(
                CallEventType.LANGUAGE_SWITCH,
                f"{LANGUAGES[previous].name if previous else '—'} → {LANGUAGES[current].name}",
                language=current,
                data={"from": previous, "to": current},
            )

        vad = SileroVADAnalyzer(params=VADParams(stop_secs=0.2))
        user_params = LLMUserAggregatorParams(
            vad_analyzer=vad, user_idle_timeout=config.limits.silence_timeout_seconds
        )

        if config.mode == "gemini_live":
            live = config.gemini_live
            llm: Any = GeminiLiveLLMService(
                api_key=self._key("google"),
                settings=GeminiLiveLLMService.Settings(
                    model=live.model,
                    voice=live.voice,
                    temperature=live.temperature,
                    system_instruction=instruction,
                    # Native-audio models choose their reply language themselves; forcing a
                    # language code locks them into it, so it is deliberately not sent.
                    language=None,  # type: ignore[arg-type]
                ),
                tools=tools,
            )
            llm.register_function("end_call", self._end_call)
            context = LLMContext(messages=[{"role": "user", "content": self._live_kickoff()}])
            pair = LLMContextAggregatorPair(context, user_params=user_params)
            guard = LanguageGuard(
                allowed=config.languages, initial=self.opening, on_switch=on_switch, gemini_live=llm
            )
            pipeline = Pipeline(
                [
                    self.transport.input(),
                    pair.user(),
                    llm,
                    guard,
                    self.transport.output(),
                    self.recording.processor,
                    pair.assistant(),
                ]
            )
            return pipeline, context, GEMINI_OUTPUT_SAMPLE_RATE, guard, pair

        p = config.pipeline
        stt = SarvamSTTService(api_key=self._key("sarvam"), sample_rate=PIPELINE_SAMPLE_RATE)
        # Main model plus a backup that takes over automatically if the main one fails.
        models = list(dict.fromkeys([p.llm_model, p.backup_llm_model]))
        llms: list[FrameProcessor] = [
            GoogleLLMService(
                api_key=self._key("google"),
                settings=GoogleLLMService.Settings(
                    model=model, temperature=p.temperature, system_instruction=instruction
                ),
            )
            for model in models
        ]
        for service in llms:
            assert isinstance(service, GoogleLLMService)
            service.register_function("end_call", self._end_call)
        llm = ServiceSwitcher(services=llms, strategy_type=ServiceSwitcherStrategyFailover)

        async def on_switched(_: Any, service: Any) -> None:
            await self.recorder.event(
                CallEventType.ERROR,
                "Main AI model failed; switched to the backup model",
                data={"model": getattr(service, "model_name", None)},
            )

        llm.strategy.add_event_handler("on_service_switched", on_switched)
        tts = SarvamTTSService(
            api_key=self._key("sarvam"),
            sample_rate=self.setup.output_sample_rate,
            settings=SarvamTTSService.Settings(
                model="bulbul:v3",
                voice=p.voice,
                language=LANGUAGES[self.opening].pipecat,
                pace=p.speaking_pace,
            ),
        )
        context = LLMContext(messages=[], tools=tools)
        pair = LLMContextAggregatorPair(context, user_params=user_params)
        guard = LanguageGuard(
            allowed=config.languages, initial=self.opening, on_switch=on_switch, tts=tts
        )
        pipeline = Pipeline(
            [
                self.transport.input(),
                stt,
                guard,
                pair.user(),
                llm,
                tts,
                self.transport.output(),
                self.recording.processor,
                pair.assistant(),
            ]
        )
        return pipeline, context, self.setup.output_sample_rate, guard, pair

    def _live_kickoff(self) -> str:
        announcement, greeting = opening_lines(
            self.setup.config, self.setup.variables, self.opening
        )
        language = LANGUAGES[self.opening].name
        lines = ["[The phone call has just connected.]"]
        if announcement:
            lines.append(f'First say exactly, in {language}: "{announcement}"')
        if greeting:
            lines.append(f'Then say exactly: "{greeting}"')
        else:
            lines.append(f"Then greet the caller in {language} and introduce yourself briefly.")
        return " ".join(lines)

    async def _open_pipeline_mode(self, context: LLMContext) -> None:
        assert self.task is not None
        announcement, greeting = opening_lines(
            self.setup.config, self.setup.variables, self.opening
        )
        if announcement:
            await self.task.queue_frame(TTSSpeakFrame(announcement))
        if greeting:
            # Spoken text is added to the conversation by the assistant aggregator.
            await self.task.queue_frame(TTSSpeakFrame(greeting))
        else:
            language = LANGUAGES[self.opening].name
            context.add_message(
                {
                    "role": "user",
                    "content": f"[The call has connected. Greet the caller in {language} and "
                    "introduce yourself briefly.]",
                }
            )
            await self.task.queue_frame(LLMRunFrame())

    # ---- run ---------------------------------------------------------------------------

    async def run(self) -> None:
        config = self.setup.config
        status, error = CallStatus.COMPLETED, None
        try:
            pipeline, context, out_rate, guard, pair = self._build()
        except MissingCredentialError as exc:
            await self.recorder.event(CallEventType.ERROR, str(exc))
            await self.recorder.finish(
                status=CallStatus.FAILED, end_reason="setup_failed", metrics=None, error=str(exc)
            )
            raise

        self.task = PipelineTask(
            pipeline,
            params=PipelineParams(
                audio_in_sample_rate=PIPELINE_SAMPLE_RATE,
                audio_out_sample_rate=out_rate,
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
            observers=[self.metrics],
            idle_timeout_secs=config.limits.silence_timeout_seconds * 3 + 30,
            enable_rtvi=False,
        )
        task = self.task

        async def _user_turn(_: Any, message: Any) -> None:
            await self.recorder.event(
                CallEventType.USER_TURN, message.content, language=guard.current
            )

        async def _agent_turn(_: Any, message: Any) -> None:
            if message.content:
                await self.recorder.event(
                    CallEventType.AGENT_TURN,
                    message.content,
                    language=guard.current,
                    data={"interrupted": message.interrupted},
                )

        async def _idle(_: Any) -> None:
            self._idle_prompts += 1
            if self._idle_prompts > 1:
                self._ended_by = "caller_silent"
                await task.queue_frame(EndWorkerFrame(reason="caller silent"))
                return
            await task.queue_frame(
                LLMMessagesAppendFrame(
                    messages=[
                        {
                            "role": "user",
                            "content": "[The caller has been silent for a while. Briefly ask, in "
                            "the current language, whether they can hear you.]",
                        }
                    ],
                    run_llm=True,
                )
            )

        async def _disconnected(*_: Any) -> None:
            await task.cancel(reason="caller disconnected")

        pair.user().add_event_handler("on_user_turn_message_added", _user_turn)
        pair.assistant().add_event_handler("on_assistant_turn_stopped", _agent_turn)
        pair.user().add_event_handler("on_user_turn_idle", _idle)
        self.transport.add_event_handler("on_client_disconnected", _disconnected)

        async def _limit() -> None:
            await asyncio.sleep(config.limits.max_duration_seconds)
            self._ended_by = "max_duration"
            await task.queue_frame(EndWorkerFrame(reason="maximum call duration"))

        await self.recorder.mark_started(self.setup.provider_call_id)
        await self.recorder.event(
            CallEventType.STATUS,
            "Call started",
            language=self.opening,
            data={"mode": config.mode, "opening_language": self.opening},
        )
        if config.mode == "pipeline":
            await self._open_pipeline_mode(context)
        else:
            # Sends the opening instructions to Gemini so it greets without waiting for the caller.
            await task.queue_frame(LLMRunFrame())

        limit = asyncio.create_task(_limit())
        try:
            await PipelineRunner(handle_sigint=False).run(task)
        except Exception as exc:
            status, error = CallStatus.FAILED, f"{exc.__class__.__name__}: {exc}"
            self._ended_by = "error"
            log.exception("call pipeline failed", call_id=str(self.setup.call_id))
        finally:
            limit.cancel()
            await self.recorder.event(
                CallEventType.STATUS, "Call ended", data={"reason": self._ended_by}
            )
            await self.recorder.finish(
                status=status,
                end_reason=self._ended_by,
                metrics=self.metrics,
                error=error,
                recording_key=self.recording.close(),
            )
