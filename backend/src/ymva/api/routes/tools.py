"""Tools agents can use during calls: create, edit, test, and build from a description."""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from google.genai import types
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select

from ymva.api.deps import AdminUser, CurrentUser, DbSession, client_ip
from ymva.api.routes.agents import _latest
from ymva.models import Agent, AgentVersion, Tool, ToolType
from ymva.redis import get_redis
from ymva.security import decrypt_json, encrypt_json
from ymva.services import audit, ratelimit
from ymva.services.builder import BuilderError, ask_gemini
from ymva.services.integrations import load_ai_keys
from ymva.services.tools import (
    CONFIG_TYPES,
    RESERVED_NAMES,
    SECRET_TYPES,
    TOOL_NAME_RE,
    ToolParam,
    call_facts,
    placeholders,
    run_tool,
    secret_hint,
)
from ymva.voice.config import AgentConfig, detected_variables

router = APIRouter(prefix="/tools", tags=["tools"])

SUPPORTED = Literal["google_sheets", "http"]


class ToolIn(BaseModel):
    name: str = Field(pattern=TOOL_NAME_RE)
    description: str = Field(min_length=5, max_length=1000)
    type: SUPPORTED
    config: dict[str, Any]
    params: list[ToolParam] = Field(default_factory=list, max_length=20)
    # Omit to keep the stored secrets; send {} to remove them.
    secrets: dict[str, Any] | None = None
    is_active: bool = True
    # On create: also switch the tool on for this agent (in its draft).
    agent_id: uuid.UUID | None = None

    @field_validator("name")
    @classmethod
    def _not_reserved(cls, v: str) -> str:
        if v in RESERVED_NAMES:
            raise ValueError(f"'{v}' is built in; pick another name")
        return v


class ToolOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    type: str
    config: dict[str, Any]
    params: list[ToolParam]
    has_secrets: bool
    secret_hint: str | None
    is_active: bool
    used_by: list[str]
    updated_at: datetime


class TestIn(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)


class GenerateIn(BaseModel):
    description: str = Field(min_length=10, max_length=4000)
    # The agent the tool is for: its customer details become the tool's {{placeholders}}.
    agent_id: uuid.UUID | None = None


def _secrets(tool: Tool) -> dict[str, Any] | None:
    return decrypt_json(tool.encrypted_secrets) if tool.encrypted_secrets else None


async def _used_by(db: DbSession) -> dict[str, list[str]]:
    """Tool name -> agents whose live version or draft uses it, in calls or after them."""
    users: dict[str, list[str]] = {}
    for agent in await db.scalars(select(Agent)):
        configs = [agent.draft_config or {}]
        if agent.published_version_id:
            version = await db.get(AgentVersion, agent.published_version_id)
            configs.append(version.config if version else {})
        names = {n for c in configs for n in c.get("tools", [])}
        names |= {n for c in configs for n in c.get("analysis", {}).get("send_to", [])}
        for name in names:
            users.setdefault(name, []).append(agent.name)
    return users


def _out(tool: Tool, used_by: dict[str, list[str]]) -> ToolOut:
    return ToolOut(
        id=tool.id,
        name=tool.name,
        description=tool.description,
        type=tool.type.value,
        config=tool.config,
        params=[ToolParam(**p) for p in tool.parameters_schema.get("params", [])],
        has_secrets=bool(tool.encrypted_secrets),
        secret_hint=secret_hint(tool.type.value, _secrets(tool)),
        is_active=tool.is_active,
        used_by=sorted(used_by.get(tool.name, [])),
        updated_at=tool.updated_at,
    )


# How the form names each setting, so errors point at the right box.
FIELD_LABELS = {
    "spreadsheet": "Sheet link",
    "sheet": "Tab",
    "header": "Column title",
    "match_column": "Column to search",
    "url": "URL",
    "service_account": "Service account key",
}
PLAIN_ERRORS = {
    ("spreadsheet", "string_too_short"): "Paste the link to your Google Sheet",
    ("url", "string_pattern_mismatch"): "The URL must start with https:// or http://",
    ("header", "string_too_short"): "Every column needs a title",
}


