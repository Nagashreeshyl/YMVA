"""After-call analysis: a short summary, the call's outcome and the details the agent tracks,
read from the transcript by Gemini and stored on the call."""

import asyncio
import re
import uuid
from typing import Any, Literal
from zoneinfo import ZoneInfo

from google.genai import types
from pydantic import BaseModel, Field, ValidationError, create_model
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ymva.config import get_settings
from ymva.db import get_sessionmaker
from ymva.logging import get_logger
from ymva.models import AgentVersion, Call, CallDirection, CallEvent, CallEventType, DoNotCall
from ymva.services import callback
from ymva.services.builder import ask_gemini
from ymva.services.integrations import load_ai_keys
from ymva.services.tools import call_facts, load_tools, run_tool
from ymva.voice.config import AgentConfig, Analysis

log = get_logger(__name__)

NO_CONVERSATION = "No conversation"
OTHER = "Other"

SYSTEM = """\
You read the transcript of a phone call between a voice agent from Yellow Metal (a gold loan
company in India) and a customer, and write down what happened.
- summary: one to three short, plain English sentences a manager can read in five seconds,
  whatever language the call was in. Say what the customer said and agreed to.
- outcome: the single option that fits best; "Other" only if none fits.
- details: only details the customer actually said. Leave out anything not said; never guess.
  Write dates as YYYY-MM-DD, working out words like "tomorrow" or "the 20th" from the call
  date. Keep each value short.
- do_not_call: true only if the customer clearly asked not to be called again (for example
  "don't call me again", "stop calling me"). Being busy or asking for a later call is false."""

# Calls still being analysed; holding them stops the tasks being garbage-collected.
_running: set[asyncio.Task[None]] = set()


def _result_model(analysis: Analysis) -> type[BaseModel]:
    # Built per agent so Gemini can only answer with that agent's own outcome and detail names.
    outcomes: Any = Literal[tuple(dict.fromkeys([o.name for o in analysis.outcomes] + [OTHER]))]
    fields: dict[str, Any] = {
        "summary": (str, ...),
        "outcome": (outcomes, ...),
        "do_not_call": (bool, False),
    }
    if analysis.details:
        names: Any = Literal[tuple(d.name for d in analysis.details)]
        detail: Any = create_model("Detail", name=(names, ...), value=(str, ...))
        fields["details"] = (list[detail], Field(default_factory=list))
    return create_model("CallAnalysis", **fields)


def _text(text: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=text)])


def _prompt(call: Call, analysis: Analysis, transcript: list[str]) -> str:
    when = (call.started_at or call.created_at).astimezone(ZoneInfo(get_settings().timezone))
    lines = [f"Call date: {when:%A, %d %B %Y}"]
    known = {k: v for k, v in (call.variables or {}).items() if v}
    if known:
        lines.append(
            "Customer details the agent had: " + "; ".join(f"{k}: {v}" for k, v in known.items())
        )
    lines.append("Outcome options:")
    lines += [f"- {o.name}: {o.description}" for o in analysis.outcomes]
    lines.append(f"- {OTHER}: none of the above")
    if analysis.details:
        lines.append("Details to write down if the customer said them:")
        lines += [f"- {d.name}: {d.description}" for d in analysis.details]
    lines.append("\nTranscript:")
    lines += transcript
    return "\n".join(lines)


async def _analysis(db: AsyncSession, call: Call) -> Analysis:
    """The after-call settings the call ran with. Phone calls made before calls kept their own
    copy of the settings fall back to the agent version they used."""
    raw = call.agent_config
    if raw is None and call.agent_version_id is not None:
        version = await db.get(AgentVersion, call.agent_version_id)
        raw = version.config if version else None
    try:
        return AgentConfig.model_validate(raw or {}).analysis
    except ValidationError:
        return Analysis()  # calls made before analysis existed


