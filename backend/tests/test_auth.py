import httpx

from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, login


async def test_health(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/health")).json()["status"] == "ok"
    ready = await client.get("/api/health/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"] == {"database": "ok", "redis": "ok"}


async def test_requires_sign_in(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await client.get("/api/overview")).status_code == 401


async def test_login_me_logout(client: httpx.AsyncClient) -> None:
    resp = await login(client, ADMIN_EMAIL.upper(), ADMIN_PASSWORD)
    assert resp.status_code == 200
    assert resp.json()["role"] == "owner"
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie

    me = await client.get("/api/auth/me")
    assert me.json()["email"] == ADMIN_EMAIL

    assert (await client.post("/api/auth/logout")).status_code == 204
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_wrong_password_and_unknown_user_look_the_same(client: httpx.AsyncClient) -> None:
    wrong = await login(client, ADMIN_EMAIL, "not-the-password")
    unknown = await login(client, "nobody@yellowmetal.co", "not-the-password")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


async def test_login_rate_limited(client: httpx.AsyncClient) -> None:
    for _ in range(5):
        assert (await login(client, ADMIN_EMAIL, "wrong-password")).status_code == 401
    resp = await login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    assert resp.status_code == 429


async def test_stolen_session_cookie_is_rejected_after_logout(client: httpx.AsyncClient) -> None:
    await login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    token = client.cookies["ymva_session"]
    await client.post("/api/auth/logout")
    client.cookies.set("ymva_session", token)
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_change_password_signs_out_other_sessions(admin_client: httpx.AsyncClient) -> None:
    transport = admin_client._transport
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as other:
        await login(other, ADMIN_EMAIL, ADMIN_PASSWORD)
        assert (await other.get("/api/auth/me")).status_code == 200

        bad = await admin_client.post(
            "/api/auth/change-password",
            json={"current_password": "wrong", "new_password": "new-password-456"},
        )
        assert bad.status_code == 400
        ok = await admin_client.post(
            "/api/auth/change-password",
            json={"current_password": ADMIN_PASSWORD, "new_password": "new-password-456"},
        )
        assert ok.status_code == 204
        assert (await admin_client.get("/api/auth/me")).status_code == 200
        assert (await other.get("/api/auth/me")).status_code == 401


async def test_cross_origin_writes_blocked(admin_client: httpx.AsyncClient) -> None:
    resp = await admin_client.post(
        "/api/auth/logout", headers={"origin": "https://evil.example.com"}
    )
    assert resp.status_code == 403
    assert (await admin_client.get("/api/auth/me")).status_code == 200
