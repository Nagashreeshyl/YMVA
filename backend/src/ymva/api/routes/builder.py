"""Build with AI: create an agent from a description, and the assistant chat that edits it."""

import re
import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from ymva.api.deps import AgentEditor, AgentViewer, DbSession, client_ip
from ymva.api.routes.agents import AgentDetail, _detail, _get_agent, _latest
from ymva.config import get_settings
from ymva.models import (
    Agent,
    AgentAssistantMessage,
    AgentVersion,
    Call,
    CallEvent,
    CallEventType,
    Tool,
)
from ymva.permissions import Perm, can
from ymva.redis import get_redis
from ymva.security import decrypt_json
from ymva.services import audit, ratelimit
from ymva.services.builder import (
    INTERVIEWER_NAME,
    BuilderError,
    ToolRequest,
    ToolTest,
    brief_from_interview,
    edit_agent,
    generate_agent,
)
from ymva.services.integrations import load_ai_keys
from ymva.services.tool_builder import apply_draft, draft_tool, missing, tool_by_name
from ymva.services.tools import call_facts, run_tool
from ymva.voice.config import AgentConfig, call_details
from ymva.voice.templates import blank

router = APIRouter(prefix="/agents", tags=["builder"])

# Each request is a paid Gemini call; this is generous for one person editing.
BUILDER_REQUESTS_PER_HOUR = 120
# How much of the chat the assistant sees, so long conversations stay quick and cheap.
HISTORY_MESSAGES = 16


class GenerateIn(BaseModel):
    # Either a written description, or the builder interview call it was talked through in.
    description: str | None = Field(default=None, min_length=10, max_length=6000)
    call_id: uuid.UUID | None = None
    name: str | None = Field(default=None, max_length=120)


async def _interview_brief(db: DbSession, call_id: uuid.UUID, admin: AgentEditor) -> str:
    call = await db.get(Call, call_id)
    if call is None or call.agent_name != INTERVIEWER_NAME or call.placed_by_id != admin.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    events = await db.scalars(
        select(CallEvent)
        .where(
            CallEvent.call_id == call_id,
            CallEvent.type.in_([CallEventType.USER_TURN, CallEventType.AGENT_TURN]),
        )
        .order_by(CallEvent.seq)
    )
    lines = [
        ("Admin" if e.type == CallEventType.USER_TURN else "Interviewer", e.content) for e in events
    ]
    if not any(who == "Admin" for who, _ in lines):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Nothing was said about the agent. Talk it through first."
        )
    return brief_from_interview(lines)


class AssistantIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class AssistantMessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    changes: list[str]
    can_undo: bool
    undone: bool
    created_at: datetime


class AssistantTurnOut(BaseModel):
    messages: list[AssistantMessageOut]
    agent: AgentDetail


async def _google_key(db: DbSession, admin: AgentEditor) -> str:
    if not await ratelimit.hit(get_redis(), f"builder:{admin.id}", BUILDER_REQUESTS_PER_HOUR, 3600):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many AI requests. Try again later."
        )
    keys = await load_ai_keys(db)
    if "google" not in keys:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Add the Google (Gemini) API key in Integrations first"
        )
    return keys["google"]


def _current_config(agent: Agent, latest: AgentVersion) -> dict[str, Any]:
    return agent.draft_config if agent.draft_config is not None else latest.config


def _out(message: AgentAssistantMessage, latest_change_id: uuid.UUID | None) -> AssistantMessageOut:
    return AssistantMessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        changes=message.changes,
        # Only the most recent change can be undone, so undo never skips over later edits.
        can_undo=message.id == latest_change_id and message.undone_at is None,
        undone=message.undone_at is not None,
        created_at=message.created_at,
    )


async def _messages(db: DbSession, agent_id: uuid.UUID) -> list[AgentAssistantMessage]:
    return list(
        await db.scalars(
            select(AgentAssistantMessage)
            .where(AgentAssistantMessage.agent_id == agent_id)
            .order_by(AgentAssistantMessage.created_at, AgentAssistantMessage.role.desc())
        )
    )


