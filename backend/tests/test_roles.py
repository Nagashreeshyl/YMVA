"""Each of the six roles can reach exactly what it is allowed to, and nothing else."""

from typing import Any

import httpx

from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, login
from tests.test_calls_api import _no_network, _setup  # noqa: F401  (fixture used below)

ROLES = ["owner", "admin", "campaign_manager", "caller", "reviewer", "viewer"]
EVERYONE = set(ROLES)
MANAGERS = {"owner", "admin"}


async def _as(c: httpx.AsyncClient, role: str) -> None:
    await c.post("/api/auth/logout")
    email, password = (
        (ADMIN_EMAIL, ADMIN_PASSWORD) if role == "owner" else (f"{role}@x.co", f"{role}-password-1")
    )
    assert (await login(c, email, password)).status_code == 200


async def test_each_role_reaches_only_what_it_may(admin_client: httpx.AsyncClient) -> None:
    c = admin_client
    setup = await _setup(c)
    aid, number = setup["agent"]["id"], setup["number"]["id"]
    for role in ROLES[1:]:
        user = {
            "email": f"{role}@x.co",
            "name": role,
            "role": role,
            "password": f"{role}-password-1",
        }
        assert (await c.post("/api/users", json=user)).status_code == 201

    call: dict[str, Any] = {"agent_id": aid, "from_number_id": number, "to": "98450 11111"}
    checks: list[tuple[str, str, Any, set[str]]] = [
        ("GET", "/api/overview", None, EVERYONE),
        ("GET", "/api/agents", None, EVERYONE),
        ("GET", f"/api/agents/{aid}", None, EVERYONE - {"reviewer"}),
        ("GET", "/api/calls", None, EVERYONE),
        ("GET", "/api/campaigns", None, {"owner", "admin", "campaign_manager", "viewer"}),
        ("GET", "/api/numbers", None, {"owner", "admin", "campaign_manager", "caller"}),
        ("GET", "/api/tools", None, {"owner", "admin", "campaign_manager", "viewer"}),
        ("GET", "/api/integrations", None, MANAGERS),
        ("GET", "/api/do-not-call", None, MANAGERS),
        ("GET", "/api/system/health", None, MANAGERS),
        ("GET", "/api/audit-logs", None, MANAGERS),
        ("GET", "/api/users", None, MANAGERS),
        ("PATCH", f"/api/agents/{aid}", {"name": "Reminder"}, MANAGERS),
        ("POST", "/api/campaigns", {"name": "N", "agent_id": aid, "phone_number_ids": [number]},
         {"owner", "admin", "campaign_manager"}),
        ("POST", "/api/calls/outbound", call, {"owner", "admin", "campaign_manager", "caller"}),
    ]  # fmt: skip
    for role in ROLES:
        await _as(c, role)
        for method, url, body, allowed in checks:
            resp = await c.request(method, url, json=body)
            assert (resp.status_code != 403) == (role in allowed), (role, method, url, resp.text)
        me = (await c.get("/api/auth/me")).json()
        assert me["role"] == role and ("costs.view" in me["permissions"]) == (
            role in {"owner", "admin", "campaign_manager", "viewer"}
        )


async def test_callers_see_only_their_own_calls_and_no_costs(
    admin_client: httpx.AsyncClient,
) -> None:
    c = admin_client
    setup = await _setup(c)
    body = {"agent_id": setup["agent"]["id"], "from_number_id": setup["number"]["id"]}
    owners = (await c.post("/api/calls/outbound", json={**body, "to": "98450 11111"})).json()
    user = {"email": "caller@x.co", "name": "C", "role": "caller", "password": "caller-password-1"}
    await c.post("/api/users", json=user)
    await _as(c, "caller")
    mine = (await c.post("/api/calls/outbound", json={**body, "to": "98450 22222"})).json()

    listed = (await c.get("/api/calls")).json()["items"]
    assert [x["id"] for x in listed] == [mine["id"]]
    assert all(x["cost_total"] is None for x in listed)
    assert (await c.get(f"/api/calls/{owners['id']}")).status_code == 404
    assert (await c.post(f"/api/calls/{owners['id']}/hangup")).status_code == 404
    assert (await c.get(f"/api/calls/{mine['id']}")).status_code == 200
    assert (await c.get("/api/overview")).json()["cost_7d"] is None


async def test_only_owners_manage_owners_and_one_owner_stays(
    admin_client: httpx.AsyncClient,
) -> None:
    c = admin_client
    me = (await c.get("/api/auth/me")).json()
    demote = await c.patch(f"/api/users/{me['id']}", json={"role": "admin"})
    assert demote.status_code == 400  # the last owner

    admin = {"email": "admin@x.co", "name": "A", "role": "admin", "password": "admin-password-1"}
    await c.post("/api/users", json=admin)
    await _as(c, "admin")
    new_owner = {**admin, "email": "o2@x.co", "role": "owner"}
    assert (await c.post("/api/users", json=new_owner)).status_code == 403
    assert (await c.patch(f"/api/users/{me['id']}", json={"is_active": False})).status_code == 403
    viewer = {**admin, "email": "v@x.co", "role": "viewer"}
    assert (await c.post("/api/users", json=viewer)).status_code == 201
