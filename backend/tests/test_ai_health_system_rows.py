"""Health-board system rows (Phase B B2): chat / transcription / topic_scorer
/ embedding / maintenance — the capabilities outside the agent-task loop.

Each row resolves through its REAL runtime resolver (mocked here at the
resolver seam) and reports origin + status; a resolver crash degrades to an
``error`` row without sinking the board.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.ai import ai_health
from app.services.ai.providers.ai_provider_helpers import ResolvedAIConfig

pytestmark = pytest.mark.asyncio


def _patch_all(monkeypatch, **overrides):
    """Patch every system-row seam with sane defaults; override per test."""
    defaults = {
        "resolve_transcription_config": AsyncMock(
            return_value=ResolvedAIConfig(
                "volcengine",
                {"api_key": "k", "model": ""},
                "volcengine:asr-1",
                "",
                "byok",
            )
        ),
        "resolve_scorer_config": AsyncMock(
            return_value=ResolvedAIConfig(
                "deepseek",
                {"api_key": "k", "base_url": "https://c/v1", "model": "m"},
                "deepseek-v4-flash",
                "topic-scorer",
                "platform",
            )
        ),
        "resolve_embedding_ai_config": AsyncMock(
            return_value=ResolvedAIConfig(
                "",
                {
                    "api_key": "k",
                    "base_url": "https://e/v1",
                    "model": "qwen3-embedding-8b",
                },
                "qwen3-embedding-8b",
                "",
                "governance",
            )
        ),
    }
    # Only the resolver names actually imported into ai_health get patched
    # there; the chat/maintenance seams live in their own modules below.
    _AI_HEALTH_SEAMS = set(defaults)
    defaults.update({k: v for k, v in overrides.items() if k in _AI_HEALTH_SEAMS})
    for name, mock in defaults.items():
        monkeypatch.setattr(ai_health, name, mock)

    # chat seams live in ai_governance (imported inside the function)
    import app.services.ai.governance.ai_governance as gov_mod

    monkeypatch.setattr(
        gov_mod,
        "get_module_governance",
        overrides.get(
            "get_module_governance",
            AsyncMock(return_value=SimpleNamespace(allowed=True)),
        ),
    )
    monkeypatch.setattr(
        gov_mod,
        "get_platform_ai_providers",
        overrides.get(
            "get_platform_ai_providers",
            AsyncMock(return_value={"doubao": {"api_key": "sk-plat"}}),
        ),
    )

    # maintenance seams
    import app.services.ai.providers.ai_provider_helpers as helpers_mod

    monkeypatch.setattr(
        helpers_mod,
        "get_maintenance_model",
        overrides.get(
            "get_maintenance_model",
            AsyncMock(return_value="mediahub-doubao-seed-2-0-lite"),
        ),
    )
    monkeypatch.setattr(
        helpers_mod,
        "resolve_mediahub_model",
        overrides.get(
            "resolve_mediahub_model",
            AsyncMock(
                return_value=(
                    "doubao",
                    {"api_key": "sk", "base_url": "https://a/v1"},
                    "doubao-seed-2-0-lite-260428",
                )
            ),
        ),
    )


async def _rows(monkeypatch, **overrides):
    _patch_all(monkeypatch, **overrides)
    rows = await ai_health._system_capability_rows(
        "u1", {"ai_providers": {"qwen": {"api_key": "sk-user"}}}
    )
    return {r["capability"]: r for r in rows}


async def test_all_five_system_rows_present(monkeypatch):
    rows = await _rows(monkeypatch)
    assert set(rows) == {
        "chat",
        "transcription",
        "topic_scorer",
        "embedding",
        "maintenance",
    }


async def test_transcription_row_shows_descriptor_and_origin(monkeypatch):
    rows = await _rows(monkeypatch)
    t = rows["transcription"]
    assert t["model"] == "volcengine:asr-1"
    assert t["origin"] == "byok"


async def test_transcription_governance_shows_config_model(monkeypatch):
    rows = await _rows(
        monkeypatch,
        resolve_transcription_config=AsyncMock(
            return_value=ResolvedAIConfig(
                "doubao",
                {"api_key": "sk-admin", "model": "asr-admin"},
                "",
                "",
                "governance",
            )
        ),
    )
    t = rows["transcription"]
    assert t["model"] == "asr-admin"
    assert t["origin"] == "governance"
    assert t["status"] == "ok"


async def test_chat_ok_when_platform_key_exists(monkeypatch):
    rows = await _rows(monkeypatch)
    assert rows["chat"]["status"] == "ok"
    assert rows["chat"]["origin"] == "byok"


async def test_chat_not_configured_when_no_keys_anywhere(monkeypatch):
    _patch_all(
        monkeypatch,
        get_platform_ai_providers=AsyncMock(return_value={}),
    )
    rows = await ai_health._system_capability_rows("u1", {"ai_providers": {}})
    chat = next(r for r in rows if r["capability"] == "chat")
    assert chat["status"] == "not_configured"
    assert "Settings" in chat["hint"]


async def test_chat_locked_reports_governance_origin(monkeypatch):
    rows = await _rows(
        monkeypatch,
        get_module_governance=AsyncMock(return_value=SimpleNamespace(allowed=False)),
    )
    assert rows["chat"]["origin"] == "governance"


async def test_scorer_platform_row(monkeypatch):
    rows = await _rows(monkeypatch)
    s = rows["topic_scorer"]
    assert s["origin"] == "platform"
    assert s["model"] == "deepseek-v4-flash"
    assert s["status"] == "ok"


async def test_scorer_not_configured(monkeypatch):
    rows = await _rows(
        monkeypatch,
        resolve_scorer_config=AsyncMock(
            return_value=ResolvedAIConfig(
                "", {"api_key": "", "model": ""}, "", "topic-scorer", "env"
            )
        ),
    )
    assert rows["topic_scorer"]["status"] == "not_configured"


async def test_embedding_admin_managed_unknown_prefix_is_ok(monkeypatch):
    # Admin-managed rows carry their own creds — an unknown factory prefix
    # (self-hosted embedding model) must NOT flag unknown_provider.
    rows = await _rows(monkeypatch)
    e = rows["embedding"]
    assert e["origin"] == "governance"
    assert e["status"] == "ok"


async def test_maintenance_ok_on_catalog_hit(monkeypatch):
    rows = await _rows(monkeypatch)
    m = rows["maintenance"]
    assert m["status"] == "ok"
    assert m["model"] == "doubao-seed-2-0-lite-260428"
    assert m["origin"] == "platform"


async def test_maintenance_not_configured_on_catalog_miss(monkeypatch):
    rows = await _rows(monkeypatch, resolve_mediahub_model=AsyncMock(return_value=None))
    m = rows["maintenance"]
    assert m["status"] == "not_configured"
    assert "maintenance_llm_model" in m["hint"]


async def test_system_row_failure_is_isolated(monkeypatch):
    rows = await _rows(
        monkeypatch,
        resolve_scorer_config=AsyncMock(side_effect=RuntimeError("boom")),
    )
    assert rows["topic_scorer"]["status"] == "error"
    # siblings unaffected
    assert rows["embedding"]["status"] == "ok"
    assert rows["maintenance"]["status"] == "ok"