def _latest_change(messages: list[AgentAssistantMessage]) -> uuid.UUID | None:
    changed = [m for m in messages if m.config_after is not None]
    return changed[-1].id if changed else None


@router.post("/generate", response_model=AgentDetail, status_code=status.HTTP_201_CREATED)
async def generate(
    body: GenerateIn, request: Request, admin: AgentEditor, db: DbSession
) -> AgentDetail:
    """Write a new agent from a plain-English description."""
    if body.call_id is not None:
        brief = await _interview_brief(db, body.call_id, admin)
    elif body.description:
        brief = body.description
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Describe the agent first")
    key = await _google_key(db, admin)
    try:
        spec, config = await generate_agent(brief, key, blank())
    except BuilderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    name = (body.name or "").strip() or spec.name.strip()[:120] or "New agent"
    agent = Agent(name=name, description=spec.description[:2000], created_by_id=admin.id)
    db.add(agent)
    await db.flush()
    db.add(
        AgentVersion(
            agent_id=agent.id,
            version=1,
            config=config.model_dump(mode="json"),
            note="Built with AI",
            source="ai",
            created_by_id=admin.id,
        )
    )
    # The brief starts the assistant chat, so later edits can refer back to it.
    db.add(
        AgentAssistantMessage(agent_id=agent.id, role="user", content=brief, created_by_id=admin.id)
    )
    await db.flush()
    db.add(
        AgentAssistantMessage(
            agent_id=agent.id,
            role="assistant",
            content=f"I built {name}. Test it in the browser, then tell me what to change.",
        )
    )
    await audit.record(
        db,
        actor=admin,
        action="agent.create",
        entity_type="agent",
        entity_id=agent.id,
        summary=f"Built agent '{agent.name}' with AI",
        ip_address=client_ip(request),
    )
    await db.commit()
    return await _detail(db, agent)


def _tool_line(tool: Tool) -> str:
    needs = missing(tool)
    state = f"still needs: {', '.join(needs)}" if needs else "ready"
    return f"- {tool.name} ({tool.type.value}, {state}): {tool.description}"


NEEDS_WORDS = {
    "link": "paste the Google Sheet's link here",
    "address": "tell me the API's address",
    "key": "add its key in the secure field below the chat",
}


async def _make_tools(
    db: DbSession,
    admin: AgentEditor,
    request: Request,
    key: str,
    requests: list[ToolRequest],
    config: AgentConfig,
) -> tuple[AgentConfig, list[str], list[str]]:
    """Create or change the tools YMA asked for and switch them on for this agent. Returns the
    agent with its tools, a change line per tool, and what the admin must still give."""
    if not can(admin.role, Perm.TOOLS_MANAGE):
        return config, [], ["Setting up tools needs an admin; ask one to add it on the Tools page."]
    names, changes, notes = list(config.tools), [], []
    after = list(config.analysis.send_to)
    known = [d.name for d in call_details(config)]
    for asked in requests[:5]:
        name = re.sub(r"[^a-z0-9_]+", "_", asked.name.strip().lower()).strip("_")[:64]
        tool = await tool_by_name(db, name) if name else None
        text = asked.request
        if asked.when == "after_call":
            text += (
                "\n\nThis tool runs after the call to record its results: no params; use "
                "{{summary}}, {{outcome}} and the call's details."
            )
        try:
            draft = await draft_tool(key, text, known, tool)
        except BuilderError:
            notes.append(f"I couldn't set up the {name or 'new'} tool; try describing it again.")
            continue
        if tool is None:
            # The name YMA chose is the one its instructions tell the agent to use.
            draft["name"] = name or re.sub(r"[^a-z0-9_]+", "_", draft["name"]).strip("_")[:60]
            if not re.match(r"^[a-z][a-z0-9_]{1,63}$", draft["name"]):
                draft["name"] = f"tool_{draft['name']}"[:64].rstrip("_") or "tool"
            if await tool_by_name(db, draft["name"]) is not None:
                draft["name"] = f"{draft['name'][:58]}_{uuid.uuid4().hex[:5]}"
            tool = Tool(created_by_id=admin.id)
            apply_draft(tool, draft)
            db.add(tool)
            await db.flush()
            changes.append(f"Tool created: {tool.name}")
            action = "tool.create"
        else:
            apply_draft(tool, draft)  # its key, if any, is kept
            changes.append(f"Tool updated: {tool.name}")
            action = "tool.update"
        await audit.record(
            db,
            actor=admin,
            action=action,
            entity_type="tool",
            entity_id=tool.id,
            summary=f"YMA set up tool '{tool.name}' for agent use",
            ip_address=client_ip(request),
        )
        during, other = (names, after) if asked.when == "during_call" else (after, names)
        if tool.name not in during:
            during.append(tool.name)
        if tool.name in other:
            other.remove(tool.name)
        if needs := missing(tool):
            todo = " and ".join(NEEDS_WORDS[n] for n in needs)
            notes.append(f"To finish {tool.name}, {todo}; it stays off until then.")
    analysis = config.analysis.model_copy(update={"send_to": after})
    return config.model_copy(update={"tools": names, "analysis": analysis}), changes, notes


