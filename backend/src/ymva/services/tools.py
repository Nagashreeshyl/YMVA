"""Tools agents use mid-call: append or look up a Google Sheets row, or call an HTTP API.

Google Sheets connects one of two ways: a service-account key (Sheets API), or an Apps Script
web app the admin pastes into the sheet (no Google Cloud project or key needed).

A tool's settings use {{name}} placeholders filled from what the agent collected (the tool's
parameters), the call's customer details and a few call facts (call_date, call_time,
customer_phone). Secrets (a service-account key, API headers) are stored encrypted and only
decrypted to run the tool. A tool never raises into the call: failures come back as
{"ok": false, "error": ...} so the agent can tell the caller someone will follow up.
"""

import asyncio
import json
import re
import time
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import quote, urlsplit

import httpx
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ymva.logging import get_logger
from ymva.models import Tool
from ymva.security import decrypt_json
from ymva.services import egress
from ymva.voice.config import VARIABLE_RE, render

log = get_logger(__name__)

TOOL_NAME_RE = r"^[a-z][a-z0-9_]{1,63}$"
RESERVED_NAMES = {"end_call"}
TOOL_TIMEOUT_SECS = 10.0
# What the model sees from a tool's answer; long API responses only slow the reply down.
MAX_RESULT_CHARS = 2000
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
APPS_SCRIPT_URL_RE = r"^https://script\.google\.com/(macros|a/macros/[^/]+)/s/[A-Za-z0-9_-]+/exec$"


class ToolError(Exception):
    """A problem worth showing the admin (and the agent) in plain words."""


class ToolParam(BaseModel):
    """Something the agent fills in when it uses the tool, e.g. promised_date."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    description: str = Field(default="", max_length=300)
    required: bool = True


class SheetColumn(BaseModel):
    header: str = Field(min_length=1, max_length=100)
    value: str = Field(default="", max_length=500)  # e.g. "{{promised_date}}"


class SheetsConfig(BaseModel):
    connection: Literal["service_account", "apps_script"] = "service_account"
    spreadsheet: str = Field(min_length=10, max_length=300)  # the sheet's link or ID
    # Tab name; empty means the tab in the link (#gid=...), else the first tab.
    sheet: str = Field(default="", max_length=100)
    action: Literal["append_row", "find_row"] = "append_row"
    columns: list[SheetColumn] = Field(default_factory=list, max_length=50)
    match_column: str = Field(default="", max_length=100)
    match_value: str = Field(default="", max_length=300)
    # Columns (and what the agent fills in) come from the agent's "Details to write down",
    # plus the call's time, phone, outcome and summary; see with_agent_details.
    agent_details: bool = False

    @property
    def spreadsheet_id(self) -> str:
        found = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", self.spreadsheet)
        return found.group(1) if found else self.spreadsheet.strip()

    @property
    def gid(self) -> int | None:
        found = re.search(r"[#&?]gid=(\d+)", self.spreadsheet)
        return int(found.group(1)) if found else None


class HttpConfig(BaseModel):
    method: Literal["GET", "POST", "PUT", "PATCH"] = "POST"
    url: str = Field(pattern=r"^https?://", max_length=2000)
    headers: dict[str, str] = Field(default_factory=dict)
    # JSON body with {{placeholders}}; empty sends the agent's values as a JSON object.
    body: str = Field(default="", max_length=10000)

    @field_validator("url")
    @classmethod
    def _fixed_host(cls, v: str) -> str:
        # Callers' words fill {{placeholders}}; in the address part they could pick the server.
        if "{{" in v.split("/", 3)[2]:  # "https:", "", host, path
            raise ValueError("Placeholders can go in the path or query, not the web address")
        return v


class DatabaseConfig(BaseModel):
    # SQL with {{placeholders}}; they're sent as query parameters, never pasted into the SQL.
    query: str = Field(min_length=6, max_length=5000)
    mode: Literal["read", "write"] = "read"


class McpConfig(BaseModel):
    server_url: str = Field(pattern=r"^https?://", max_length=2000)
    tool: str = Field(min_length=1, max_length=200)  # the tool's name on the MCP server
    # JSON types of the tool's inputs, so the agent's text is sent as numbers/booleans.
    arg_types: dict[str, str] = Field(default_factory=dict)


CONFIG_TYPES: dict[str, type[BaseModel]] = {
    "google_sheets": SheetsConfig,
    "http": HttpConfig,
    "database": DatabaseConfig,
    "mcp": McpConfig,
}


class SheetsSecrets(BaseModel):
    service_account: dict[str, Any] | None = None
    # The Apps Script web app's address; anyone holding it can write, so it's kept secret.
    script_url: str | None = Field(default=None, pattern=APPS_SCRIPT_URL_RE, max_length=500)

    @field_validator("service_account")
    @classmethod
    def _key(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None and (
            v.get("type") != "service_account"
            or not v.get("client_email")
            or not v.get("private_key")
        ):
            raise ValueError("Paste the whole JSON key file of a Google service account")
        return v


class HttpSecrets(BaseModel):
    headers: dict[str, str] = Field(default_factory=dict)  # e.g. Authorization
    # Filled into {{secret.NAME}} in the URL, headers or body, e.g. a Telegram bot token in
    # https://api.telegram.org/bot{{secret.bot_token}}/sendMessage.
    values: dict[str, str] = Field(default_factory=dict)


# {{secret.NAME}}: filled from the tool's encrypted secrets, never from the agent or caller.
SECRET_RE = re.compile(r"\{\{\s*secret\.([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def secret_names(config: dict[str, Any]) -> list[str]:
    """Every {{secret.NAME}} a tool's settings use, in order."""
    return list(dict.fromkeys(m.group(1) for m in SECRET_RE.finditer(json.dumps(config))))


