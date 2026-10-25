import os
from collections.abc import AsyncIterator, Iterator

from cryptography.fernet import Fernet

os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": os.environ.get(
            "TEST_DATABASE_URL", "postgresql+asyncpg://ymva:ymva@localhost:5432/ymva_test"
        ),
        "REDIS_URL": os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15"),
        "SECRET_KEY": "test-secret-key",
        "ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "ADMIN_EMAIL": "admin@yellowmetal.co",
        "ADMIN_PASSWORD": "admin-password-123",
        "PUBLIC_URL": "http://test",
    }
)

import httpx
import pytest
from sqlalchemy import text

from ymva.cli import migrate
from ymva.db import get_sessionmaker
from ymva.main import create_app
from ymva.models import Base
from ymva.redis import get_redis
from ymva.services.bootstrap import ensure_admin

ADMIN_EMAIL = os.environ["ADMIN_EMAIL"]
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]


@pytest.fixture(scope="session", autouse=True)
def _migrated_db() -> Iterator[None]:
    migrate()
    yield


@pytest.fixture(autouse=True)
async def _clean_state() -> AsyncIterator[None]:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with get_sessionmaker()() as session:
        await session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        await session.commit()
        await ensure_admin(session)
    await get_redis().flushdb()
    yield


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def login(client: httpx.AsyncClient, email: str, password: str) -> httpx.Response:
    return await client.post("/api/auth/login", json={"email": email, "password": password})


@pytest.fixture
async def admin_client(client: httpx.AsyncClient) -> httpx.AsyncClient:
    resp = await login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    assert resp.status_code == 200, resp.text
    return client