async def _drop_tools(
    db: DbSession, names: list[str], config: AgentConfig
) -> tuple[AgentConfig, list[str]]:
    """Stop this agent using the tools. One that was never finished and no other agent uses
    is deleted; a working tool stays on the Tools page for later."""
    kept, after, removed = list(config.tools), list(config.analysis.send_to), []
    used = await _tools_in_use(db)
    for name in names:
        if name not in kept and name not in after:
            continue
        for where in (kept, after):
            if name in where:
                where.remove(name)
        removed.append(f"Tool removed: {name}")
        tool = await tool_by_name(db, name)
        if tool is not None and missing(tool) and used.get(name, 0) <= 1:
            await db.delete(tool)
    analysis = config.analysis.model_copy(update={"send_to": after})
    return config.model_copy(update={"tools": kept, "analysis": analysis}), removed


async def _tools_in_use(db: DbSession) -> dict[str, int]:
    """Tool name -> how many agents use it (draft or latest version)."""
    counts: dict[str, int] = {}
    for agent in await db.scalars(select(Agent)):
        latest = await _latest(db, agent.id)
        config = _current_config(agent, latest)
        for name in set(config.get("tools", [])) | set(
            config.get("analysis", {}).get("send_to", [])
        ):
            counts[name] = counts.get(name, 0) + 1
    return counts


async def _test_tool(db: DbSession, admin: AgentEditor, request: Request, test: ToolTest) -> str:
    """Run a tool for real because the admin asked YMA to test it."""
    tool = await tool_by_name(db, test.name)
    if tool is None:
        return f"There's no tool called {test.name} to test."
    if needs := missing(tool):
        todo = " and ".join(NEEDS_WORDS[n] for n in needs)
        return f"I can't test {tool.name} yet: {todo}."
    if not can(admin.role, Perm.TOOLS_MANAGE):
        return "Testing tools needs an admin."
    now = datetime.now(ZoneInfo(get_settings().timezone))
    values = call_facts(now, None) | {v.name: v.value for v in test.values}
    secrets = decrypt_json(tool.encrypted_secrets) if tool.encrypted_secrets else None
    outcome = await run_tool(tool.type.value, tool.config, secrets, values)
    await audit.record(
        db,
        actor=admin,
        action="tool.test",
        entity_type="tool",
        entity_id=tool.id,
        summary=f"YMA tested tool '{tool.name}'",
        changes={"ok": outcome.get("ok")},
        ip_address=client_ip(request),
    )
    if outcome.get("ok"):
        return f"Test: I ran {tool.name} and it worked."
    return f"Test: {tool.name} didn't work: {outcome.get('error', 'unknown error')}"


@router.get("/{agent_id}/assistant", response_model=list[AssistantMessageOut])
async def assistant_history(
    agent_id: uuid.UUID, _: AgentViewer, db: DbSession
) -> list[AssistantMessageOut]:
    await _get_agent(db, agent_id)
    messages = await _messages(db, agent_id)
    latest = _latest_change(messages)
    return [_out(m, latest) for m in messages]