class DatabaseSecrets(BaseModel):
    connection: str = Field(pattern=r"^postgres(ql)?://", max_length=1000)


SECRET_TYPES: dict[str, type[BaseModel]] = {
    "google_sheets": SheetsSecrets,
    "http": HttpSecrets,
    "database": DatabaseSecrets,
    "mcp": HttpSecrets,
}


def secret_hint(tool_type: str, secrets: dict[str, Any] | None) -> str | None:
    """What the admin may see about a stored secret: the service account's email (the sheet
    must be shared with it) or the names of the secret headers."""
    if not secrets:
        return None
    if tool_type == "google_sheets":
        if secrets.get("script_url"):
            return "Apps Script web app"
        return str((secrets.get("service_account") or {}).get("client_email", "")) or None
    if tool_type == "database":
        url = urlsplit(str(secrets.get("connection", "")))
        return f"{url.hostname}{url.path}" if url.hostname else None
    names = [*secrets.get("headers", {}), *secrets.get("values", {})]
    return ", ".join(names) or None


def placeholders(config: dict[str, Any]) -> list[str]:
    """Every {{name}} used in a tool's settings, in order."""
    return list(dict.fromkeys(m.group(1) for m in VARIABLE_RE.finditer(json.dumps(config))))


def call_facts(now: datetime, customer_phone: str | None) -> dict[str, str]:
    return {
        "call_date": now.strftime("%Y-%m-%d"),
        "call_time": now.strftime("%Y-%m-%d %H:%M"),
        "customer_phone": customer_phone or "",
    }


# ---- Google Sheets ----------------------------------------------------------------------

_tokens: dict[str, tuple[str, float]] = {}  # service account email -> (token, expiry)


async def _sheets_token(service_account: dict[str, Any]) -> str:
    email = service_account["client_email"]
    cached = _tokens.get(email)
    if cached and cached[1] - time.time() > 60:
        return cached[0]
    from google.auth.transport.requests import Request
    from google.oauth2.service_account import Credentials

    # The pasted file's token_uri would be fetched outside the egress guard: always use Google's.
    service_account = {**service_account, "token_uri": "https://oauth2.googleapis.com/token"}
    creds = Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
        service_account, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    try:
        await asyncio.to_thread(creds.refresh, Request())
    except Exception as exc:
        raise ToolError("Google rejected the service account key. Check it's current.") from exc
    _tokens[email] = (creds.token, time.time() + 3000)
    return str(creds.token)


