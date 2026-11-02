import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, login
from ymva.config import get_settings
from ymva.db import get_sessionmaker
from ymva.models import User
from ymva.services.bootstrap import ensure_admin, reset_admin

NEW_EMAIL = "Shreeshyl@YellowMetal.co"
NEW_PASSWORD = "new-env-password-1"


@pytest.fixture
def env_admin_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "admin_email", NEW_EMAIL)
    monkeypatch.setattr(settings, "admin_name", "Shreeshyl")
    monkeypatch.setattr(settings, "admin_password", SecretStr(NEW_PASSWORD))


async def test_changing_admin_email_in_env_moves_first_admin(
    client: httpx.AsyncClient, env_admin_changed: None
) -> None:
    async with get_sessionmaker()() as session:
        await ensure_admin(session)
        users = list(await session.scalars(select(User)))
    assert [(u.email, u.name) for u in users] == [("shreeshyl@yellowmetal.co", "Shreeshyl")]

    assert (await login(client, ADMIN_EMAIL, ADMIN_PASSWORD)).status_code == 401
    resp = await login(client, NEW_EMAIL, NEW_PASSWORD)
    assert resp.status_code == 200
    assert resp.json()["role"] == "owner"

    actions = [i["action"] for i in (await client.get("/api/audit-logs")).json()["items"]]
    assert "user.admin_sync" in actions


async def test_existing_admin_password_is_kept_on_restart(admin_client: httpx.AsyncClient) -> None:
    await admin_client.post(
        "/api/auth/change-password",
        json={"current_password": ADMIN_PASSWORD, "new_password": "changed-in-ui-123"},
    )
    async with get_sessionmaker()() as session:
        await ensure_admin(session)
    await admin_client.post("/api/auth/logout")
    assert (await login(admin_client, ADMIN_EMAIL, "changed-in-ui-123")).status_code == 200


async def test_reset_admin_restores_env_password(admin_client: httpx.AsyncClient) -> None:
    me = (await admin_client.get("/api/auth/me")).json()
    await admin_client.post(
        "/api/auth/change-password",
        json={"current_password": ADMIN_PASSWORD, "new_password": "forgotten-password-9"},
    )
    async with get_sessionmaker()() as session:
        message = await reset_admin(session)
    assert message.startswith("Reset admin")
    # Existing sessions are revoked by the reset.
    assert (await admin_client.get("/api/auth/me")).status_code == 401
    resp = await login(admin_client, ADMIN_EMAIL, ADMIN_PASSWORD)
    assert resp.json()["id"] == me["id"]


async def test_reset_admin_creates_missing_admin(
    client: httpx.AsyncClient, env_admin_changed: None
) -> None:
    async with get_sessionmaker()() as session:
        message = await reset_admin(session)
        emails = set(await session.scalars(select(User.email)))
    assert message.startswith("Created admin shreeshyl@yellowmetal.co")
    assert emails == {ADMIN_EMAIL, "shreeshyl@yellowmetal.co"}
    assert (await login(client, NEW_EMAIL, NEW_PASSWORD)).status_code == 200


async def test_account_lockout_applies_across_addresses(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "login_max_attempts_per_account", 3)
    for _ in range(3):
        assert (await login(client, ADMIN_EMAIL, "wrong-password")).status_code == 401
    assert (await login(client, ADMIN_EMAIL, ADMIN_PASSWORD)).status_code == 429
