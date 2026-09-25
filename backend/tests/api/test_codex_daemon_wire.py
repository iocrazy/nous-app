"""Codex daemon routes: wire parity after they gained response models (P9).

Two clients read these bodies: the settings page and the daemon itself
(``tools/codex-daemon/index.mjs``), which lives on users' machines and is not
redeployed with the server. So every body must stay byte-for-byte what it was:
each test builds the dict the handler used to return and asserts the HTTP
response equals ``jsonable_encoder`` of it (``tests/api/wire_parity.py``).

The device list and revoke run through the real repository; only the session
is scripted. The revoke session evaluates the UPDATE's own bound parameters,
so the owner/stranger pair tests the WHERE clause the route actually sends.
"""

from __future__ import annotations

import json
import pathlib
import sys
from contextlib import asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models.codex_daemon import CodexDaemons
from app.schemas.codex_daemon import CodexDaemonDevice
from tests.api.wire_parity import assert_wire_unchanged, column_names, sample_orm

r = sys.modules["app.api.codex_daemon_router"]

pytestmark = pytest.mark.unit

OWNER = "00000000-0000-0000-0000-000000000042"
STRANGER = "00000000-0000-0000-0000-000000000099"
DEVICE_ID = 7300000000000000123
TOOLS = pathlib.Path(__file__).resolve().parents[3] / "tools" / "codex-daemon"

_CALLER = {"user_id": OWNER}


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=_CALLER["user_id"], auth_type="jwt")


@pytest.fixture(autouse=True)
def _wiring():
    _CALLER["user_id"] = OWNER
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── pairing / upload ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pair_code_wire_unchanged(monkeypatch, client):
    async def _store(code: str, user_id: str) -> None:
        return None

    monkeypatch.setattr(r, "_store_pair_code", _store)
    monkeypatch.setattr(r, "_mint_code", lambda: "ABCD2345")
    resp = await client.post("/api/v1/codex-daemon/pair-code")
    assert_wire_unchanged(
        resp, {"data": {"code": "ABCD2345", "expires_in_seconds": r.PAIR_TTL_SECONDS}}
    )


@pytest.mark.asyncio
async def test_pair_wire_unchanged(monkeypatch, client):
    async def _consume(code: str):
        return OWNER

    async def _insert(**kwargs: Any) -> dict:
        return {"id": DEVICE_ID, "device_name": kwargs["device_name"]}

    monkeypatch.setattr(r, "_consume_pair_code", _consume)
    monkeypatch.setattr(r, "_insert_daemon", _insert)
    monkeypatch.setattr(r.secrets, "token_urlsafe", lambda n: "tok-" + "x" * 40)
    resp = await client.post(
        "/api/v1/codex-daemon/pair",
        json={"code": "ABCD2345", "device_name": "mac-mini", "platform": "darwin"},
    )
    assert_wire_unchanged(
        resp,
        {"data": {"device_id": str(DEVICE_ID), "device_token": "tok-" + "x" * 40}},
    )


@pytest.mark.asyncio
async def test_upload_wire_unchanged(monkeypatch, client):
    async def _consume(ticket: str):
        return {"user_id": OWNER, "scope_id": 42, "job_id": "j-1"}

    async def _register(**kwargs: Any) -> dict:
        return {"id": DEVICE_ID}

    monkeypatch.setattr(r, "_consume_upload_ticket", _consume)
    monkeypatch.setattr(r, "_register_daemon_result", _register)
    resp = await client.post(
        "/api/v1/codex-daemon/upload",
        files={"file": ("out.png", b"\x89PNG data", "image/png")},
        data={"ticket": "good"},
    )
    assert_wire_unchanged(resp, {"data": {"gen_id": str(DEVICE_ID)}})


# ── devices (real repository, scripted session) ───────────────────────────


class _Scalars:
    def __init__(self, objs: list[Any]):
        self._objs = objs

    def all(self) -> list[Any]:
        return list(self._objs)


class _Result:
    def __init__(self, objs: list[Any] | None = None, rowcount: int = 0):
        self._objs = objs or []
        self.rowcount = rowcount

    def scalars(self) -> _Scalars:
        return _Scalars(self._objs)


def _script_session(monkeypatch, handler) -> None:
    import app.repositories.codex_daemon_repository as mod

    class _Session:
        async def execute(self, stmt, *a, **kw):
            return handler(stmt)

    @asynccontextmanager
    async def _session(scope):
        yield _Session()

    monkeypatch.setattr(mod, "user_session", _session)


