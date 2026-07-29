"""Tests for the agent usage registry + GET /agents/{slug}/usage.

The registry is the static "which product modules call this agent" map
(user-approved design: registry lives in backend, next to the code that
does the calling). The endpoint merges it with dynamic evidence:
agent_runs.trigger counts (30d), conversation bindings, and routines.

Pinned here:
    - Registry ↔ seeds consistency in BOTH directions: every registry
      slug must be a seeded system agent, and every seeded agent must
      have a registry entry (an explicit [] means "no module uses it" —
      adding a new agent forces a conscious decision).
    - Every trigger value observed in production has a feature mapping.
    - Endpoint: 404 on unknown slug; aggregation shape; unmapped
      triggers fall back to feature_key="other".
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.services.ai.agent_usage_registry import (
    AGENT_MODULE_REGISTRY,
    TRIGGER_FEATURE_MAP,
    feature_for_trigger,
)

BASE = "/api/v1/ai-library"
FAKE_USER_ID = str(uuid4())

SEEDS_DIR = Path(__file__).resolve().parents[1] / "seeds" / "agents"

# Trigger values observed in production agent_runs (2026-07-28). Any of
# these missing from TRIGGER_FEATURE_MAP means the usage panel would
# lump real traffic under "other".
PRODUCTION_TRIGGERS = [
    "chat",
    "script_ai",
    "issue_reply",
    "visual_analysis_l1",
    "prompt_caption",
    "issue_dispatch",
    "chat_summon",
    "asset_classify",
]


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ─── Registry consistency ───────────────────────────────────────────


@pytest.mark.unit
def test_registry_slugs_all_exist_in_seeds():
    seeded = {p.name for p in SEEDS_DIR.iterdir() if p.is_dir()}
    unknown = set(AGENT_MODULE_REGISTRY) - seeded
    assert not unknown, f"registry references non-seeded agents: {sorted(unknown)}"


@pytest.mark.unit
def test_every_seeded_agent_has_a_registry_entry():
    """A new seeded agent must get an explicit entry (possibly []) so the
    usage panel never silently shows nothing because someone forgot."""
    seeded = {p.name for p in SEEDS_DIR.iterdir() if p.is_dir()}
    missing = seeded - set(AGENT_MODULE_REGISTRY)
    assert not missing, f"seeded agents missing registry entries: {sorted(missing)}"


@pytest.mark.unit
def test_registry_entries_have_module_and_feature_keys():
    for slug, refs in AGENT_MODULE_REGISTRY.items():
        for ref in refs:
            assert ref.get("module_key"), f"{slug}: empty module_key in {ref}"
            assert ref.get("feature_key"), f"{slug}: empty feature_key in {ref}"


@pytest.mark.unit
def test_production_triggers_all_mapped():
    unmapped = [t for t in PRODUCTION_TRIGGERS if t not in TRIGGER_FEATURE_MAP]
    assert not unmapped, f"production triggers without feature mapping: {unmapped}"


@pytest.mark.unit
def test_feature_for_trigger_falls_back_to_other():
    assert feature_for_trigger("some_future_trigger") == "other"
    assert feature_for_trigger("chat") == TRIGGER_FEATURE_MAP["chat"]


# ─── Endpoint ───────────────────────────────────────────────────────


def _orm_read_scope(tables: dict[str, list]):
    """read_scope() stand-in dispatching by table name in compiled SQL."""
    from contextlib import asynccontextmanager

    class _M:
        def __init__(self, d):
            self._d = d

        def all(self):
            return self._d

        def first(self):
            return self._d[0] if self._d else None

    class _R:
        def __init__(self, d):
            self._d = d

        def mappings(self):
            return _M(self._d)

        def scalar(self):
            return self._d[0] if self._d else 0

    class _S:
        async def execute(self, stmt):
            sql = str(stmt).lower()
            for name, data in tables.items():
                if name in sql:
                    return _R(data)
            return _R([])

    @asynccontextmanager
    async def _scope():
        yield _S()

    return _scope


def _agent_repo_returning(agent):
    repo = MagicMock()

    async def _get_by_slug(slug, **kwargs):
        return agent

    repo.get_by_slug = _get_by_slug
    return repo


@pytest.mark.unit
@pytest.mark.asyncio
async def test_usage_unknown_slug_404(client):
    with patch(
        "app.api.ai_library_router._repos",
        return_value=(_agent_repo_returning(None), MagicMock()),
    ):
        resp = await client.get(f"{BASE}/agents/nope/usage")
    assert resp.status_code == 404


@pytest.mark.unit
@pytest.mark.asyncio
async def test_usage_aggregates_triggers_and_counts(client):
    agent = {"id": str(uuid4()), "slug": "analyze", "name": "Analyze"}
    tables = {
        "agent_runs": [
            {"trigger": "visual_analysis_l1", "count": 8},
            {"trigger": "chat", "count": 3},
            {"trigger": "mystery_trigger", "count": 1},
        ],
        "conversation_ai_meta": [5],
        "user_schedules": [2],
    }
    with (
        patch(
            "app.api.ai_library_router._repos",
            return_value=(_agent_repo_returning(agent), MagicMock()),
        ),
        patch("app.db.session.read_scope", _orm_read_scope(tables)),
    ):
        resp = await client.get(f"{BASE}/agents/analyze/usage")

    assert resp.status_code == 200
    body = resp.json()
    assert body["window_days"] == 30
    # Static registry: analyze must declare at least one consuming module.
    assert body["modules"], "analyze should have registry modules"
    assert all("module_key" in m and "feature_key" in m for m in body["modules"])
    # Dynamic: trigger rows carry mapped feature keys; unknown → "other".
    by_trigger = {t["trigger"]: t for t in body["trigger_counts"]}
    assert by_trigger["visual_analysis_l1"]["count"] == 8
    assert by_trigger["visual_analysis_l1"]["feature_key"] != "other"
    assert by_trigger["mystery_trigger"]["feature_key"] == "other"
    assert body["conversation_count"] == 5
    assert body["routine_count"] == 2
