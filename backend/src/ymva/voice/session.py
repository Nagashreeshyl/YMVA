"""Runs one call: builds the Pipecat pipeline for the agent's mode and drives it to the end."""

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from google.genai.types import EndSensitivity, StartSensitivity, ThinkingConfig
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
    TTSAudioRawFrame,
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
from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService, GeminiVADParams
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.services.tts_service import TextAggregationMode
from pipecat.transports.base_transport import BaseTransport
from pipecat.turns.user_start import VADUserTurnStartStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from ymva.config import get_settings
from ymva.logging import get_logger
from ymva.models import CallEventType, CallStatus
from ymva.services import analysis
from ymva.services.tools import LoadedTool, call_facts, run_tool
from ymva.telephony.plivo_family import CheckpointSerializer
from ymva.voice import greeting
from ymva.voice.ambience import AmbienceMixer
from ymva.voice.config import AgentConfig
from ymva.voice.gemini_live import ResilientGeminiLive
from ymva.voice.guard import LanguageGuard
from ymva.voice.languages import LANGUAGES
from ymva.voice.live_state import ConversationState
from ymva.voice.playback import PlaybackGuard
from ymva.voice.prompts import (
    GOODBYES,
    opening_language,
    opening_lines,
    system_instruction,
)
from ymva.voice.recorder import CallRecorder, ErrorReporter, MetricsObserver
from ymva.voice.recording import CallRecording
from ymva.voice.sarvam_voice import SarvamVoice
from ymva.voice.trim import LeadingSilenceTrimmer
from ymva.voice.turns import turn_stop_strategy

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


