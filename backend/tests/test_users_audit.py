import httpx

from tests.conftest import ADMIN_EMAIL, login

VIEWER = {
    "email": "ceo@yellowmetal.co",
    "name": "CEO",
    "role": "viewer",
    "password": "viewer-password-1",
}


async def test_admin_manages_users(admin_client: httpx.AsyncClient) -> None:
    created = await admin_client.post("/api/users", json=VIEWER)
    assert created.status_code == 201
    assert created.json()["role"] == "viewer"

    dup = await admin_client.post("/api/users", json=VIEWER)
    assert dup.status_code == 409

    short = await admin_client.post(
        "/api/users", json={**VIEWER, "email": "x@yellowmetal.co", "password": "short"}
    )
    assert short.status_code == 422

    users = (await admin_client.get("/api/users")).json()
    assert {u["email"] for u in users} == {ADMIN_EMAIL, VIEWER["email"]}


async def test_viewer_is_read_only(admin_client: httpx.AsyncClient) -> None:
    await admin_client.post("/api/users", json=VIEWER)
    await admin_client.post("/api/auth/logout")
    assert (await login(admin_client, VIEWER["email"], VIEWER["password"])).status_code == 200

    assert (await admin_client.get("/api/overview")).status_code == 200
    assert (await admin_client.get("/api/audit-logs")).status_code == 200
    assert (await admin_client.get("/api/users")).status_code == 403
    assert (await admin_client.post("/api/users", json=VIEWER)).status_code == 403


async def test_last_admin_cannot_be_removed(admin_client: httpx.AsyncClient) -> None:
    me = (await admin_client.get("/api/auth/me")).json()
    demote = await admin_client.patch(f"/api/users/{me['id']}", json={"role": "viewer"})
    assert demote.status_code == 400
    deactivate = await admin_client.patch(f"/api/users/{me['id']}", json={"is_active": False})
    assert deactivate.status_code == 400


async def test_deactivated_user_is_signed_out(admin_client: httpx.AsyncClient) -> None:
    viewer = (await admin_client.post("/api/users", json=VIEWER)).json()
    async with httpx.AsyncClient(
        transport=admin_client._transport, base_url="http://test"
    ) as other:
        await login(other, VIEWER["email"], VIEWER["password"])
        assert (await other.get("/api/auth/me")).status_code == 200
        resp = await admin_client.patch(f"/api/users/{viewer['id']}", json={"is_active": False})
        assert resp.status_code == 200
        assert (await other.get("/api/auth/me")).status_code == 401
        assert (await login(other, VIEWER["email"], VIEWER["password"])).status_code == 401


async def test_changes_are_audited(admin_client: httpx.AsyncClient) -> None:
    viewer = (await admin_client.post("/api/users", json=VIEWER)).json()
    await admin_client.patch(f"/api/users/{viewer['id']}", json={"name": "Chief Executive"})

    page = (await admin_client.get("/api/audit-logs")).json()
    actions = [item["action"] for item in page["items"]]
    assert actions[:3] == ["user.update", "user.create", "auth.login"]
    assert "user.bootstrap" in actions
    update = page["items"][0]
    assert update["changes"] == {"name": ["CEO", "Chief Executive"]}
    assert update["actor_label"] == ADMIN_EMAIL
