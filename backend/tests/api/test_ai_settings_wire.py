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
from tests.api.wire_parity import assert_wire_unchanged, sample_orm

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


# ─── platform provider view (real repository + scripted engine) ───

ENGINE = "http://engine.test/v1"


def _orm_row(name: str, **over: Any) -> NousModels:
    values = {
        "name": name,
        "display_name": name.title(),
        "type": "llm",
        "actual_provider": "ark",
        "actual_model": f"{name}-upstream",
        "api_key": "sk-platform",
        "base_url": "https://ark.example/api/v3",
        "pricing_type": "per_token",
        "is_enabled": True,
        "owner_user_id": None,
        "last_test_status": "ok",
        "last_test_code": None,
    }
    values.update(over)
    return sample_orm(NousModels, **values)


# nous-engine /v1/models?include_unready=1 — the real wire body.
ENGINE_BODY = {
    "object": "list",
    "data": [
        {
            "id": "qwen3-8b",
            "object": "model",
            "type": "llm",
            "ready": True,
            "context_window": 32768,
            "capabilities": {"vision": False},
        },
        {
            "id": "wemm-2b",
            "object": "model",
            "type": "embedding",
            "ready": False,
            "context_window": None,
            "capabilities": None,
        },
    ],
}


@pytest.fixture
def platform(monkeypatch):
    """Real ``list_enabled_private`` over ORM rows, governance on, and an
    engine answering through ``httpx.MockTransport``."""
    import httpx

    import app.repositories.nous_model_repository as repo_mod
    import app.services.ai.engine_catalog as ec
    import app.services.ai.governance.ai_governance as gov

    state = SimpleNamespace(
        rows=[
            _orm_row("nous-doubao", sort_order=1),
            _orm_row(
                "nous-qwen3-8b",
                actual_provider="nous",
                actual_model="qwen3-8b",
                base_url=ENGINE,
                api_key="sk-engine",
                context_window_tokens=32768,
                sort_order=2,
            ),
            _orm_row(
                "nous-wemm-2b",
                type="embedding",
                actual_provider="nous",
                actual_model="wemm-2b",
                base_url=ENGINE,
                api_key="sk-engine",
                sort_order=3,
            ),
            _orm_row(
                "nous-revoked",
                actual_provider="nous",
                actual_model="revoked",
                base_url=ENGINE,
                api_key="sk-engine",
                sort_order=4,
            ),
            _orm_row("nous-broken", last_test_status="fail", sort_order=5),
            _orm_row(
                "nous-codex-image",
                type="image",
                actual_provider="codex-local",
                last_test_status=None,
                sort_order=6,
            ),
        ],
        engine=lambda request: httpx.Response(200, json=ENGINE_BODY),
        engine_calls=0,
    )

    class _Session:
        async def execute(self, _stmt):
            result = MagicMock()
            result.scalars.return_value.all.return_value = list(state.rows)
            return result

    @asynccontextmanager
    async def _scope():
        yield _Session()

    def _handler(request):
        state.engine_calls += 1
        return state.engine(request)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(repo_mod, "read_scope", _scope)
    monkeypatch.setattr(gov, "is_nous_globally_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        ec.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(_handler), **kw),
    )
    ec.reset_engine_cache()
    yield state
    ec.reset_engine_cache()


def _stored_settings(nous: dict | None = None, **ai: Any) -> dict:
    providers: dict[str, Any] = {
        "deepseek": {
            "enabled": True,
            "api_key": "sk-deepseek-1234",
            "models": ["deepseek-chat"],
            "enabled_models": ["deepseek-chat"],
        }
    }
    if nous is not None:
        providers["nous"] = nous
    return {"settings_json": {"ai_settings": {"ai_providers": providers, **ai}}}


