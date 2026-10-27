"""Break-it tests over every API route: who may call what, and random junk never crashes."""

import asyncio
import random
import re
import uuid
from typing import Any
from urllib.parse import quote

import httpx
import pytest

from tests.test_calls_api import _no_network, _setup  # noqa: F401  (fixture used below)
from ymva.main import create_app

PUBLIC = ("/api/health", "/api/auth/login", "/api/auth/logout", "/api/telephony/", "/api/metrics")
# Changing your own sign-in is allowed for every role.
OWN_ACCOUNT = ("/api/auth/",)
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
JUNK_STRINGS = [
    "",
    " ",
    "a" * 10_000,
    "' OR '1'='1",
    "1; DROP TABLE calls;--",
    "../../../../etc/passwd",
    "<script>alert(1)</script>",
    "{{7*7}}",
    "\x00\x01",
    "ಕನ್ನಡ 🧪",
    "-1",
    "99999999999999999999999",
    "http://169.254.169.254/latest/meta-data/",
    "null",
]


def _routes() -> list[tuple[str, str]]:
    paths = create_app().openapi()["paths"]
    found = sorted((m.upper(), p) for p, ops in paths.items() for m in ops)
    assert len(found) > 50  # the API's routes were really found
    return found


def _fill(path: str, rng: random.Random, junk: bool = True) -> str:
    if not junk:  # a well-formed id, so only the permission check can refuse
        return re.sub(r"\{[^}]+\}", lambda _: str(uuid.uuid4()), path)
    return re.sub(
        r"\{[^}]+\}",
        lambda _: quote(
            rng.choice([str(uuid.uuid4()), rng.choice(JUNK_STRINGS)[:50] or "x"]), safe=""
        ),
        path,
    )


def _junk(rng: random.Random, depth: int = 0) -> Any:
    kind = rng.randrange(8 if depth < 2 else 5)
    if kind == 0:
        return rng.choice(JUNK_STRINGS)
    if kind == 1:
        return rng.choice([0, -1, 2**63, 1.5e308, -0.0])
    if kind == 2:
        return rng.choice([True, False, None])
    if kind == 3:
        return str(uuid.uuid4())
    if kind == 4:
        return rng.choice(
            ["admin", "viewer", "pipeline", "gemini_live", "running", "+919845012345"]
        )
    if kind == 5:
        return [_junk(rng, depth + 1) for _ in range(rng.randrange(4))]
    keys = [
        "name",
        "config",
        "id",
        "role",
        "status",
        "phone",
        "numbers",
        "csv",
        "url",
        "type",
        "agent_id",
        "email",
        "password",
        "to",
    ]
    keys += ["variables", "api_key", "webhook_url"]
    return {rng.choice(keys): _junk(rng, depth + 1) for _ in range(rng.randrange(1, 6))}


async def test_signed_out_visitors_get_nothing(client: httpx.AsyncClient) -> None:
    rng = random.Random(  # noqa: S311 (repeatable junk, not security)
        1
    )
    leaks = []
    for method, path in _routes():
        if path.startswith(PUBLIC):
            continue
        resp = await client.request(method, _fill(path, rng, junk=False), json={})
        if resp.status_code != 401:
            leaks.append(f"{method} {path} -> {resp.status_code}")
    assert leaks == []


async def test_viewers_cannot_change_anything(admin_client: httpx.AsyncClient) -> None:
    await admin_client.post(
        "/api/users",
        json={"email": "ceo@yellowmetal.co", "name": "CEO", "role": "viewer",
              "password": "viewer-password-1"},
    )  # fmt: skip
    await admin_client.post("/api/auth/logout")
    signed_in = await admin_client.post(
        "/api/auth/login", json={"email": "ceo@yellowmetal.co", "password": "viewer-password-1"}
    )
    assert signed_in.status_code == 200
    rng = random.Random(  # noqa: S311 (repeatable junk, not security)
        2
    )
    allowed = []
    for method, path in _routes():
        if method not in UNSAFE or path.startswith(PUBLIC + OWN_ACCOUNT):
            continue
        resp = await admin_client.request(method, _fill(path, rng, junk=False), json={})
        if resp.status_code != 403:
            allowed.append(f"{method} {path} -> {resp.status_code}")
    assert allowed == []


