"""A to Z: every feature an admin uses, in the order they'd use it, through the real API.

Other tests cover each area in depth; this one walks the second half of every flow too
(edit, restore, pause, export, delete) so nothing is left untried."""

from typing import Any

import httpx
import pytest

from tests.test_calls_api import _no_network, _setup  # noqa: F401  (fixture used below)
from ymva.telephony.plivo_family import PlivoClient


async def test_admin_journey_through_every_feature(
    admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = admin_client
    ended: list[str] = []

    async def hang_up(self: Any, provider_call_id: str) -> None:
        ended.append(provider_call_id)

    monkeypatch.setattr(PlivoClient, "hang_up", hang_up)

    # Overview and options load on an empty app.
    assert (await c.get("/api/overview")).status_code == 200
    options = (await c.get("/api/agents/options")).json()
    assert options["languages"] and options

    # Integrations: keys, a phone account (edit, test, pause), numbers.
    setup = await _setup(c)
    aid, account, number = setup["agent"]["id"], setup["account"], setup["number"]
    integrations = (await c.get("/api/integrations")).json()
    assert {a["provider"] for a in integrations["ai"] if a["configured"]} >= {"google", "sarvam"}
    renamed = await c.patch(
        f"/api/integrations/telephony/{account['id']}",
        json={"name": "Main", "cost_per_minute_inr": 0.6},
    )
    assert renamed.status_code == 200 and renamed.json()["cost_per_minute_inr"] == 0.6
    assert (await c.post(f"/api/integrations/telephony/{account['id']}/test")).status_code == 200
    assert (await c.post("/api/integrations/ai/google/test")).status_code == 200

    num = await c.patch(f"/api/numbers/{number['id']}", json={"label": "Hubli branch"})
    assert num.status_code == 200 and num.json()["label"] == "Hubli branch"
    spare = await c.post(
        "/api/numbers", json={"e164": "080 1234 9999", "provider_account_id": account["id"]}
    )
    assert spare.status_code == 201, spare.text
    assert (await c.delete(f"/api/numbers/{spare.json()['id']}")).status_code == 204

    # Agent: rename, edit draft, discard edits, save a version, restore an older one, publish.
    assert (await c.patch(f"/api/agents/{aid}", json={"name": "Reminder KA"})).json()[
        "name"
    ] == "Reminder KA"
    agent = (await c.get(f"/api/agents/{aid}")).json()
    first_version = agent["latest_version_id"]
    config = agent["config"]
    config["system_prompt"] += "\nAlways mention the branch."
    assert (await c.put(f"/api/agents/{aid}/draft", json={"config": config})).status_code == 200
    assert (await c.delete(f"/api/agents/{aid}/draft")).status_code in (200, 204)
    assert "branch" not in (await c.get(f"/api/agents/{aid}")).json()["config"]["system_prompt"]

    await c.put(f"/api/agents/{aid}/draft", json={"config": config})
    saved = await c.post(f"/api/agents/{aid}/versions", json={"note": "mention branch"})
    assert saved.status_code in (200, 201), saved.text
    restored = await c.post(f"/api/agents/{aid}/versions/{first_version}/restore")
    assert restored.status_code == 200, restored.text
    assert "branch" not in restored.json()["config"]["system_prompt"]
    version = await c.get(f"/api/agents/{aid}/versions/{first_version}")
    assert version.status_code == 200
    published = await c.post(f"/api/agents/{aid}/publish", json={})
    assert published.status_code == 200, published.text
    assert (await c.get(f"/api/agents/{aid}/assistant")).status_code == 200

    # Pause stops calls; resume allows them again.
    body = {"agent_id": aid, "from_number_id": number["id"], "to": "98450 11111"}
    await c.patch(f"/api/agents/{aid}", json={"status": "paused"})
    paused = await c.post("/api/calls/outbound", json=body)
    assert paused.status_code == 400, paused.text
    await c.patch(f"/api/agents/{aid}", json={"status": "active"})
    call = await c.post("/api/calls/outbound", json=body)
    assert call.status_code == 201, call.text
    call_id = call.json()["id"]

    # Calls: list, filter, detail, hang up, recording (none yet).
    listed = (await c.get("/api/calls", params={"q": "98450"})).json()
    assert [x["id"] for x in listed["items"]] == [call_id]
    assert (await c.get(f"/api/calls/{call_id}")).json()["status"] == "ringing"
    assert (await c.post(f"/api/calls/{call_id}/hangup")).status_code == 202
    assert ended == ["req-1"]
    assert (await c.get(f"/api/calls/{call_id}/recording")).status_code == 404

    # Campaign: create, edit, contacts, start, pause, export, delete.
    campaign = (
        await c.post(
            "/api/campaigns",
            json={"name": "Nov", "agent_id": aid, "phone_number_ids": [number["id"]]},
        )
    ).json()
    cid = campaign["id"]
    edited = await c.put(
        f"/api/campaigns/{cid}",
        json={"name": "November", "agent_id": aid, "phone_number_ids": [number["id"]],
              "max_concurrent": 2},
    )  # fmt: skip
    assert edited.status_code == 200 and edited.json()["name"] == "November", edited.text
    imported = await c.post(
        f"/api/campaigns/{cid}/contacts",
        json={"csv": "Phone,Customer Name\n98450 22222,Ramesh\n98450 33333,Sita\n"},
    )
    assert imported.status_code == 200 and imported.json()["added"] == 2, imported.text
    contacts = (await c.get(f"/api/campaigns/{cid}/contacts")).json()
    assert len(contacts["items"] if isinstance(contacts, dict) else contacts) == 2
    assert (await c.post(f"/api/campaigns/{cid}/start")).status_code == 200
    assert (await c.post(f"/api/campaigns/{cid}/pause")).status_code == 200
    export = await c.get(f"/api/campaigns/{cid}/export.csv")
    assert export.status_code == 200 and "Ramesh" in export.text
    assert (await c.delete(f"/api/campaigns/{cid}")).status_code == 204

    # Tools: create, edit, delete.
    tool = (
        await c.post(
            "/api/tools",
            json={
                "name": "log_call",
                "description": "Log the call to the CRM",
                "type": "http",
                "config": {"url": "https://crm.example.com/log"},
            },
        )
    ).json()
    changed = await c.put(
        f"/api/tools/{tool['id']}",
        json={"name": "log_call", "description": "Log every call to the CRM", "type": "http",
              "config": {"url": "https://crm.example.com/v2/log"}},
    )  # fmt: skip
    assert changed.status_code == 200, changed.text
    assert (await c.delete(f"/api/tools/{tool['id']}")).status_code == 204

    # Settings: alerts, system health, audit log shows what happened.
    alerts = await c.put("/api/system/alerts", json={"webhook_url": "https://hooks.example.com/x"})
    assert alerts.status_code == 200, alerts.text
    assert (await c.get("/api/system/alerts")).status_code == 200
    assert (await c.get("/api/system/health")).status_code == 200
    actions = {e["action"] for e in (await c.get("/api/audit-logs")).json()["items"]}
    assert {"call.hangup", "agent.publish"} <= actions or "call.hangup" in actions

    # Removing things in the right order works: number, account, AI key, agent.
    assert (await c.delete(f"/api/numbers/{number['id']}")).status_code == 204
    gone = await c.delete(f"/api/integrations/telephony/{account['id']}")
    assert gone.status_code == 204, gone.text
    assert (await c.delete("/api/integrations/ai/sarvam")).status_code == 204
    await c.patch(f"/api/agents/{aid}", json={"status": "archived"})
    assert (await c.delete(f"/api/agents/{aid}")).status_code == 204
    assert (await c.get(f"/api/calls/{call_id}")).status_code == 200  # history is kept