@pytest.fixture
def settings_repo(monkeypatch):
    from cryptography.fernet import Fernet

    # A PUT re-conceals the stored BYOK key; that needs a real key.
    monkeypatch.setenv("NOUS_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    repo = MagicMock()
    repo.get_by_user_id = AsyncMock(
        return_value=_stored_settings(
            {"enabled": True, "disabled_models": ["nous-doubao"]}
        )
    )
    repo.patch_settings_json = AsyncMock()
    monkeypatch.setattr(ai_settings_router, "UserSettingsRepository", lambda: repo)
    monkeypatch.setattr(
        "app.services.ai.platform_model_visibility.stored_nous_settings",
        AsyncMock(return_value={"enabled": True, "disabled_models": ["nous-doubao"]}),
    )
    return repo


@pytest.mark.asyncio
async def test_settings_carry_the_platform_view(
    client, platform, settings_repo
) -> None:
    raw = await ai_settings_router.get_ai_settings(_ctx())
    response = await client.get("/api/v1/ai/settings")
    assert_wire_unchanged(response, raw)
    body = response.json()
    assert body["ai_providers"]["nous"] == {
        "enabled": True,
        "managed": True,
        # revoked (not listed by the engine) and broken (fail) are gone.
        "models": ["nous-doubao", "nous-qwen3-8b", "nous-wemm-2b", "nous-codex-image"],
        "enabled_models": ["nous-qwen3-8b", "nous-wemm-2b", "nous-codex-image"],
        "disabled_models": ["nous-doubao"],
    }
    models = body["platform_models"]
    assert set(models) == set(body["ai_providers"]["nous"]["models"])
    assert models["nous-qwen3-8b"] == {
        "actual_model": "qwen3-8b",
        "type": "llm",
        "status": "ok",
        "is_local": False,
        "pricing_type": "per_token",
        "pricing_value": models["nous-qwen3-8b"]["pricing_value"],
        "context_window_tokens": 32768,
    }
    assert models["nous-wemm-2b"]["status"] == "idle"
    assert models["nous-codex-image"]["is_local"] is True
    assert models["nous-codex-image"]["status"] == "not_probed"
    engine = body["platform_engine"]
    assert engine["reachable"] is True and engine["stale"] is False
    assert isinstance(engine["checked_at"], str)
    # BYOK entries are untouched apart from the usual key masking.
    assert body["ai_providers"]["deepseek"]["api_key_hint"] == "1234"
    assert "api_key" not in body["ai_providers"]["deepseek"]
    # Nothing credential-shaped anywhere in the platform part.
    text = response.text
    for secret in ("sk-engine", "sk-platform", ENGINE, "ark.example"):
        assert secret not in text


@pytest.mark.asyncio
async def test_unreachable_engine_keeps_the_list(
    client, platform, settings_repo
) -> None:
    import httpx

    def _down(request):
        raise httpx.ConnectError("connection refused")

    platform.engine = _down
    body = (await client.get("/api/v1/ai/settings")).json()
    assert body["platform_engine"] == {
        "reachable": False,
        "stale": False,
        "checked_at": None,
    }
    models = body["platform_models"]
    # Could not reach ≠ revoked: every engine row stays, unknown.
    assert models["nous-revoked"]["status"] == "not_probed"
    assert models["nous-qwen3-8b"]["status"] == "not_probed"
    assert "nous-qwen3-8b" in body["ai_providers"]["nous"]["enabled_models"]


@pytest.mark.asyncio
async def test_view_failure_is_unknown_not_empty(
    client, platform, settings_repo, monkeypatch
) -> None:
    import app.services.ai.platform_provider as pp

    monkeypatch.setattr(
        pp, "live_platform_rows", AsyncMock(side_effect=RuntimeError("db down"))
    )
    body = (await client.get("/api/v1/ai/settings")).json()
    assert body["platform_models"] is None and body["platform_engine"] is None
    assert body["ai_providers"]["nous"]["disabled_models"] == ["nous-doubao"]


@pytest.mark.asyncio
async def test_put_persists_only_the_user_owned_nous_fields(
    client, platform, settings_repo
) -> None:
    payload = {
        "ai_providers": {
            "nous": {
                "enabled": False,
                "managed": True,
                "models": ["nous-doubao", "nous-qwen3-8b"],
                "enabled_models": ["nous-qwen3-8b"],
                "disabled_models": ["nous-doubao", "nous-wemm-2b"],
            },
            "deepseek": {
                "enabled": True,
                "api_key": "",
                "models": ["deepseek-chat"],
                "enabled_models": ["deepseek-chat"],
            },
        }
    }
    response = await client.put("/api/v1/ai/settings", json=payload)
    assert response.status_code == 200, response.text
    stored = settings_repo.patch_settings_json.await_args.args[1]
    nous = stored["ai_settings"]["ai_providers"]["nous"]
    assert nous == {
        "enabled": False,
        "disabled_models": ["nous-doubao", "nous-wemm-2b"],
    }
    # BYOK entries keep their own models/enabled_models (they ARE stored).
    deepseek = stored["ai_settings"]["ai_providers"]["deepseek"]
    assert deepseek["enabled_models"] == ["deepseek-chat"]
    # The echo is the recomputed view, not the payload.
    body = response.json()
    assert body["ai_providers"]["nous"]["enabled"] is False
    assert body["ai_providers"]["nous"]["enabled_models"] == [
        "nous-qwen3-8b",
        "nous-codex-image",
    ]
    assert set(body["platform_models"]) == set(body["ai_providers"]["nous"]["models"])


@pytest.mark.asyncio
async def test_put_without_enabled_keeps_the_stored_switch(
    client, platform, settings_repo
) -> None:
    settings_repo.get_by_user_id = AsyncMock(
        return_value=_stored_settings({"enabled": False, "disabled_models": []})
    )
    payload = {"ai_providers": {"nous": {"disabled_models": ["nous-wemm-2b"]}}}
    response = await client.put("/api/v1/ai/settings", json=payload)
    assert response.status_code == 200, response.text
    stored = settings_repo.patch_settings_json.await_args.args[1]
    assert stored["ai_settings"]["ai_providers"]["nous"] == {
        "enabled": False,
        "disabled_models": ["nous-wemm-2b"],
    }


@pytest.mark.asyncio
async def test_nous_models_is_row_for_row_the_settings_view(
    client, platform, settings_repo
) -> None:
    raw = await ai_settings_router.list_nous_models(_ctx())
    response = await client.get("/api/v1/ai/nous-models")
    assert_wire_unchanged(response, raw)
    models = response.json()["models"]
    assert set(models[0]) == set(AiNousModelPublic.model_fields)
    # Native BIGINT, as it always was on this route; upstream identity stays
    # private, only the derived bit leaves.
    assert isinstance(models[0]["id"], int)
    assert "actual_provider" not in models[0]
    settings = (await client.get("/api/v1/ai/settings")).json()
    assert [m["name"] for m in models] == settings["ai_providers"]["nous"]["models"]
    assert {m["name"]: m["last_test_status"] for m in models} == {
        name: entry["status"] for name, entry in settings["platform_models"].items()
    }
    llm = (await client.get("/api/v1/ai/nous-models?type=llm")).json()["models"]
    assert [m["name"] for m in llm] == ["nous-doubao", "nous-qwen3-8b"]


@pytest.mark.asyncio
async def test_engine_is_read_once_per_ttl(client, platform, settings_repo) -> None:
    await client.get("/api/v1/ai/settings")
    await client.get("/api/v1/ai/nous-models")
    await client.get("/api/v1/ai/platform-status")
    assert platform.engine_calls == 1


@pytest.mark.asyncio
async def test_platform_status(client, platform, settings_repo, monkeypatch) -> None:
    from app.services.generation import local_readiness as lr

    monkeypatch.setattr(
        lr,
        "local_engine_readiness",
        AsyncMock(return_value=lr.LocalReadiness(codex=True)),
    )
    raw = await ai_settings_router.get_platform_status(_ctx())
    response = await client.get("/api/v1/ai/platform-status")
    assert_wire_unchanged(response, raw)
    body = response.json()
    assert body["models"]["nous-qwen3-8b"] == {
        "status": "ok",
        "local_ready": None,
        "superseded": False,
    }
    assert body["models"]["nous-codex-image"]["local_ready"] is True
    assert body["engine"]["reachable"] is True


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
