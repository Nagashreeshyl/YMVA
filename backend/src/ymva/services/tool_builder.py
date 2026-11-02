"""Tools written from plain words, by the tools page and by YMA in the agent editor."""

from typing import Any, Literal

from google.genai import types
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ymva.models import Tool, ToolType
from ymva.security import decrypt_json
from ymva.services.builder import ask_gemini
from ymva.services.tools import (
    CONFIG_TYPES,
    RESERVED_NAMES,
    HttpSecrets,
    ToolParam,
    placeholders,
    secret_names,
)


class GeneratedColumn(BaseModel):
    header: str
    value: str


class GeneratedTool(BaseModel):
    name: str = Field(description="snake_case, e.g. save_payment_promise")
    description: str = Field(description="When the agent should use it, for the agent")
    # MCP tools come from the server's own list (Load tools), not from a description.
    type: Literal["google_sheets", "http", "database"]
    params: list[ToolParam]
    sheet_action: Literal["append_row", "find_row"]
    sheet_columns: list[GeneratedColumn]
    sheet_match_column: str
    sheet_match_value: str
    sheet_link: str = Field(
        default="", description="The Google Sheet link if the admin gave one, else empty"
    )
    http_method: Literal["GET", "POST", "PUT", "PATCH"]
    http_url: str
    http_body: str
    db_query: str
    db_mode: Literal["read", "write"]
    needs_key: bool = Field(
        default=False,
        description="http only: true if the API needs a key, token or password to be called",
    )


BUILDER_GUIDE = """\
You set up tools that a phone voice agent for Yellow Metal (a gold loan company in India) uses
during calls. Turn the admin's description into one tool.
- type "google_sheets" to add a row to a sheet (append_row) or read one row (find_row);
  "http" to call a web API (CRM, webhook, Apps Script); "database" to run one PostgreSQL
  query (db_query, with {{placeholders}} where values go, e.g.
  select interest_due from loans where loan_id = {{loan_id}}; db_mode "read" unless it
  changes data). Never guess table names you weren't told: use clear placeholder names.
- params: only what the agent must get from the caller during the call (snake_case, a clear
  description including the format, e.g. "Date the customer will pay, as YYYY-MM-DD").
- Values may use {{param}} placeholders, the call's customer details (e.g. {{customer_name}},
  {{loan_id}}, {{due_amount}}) and call facts {{call_date}}, {{call_time}}, {{customer_phone}}.
- sheet_columns: header = the sheet's column title, value = what goes in it. For find_row set
  sheet_match_column (the column to search) and sheet_match_value (e.g. {{loan_id}}).
- For http, http_url is the address if the admin gave one, else "https://example.com"; keep
  http_body empty unless a specific JSON shape was asked for. Set needs_key if the API needs
  a key or token (most do unless the admin says it's open). Never invent secrets or put a
  key, token or password in any field: the admin adds those in a secure field.
- Where the API expects a key, token or password, or a fixed value only the admin knows
  (an account or chat ID), write {{secret.NAME}} (e.g. {{secret.api_key}}, {{secret.chat_id}})
  in the URL, header or body: the admin fills it in securely. Never make these params and
  never use a plain {{NAME}} for them. Example, a Telegram message: POST
  https://api.telegram.org/bot{{secret.bot_token}}/sendMessage with body
  {"chat_id": "{{secret.chat_id}}", "text": "..."}.
- For google_sheets, use the sheet link if the admin gave one (sheet_link), else leave it
  empty.
- If the tool should run after the call to record its results (not during the call), it
  needs no params and may use {{summary}}, {{outcome}}, {{duration_seconds}}, {{agent_name}}
  and the call's details in snake_case, e.g. {{promised_payment_date}}, {{paid_on}},
  {{call_back_at}}, {{reason_for_difficulty}}.
- description tells the agent exactly when to use the tool, in one or two sentences.
Fill fields of the other type with empty values."""