def _device_rows() -> list[CodexDaemons]:
    live = sample_orm(CodexDaemons, id=DEVICE_ID)
    never_seen = sample_orm(
        CodexDaemons, id=DEVICE_ID + 1, last_seen_at=None, env_report=None
    )
    return [live, never_seen]


def _as_repo_dict(row: CodexDaemons) -> dict:
    """What ``list_for_user`` built before the route declared a model."""
    return {
        "id": str(row.id),
        "device_name": row.device_name,
        "platform": row.platform,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "env_report": row.env_report,
    }


@pytest.mark.asyncio
async def test_list_devices_wire_unchanged(monkeypatch, client):
    rows = _device_rows()
    _script_session(monkeypatch, lambda stmt: _Result(objs=rows))
    resp = await client.get("/api/v1/codex-daemon/devices")
    assert_wire_unchanged(resp, {"data": [_as_repo_dict(x) for x in rows]})


def test_device_schema_covers_every_key_the_repository_emits():
    emitted = set(_as_repo_dict(_device_rows()[0]))
    assert set(CodexDaemonDevice.model_fields) == emitted
    # The token hash, the owner and the revocation stamp never leave the server.
    assert {"token_hash", "user_id", "revoked_at"} <= column_names(CodexDaemons)
    assert not emitted & {"token_hash", "user_id", "revoked_at"}


def _revoke_handler(owner: str):
    """Answer the UPDATE by its own bound parameters, the way Postgres would
    for a table holding one live device of ``owner``: it matches when it names
    the device, and every ``user_id`` condition it carries names the owner.
    Drop the owner condition and a stranger matches too."""

    def _handle(stmt):
        params = stmt.compile().params
        users = [v for k, v in params.items() if k.startswith("user_id")]
        hit = DEVICE_ID in params.values() and all(str(u) == owner for u in users)
        return _Result(rowcount=1 if hit else 0)

    return _handle


@pytest.mark.asyncio
async def test_revoke_device_owner_wire_unchanged(monkeypatch, client):
    disconnected: list = []

    async def _disconnect(user_id: str, device_id: str) -> None:
        disconnected.append((user_id, device_id))

    from app.services.codex.daemon_registry import registry

    monkeypatch.setattr(registry, "disconnect_device", _disconnect)
    _script_session(monkeypatch, _revoke_handler(OWNER))
    resp = await client.delete(f"/api/v1/codex-daemon/devices/{DEVICE_ID}")
    assert_wire_unchanged(resp, {"data": {"revoked": True}})
    assert disconnected == [(OWNER, str(DEVICE_ID))]


@pytest.mark.asyncio
async def test_revoke_device_stranger_gets_typed_404(monkeypatch, client):
    disconnected: list = []

    async def _disconnect(user_id: str, device_id: str) -> None:
        disconnected.append((user_id, device_id))

    from app.services.codex.daemon_registry import registry

    monkeypatch.setattr(registry, "disconnect_device", _disconnect)
    _script_session(monkeypatch, _revoke_handler(OWNER))
    _CALLER["user_id"] = STRANGER
    resp = await client.delete(f"/api/v1/codex-daemon/devices/{DEVICE_ID}")
    assert resp.status_code == 404, resp.text
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"
    assert disconnected == []


# ── dist (public, read by the installer and ``--update``) ─────────────────


@pytest.mark.asyncio
async def test_dist_version_bytes_and_headers_unchanged(client):
    resp = await client.get("/api/v1/codex-daemon/dist/version.json")
    pkg = json.loads((TOOLS / "package.json").read_text(encoding="utf-8"))
    # What ``JSONResponse({"version": ...})`` rendered before.
    expected = json.dumps(
        {"version": str(pkg.get("version") or "")},
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode()
    assert resp.status_code == 200
    assert resp.content == expected
    assert resp.headers["content-type"] == "application/json"
    assert resp.headers["cache-control"] == "no-cache, must-revalidate"


def test_dist_file_is_declared_as_text_not_json():
    schema = app.openapi()
    op = schema["paths"]["/api/v1/codex-daemon/dist/{name}"]["get"]
    content = op["responses"]["200"]["content"]
    assert "application/json" not in content
    assert {"text/x-shellscript", "text/javascript", "text/plain"} <= set(content)