async def analyze_call(call_id: uuid.UUID) -> None:
    async with get_sessionmaker()() as db:
        call = await db.get(Call, call_id)
        if call is None:
            return
        events = await db.scalars(
            select(CallEvent)
            .where(
                CallEvent.call_id == call_id,
                CallEvent.type.in_([CallEventType.USER_TURN, CallEventType.AGENT_TURN]),
            )
            .order_by(CallEvent.seq)
        )
        transcript = [
            f"{'Customer' if e.type == CallEventType.USER_TURN else 'Agent'}: {e.content}"
            for e in events
        ]
        if not any(line.startswith("Customer:") for line in transcript):
            call.outcome = NO_CONVERSATION
            call.summary = "The customer didn't say anything."
            call.extracted = {}
            await db.commit()
            return
        analysis = await _analysis(db, call)
        keys = await load_ai_keys(db)
        if "google" not in keys:
            return
        model = _result_model(analysis)
        try:
            result = await ask_gemini(
                keys["google"],
                SYSTEM,
                [
                    types.Content(
                        role="user", parts=[types.Part(text=_prompt(call, analysis, transcript))]
                    )
                ],
                model,
            )
        except Exception as exc:
            log.warning("call analysis failed", call_id=str(call_id), error=str(exc)[:300])
            return
        data = result.model_dump()
        call.summary = data["summary"].strip()[:2000]
        call.outcome = data["outcome"]
        call.extracted = {
            d["name"]: d["value"].strip() for d in data.get("details", []) if d["value"].strip()
        }
        phone = call.to_number if call.direction == CallDirection.OUTBOUND else call.from_number
        if data["do_not_call"] and call.direction != CallDirection.WEB and phone:
            await db.execute(
                insert(DoNotCall)
                .values(
                    phone=phone,
                    reason="Asked on a call not to be called again",
                    pending=True,  # an admin approves it in Settings -> Do not call
                    call_id=call.id,
                )
                .on_conflict_do_nothing()  # already listed (approved or waiting)
            )
        await db.commit()


def field_name(detail: str) -> str:
    """'Promised payment date' -> promised_payment_date, for {{placeholders}}."""
    return re.sub(r"[^a-z0-9]+", "_", detail.lower()).strip("_")


def result_values(call: Call, analysis: Analysis) -> dict[str, str]:
    """What a post-call tool can use: customer details, call facts and the results."""
    when = (call.started_at or call.created_at).astimezone(ZoneInfo(get_settings().timezone))
    phone = call.to_number if call.direction == CallDirection.OUTBOUND else call.from_number
    values = {k: str(v) for k, v in (call.variables or {}).items()}
    values |= call_facts(when, phone)
    values |= {
        "agent_name": call.agent_name or "",
        "call_id": str(call.id),
        "duration_seconds": str(call.duration_seconds or 0),
        "outcome": call.outcome or "",
        "summary": call.summary or "",
    }
    # Every detail the agent tracks, blank when the customer didn't give it.
    values |= {field_name(d.name): call.extracted.get(d.name, "") for d in analysis.details}
    return values


async def send_results(call_id: uuid.UUID) -> None:
    """Run the agent's after-call tools with the call's results; each run is logged on the call."""
    async with get_sessionmaker()() as db:
        call = await db.get(Call, call_id)
        if call is None or call.outcome is None:
            return
        analysis = await _analysis(db, call)
        tools = await load_tools(db, analysis.send_to, analysis.details)
        values = result_values(call, analysis)
        seq = await db.scalar(select(func.max(CallEvent.seq)).where(CallEvent.call_id == call_id))
        # What each tool remembered during the call (e.g. the sheet row it added), so the
        # results complete that row instead of adding a second one.
        memories: dict[str, dict[str, Any]] = {}
        for event in await db.scalars(
            select(CallEvent)
            .where(CallEvent.call_id == call_id, CallEvent.type == CallEventType.TOOL_RESULT)
            .order_by(CallEvent.seq)
        ):
            if isinstance((event.data or {}).get("memory"), dict):
                memories[event.data["tool"]] = dict(event.data["memory"])
        for tool in tools:
            result = await run_tool(
                tool.type, tool.config, tool.secrets, values, memories.get(tool.name, {})
            )
            seq = (seq or 0) + 1
            db.add(
                CallEvent(
                    call_id=call_id,
                    seq=seq,
                    type=CallEventType.TOOL_RESULT,
                    content=f"Sent results to {tool.name}: "
                    + ("ok" if result.get("ok") else str(result.get("error", "failed"))),
                    data={"tool": tool.name, "after_call": True, "result": result},
                )
            )
        await db.commit()


async def _after_call(call_id: uuid.UUID) -> None:
    await analyze_call(call_id)
    await send_results(call_id)
    await callback.send_callback(call_id)


def schedule(call_id: uuid.UUID) -> None:
    """Analyse the call in the background, after it has ended, then send the results on."""
    task = asyncio.create_task(_after_call(call_id))
    _running.add(task)
    task.add_done_callback(_running.discard)
