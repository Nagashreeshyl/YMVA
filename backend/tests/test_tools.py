import json
from typing import Any

import httpx
import pytest

from ymva.services import tools as tools_service

SERVICE_ACCOUNT = {
    "type": "service_account",
    "client_email": "ymva@project.iam.gserviceaccount.com",
    "private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
}


@pytest.fixture
def requests_seen(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Every outgoing tool request, answered by a fake Google Sheets / web service."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("!1:1"):
            return httpx.Response(200, json={"values": [["Loan ID", "Promised date", "Called at"]]})
        if ":append" in request.url.path:
            return httpx.Response(200, json={"updates": {"updatedRows": 1}})
        if "spreadsheets" in request.url.host or "sheets" in request.url.host:
            return httpx.Response(
                200, json={"values": [["Loan ID", "Branch"], ["YM1", "Hubli"], ["YM2", "Mysuru"]]}
            )
        return httpx.Response(200, json={"received": json.loads(request.content or b"null")})

    real = httpx.AsyncClient

    def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)

    async def token(_: dict[str, Any]) -> str:
        return "fake-token"

    monkeypatch.setattr(tools_service.httpx, "AsyncClient", client)
    monkeypatch.setattr(tools_service, "_sheets_token", token)
    return seen


SHEET_TOOL = {
    "name": "save_promise",
    "description": "Save the date the customer promises to pay",
    "type": "google_sheets",
    "config": {
        "spreadsheet": "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjK/edit#gid=0",
        "action": "append_row",
        "columns": [
            {"header": "Promised date", "value": "{{promised_date}}"},
            {"header": "Loan ID", "value": "{{loan_id}}"},
        ],
    },
    "params": [{"name": "promised_date", "description": "YYYY-MM-DD"}],
    "secrets": {"service_account": SERVICE_ACCOUNT},
}


async def test_sheet_tool_adds_row_under_matching_headers(
    admin_client: httpx.AsyncClient, requests_seen: list[httpx.Request]
) -> None:
    resp = await admin_client.post("/api/tools", json=SHEET_TOOL)
    assert resp.status_code == 201, resp.text
    tool = resp.json()
    assert tool["has_secrets"] and tool["secret_hint"] == SERVICE_ACCOUNT["client_email"]
    assert "private_key" not in resp.text  # secrets never leave the server

    resp = await admin_client.post(
        f"/api/tools/{tool['id']}/test",
        json={"values": {"promised_date": "2026-11-20", "loan_id": "YM1"}},
    )
    assert resp.json()["ok"], resp.json()
    append = requests_seen[-1]
    assert "/1AbCdEfGhIjK/" in append.url.path  # ID taken from the pasted link
    # Sheet order is Loan ID, Promised date, Called at; unmapped columns stay empty.
    assert json.loads(append.content)["values"] == [["YM1", "2026-11-20", ""]]


async def test_sheet_tool_reports_missing_column(
    admin_client: httpx.AsyncClient, requests_seen: list[httpx.Request]
) -> None:
    body = json.loads(json.dumps(SHEET_TOOL))
    body["config"]["columns"].append({"header": "Amount", "value": "x"})
    tool = (await admin_client.post("/api/tools", json=body)).json()
    result = (await admin_client.post(f"/api/tools/{tool['id']}/test", json={})).json()
    assert result == {"ok": False, "error": "The sheet has no column named Amount"}


async def test_sheet_lookup_finds_row(requests_seen: list[httpx.Request]) -> None:
    result = await tools_service.run_tool(
        "google_sheets",
        {
            "spreadsheet": "1AbCdEfGhIjK",
            "action": "find_row",
            "match_column": "Loan ID",
            "match_value": "{{loan_id}}",
        },
        {"service_account": SERVICE_ACCOUNT},
        {"loan_id": "ym2"},
    )
    assert result == {"ok": True, "found": True, "row": {"Loan ID": "YM2", "Branch": "Mysuru"}}


async def test_http_tool_escapes_caller_words(requests_seen: list[httpx.Request]) -> None:
    result = await tools_service.run_tool(
        "http",
        {"url": "https://crm.example.com/notes/{{loan_id}}", "body": '{"note": "{{note}}"}'},
        {"headers": {"Authorization": "Bearer secret"}},
        {"loan_id": "YM 1/2", "note": 'said "pay later", ok'},
    )
    sent = requests_seen[-1]
    assert sent.url.raw_path == b"/notes/YM%201%2F2"  # the value cannot add path parts
    assert sent.headers["Authorization"] == "Bearer secret"
    assert result["response"] == {"received": {"note": 'said "pay later", ok'}}


async def test_tool_names_and_deletion_rules(admin_client: httpx.AsyncClient) -> None:
    bad = await admin_client.post("/api/tools", json={**SHEET_TOOL, "name": "end_call"})
    assert bad.status_code == 422
    tool = (await admin_client.post("/api/tools", json=SHEET_TOOL)).json()
    assert (await admin_client.post("/api/tools", json=SHEET_TOOL)).status_code == 409

    agent = (await admin_client.post("/api/agents", json={"name": "Reminder"})).json()
    config = {**agent["config"], "tools": ["save_promise"]}
    await admin_client.put(f"/api/agents/{agent['id']}/draft", json={"config": config})
    listed = (await admin_client.get("/api/tools")).json()
    assert listed[0]["used_by"] == ["Reminder"]
    assert (await admin_client.delete(f"/api/tools/{tool['id']}")).status_code == 409


