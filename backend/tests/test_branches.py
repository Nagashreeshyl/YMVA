"""Calls are transferred to the manager of the branch where the customer took their loan."""

import httpx

from ymva.db import get_sessionmaker
from ymva.services.branches import transfer_number
from ymva.voice.config import AgentConfig, Transfer, call_details


def _agent(**transfer: object) -> AgentConfig:
    return AgentConfig(system_prompt="Remind {{customer_name}}.", transfer=Transfer(**transfer))


async def test_transfer_goes_to_the_customers_branch_manager(
    admin_client: httpx.AsyncClient,
) -> None:
    c = admin_client
    made = await c.post(
        "/api/branches",
        json={"name": "Chintamani", "manager_name": "Ravi", "manager_phone": "98450 11111"},
    )
    assert made.status_code == 201, made.text
    assert made.json()["manager_phone"] == "+919845011111"
    await c.post(
        "/api/branches",
        json={
            "name": "Bangalore",
            "other_names": ["Bengaluru", " BLR "],
            "manager_name": "Asha",
            "manager_phone": "+918012345678",
        },
    )
    twin = await c.post("/api/branches", json={"name": "Chintamani", "manager_phone": "9845022222"})
    assert twin.status_code == 409
    assert [b["name"] for b in (await c.get("/api/branches")).json()][:2] == [
        "Bangalore",
        "Chintamani",
    ]

    by_branch = _agent(enabled=True, to="branch_manager", phone="+918000000000")
    async with get_sessionmaker()() as db:
        assert await transfer_number(db, by_branch, {"branch": "Chintamani"}) == "+919845011111"
        assert await transfer_number(db, by_branch, {"branch": "bengaluru"}) == "+918012345678"
        assert await transfer_number(db, by_branch, {"branch": "BLR"}) == "+918012345678"
        # Unknown or missing branch: the agent's fixed number.
        assert await transfer_number(db, by_branch, {"branch": "Kolar"}) == "+918000000000"
        assert await transfer_number(db, by_branch, {}) == "+918000000000"
        fixed = _agent(enabled=True, phone="+918000000000")
        assert await transfer_number(db, fixed, {"branch": "Chintamani"}) == "+918000000000"
        assert await transfer_number(db, _agent(), {"branch": "Chintamani"}) is None

    # Apps must then send the branch with every call.
    assert [d.name for d in call_details(by_branch)] == ["customer_name", "branch"]
    assert [d.name for d in call_details(_agent(enabled=True))] == ["customer_name"]


async def test_only_admins_change_branches(admin_client: httpx.AsyncClient) -> None:
    from tests.conftest import login

    c = admin_client
    user = {"email": "caller@x.co", "name": "c", "role": "caller", "password": "caller-password-1"}
    assert (await c.post("/api/users", json=user)).status_code == 201
    await c.post("/api/auth/logout")
    assert (await login(c, "caller@x.co", "caller-password-1")).status_code == 200
    assert (await c.get("/api/branches")).status_code == 200  # to pick a customer's branch
    denied = await c.post("/api/branches", json={"name": "X", "manager_phone": "9845011111"})
    assert denied.status_code == 403