def _config(draft: GeneratedTool) -> dict[str, Any]:
    if draft.type == "google_sheets":
        return {
            "spreadsheet": draft.sheet_link.strip(),
            "sheet": "",
            "action": draft.sheet_action,
            "columns": [c.model_dump() for c in draft.sheet_columns],
            "match_column": draft.sheet_match_column,
            "match_value": draft.sheet_match_value,
        }
    if draft.type == "database":
        return {"query": draft.db_query, "mode": draft.db_mode}
    return {
        "method": draft.http_method,
        "url": draft.http_url,
        "headers": {},
        "body": draft.http_body,
    }


async def draft_tool(
    api_key: str, request: str, known: list[str], existing: Tool | None = None
) -> dict[str, Any]:
    """A tool's settings from plain words; `existing` is changed instead of starting over."""
    if known:
        request += (
            "\n\nCustomer details the agent already has for every call (use these exact "
            "names): " + ", ".join(f"{{{{{v}}}}}" for v in known)
        )
    if existing is not None:
        request = (
            f"Change this existing tool as asked, keeping its type, name and everything not "
            f"mentioned. Current tool: name {existing.name}, type {existing.type.value}, "
            f"description {existing.description!r}, settings {existing.config}, "
            f"params {existing.parameters_schema.get('params', [])}.\n\nChange: {request}"
        )
    content = types.Content(role="user", parts=[types.Part(text=request)])
    draft = await ask_gemini(api_key, BUILDER_GUIDE, [content], GeneratedTool)
    name = draft.name.strip().lower()[:64]
    name = name if name not in RESERVED_NAMES else f"{name}_tool"
    config = _config(draft)
    if existing is not None:
        if draft.type == "google_sheets" and not config["spreadsheet"]:
            config["spreadsheet"] = existing.config.get("spreadsheet", "")
        if draft.type == "http" and "example.com" in config["url"]:
            config["url"] = existing.config.get("url", config["url"])
    return {
        "name": existing.name
        if existing
        else (name if name not in RESERVED_NAMES else f"{name}_tool"),
        "description": draft.description,
        "type": existing.type.value if existing else draft.type,
        "config": config,
        "params": [p.model_dump() for p in draft.params],
        "needs_key": draft.type == "http" and draft.needs_key,
        "placeholders": placeholders(config),
    }


def missing(tool: Tool) -> list[str]:
    """What the admin still has to give before the tool can run: "link" (the Google Sheet's
    link), "address" (the API's URL) or "key" (a secret, added in a secure field)."""
    has_secrets = bool(tool.encrypted_secrets and decrypt_json(tool.encrypted_secrets))
    needs: list[str] = []
    if tool.type == ToolType.GOOGLE_SHEETS:
        if len(tool.config.get("spreadsheet", "")) < 10:
            needs.append("link")
        if not has_secrets:
            needs.append("key")
    elif tool.type == ToolType.HTTP:
        url = str(tool.config.get("url", ""))
        if not url.startswith(("http://", "https://")) or "example.com" in url:
            needs.append("address")
        stored = HttpSecrets.model_validate(
            decrypt_json(tool.encrypted_secrets) if tool.encrypted_secrets else {}
        )
        wanted = secret_names(tool.config)
        if any(not stored.values.get(name) for name in wanted) or (
            not wanted and tool.parameters_schema.get("needs_key") and not has_secrets
        ):
            needs.append("key")
    elif tool.type == ToolType.DATABASE and not has_secrets:
        needs.append("key")
    return needs


def apply_draft(tool: Tool, draft: dict[str, Any]) -> None:
    """Store a drafted tool. An incomplete one (no sheet link yet) is kept as drafted and stays
    switched off until `missing` is empty, so calls never use a half-set-up tool."""
    try:
        config = CONFIG_TYPES[draft["type"]].model_validate(draft["config"]).model_dump()
    except ValidationError:
        config = draft["config"]
    tool.name, tool.description = draft["name"], draft["description"]
    tool.type, tool.config = ToolType(draft["type"]), config
    tool.parameters_schema = {"params": draft["params"], "needs_key": draft.get("needs_key", False)}
    tool.is_active = not missing(tool)


async def tool_by_name(db: AsyncSession, name: str) -> Tool | None:
    return await db.scalar(select(Tool).where(Tool.name == name))