def _sheets_error(response: httpx.Response, email: str) -> ToolError:
    if response.status_code in (403, 404):
        return ToolError(
            f"Can't open the sheet. Share it with {email} as an editor and check the link."
        )
    try:
        detail = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        detail = response.text[:200]
    return ToolError(f"Google Sheets answered {response.status_code}: {detail}")


async def _tab(client: httpx.AsyncClient, config: SheetsConfig, email: str) -> str:
    """The tab to use: the one named, else the one in the link, else the first."""
    meta = await client.get(
        f"{SHEETS_API}/{config.spreadsheet_id}", params={"fields": "sheets.properties"}
    )
    if meta.is_error:
        raise _sheets_error(meta, email)
    tabs = [s["properties"] for s in meta.json().get("sheets", [])]
    if config.sheet.strip():
        if not any(t["title"] == config.sheet.strip() for t in tabs):
            names = ", ".join(t["title"] for t in tabs)
            raise ToolError(f"The sheet has no tab named '{config.sheet}'. Its tabs: {names}")
        return config.sheet.strip()
    by_gid = [t["title"] for t in tabs if t.get("sheetId") == config.gid]
    return str(by_gid[0] if by_gid else tabs[0]["title"])


def _saved(wanted: dict[str, str], updated: bool) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": True, "saved": {h: v for h, v in wanted.items() if v}}
    if updated:
        result["note"] = "Updated the row saved earlier in this call; no new row was added."
    return result


def _cell(value: str) -> str:
    # A caller's words must stay text: a leading "=" would make Google run it as a formula.
    return "'" + value if value.startswith("=") else value


async def _run_apps_script(
    config: SheetsConfig, secrets: dict[str, Any], values: dict[str, str], memory: dict[str, Any]
) -> dict[str, Any]:
    if not secrets.get("script_url"):
        raise ToolError(
            "This tool has no Apps Script web app address yet. Follow the steps in the tool "
            "to add the script to the sheet, then paste its web app URL."
        )
    columns = [
        [c.header.strip(), _cell(render(c.value, values, missing=""))] for c in config.columns
    ]
    request = {
        "spreadsheet_id": config.spreadsheet_id,
        "gid": config.gid,
        "sheet": config.sheet.strip(),
        "action": config.action,
        "columns": columns,
        "match_column": config.match_column.strip(),
        "match_value": render(config.match_value, values, missing="").strip(),
        # Set only after this call added a row: the script then updates that row instead.
        "row": memory.get("row"),
    }
    # Google answers a web app POST with a redirect to the result, which httpx follows as GET.
    async with httpx.AsyncClient(
        timeout=TOOL_TIMEOUT_SECS,
        follow_redirects=True,
        event_hooks=egress.EVENT_HOOKS,
        transport=egress.guarded_transport(),
    ) as client:
        response = await client.post(str(secrets["script_url"]), json=request)
    try:
        data = response.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        if response.status_code == 404:
            raise ToolError("Google can't find this Apps Script web app. Check the URL.")
        raise ToolError(
            "The Apps Script didn't answer. Deploy it as a web app with 'Who has access: "
            "Anyone', and paste the URL ending in /exec."
        )
    if not data.get("ok"):
        raise ToolError(str(data.get("error") or "The Apps Script reported an error")[:300])
    if config.action == "append_row":
        updated = memory.get("row") is not None
        if isinstance(data.get("row"), int):
            memory["row"] = data["row"]
        return _saved(dict(columns), updated)
    if data.get("found"):
        return {"ok": True, "found": True, "row": data.get("row", {})}
    return {"ok": True, "found": False}