def transfer_schema(when: str) -> FunctionSchema:
    return FunctionSchema(
        name="transfer_call",
        description=(
            f"Connect the caller to a human colleague by phone. Use it when: {when}. First "
            "tell the caller in one short sentence that you are connecting them to a "
            "colleague, then call this. If it fails, say a colleague will call them back soon."
        ),
        properties={
            "reason": {"type": "string", "description": "Why, briefly, for the colleague."}
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
    # Call atmosphere mixer, shared with the output transport (see make_mixer).
    mixer: AmbienceMixer | None = None
    provider_call_id: str | None = None
    tools: list[LoadedTool] = field(default_factory=list)
    customer_phone: str | None = None
    # Phone calls: moves the call to the agent's transfer number (raises TelephonyError).
    transfer: Callable[[], Awaitable[None]] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def make_mixer(config: AgentConfig) -> AmbienceMixer | None:
    a = config.atmosphere
    if not (a.room_tone or a.typing_while_thinking) or a.volume == 0:
        return None
    return AmbienceMixer(room_tone=a.room_tone, typing=a.typing_while_thinking, volume=a.volume)


# How long the opening may wait for the prepared greeting before speaking it the normal way.
GREETING_WAIT_SECS = 3.0
# Gemini Live greets by itself in about a second, so waiting longer for the prepared audio
# would only add silence.
LIVE_GREETING_WAIT_SECS = 1.0
# After the goodbye, the caller must stay quiet this long before the agent hangs up.
HANG_UP_GRACE_SECS = 1.5
# Characters Sarvam collects before it starts speaking (default 50; 30 is its minimum), so the
# voice starts after the model's first few words instead of a whole sentence later.
TTS_START_CHARS = 30
LLM_RETRY_AFTER_SECS = 2.0
# Gemini Live: caller finished but no reply or tool use for this long counts as a stall.
STALL_SECS = 5.0
MAX_STALL_RECOVERIES = 3
LLM_GIVE_UP_AFTER_SECS = 5.0


def no_thinking(model: str) -> Any:
    """Thinking switched off (Gemini 3 models: their "minimal" level, which skips it)."""
    if model.startswith("gemini-2"):
        return GoogleLLMService.ThinkingConfig(thinking_budget=0, include_thoughts=False)
    return GoogleLLMService.ThinkingConfig(thinking_level="minimal", include_thoughts=False)


# Who decides the caller has finished in Gemini Live: "gemini" (its built-in detection, inside
# the model) or "ymva" (our voice detection + end-of-turn model, as in Standard mode).
# Measured 28 Oct 2026, same simulated calls (Kannada, a short "haan", a switch to Hindi):
# ymva heard "ಹೌದು, ನಾನೇ..." as "How do", missed "haan", replied in English, worst reply 16 s;
# gemini heard every line in its own language, replied in it, typical 1.0-1.3 s, worst 2.7 s.
LIVE_TURN_DETECTION = "gemini"
# Silence that ends the caller's turn. 300 ms with high start sensitivity was worse: it misheard
# Kannada and answered in English.
LIVE_SILENCE_MS = 400


def live_vad() -> GeminiVADParams:
    if LIVE_TURN_DETECTION == "gemini":
        return GeminiVADParams(
            disabled=False,
            start_sensitivity=StartSensitivity.START_SENSITIVITY_LOW,  # background stays out
            end_sensitivity=EndSensitivity.END_SENSITIVITY_HIGH,
            prefix_padding_ms=200,
            silence_duration_ms=LIVE_SILENCE_MS,
        )
    return GeminiVADParams(disabled=True)


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
        self._ending: asyncio.Task[None] | None = None
        self.conversation = ConversationState(
            setup.mixer, self._emit if setup.send_live_events else None
        )
        self._idle_prompts = 0
        self._live_llm: Any = None
        self._guard: LanguageGuard | None = None
        self._transferring = False
        # Tool runs in this call: a repeat with the same details is not run again, and each
        # tool's memory lets a second save update the row this call added (see run_tool).
        self._tool_results: dict[tuple[str, str], dict[str, Any]] = {}
        self._tool_memory: dict[str, dict[str, Any]] = {}
        self._tool_lock = asyncio.Lock()

    async def _warm_up_llm(self) -> None:
        """Open the connection to the model while the greeting plays, so the first reply
        doesn't pay for connection setup (measured: ~1-1.8 s cold vs ~0.2-0.6 s warm)."""
        llm = getattr(self, "_warm_llm", None)
        client = getattr(llm, "_client", None)
        if client is None:
            return
        try:
            await client.aio.models.generate_content(
                model=self.setup.config.pipeline.llm_model,
                contents="Reply with OK.",
                config={"max_output_tokens": 1},
            )
        except Exception as exc:  # warm-up is best effort
            log.debug("llm warm-up failed", error=str(exc))

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
        await self.recorder.event(
            CallEventType.TOOL_CALL,
            "end_call",
            data={"tool": "end_call", "arguments": params.arguments},
        )
        await params.result_callback(
            {"status": "ending call"}, properties=FunctionCallResultProperties(run_llm=False)
        )
        if self._ending is None or self._ending.done():
            self._ending = asyncio.create_task(self._hang_up_after_goodbye(reason))

    @property
    def _can_transfer(self) -> bool:
        t = self.setup.config.transfer
        return t.enabled and t.number is not None and self.setup.transfer is not None

    async def _transfer_call(self, params: FunctionCallParams) -> None:
        assert self.task is not None and self.setup.transfer is not None
        if self._transferring:  # the caller asked again while it was already under way
            await params.result_callback(
                {"ok": True}, properties=FunctionCallResultProperties(run_llm=False)
            )
            return
        self._transferring = True
        phone = self.setup.config.transfer.number
        await self.recorder.event(
            CallEventType.TOOL_CALL,
            "transfer_call",
            data={"tool": "transfer_call", "arguments": params.arguments, "to": phone},
        )
        await asyncio.sleep(1.0)  # the "connecting you" sentence may still be on its way
        for _ in range(150):  # let the agent finish speaking, up to 15 s
            if not self.conversation.bot_speaking:
                break
            await asyncio.sleep(0.1)
        try:
            await self.setup.transfer()
        except Exception as exc:
            log.warning("transfer failed", call_id=str(self.setup.call_id), error=str(exc))
            await self.recorder.event(CallEventType.ERROR, f"Transfer failed: {exc}"[:300])
            self._transferring = False
            await params.result_callback(
                {"ok": False, "error": "The transfer didn't go through. Tell the caller a "
                 "colleague will call them back soon."}
            )  # fmt: skip
            return
        await self.recorder.event(CallEventType.STATUS, f"Transferred to {phone}")
        self._ended_by = "transferred"
        await params.result_callback(
            {"ok": True}, properties=FunctionCallResultProperties(run_llm=False)
        )
        # The provider closes the audio stream once the transfer starts; end ours regardless.
        await asyncio.sleep(5)
        await self.task.queue_frame(EndWorkerFrame(reason="transferred"))

    async def _watch_for_stalls(self) -> None:
        """Gemini Live: if the caller has finished and nothing happens for a while, recover."""
        recoveries = 0
        while recoveries < MAX_STALL_RECOVERIES:
            await asyncio.sleep(0.5)
            state = self.conversation
            quiet_for = time.monotonic() - state.last_activity
            if state.state == "thinking" and not state.bot_speaking and quiet_for > STALL_SECS:
                recoveries += 1
                state.last_activity = time.monotonic()  # one recovery per silence
                await self._live_llm.recover_stall()

    async def _run_tool(self, params: FunctionCallParams) -> None:
        tool = next(t for t in self.setup.tools if t.name == params.function_name)
        args = {k: str(v) for k, v in (params.arguments or {}).items() if v is not None}
        await self.recorder.event(
            CallEventType.TOOL_CALL, tool.name, data={"tool": tool.name, "arguments": args}
        )
        now = datetime.now(ZoneInfo(get_settings().timezone))
        values = call_facts(now, self.setup.customer_phone) | self.setup.variables | args
        same = (tool.name, json.dumps({k: v.strip().casefold() for k, v in args.items()},
                                      sort_keys=True))  # fmt: skip
        # One tool run at a time per call, so two quick identical requests can't both run.
        async with self._tool_lock:
            done = self._tool_results.get(same)
            if done is not None and done.get("ok"):
                await self.recorder.event(
                    CallEventType.STATUS,
                    f"Didn't run {tool.name} again: same details as earlier in this call",
                )
                await params.result_callback(
                    {**done, "note": "Already done earlier in this call with these same "
                     "details, so it wasn't run again. Don't repeat it; carry on."}
                )  # fmt: skip
                return
            memory = self._tool_memory.setdefault(tool.name, {})
            result = await run_tool(tool.type, tool.config, tool.secrets, values, memory)
            self._tool_results[same] = result
        await self.recorder.event(
            CallEventType.TOOL_RESULT,
            "ok" if result.get("ok") else str(result.get("error", "failed")),
            data={"tool": tool.name, "result": result, "memory": dict(memory)},
        )
        await params.result_callback(result)

    async def _hang_up_after_goodbye(self, reason: str) -> None:
        """Models sometimes call end_call too early. Let the agent finish speaking, then hang
        up only if the caller stays quiet for a moment; if they start talking, keep going."""
        assert self.task is not None
        await asyncio.sleep(1.0)  # the goodbye may still be on its way to the speaker
        if (
            not self.conversation.agent_replied
            and self.setup.config.mode == "pipeline"
            and self._guard is not None
        ):
            # The model hung up without a word; never drop the caller in silence.
            await self.task.queue_frame(TTSSpeakFrame(GOODBYES[self._guard.current]))
            await asyncio.sleep(1.0)
        for _ in range(300):  # up to 30 s of agent speech
            if self.conversation.state != "agent_speaking":
                break
            await asyncio.sleep(0.1)
        for _ in range(int(HANG_UP_GRACE_SECS * 10)):
            if self.conversation.state == "user_speaking":
                await self.recorder.event(
                    CallEventType.STATUS, "Kept the call open: the caller was still talking"
                )
                return
            await asyncio.sleep(0.1)
        self._ended_by = "agent_ended"
        await self.task.queue_frame(EndWorkerFrame(reason=reason))

    # ---- pipeline construction ---------------------------------------------------------

    def _playback_guard(self) -> list[FrameProcessor]:
        """Vobiz/Plivo calls: keep the phone's playback queue from falling behind."""
        serializer = self.setup.extra.get("serializer")
        if not isinstance(serializer, CheckpointSerializer):
            return []

        async def resynced(lag: float) -> None:
            await self.recorder.event(
                CallEventType.STATUS, f"The phone was {lag:.1f} s behind the agent; caught up"
            )

        return [PlaybackGuard(serializer, resynced)]

    def _switchable(self) -> list[str]:
        """Languages the call may switch to: only the opening one when the agent keeps to it."""
        config = self.setup.config
        return config.languages if config.follow_caller_language else [self.opening]

    def _build(self) -> tuple[Pipeline, LLMContext, int, LanguageGuard, Any]:
        config = self.setup.config
        variables = self.setup.variables
        self.opening = opening_language(config, variables)
        instruction = system_instruction(config, variables, self.opening, get_settings().timezone)
        transfer = [transfer_schema(config.transfer.when)] if self._can_transfer else []
        tools = ToolsSchema(
            standard_tools=[END_CALL, *transfer, *(t.schema() for t in self.setup.tools)]
        )

        async def on_switch(previous: str | None, current: str) -> None:
            await self.recorder.event(
                CallEventType.LANGUAGE_SWITCH,
                f"{LANGUAGES[previous].name if previous else '—'} → {LANGUAGES[current].name}",
                language=current,
                data={"from": previous, "to": current},
            )

        vad = SileroVADAnalyzer(params=VADParams(stop_secs=0.2))
        turn_end = turn_stop_strategy()
        user_params = LLMUserAggregatorParams(
            vad_analyzer=vad,
            user_idle_timeout=config.limits.silence_timeout_seconds,
            user_turn_strategies=UserTurnStrategies(
                # With Gemini's detection in charge, ours only marks turns for the transcript:
                # two detectors both interrupting made Gemini cancel and restart replies (2-5 s).
                start=[VADUserTurnStartStrategy(enable_interruptions=False)]
                if config.mode == "gemini_live" and LIVE_TURN_DETECTION == "gemini"
                else None,
                stop=[turn_end],
            ),
        )

        if config.mode == "gemini_live":
            live = config.gemini_live
            llm: Any = ResilientGeminiLive(
                api_key=self._key("google"),
                was_replying=lambda: (
                    self.conversation.state in ("thinking", "using_tool", "agent_speaking")
                ),
                settings=GeminiLiveLLMService.Settings(
                    model=live.model,
                    voice=live.voice,
                    temperature=live.temperature,
                    system_instruction=instruction,
                    # Native-audio models choose their reply language themselves; forcing a
                    # language code locks them into it, so it is deliberately not sent.
                    language=None,  # type: ignore[arg-type]
                    vad=live_vad(),
                    # No reasoning before speaking: replies start about 0.2 s sooner. (Adding
                    # include_thoughts=False here stops Gemini answering the caller at all.)
                    thinking=ThinkingConfig(thinking_budget=0),
                ),
                tools=tools,
                # Audio from just before our VAD notices speech; Pipecat's 0.3 s clipped short
                # answers ("haudu" was heard as "audio").
                user_audio_preroll_secs=0.6,
                # The opening decides whether Gemini speaks first (see _open_live_mode).
                inference_on_context_initialization=False,
            )
            llm.register_function("end_call", self._end_call)
            # The caller often keeps talking ("connect me!") while it starts; finish it anyway.
            llm.register_function(
                "transfer_call", self._transfer_call, cancel_on_interruption=False
            )
            for tool in self.setup.tools:
                llm.register_function(tool.name, self._run_tool)
            self._live_llm = llm
            context = LLMContext(messages=[])
            pair = LLMContextAggregatorPair(context, user_params=user_params)
            guard = LanguageGuard(
                allowed=self._switchable(), initial=self.opening, on_switch=on_switch,
                gemini_live=llm, auto=self.setup.config.follow_caller_language,
            )  # fmt: skip
            pipeline = Pipeline(
                [
                    self.transport.input(),
                    pair.user(),
                    # Gemini sends what the caller said back upstream, so the guard must sit
                    # before the model to see it.
                    guard,
                    llm,
                    LeadingSilenceTrimmer(),  # Gemini's voice can start with a beat of silence
                    *self._playback_guard(),
                    self.transport.output(),
                    self.recording.processor,
                    pair.assistant(),
                ]
            )
            return pipeline, context, GEMINI_OUTPUT_SAMPLE_RATE, guard, pair

        p = config.pipeline
        stt = SarvamSTTService(
            api_key=self._key("sarvam"),
            sample_rate=PIPELINE_SAMPLE_RATE,
            # "codemix" keeps English words in English letters inside Kannada/Telugu/... text,
            # the way people actually speak.
            mode="codemix" if config.speaking_style == "everyday_mix" else "transcribe",
        )
        # Main model plus a backup that takes over automatically if the main one fails.
        models = list(dict.fromkeys([p.llm_model, p.backup_llm_model]))
        llms: list[FrameProcessor] = [
            GoogleLLMService(
                api_key=self._key("google"),
                settings=GoogleLLMService.Settings(
                    model=model,
                    temperature=p.temperature,
                    system_instruction=instruction,
                    thinking=no_thinking(model),
                ),
                # Google occasionally takes 5-15 s to start an answer it normally starts in
                # under a second. Ask again after 2 s; if that stalls too, the error hands the
                # turn to the backup model.
                retry_on_timeout=True,
                retry_timeout_secs=LLM_RETRY_AFTER_SECS,
                stream_idle_timeout_secs=LLM_GIVE_UP_AFTER_SECS,
            )
            for model in models
        ]
        for service in llms:
            assert isinstance(service, GoogleLLMService)
            service.register_function("end_call", self._end_call)
            # The caller often keeps talking ("connect me!") while it starts; finish it anyway.
            service.register_function(
                "transfer_call", self._transfer_call, cancel_on_interruption=False
            )
            for tool in self.setup.tools:
                service.register_function(tool.name, self._run_tool)
        self._warm_llm = llms[0]
        llm = ServiceSwitcher(services=llms, strategy_type=ServiceSwitcherStrategyFailover)

        async def on_switched(_: Any, service: Any) -> None:
            await self.recorder.event(
                CallEventType.ERROR,
                "Main AI model failed; switched to the backup model",
                data={"model": getattr(service, "model_name", None)},
            )

        llm.strategy.add_event_handler("on_service_switched", on_switched)
        tts = SarvamVoice(
            api_key=self._key("sarvam"),
            sample_rate=self.setup.output_sample_rate,
            # Stream the model's words to the voice as they arrive instead of waiting for a
            # full sentence.
            text_aggregation_mode=TextAggregationMode.TOKEN,
            settings=SarvamTTSService.Settings(
                model="bulbul:v3",
                voice=p.voice,
                language=LANGUAGES[self.opening].pipecat,
                pace=p.speaking_pace,
                min_buffer_size=TTS_START_CHARS,
            ),
        )
        context = LLMContext(messages=[], tools=tools)
        pair = LLMContextAggregatorPair(context, user_params=user_params)
        guard = LanguageGuard(
            allowed=self._switchable(), initial=self.opening, on_switch=on_switch, tts=tts,
            auto=self.setup.config.follow_caller_language,
        )  # fmt: skip
        pipeline = Pipeline(
            [
                self.transport.input(),
                stt,
                guard,
                pair.user(),
                llm,
                tts,
                LeadingSilenceTrimmer(),
                *self._playback_guard(),
                self.transport.output(),
                self.recording.processor,
                pair.assistant(),
            ]
        )
        return pipeline, context, self.setup.output_sample_rate, guard, pair

    async def _open_live_mode(self, context: LLMContext) -> None:
        assert self.task is not None
        ready = await greeting.take(self.setup.call_id, wait_seconds=LIVE_GREETING_WAIT_SECS)
        if ready is not None:
            await self._play_prepared(ready, context)
            # Gives Gemini the conversation so far (its own greeting) without a new reply.
            await self.task.queue_frame(LLMRunFrame())
            return
        # Gemini Live only reacts to the context it starts with; messages added afterwards are
        # ignored. So the request to greet goes into the first context, with a reply asked for.
        context.add_message({"role": "user", "content": self._live_kickoff()})
        self._live_llm._inference_on_context_initialization = True
        await self.task.queue_frame(LLMRunFrame())

    async def _play_prepared(self, ready: greeting.GreetingAudio, context: LLMContext) -> None:
        assert self.task is not None
        chunk = ready.sample_rate * 2 // 50  # 20 ms
        await self.task.queue_frames(
            [
                TTSAudioRawFrame(
                    audio=ready.pcm[i : i + chunk], sample_rate=ready.sample_rate, num_channels=1
                )
                for i in range(0, len(ready.pcm), chunk)
            ]
        )
        context.add_message({"role": "assistant", "content": ready.text})
        await self.recorder.event(CallEventType.AGENT_TURN, ready.text, language=ready.language)

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
        # Prepared in advance (while ringing / connecting): play it as soon as audio starts.
        ready = await greeting.take(self.setup.call_id, wait_seconds=GREETING_WAIT_SECS)
        if ready is not None:
            await self._play_prepared(ready, context)
            return

        announcement, greeting_text = opening_lines(
            self.setup.config, self.setup.variables, self.opening
        )
        if announcement:
            await self.task.queue_frame(TTSSpeakFrame(announcement))
        if greeting_text:
            # Spoken text is added to the conversation by the assistant aggregator.
            await self.task.queue_frame(TTSSpeakFrame(greeting_text))
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
            self._guard = guard
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
            observers=[self.metrics, self.conversation, ErrorReporter(self.recorder)],
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
        warm_up: asyncio.Task[None] | None = None
        watchdog: asyncio.Task[None] | None = None
        opening: asyncio.Task[None] | None = None
        if config.mode == "pipeline":
            # Runs alongside pipeline start-up, so waiting for the prepared greeting costs nothing.
            opening = asyncio.create_task(self._open_pipeline_mode(context))
            warm_up = asyncio.create_task(self._warm_up_llm())
        else:
            # Gemini greets first: with the prepared greeting if ready, otherwise by itself.
            opening = asyncio.create_task(self._open_live_mode(context))
            # Only when we decide turn ends: with Gemini's detection, "the caller has finished"
            # is Gemini's call, and reconnecting while it still listens loses what was said.
            if LIVE_TURN_DETECTION == "ymva":
                watchdog = asyncio.create_task(self._watch_for_stalls())

        limit = asyncio.create_task(_limit())
        try:
            await PipelineRunner(handle_sigint=False).run(task)
        except Exception as exc:
            status, error = CallStatus.FAILED, f"{exc.__class__.__name__}: {exc}"
            self._ended_by = "error"
            log.exception("call pipeline failed", call_id=str(self.setup.call_id))
        finally:
            limit.cancel()
            for background in (warm_up, watchdog, opening, self._ending):
                if background is not None:
                    background.cancel()
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
            analysis.schedule(self.setup.call_id)