@router.post("/{agent_id}/assistant", response_model=AssistantTurnOut)
async def assistant_turn(
    agent_id: uuid.UUID, body: AssistantIn, request: Request, admin: AgentEditor, db: DbSession
) -> AssistantTurnOut:
    """Ask the assistant something; any change it makes is saved to the draft."""
    agent = await _get_agent(db, agent_id)
    key = await _google_key(db, admin)
    latest = await _latest(db, agent.id)
    before = _current_config(agent, latest)
    history = [(m.role, m.content) for m in (await _messages(db, agent.id))[-HISTORY_MESSAGES:]]
    existing_tools = list(await db.scalars(select(Tool).order_by(Tool.name)))
    try:
        result, updated = await edit_agent(
            config=AgentConfig.model_validate(before),
            name=agent.name,
            description=agent.description,
            history=history,
            message=body.message,
            api_key=key,
            tools="\n".join(_tool_line(t) for t in existing_tools),
        )
    except BuilderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    tool_changes: list[str] = []
    tool_notes: list[str] = []
    if result.tools:
        updated, tool_changes, tool_notes = await _make_tools(
            db, admin, request, key, result.tools, updated or AgentConfig.model_validate(before)
        )
    if result.remove_tools:
        updated, removed = await _drop_tools(
            db, result.remove_tools, updated or AgentConfig.model_validate(before)
        )
        tool_changes += removed
    if result.test_tool is not None:
        tool_notes.append(await _test_tool(db, admin, request, result.test_tool))

    asked = AgentAssistantMessage(
        agent_id=agent.id, role="user", content=body.message, created_by_id=admin.id
    )
    db.add(asked)
    await db.flush()  # keeps the question ordered before the answer
    after = updated.model_dump(mode="json") if updated is not None else None
    answer = AgentAssistantMessage(
        agent_id=agent.id,
        role="assistant",
        content=" ".join([result.reply, *tool_notes]),
        changes=[*result.changes, *tool_changes] if after is not None else [],
        config_before=before if after is not None else None,
        config_after=after,
    )
    db.add(answer)
    if after is not None:
        agent.draft_config = None if after == latest.config else after
        agent.draft_updated_at = datetime.now(UTC)
        if result.agent is not None and result.agent.description.strip():
            agent.description = result.agent.description.strip()[:2000]
        await audit.record(
            db,
            actor=admin,
            action="agent.assistant_edit",
            entity_type="agent",
            entity_id=agent.id,
            summary=f"AI assistant edited '{agent.name}'",
            changes={"changes": result.changes},
            ip_address=client_ip(request),
        )
    await db.commit()
    return AssistantTurnOut(
        messages=[_out(asked, None), _out(answer, answer.id if after is not None else None)],
        agent=await _detail(db, agent),
    )


@router.post("/{agent_id}/assistant/{message_id}/undo", response_model=AgentDetail)
async def undo_assistant_change(
    agent_id: uuid.UUID, message_id: uuid.UUID, request: Request, admin: AgentEditor, db: DbSession
) -> AgentDetail:
    """Put the draft back the way it was before the assistant's latest change."""
    agent = await _get_agent(db, agent_id)
    message = await db.get(AgentAssistantMessage, message_id)
    if message is None or message.agent_id != agent.id or message.config_before is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Change not found")
    if message.id != _latest_change(await _messages(db, agent.id)) or message.undone_at:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only the latest change can be undone")
    latest = await _latest(db, agent.id)
    if _current_config(agent, latest) != message.config_after:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The agent was edited after this change, so it can't be undone automatically",
        )
    agent.draft_config = None if message.config_before == latest.config else message.config_before
    agent.draft_updated_at = datetime.now(UTC)
    message.undone_at = datetime.now(UTC)
    await audit.record(
        db,
        actor=admin,
        action="agent.assistant_undo",
        entity_type="agent",
        entity_id=agent.id,
        summary=f"Undid an AI assistant change to '{agent.name}'",
        ip_address=client_ip(request),
    )
    await db.commit()
    return await _detail(db, agent)