def _problems(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors():
        field = next((str(p) for p in reversed(error["loc"]) if isinstance(p, str)), "")
        message = PLAIN_ERRORS.get((field, error["type"]), error["msg"])
        label = FIELD_LABELS.get(field)
        lines.append(
            f"{label}: {message}"
            if label and message != PLAIN_ERRORS.get((field, error["type"]))
            else message
        )
    return "; ".join(dict.fromkeys(lines))


def _apply(tool: Tool, body: ToolIn) -> None:
    try:
        config = CONFIG_TYPES[body.type].model_validate(body.config).model_dump()
        secrets = (
            SECRET_TYPES[body.type].model_validate(body.secrets).model_dump()
            if body.secrets
            else None
        )
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, _problems(exc)) from exc
    tool.name, tool.description, tool.type = body.name, body.description, ToolType(body.type)
    tool.config, tool.is_active = config, body.is_active
    tool.parameters_schema = {"params": [p.model_dump() for p in body.params]}
    if body.secrets is not None:
        tool.encrypted_secrets = encrypt_json(secrets) if secrets else None


async def _agent_config(db: DbSession, agent_id: uuid.UUID) -> tuple[Agent, dict[str, Any]]:
    """The agent and the config its editor shows (draft, else latest version)."""
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    latest = await _latest(db, agent.id)
    return agent, agent.draft_config if agent.draft_config is not None else latest.config


async def _switch_on(db: DbSession, agent_id: uuid.UUID, name: str) -> None:
    agent, config = await _agent_config(db, agent_id)
    if name not in config.get("tools", []):
        agent.draft_config = {**config, "tools": [*config.get("tools", []), name]}
        agent.draft_updated_at = datetime.now(UTC)


async def _get(db: DbSession, tool_id: uuid.UUID) -> Tool:
    tool = await db.get(Tool, tool_id)
    if tool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tool not found")
    return tool


async def _name_free(db: DbSession, name: str, tool_id: uuid.UUID | None = None) -> None:
    taken = await db.scalar(select(Tool).where(Tool.name == name))
    if taken is not None and taken.id != tool_id:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A tool named '{name}' already exists")


@router.get("", response_model=list[ToolOut])
async def list_tools(_: CurrentUser, db: DbSession) -> list[ToolOut]:
    used_by = await _used_by(db)
    return [_out(t, used_by) for t in await db.scalars(select(Tool).order_by(Tool.name))]


@router.post("", response_model=ToolOut, status_code=status.HTTP_201_CREATED)
async def create_tool(body: ToolIn, request: Request, admin: AdminUser, db: DbSession) -> ToolOut:
    await _name_free(db, body.name)
    tool = Tool(created_by_id=admin.id)
    _apply(tool, body)
    db.add(tool)
    await db.flush()
    if body.agent_id is not None:
        await _switch_on(db, body.agent_id, tool.name)
    await audit.record(
        db,
        actor=admin,
        action="tool.create",
        entity_type="tool",
        entity_id=tool.id,
        summary=f"Created tool '{tool.name}'",
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(tool)
    return _out(tool, {})


@router.put("/{tool_id}", response_model=ToolOut)
async def update_tool(
    tool_id: uuid.UUID, body: ToolIn, request: Request, admin: AdminUser, db: DbSession
) -> ToolOut:
    tool = await _get(db, tool_id)
    await _name_free(db, body.name, tool.id)
    used_by = await _used_by(db)
    if body.name != tool.name and used_by.get(tool.name):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Agents use this tool by name ({', '.join(used_by[tool.name])}); keep the name",
        )
    _apply(tool, body)
    await audit.record(
        db,
        actor=admin,
        action="tool.update",
        entity_type="tool",
        entity_id=tool.id,
        summary=f"Updated tool '{tool.name}'",
        changes={"secrets": "replaced"} if body.secrets is not None else {},
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(tool)
    return _out(tool, used_by)


@router.delete("/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tool(
    tool_id: uuid.UUID, request: Request, admin: AdminUser, db: DbSession
) -> None:
    tool = await _get(db, tool_id)
    users = (await _used_by(db)).get(tool.name)
    if users:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Remove it from {', '.join(users)} before deleting it"
        )
    await audit.record(
        db,
        actor=admin,
        action="tool.delete",
        entity_type="tool",
        entity_id=tool.id,
        summary=f"Deleted tool '{tool.name}'",
        ip_address=client_ip(request),
    )
    await db.delete(tool)
    await db.commit()