async def test_tool_builder_returns_a_form_to_review(
    admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ymva.api.routes import tools as tools_route
    from ymva.services import integrations as integrations_service

    async def ask(api_key: str, system: str, contents: list[Any], schema: type) -> Any:
        return schema.model_validate(
            {
                "name": "Save_Promise",
                "description": "Use when the customer gives a payment date.",
                "type": "google_sheets",
                "params": [
                    {"name": "promised_date", "description": "YYYY-MM-DD", "required": True}
                ],
                "sheet_action": "append_row",
                "sheet_columns": [{"header": "Promised date", "value": "{{promised_date}}"}],
                "sheet_match_column": "",
                "sheet_match_value": "",
                "http_method": "POST",
                "http_url": "",
                "http_body": "",
                "db_query": "",
                "db_mode": "read",
            }
        )

    async def verify_ai_key(*_: Any) -> None:
        return None

    monkeypatch.setattr(tools_route, "ask_gemini", ask)
    monkeypatch.setattr(integrations_service, "verify_ai_key", verify_ai_key)
    await admin_client.put("/api/integrations/ai/google", json={"api_key": "AIza-google-12345678"})
    resp = await admin_client.post(
        "/api/tools/generate", json={"description": "Save promised dates"}
    )
    assert resp.status_code == 200, resp.text
    draft = resp.json()
    assert draft["name"] == "save_promise" and draft["config"]["action"] == "append_row"
    assert draft["placeholders"] == ["promised_date"]
    assert (await admin_client.get("/api/tools")).json() == []  # nothing saved yet


async def test_missing_sheet_link_is_explained_and_agent_gets_tool(
    admin_client: httpx.AsyncClient,
) -> None:
    no_link = json.loads(json.dumps(SHEET_TOOL))
    no_link["config"]["spreadsheet"] = ""
    resp = await admin_client.post("/api/tools", json=no_link)
    assert (
        resp.status_code == 422 and resp.json()["detail"] == "Paste the link to your Google Sheet"
    )

    agent = (await admin_client.post("/api/agents", json={"name": "Reminder"})).json()
    resp = await admin_client.post("/api/tools", json={**SHEET_TOOL, "agent_id": agent["id"]})
    assert resp.status_code == 201, resp.text
    agent = (await admin_client.get(f"/api/agents/{agent['id']}")).json()
    assert agent["config"]["tools"] == ["save_promise"] and agent["has_draft_changes"]


async def test_database_tool_binds_values_and_respects_read_only() -> None:
    import os

    import asyncpg

    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    await conn.execute(
        "drop table if exists zz_loans; create table zz_loans"
        " (loan_id text primary key, interest_due numeric, promised date)"
    )
    await conn.execute("insert into zz_loans values ('YM1', 2450.50, null)")
    secrets = {"connection": url}
    try:
        read = {"query": "select interest_due from zz_loans where loan_id = {{loan_id}}"}
        found = await tools_service.run_tool("database", read, secrets, {"loan_id": "YM1"})
        assert found == {"ok": True, "rows": [{"interest_due": "2450.50"}], "count": 1}
        sneaky = await tools_service.run_tool(
            "database", read, secrets, {"loan_id": "x' or '1'='1"}
        )
        assert sneaky["rows"] == []  # the value is data, never SQL

        update = "update zz_loans set promised = {{promised_date}} where loan_id = {{loan_id}}"
        blocked = await tools_service.run_tool(
            "database",
            {"query": update},
            secrets,
            {"loan_id": "YM1", "promised_date": "2026-11-20"},
        )
        assert not blocked["ok"] and "read-only" in blocked["error"]
        done = await tools_service.run_tool(
            "database",
            {"query": update, "mode": "write"},
            secrets,
            {"loan_id": "YM1", "promised_date": "2026-11-20"},
        )
        assert done["result"] == "UPDATE 1"
        assert str(await conn.fetchval("select promised from zz_loans")) == "2026-11-20"
    finally:
        await conn.execute("drop table zz_loans")
        await conn.close()


async def test_mcp_tool_lists_and_calls_server_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp import Client
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("crm")

    @server.tool()
    def loan_status(loan_id: str, months: int = 1) -> str:
        """Interest due for a loan."""
        return f"{loan_id}: 2450 due for {months} month(s)"

    monkeypatch.setattr(tools_service, "_mcp_client", lambda url, headers: Client(server))

    found = await tools_service.mcp_tools("https://crm.example.com/mcp", {})
    assert found[0]["tool"] == "loan_status"
    assert found[0]["arg_types"] == {"loan_id": "string", "months": "integer"}
    assert [p["name"] for p in found[0]["params"] if p["required"]] == ["loan_id"]

    config = {"server_url": "https://crm.example.com/mcp", "tool": "loan_status"}
    config["arg_types"] = found[0]["arg_types"]
    result = await tools_service.run_tool("mcp", config, {}, {"loan_id": "YM1", "months": "3"})
    assert result["ok"] and "YM1: 2450 due for 3 month(s)" in str(result["response"])