async def test_monkey_junk_never_crashes_the_api(admin_client: httpx.AsyncClient) -> None:
    await _setup(admin_client)  # some real rows, so junk also meets existing data
    rng = random.Random(  # noqa: S311 (repeatable junk, not security)
        3
    )
    crashes = []
    for method, path in _routes():
        if path in ("/api/auth/logout",):
            continue
        for _ in range(40):
            url = _fill(path, rng)
            params = {rng.choice(["q", "status", "limit", "page", "agent_id", "outcome",
                                  "call", "since"]): str(_junk(rng))[:200]}  # fmt: skip
            body = _junk(rng)
            try:
                resp = await admin_client.request(method, url, params=params, json=body)
            except Exception as exc:  # an unhandled error escaping the app
                crashes.append(f"{method} {url[:80]} {body!r:.120} -> {exc!r:.200}")
                continue
            if resp.status_code >= 500:
                crashes.append(f"{method} {url[:80]} {body!r:.120} -> {resp.status_code}")
    assert crashes == [], "\n".join(crashes[:30])


async def test_tools_and_alerts_cannot_reach_this_server_or_cloud_metadata() -> None:
    import pytest

    from ymva.services import egress, monitoring
    from ymva.services.tools import run_tool

    for url in (
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "http://127.0.0.1:8000/api/metrics",
        "http://localhost:6379/",
        "http://redis:6379/",
        "http://[::ffff:127.0.0.1]/",
    ):
        result = await run_tool("http", {"url": url, "method": "GET"}, {}, {})
        assert result["ok"] is False and "doesn't send requests" in result["error"], url
    with pytest.raises(egress.BlockedDestinationError):
        await monitoring.send_alert("http://169.254.169.254/hook", "hi")
    await egress.check_destination("https://hooks.slack.com/services/x")  # public: allowed


async def test_other_websites_cannot_act_as_a_signed_in_admin(
    admin_client: httpx.AsyncClient,
) -> None:
    evil = await admin_client.post(
        "/api/do-not-call",
        json={"numbers": "9845012345"},
        headers={"Origin": "https://evil.example"},
    )
    assert evil.status_code == 403
    ours = await admin_client.post(
        "/api/do-not-call", json={"numbers": "9845012345"}, headers={"Origin": "http://test"}
    )
    assert ours.status_code == 200


def test_webhook_secrets_never_reach_the_logs() -> None:
    from ymva.logging import _mask_value

    path = "/api/telephony/vobiz/923975b8-a16f/S3cr3t-Value/answer?call=1"
    assert "S3cr3t" not in _mask_value(path) and "/answer" in _mask_value(path)
    assert "T0ken" not in _mask_value('"WebSocket /api/telephony/plivo/ws/T0ken" [accepted]')
    assert _mask_value("/api/integrations/telephony/abc/test") == (
        "/api/integrations/telephony/abc/test"
    )


async def test_a_forged_authorization_header_cannot_skip_the_origin_check(
    admin_client: httpx.AsyncClient,
) -> None:
    evil = {"Origin": "https://evil.example", "Authorization": "Basic anything"}
    resp = await admin_client.post("/api/do-not-call", json={"numbers": "9845012345"}, headers=evil)
    assert resp.status_code == 403
    # With its own credentials, a request is judged on them alone, never the cookie.
    bogus = await admin_client.get("/api/auth/me", headers={"Authorization": "Bearer junk"})
    assert bogus.status_code == 401


async def test_tools_connect_only_to_the_address_that_was_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DNS rebinding: a name that is public when checked and internal when connecting."""
    import socket

    from ymva.services import egress
    from ymva.services.tools import HttpConfig, run_tool

    answers = iter(["93.184.215.14", "169.254.169.254"])

    async def getaddrinfo(host: str, *_: Any, **__: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers, "169.254.169.254"), 0))]

    connected: list[str] = []

    class Inner:
        async def connect_tcp(self, host: str, port: int, *_: Any, **__: Any) -> Any:
            connected.append(host)
            return None

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)
    backend = egress._GuardedBackend(Inner())
    await backend.connect_tcp("rebind.example", 443)
    assert connected == ["93.184.215.14"]  # the checked IP, not a fresh lookup
    with pytest.raises(egress.BlockedDestinationError):
        await backend.connect_tcp("rebind.example", 443)  # now internal: refused

    # Private addresses (this server's containers by IP) are blocked by default.
    result = await run_tool("http", {"url": "http://172.18.0.3:6379/", "method": "GET"}, {}, {})
    assert not result["ok"] and "doesn't send requests" in result["error"]
    # A caller's words can't choose the server.
    with pytest.raises(ValueError):
        HttpConfig(url="https://{{host}}/x")