@router.post("/{tool_id}/test")
async def test_tool(
    tool_id: uuid.UUID, body: TestIn, request: Request, admin: AdminUser, db: DbSession
) -> dict[str, Any]:
    """Run the tool for real with sample values (a test row really is added)."""
    tool = await _get(db, tool_id)
    values = call_facts(datetime.now(UTC), body.values.get("customer_phone")) | body.values
    result = await run_tool(tool.type.value, tool.config, _secrets(tool), values)
    await audit.record(
        db,
        actor=admin,
        action="tool.test",
        entity_type="tool",
        entity_id=tool.id,
        summary=f"Tested tool '{tool.name}'",
        changes={"ok": result.get("ok")},
        ip_address=client_ip(request),
    )
    await db.commit()
    return result


# ---- Tool Builder ----------------------------------------------------------------------------


class GeneratedColumn(BaseModel):
    header: str
    value: str


class GeneratedTool(BaseModel):
    name: str = Field(description="snake_case, e.g. save_payment_promise")
    description: str = Field(description="When the agent should use it, for the agent")
    type: SUPPORTED
    params: list[ToolParam]
    sheet_action: Literal["append_row", "find_row"]
    sheet_columns: list[GeneratedColumn]
    sheet_match_column: str
    sheet_match_value: str
    http_method: Literal["GET", "POST", "PUT", "PATCH"]
    http_url: str
    http_body: str


BUILDER_GUIDE = """\
You set up tools that a phone voice agent for Yellow Metal (a gold loan company in India) uses
during calls. Turn the admin's description into one tool.
- type "google_sheets" to add a row to a sheet (append_row) or read one row (find_row);
  "http" to call a web API (CRM, webhook, Apps Script).
- params: only what the agent must get from the caller during the call (snake_case, a clear
  description including the format, e.g. "Date the customer will pay, as YYYY-MM-DD").
- Values may use {{param}} placeholders, the call's customer details (e.g. {{customer_name}},
  {{loan_id}}, {{due_amount}}) and call facts {{call_date}}, {{call_time}}, {{customer_phone}}.
- sheet_columns: header = the sheet's column title, value = what goes in it. For find_row set
  sheet_match_column (the column to search) and sheet_match_value (e.g. {{loan_id}}).
- For http, http_url is the address if the admin gave one, else "https://example.com"; keep
  http_body empty unless a specific JSON shape was asked for. Never invent secrets.
- If the tool should run after the call to record its results (not during the call), it
  needs no params and may use {{summary}}, {{outcome}}, {{duration_seconds}}, {{agent_name}}
  and the call's details in snake_case, e.g. {{promised_payment_date}}, {{paid_on}},
  {{call_back_at}}, {{reason_for_difficulty}}.
- description tells the agent exactly when to use the tool, in one or two sentences.
Fill fields of the other type with empty values."""


@router.post("/generate")
async def generate_tool(body: GenerateIn, admin: AdminUser, db: DbSession) -> dict[str, Any]:
    """Draft a tool from a plain-English description. Nothing is saved: the admin reviews the
    form, adds the sheet link or secrets, and saves it."""
    if not await ratelimit.hit(get_redis(), f"builder:{admin.id}", 120, 3600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many AI requests. Try later.")
    keys = await load_ai_keys(db)
    if "google" not in keys:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Add the Google (Gemini) API key first")
    request = body.description
    if body.agent_id is not None:
        agent, agent_config = await _agent_config(db, body.agent_id)
        known = detected_variables(AgentConfig.model_validate(agent_config))
        request += (
            f"\n\nThe tool is for the agent '{agent.name}'. Customer details it already has"
            f" for every call (use these exact names): "
            + (", ".join(f"{{{{{v}}}}}" for v in known) or "none")
        )
    content = types.Content(role="user", parts=[types.Part(text=request)])
    try:
        draft = await ask_gemini(keys["google"], BUILDER_GUIDE, [content], GeneratedTool)
    except BuilderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if draft.type == "google_sheets":
        config: dict[str, Any] = {
            "spreadsheet": "",
            "sheet": "Sheet1",
            "action": draft.sheet_action,
            "columns": [c.model_dump() for c in draft.sheet_columns],
            "match_column": draft.sheet_match_column,
            "match_value": draft.sheet_match_value,
        }
    else:
        config = {
            "method": draft.http_method,
            "url": draft.http_url,
            "headers": {},
            "body": draft.http_body,
        }
    name = draft.name.strip().lower()[:64]
    return {
        "name": name if name not in RESERVED_NAMES else f"{name}_tool",
        "description": draft.description,
        "type": draft.type,
        "config": config,
        "params": [p.model_dump() for p in draft.params],
        "placeholders": placeholders(config),
    }
