"""``/ai`` settings-side and ``/ai/memory`` routes: wire parity after they
gained response models (OpenAPI P5).

Each route runs over real HTTP; the dict the handler builds comes from the
real service code where that is cheap (the nous-models repository projection
runs on ORM-typed sample rows), otherwise from a scripted service call. The
body must equal ``jsonable_encoder`` of what the handler returned
(``tests/api/wire_parity.py``).
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import NousModels
from app.schemas.ai_settings_responses import (
    AiCapabilityHealthRow,
    AiGovernanceResponse,
    AiNousModelPublic,
)
from app.services.ai.governance.ai_governance import ALL_MODULES, AIModuleGovernance
from tests.api.wire_parity import assert_wire_unchanged, sample_row

ai_settings_router = sys.modules["app.api.ai_settings_router"]
ai_memory_router = sys.modules["app.api.ai_memory_router"]

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"


def _ctx() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


async def _fake_auth() -> AuthContext:
    return _ctx()


@pytest.fixture(autouse=True)
def _auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ─── provider-health ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_provider_health_report(client, monkeypatch) -> None:
    persist = AsyncMock()
    monkeypatch.setattr(ai_settings_router, "persist_provider_health", persist)
    monkeypatch.setattr(ai_settings_router, "validate_provider_key", lambda _k: None)
    payload = {"provider_key": "ollama", "status": "ok", "detail": ""}
    from app.schemas.ai import ProviderHealthUpdate

    raw = await ai_settings_router.report_provider_health(
        ProviderHealthUpdate(**payload), _ctx()
    )
    response = await client.post("/api/v1/ai/provider-health", json=payload)
    assert_wire_unchanged(response, raw)
    # Written for the caller only: there is no way to name another user.
    assert persist.await_args.args[0] == USER


# ─── capability health board ──────────────────────────────────────


def _health_rows() -> list[dict[str, Any]]:
    """One of each row shape ``ai_health`` builds."""
    base = {
        "capability": "summarization",
        "label": "Rewrite (summary)",
        "agent_slug": "summarize",
        "assigned": True,
        "model": "qwen-max",
        "provider": "qwen",
        "needs_vision": False,
        "origin": "byok",
        "status": "ok",
        "hint": "",
    }
    runtime = {
        **base,
        "capability": "visual_analysis",
        "needs_vision": True,
        "status": "runtime_failing",
        "hint": "Latest run failed: AccessDenied.",
        "task_type": "ai_extract",
        "recent_runs": 4,
        "recent_failures": 1,
        "last_error": "AccessDenied",
    }
    # The first loop's error row carries no ``origin`` at all.
    first_loop_error = {k: v for k, v in base.items() if k != "origin"} | {
        "capability": "caption",
        "status": "error",
        "agent_slug": "",
        "model": "",
    }
    system_error = {**base, "capability": "chat", "origin": "", "status": "error"}
    return [base, runtime, first_loop_error, system_error]


@pytest.mark.asyncio
async def test_health_board(client, monkeypatch) -> None:
    import app.services.ai.ai_health as health_mod

    monkeypatch.setattr(
        health_mod, "get_capability_health", AsyncMock(return_value=_health_rows())
    )
    raw = await ai_settings_router.get_ai_health(_ctx())
    response = await client.get("/api/v1/ai/health")
    assert_wire_unchanged(response, raw)
    rows = response.json()["capabilities"]
    assert "origin" not in rows[2], "absent must stay absent, not become null"
    assert "task_type" not in rows[0]


def test_health_row_declares_every_key_the_service_builds() -> None:
    keys = set().union(*(_health_rows()))
    assert keys == set(AiCapabilityHealthRow.model_fields)


# ─── governance ───────────────────────────────────────────────────


def test_governance_model_covers_every_governed_module() -> None:
    """A module added to ALL_MODULES without a field here would be dropped
    from the response by the model — the frontend would read it as absent."""
    fields = set(AiGovernanceResponse.model_fields)
    assert fields == set(ALL_MODULES) | {"nous_enabled", "nous_modules"}


@pytest.mark.asyncio
async def test_governance(client, monkeypatch) -> None:
    import app.services.ai.governance.ai_governance as gov

    locked = {"transcription", "embedding"}

    async def _module(module):
        return AIModuleGovernance(allowed=module not in locked)

    monkeypatch.setattr(gov, "get_module_governance", _module)
    monkeypatch.setattr(gov, "is_nous_globally_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        gov, "is_nous_allowed", AsyncMock(side_effect=lambda m: m != "chat")
    )
    raw = await ai_settings_router.get_ai_governance(_ctx())
    response = await client.get("/api/v1/ai/governance")
    assert_wire_unchanged(response, raw)
    body = response.json()
    assert body["transcription"] is False and body["chat"] is True
    assert body["nous_modules"]["chat"] is False


# ─── nous-models (real repository projection) ─────────────────────

_PUBLIC = [
    "id",
    "name",
    "display_name",
    "actual_model",
    "type",
    "pricing_type",
    "pricing_value",
    "sort_order",
    "last_test_status",
    "last_tested_at",
    "last_test_code",
    "actual_provider",
]


def _model_row(**over: Any) -> dict[str, Any]:
    row = sample_row(NousModels, only=_PUBLIC)
    row.update(type="llm", pricing_type="per_token", last_test_status="ok")
    row.update(last_test_code=None, actual_provider="ark")
    row.update(over)
    return row


@pytest.mark.asyncio
async def test_nous_models(client, monkeypatch) -> None:
    import app.repositories.nous_model_repository as repo_mod

    rows = [
        _model_row(),
        _model_row(
            actual_provider="codex-local",
            last_test_status=None,
            last_tested_at=None,
            type="image",
        ),
    ]

    class _Session:
        async def execute(self, _stmt):
            result = MagicMock()
            result.mappings.return_value.all.return_value = [dict(r) for r in rows]
            return result

    @asynccontextmanager
    async def _scope():
        yield _Session()

    monkeypatch.setattr(repo_mod, "read_scope", _scope)
    raw = await ai_settings_router.list_nous_models(_ctx())
    response = await client.get("/api/v1/ai/nous-models")
    assert_wire_unchanged(response, raw)
    models = response.json()["models"]
    assert set(models[0]) == set(AiNousModelPublic.model_fields)
    # Native BIGINT, as it always was on this route; upstream identity stays
    # private, only the derived bit leaves.
    assert models[0]["id"] == rows[0]["id"]
    assert "actual_provider" not in models[0]
    assert [m["is_local"] for m in models] == [False, True]


@pytest.mark.asyncio
async def test_providers_route_is_gone(client) -> None:
    """Unauthenticated and without a caller anywhere in the repo."""
    response = await client.get("/api/v1/ai/providers")
    assert response.status_code in (404, 405), response.text


# ─── /ai/memory writes ────────────────────────────────────────────


@pytest.fixture
def memory(monkeypatch):
    service = MagicMock()
    service.set_peer_card = AsyncMock(return_value=True)
    service.get_conclusion = AsyncMock(
        return_value={"id": "c-1", "observed_id": f"user-{USER}"}
    )
    service.delete_conclusion = AsyncMock(return_value=True)
    service.forget_user = AsyncMock(return_value=3)
    monkeypatch.setattr(ai_memory_router, "get_honcho_memory_service", lambda: service)
    settings = MagicMock()
    settings.get_by_user_id = AsyncMock(return_value={"settings_json": {}})
    settings.patch_settings_json = AsyncMock()
    monkeypatch.setattr(ai_memory_router, "UserSettingsRepository", lambda: settings)
    monkeypatch.setattr(
        ai_memory_router,
        "get_memory_prefs",
        AsyncMock(return_value=SimpleNamespace(learn=True, inject=False)),
    )
    return service


@pytest.mark.asyncio
async def test_memory_prefs(client, memory) -> None:
    body = {"learn_enabled": True}
    raw = await ai_memory_router.put_memory_prefs(
        ai_memory_router.PrefsUpdate(**body), _ctx()
    )
    response = await client.put("/api/v1/ai/memory/prefs", json=body)
    assert_wire_unchanged(response, raw)


@pytest.mark.asyncio
async def test_memory_card(client, memory) -> None:
    body = {"lines": ["  Likes jazz ", "", "Works nights"]}
    raw = await ai_memory_router.put_memory_card(
        ai_memory_router.CardUpdate(**body), _ctx(), None
    )
    response = await client.put("/api/v1/ai/memory/card", json=body)
    assert_wire_unchanged(response, raw)
    assert response.json()["lines"] == ["Likes jazz", "Works nights"]


@pytest.mark.asyncio
async def test_memory_delete_observation(client, memory) -> None:
    raw = await ai_memory_router.delete_memory_observation("c-1", _ctx(), None)
    response = await client.delete("/api/v1/ai/memory/observations/c-1")
    assert_wire_unchanged(response, raw)


@pytest.mark.asyncio
async def test_memory_delete_someone_elses_observation_is_404(client, memory) -> None:
    memory.get_conclusion = AsyncMock(
        return_value={"id": "c-9", "observed_id": "user-someone-else"}
    )
    response = await client.delete("/api/v1/ai/memory/observations/c-9")
    assert response.status_code == 404
    memory.delete_conclusion.assert_not_awaited()


@pytest.mark.asyncio
async def test_memory_forget(client, memory) -> None:
    raw = await ai_memory_router.forget_all_memory(_ctx(), None)
    response = await client.delete("/api/v1/ai/memory")
    assert_wire_unchanged(response, raw)