async def _run_sheets(
    config: SheetsConfig,
    secrets: dict[str, Any],
    values: dict[str, str],
    memory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`memory` lives for one call: after this call adds a row, saving again updates that row
    (never any other), so the agent can correct what it saved without adding duplicates."""
    memory = {} if memory is None else memory
    if config.connection == "apps_script":
        return await _run_apps_script(config, secrets, values, memory)
    if not secrets.get("service_account"):
        raise ToolError(
            "This tool has no Google service account key yet. Paste the key in the tool and "
            "share the sheet with the key's email as an editor."
        )
    account = SheetsSecrets.model_validate(secrets).service_account
    assert account is not None  # checked just above
    email = account["client_email"]
    token = await _sheets_token(account)
    base = f"{SHEETS_API}/{config.spreadsheet_id}/values"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=TOOL_TIMEOUT_SECS, headers=headers) as client:
        tab = quote(f"'{await _tab(client, config, email)}'", safe="")
        if config.action == "append_row":
            # Values go under the matching column title, so column order doesn't matter.
            first = await client.get(f"{base}/{tab}!1:1")
            if first.is_error:
                raise _sheets_error(first, email)
            sheet_headers = [h.strip() for h in (first.json().get("values") or [[]])[0]]
            wanted = {
                c.header.strip(): _cell(render(c.value, values, missing="")) for c in config.columns
            }
            missing = [h for h in wanted if h not in sheet_headers]
            if missing:
                # An empty sheet gets its title row; new columns are added after the last one.
                sheet_headers += missing
                titled = await client.put(
                    f"{base}/{tab}!1:1",
                    params={"valueInputOption": "RAW"},
                    json={"values": [sheet_headers]},
                )
                if titled.is_error:
                    raise _sheets_error(titled, email)
            if isinstance(memory.get("row"), int):
                return await _update_own_row(client, f"{base}/{tab}", memory["row"],
                                             sheet_headers, wanted, email)  # fmt: skip
            row = [wanted.get(h, "") for h in sheet_headers]
            added = await client.post(
                f"{base}/{tab}!A1:append",
                params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
                json={"values": [row]},
            )
            if added.is_error:
                raise _sheets_error(added, email)
            where = str(added.json().get("updates", {}).get("updatedRange", ""))
            if found := re.search(r"![A-Z]+(\d+)", where):
                memory["row"] = int(found.group(1))
            return _saved(wanted, updated=False)

        # shortcut: reads the whole tab, fine up to tens of thousands of rows; past that use
        # a database tool.
        sheet = await client.get(f"{base}/{tab}")
        if sheet.is_error:
            raise _sheets_error(sheet, email)
        rows = sheet.json().get("values") or []
        if not rows:
            return {"ok": True, "found": False}
        sheet_headers = [h.strip() for h in rows[0]]
        if config.match_column.strip() not in sheet_headers:
            raise ToolError(f"The sheet has no column named {config.match_column}")
        col = sheet_headers.index(config.match_column.strip())
        target = render(config.match_value, values, missing="").strip().lower()
        for row in rows[1:]:
            if col < len(row) and str(row[col]).strip().lower() == target and target:
                return {
                    "ok": True,
                    "found": True,
                    "row": dict(zip(sheet_headers, row, strict=False)),
                }
        return {"ok": True, "found": False}


async def _update_own_row(
    client: httpx.AsyncClient,
    tab_url: str,
    row: int,
    sheet_headers: list[str],
    wanted: dict[str, str],
    email: str,
) -> dict[str, Any]:
    """Rewrite the row this call added: new values replace old ones, blanks keep what's there."""
    current = await client.get(f"{tab_url}!{row}:{row}")
    if current.is_error:
        raise _sheets_error(current, email)
    old = (current.json().get("values") or [[]])[0]
    merged = [
        wanted.get(h) or (old[i] if i < len(old) else "") for i, h in enumerate(sheet_headers)
    ]
    put = await client.put(
        f"{tab_url}!{row}:{row}", params={"valueInputOption": "USER_ENTERED"},
        json={"values": [merged]},
    )  # fmt: skip
    if put.is_error:
        raise _sheets_error(put, email)
    return _saved(wanted, updated=True)


# ---- HTTP ---------------------------------------------------------------------------------


async def _run_http(
    config: HttpConfig, secrets: dict[str, Any], values: dict[str, str]
) -> dict[str, Any]:
    stored = HttpSecrets.model_validate(secrets or {})

    def with_secrets(text: str, escape: Any = str) -> str:
        # Secrets go in before the caller's values, so nothing a caller says can pull one in.
        return SECRET_RE.sub(lambda m: escape(stored.values.get(m.group(1), "")), text)

    if any(v.strip() in (".", "..") for v in values.values()):
        # The URL library would resolve them as path steps and reach another endpoint.
        raise ToolError("That value isn't valid here")
    url = render(
        with_secrets(config.url, lambda v: quote(v, safe="")),
        {k: quote(v, safe="") for k, v in values.items()},
        missing="",
    )
    headers = {k: render(with_secrets(v), values, missing="") for k, v in config.headers.items()}
    headers.update(stored.headers)
    content: str | None = None
    if config.method != "GET":
        if config.body.strip():
            # Values are JSON-escaped so a caller's words can't break or change the body.
            escaped = {k: json.dumps(v)[1:-1] for k, v in values.items()}
            body = with_secrets(config.body, lambda v: json.dumps(v)[1:-1])
            content = render(body, escaped, missing="")
        else:
            content = json.dumps(values)
        headers.setdefault("Content-Type", "application/json")
    async with httpx.AsyncClient(
        timeout=TOOL_TIMEOUT_SECS,
        follow_redirects=True,
        event_hooks=egress.EVENT_HOOKS,
        transport=egress.guarded_transport(),
    ) as client:
        response = await client.request(config.method, url, headers=headers, content=content)
    text = response.text
    # The agent never gets to see a secret, even one the service echoes back escaped.
    hidden = [*stored.values.values(), *stored.headers.values()]
    hidden += [h.split()[-1] for h in stored.headers.values() if h.split()]  # "Bearer <key>"
    for secret in hidden:
        if len(secret) >= 4:
            for form in {secret, quote(secret, safe=""), json.dumps(secret)[1:-1]}:
                text = text.replace(form, "•••")
    text = text[:MAX_RESULT_CHARS]
    try:
        data: Any = json.loads(text)
    except ValueError:
        data = text
    if response.is_error:
        raise ToolError(f"The service answered {response.status_code}: {text[:200]}")
    return {"ok": True, "response": data}


# ---- PostgreSQL ---------------------------------------------------------------------------

MAX_ROWS = 20


def _bind(query: str) -> tuple[str, list[str]]:
    """{{name}} -> $1, $2, ... so values are passed as parameters."""
    names: list[str] = []

    def number(m: re.Match[str]) -> str:
        if m.group(1) not in names:
            names.append(m.group(1))
        return f"${names.index(m.group(1)) + 1}"

    return VARIABLE_RE.sub(number, query), names


def _typed(value: str, pg_type: str) -> Any:
    """The caller's words arrive as text; give Postgres the type the column expects."""
    if value == "":
        return None
    if pg_type in ("int2", "int4", "int8"):
        return int(value.replace(",", ""))
    if pg_type in ("numeric", "float4", "float8"):
        return Decimal(value.replace(",", ""))
    if pg_type == "bool":
        return value.strip().lower() in ("true", "yes", "1")
    if pg_type == "date":
        return date.fromisoformat(value)
    if pg_type in ("timestamp", "timestamptz"):
        return datetime.fromisoformat(value)
    return value


async def _run_database(
    config: DatabaseConfig, secrets: dict[str, Any], values: dict[str, str]
) -> dict[str, Any]:
    import asyncpg  # type: ignore[import-untyped]

    if not secrets.get("connection"):
        raise ToolError("This tool has no database connection yet. Add it in the tool.")
    url = DatabaseSecrets.model_validate(secrets).connection
    sql, names = _bind(config.query)
    try:
        # Connect to the checked IP only: never YMVA's own database or another container.
        host = await egress.safe_ip(urlsplit(url).hostname or "")
        conn = await asyncpg.connect(
            url,
            host=host,
            timeout=5,
            server_settings={"statement_timeout": "8000", "application_name": "ymva-tool"},
        )
    except egress.BlockedDestinationError as exc:
        raise ToolError(str(exc)) from exc
    except (OSError, httpx.HTTPError, asyncpg.PostgresError) as exc:
        raise ToolError(f"Couldn't connect to the database: {exc.__class__.__name__}") from exc
    try:
        # Read tools run read-only, so even a mistaken query can't change data.
        async with conn.transaction(readonly=config.mode == "read"):
            statement = await conn.prepare(sql)
            types = [t.name for t in statement.get_parameters()]
            try:
                args = [_typed(values.get(n, ""), t) for n, t in zip(names, types, strict=True)]
            except ValueError as exc:
                raise ToolError(f"A value doesn't fit its column: {exc}") from exc
            rows = await statement.fetch(*args)
            if config.mode == "write":
                return {"ok": True, "result": statement.get_statusmsg(), "rows": len(rows)}
    except asyncpg.PostgresError as exc:
        raise ToolError(f"The database refused the query: {exc}") from exc
    finally:
        await conn.close()
    found = [dict(r) for r in rows[:MAX_ROWS]]
    return {"ok": True, "rows": json.loads(json.dumps(found, default=str)), "count": len(rows)}


# ---- MCP ----------------------------------------------------------------------------------


def _mcp_client(server_url: str, headers: dict[str, str]) -> Any:
    """A client for the server (replaced in tests by an in-process server)."""
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    http = httpx2.AsyncClient(
        headers=headers,
        timeout=TOOL_TIMEOUT_SECS,
        event_hooks=egress.EVENT_HOOKS,
        transport=egress.guarded_transport2(),
    )
    return Client(
        streamable_http_client(server_url, http_client=http),
        read_timeout_seconds=TOOL_TIMEOUT_SECS,
    )


def _json_typed(value: str, json_type: str) -> Any:
    if json_type == "integer":
        return int(value.replace(",", ""))
    if json_type == "number":
        return float(value.replace(",", ""))
    if json_type == "boolean":
        return value.strip().lower() in ("true", "yes", "1")
    return value


async def mcp_tools(server_url: str, headers: dict[str, str]) -> list[dict[str, Any]]:
    """The tools an MCP server offers, shaped like our tool form (for the admin to pick one)."""
    try:
        async with _mcp_client(server_url, headers) as client:
            listed = await client.list_tools()
    except Exception as exc:
        raise ToolError(
            f"Couldn't load tools from the MCP server: {exc.__class__.__name__}"
        ) from exc
    found = []
    for tool in listed.tools:
        schema = tool.input_schema or {}
        required = set(schema.get("required", []))
        props: dict[str, Any] = schema.get("properties", {})
        found.append(
            {
                "tool": tool.name,
                "description": tool.description or "",
                "params": [
                    {
                        "name": name,
                        "description": str(spec.get("description", ""))[:300],
                        "required": name in required,
                    }
                    for name, spec in props.items()
                ],
                "arg_types": {n: str(spec.get("type", "string")) for n, spec in props.items()},
            }
        )
    return found


async def _run_mcp(
    config: McpConfig, secrets: dict[str, Any], values: dict[str, str]
) -> dict[str, Any]:
    try:
        args = {
            name: _json_typed(values[name], kind)
            for name, kind in config.arg_types.items()
            if values.get(name, "") != ""
        }
    except ValueError as exc:
        raise ToolError(f"A value isn't the right kind: {exc}") from exc
    headers = HttpSecrets.model_validate(secrets or {}).headers
    # shortcut: connects per use (about 0.3-1 s); keep one session per call if that's too slow.
    try:
        async with _mcp_client(config.server_url, headers) as client:
            result = await client.call_tool(config.tool, args)
    except Exception as exc:
        raise ToolError(f"The MCP server didn't answer: {exc.__class__.__name__}") from exc
    text = "\n".join(getattr(c, "text", "") for c in result.content).strip()
    if result.is_error:
        raise ToolError(text[:300] or "The MCP tool reported an error")
    answer: Any = result.structured_content
    if not answer:
        try:
            answer = json.loads(text)  # many servers send JSON as text
        except ValueError:
            answer = text
    return {"ok": True, "response": answer}


# ---- running ------------------------------------------------------------------------------


async def run_tool(
    tool_type: str,
    config: dict[str, Any],
    secrets: dict[str, Any] | None,
    values: dict[str, str],
    memory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a tool with the values filled in. Always returns a dict for the model.

    `memory` is kept by the caller for one phone call (see _run_sheets)."""
    try:
        parsed = CONFIG_TYPES[tool_type].model_validate(config)
        runners: dict[str, Any] = {
            "google_sheets": lambda c, s, v: _run_sheets(c, s, v, memory),
            "http": _run_http,
            "database": _run_database,
            "mcp": _run_mcp,
        }
        result: dict[str, Any] = await asyncio.wait_for(
            runners[tool_type](parsed, secrets or {}, values), TOOL_TIMEOUT_SECS + 2
        )
    except ToolError as exc:
        return {"ok": False, "error": str(exc)}
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return {"ok": False, "error": f"The tool isn't set up correctly ({problems})"}
    except KeyError as exc:
        return {"ok": False, "error": f"Unknown tool type {exc}"}
    except TimeoutError:
        return {"ok": False, "error": "The service took too long to answer"}
    except egress.BlockedDestinationError as exc:
        return {"ok": False, "error": str(exc)}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"Couldn't reach the service: {exc.__class__.__name__}"}
    encoded = json.dumps(result, ensure_ascii=False, default=str)
    if len(encoded) > MAX_RESULT_CHARS:
        result = {"ok": True, "response": encoded[:MAX_RESULT_CHARS] + "…"}
    return result


@dataclass
class LoadedTool:
    """A tool ready to run during a call (secrets already decrypted, held in memory only)."""

    name: str
    description: str
    type: str
    config: dict[str, Any]
    params: list[ToolParam]
    secrets: dict[str, Any] | None

    def schema(self) -> FunctionSchema:
        return FunctionSchema(
            name=self.name,
            description=self.description,
            properties={
                p.name: {"type": "string", "description": p.description} for p in self.params
            },
            required=[p.name for p in self.params if p.required],
        )


# Filled from the call itself, next to the agent's details (blank until known).
CALL_COLUMNS = [
    ("Call time", "{{call_time}}"),
    ("Customer phone", "{{customer_phone}}"),
    ("Outcome", "{{outcome}}"),
    ("Summary", "{{summary}}"),
]


def with_agent_details(tool: LoadedTool, details: list[Any]) -> LoadedTool:
    """A Sheets tool set to follow the agent: one column per detail the agent writes down, and
    the agent fills those same details in. The agent's details are its source of truth."""
    if tool.type != "google_sheets" or not tool.config.get("agent_details"):
        return tool
    from ymva.services.analysis import field_name  # analysis imports this module

    fields = [(d, field_name(d.name)) for d in details]
    fields = [(d, f) for d, f in fields if re.fullmatch(r"[a-z][a-z0-9_]{0,39}", f)]
    columns = [{"header": d.name, "value": f"{{{{{f}}}}}"} for d, f in fields]
    columns += [{"header": h, "value": v} for h, v in CALL_COLUMNS]
    return replace(
        tool,
        config={**tool.config, "action": "append_row", "columns": columns},
        params=[ToolParam(name=f, description=d.description, required=False) for d, f in fields],
    )


async def load_tools(
    db: AsyncSession, names: list[str], details: list[Any] | None = None
) -> list[LoadedTool]:
    """The agent's active tools; ones deleted or switched off since are skipped. `details` are
    the agent's "Details to write down", for Sheets tools that follow them."""
    if not names:
        return []
    rows = await db.scalars(select(Tool).where(Tool.name.in_(names), Tool.is_active))
    loaded = [
        LoadedTool(
            name=t.name,
            description=t.description,
            type=t.type.value,
            config=t.config,
            params=[ToolParam(**p) for p in t.parameters_schema.get("params", [])],
            secrets=decrypt_json(t.encrypted_secrets) if t.encrypted_secrets else None,
        )
        for t in rows
    ]
    return [with_agent_details(t, details or []) for t in loaded]
