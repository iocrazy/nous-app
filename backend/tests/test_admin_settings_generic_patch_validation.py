"""``PATCH /admin/settings/{key}`` runs the known-key allowlist before writing.

Invalid values for a known key answer 422 with a typed
``{"code": "setting_invalid", "key", "reason"}`` detail — carried in the
production ``ErrorResponse`` envelope's ``details`` — and write nothing.
"""

from __future__ import annotations

import sys

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.admin_deps import get_admin_auth
from app.core.deps import AuthContext
from app.main import app

settings_router = sys.modules["app.api.admin.settings_router"]

BASE = "/api/v1/admin/settings"


async def _fake_admin() -> AuthContext:
    return AuthContext(user_id="admin-1", auth_type="jwt")


class _FakeRepo:
    def __init__(self, rows: dict[str, object]) -> None:
        self.rows = dict(rows)
        self.writes: list[tuple[str, object]] = []

    async def exists(self, key: str) -> bool:
        return key in self.rows

    async def update(self, key: str, value: object, updated_by: str) -> dict:
        self.rows[key] = value
        self.writes.append((key, value))
        return {"key": key, "value": value, "updated_at": "2026-01-01T00:00:00"}


@pytest.fixture(autouse=True)
def _override_admin():
    app.dependency_overrides[get_admin_auth] = _fake_admin
    yield
    app.dependency_overrides.pop(get_admin_auth, None)


@pytest.fixture
def audits(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    seen: list[dict] = []

    async def _record(**kwargs: object) -> None:
        seen.append(kwargs)

    monkeypatch.setattr(settings_router, "create_audit_log", _record)
    return seen


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


def _install(monkeypatch: pytest.MonkeyPatch, rows: dict[str, object]) -> _FakeRepo:
    repo = _FakeRepo(rows)
    monkeypatch.setattr(settings_router, "get_system_settings_repository", lambda: repo)
    return repo


@pytest.mark.asyncio
async def test_invalid_cost_floor_is_422_setting_invalid(monkeypatch, client, audits):
    key = "agent_cost_anomaly.min_hour_cost_cents"
    repo = _install(monkeypatch, {key: 0.5})
    r = await client.patch(f"{BASE}/{key}", json={"value": "lots"})
    assert r.status_code == 422, r.text
    details = r.json()["details"]
    assert details["code"] == "setting_invalid"
    assert details["key"] == key
    assert details["reason"]
    assert repo.writes == [] and audits == []


@pytest.mark.asyncio
async def test_string_true_for_bool_key_is_accepted(monkeypatch, client, audits):
    repo = _install(monkeypatch, {"issue_agent_auto_close": "false"})
    r = await client.patch(f"{BASE}/issue_agent_auto_close", json={"value": "true"})
    assert r.status_code == 200, r.text
    assert repo.writes == [("issue_agent_auto_close", "true")]


@pytest.mark.asyncio
async def test_native_bool_key_is_normalised_before_write(monkeypatch, client, audits):
    repo = _install(monkeypatch, {"nous.user_enabled": False})
    r = await client.patch(f"{BASE}/nous.user_enabled", json={"value": "true"})
    assert r.status_code == 200, r.text
    # The reader checks ``value is True``: the string must not reach the row.
    assert repo.writes == [("nous.user_enabled", True)]
    assert audits[0]["details"] == {"value": True}


@pytest.mark.asyncio
async def test_unknown_key_passes_through(monkeypatch, client, audits):
    repo = _install(monkeypatch, {"some.unvalidated_key": 1})
    value = {"free": ["form", 2]}
    r = await client.patch(f"{BASE}/some.unvalidated_key", json={"value": value})
    assert r.status_code == 200, r.text
    assert repo.writes == [("some.unvalidated_key", value)]


@pytest.mark.asyncio
async def test_enum_rejects_unknown_provider(monkeypatch, client, audits):
    repo = _install(monkeypatch, {"memory.l2_provider": "none"})
    r = await client.patch(f"{BASE}/memory.l2_provider", json={"value": "mem0"})
    assert r.status_code == 422, r.text
    assert r.json()["details"]["code"] == "setting_invalid"
    assert repo.writes == []


@pytest.mark.asyncio
async def test_missing_key_is_still_404_not_422(monkeypatch, client, audits):
    _install(monkeypatch, {})
    r = await client.patch(f"{BASE}/memory.l2_provider", json={"value": "mem0"})
    assert r.status_code == 404, r.text
