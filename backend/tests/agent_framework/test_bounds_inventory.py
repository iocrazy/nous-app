"""Sprint 5.5 — bounds inventory introspection."""

from __future__ import annotations

from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_framework.bounds_inventory import (
    inventory_agent_slugs,
    inventory_providers,
    inventory_workflow_names,
    merge_lane_capacity,
)

# ─── workflow names ───────────────────────────────────────────────────


def _fake_workflow_module(*funcs):
    """Build a stand-in for app.workflows package. Each provided function
    must have __module__ starting with 'app.workflows'."""
    mod = ModuleType("app.workflows.fake_pkg")
    for fn in funcs:
        setattr(mod, fn.__name__, fn)
    return mod


@pytest.mark.unit
def test_inventory_workflow_names_basic():
    def download_workflow():  # noqa: D401
        ...

    download_workflow.__module__ = "app.workflows.download"

    def parse_workflow():  # noqa: D401
        ...

    parse_workflow.__module__ = "app.workflows.parse"

    mod = _fake_workflow_module(download_workflow, parse_workflow)
    names = inventory_workflow_names(mod)
    assert names == frozenset({"download_workflow", "parse_workflow"})


@pytest.mark.unit
def test_inventory_skips_private_and_imported_modules():
    """Underscore names + imported modules should not count as workflows."""

    def real_workflow():  # noqa: D401
        ...

    real_workflow.__module__ = "app.workflows.x"

    def _private_helper(): ...

    _private_helper.__module__ = "app.workflows.x"

    fake_imported = ModuleType("some_other_module")

    mod = _fake_workflow_module(real_workflow)
    mod._private_helper = _private_helper  # noqa: SLF001
    mod.imported_thing = fake_imported  # noqa
    names = inventory_workflow_names(mod)
    assert names == frozenset({"real_workflow"})


@pytest.mark.unit
def test_inventory_skips_external_module_callables():
    """Callable defined OUTSIDE app.workflows.* shouldn't be advertised."""

    def stranger(): ...

    stranger.__module__ = "third_party.utils"

    mod = _fake_workflow_module(stranger)
    assert inventory_workflow_names(mod) == frozenset()


# ─── agent slugs ──────────────────────────────────────────────────────


class _FakeAgentRepo:
    def __init__(self, slugs):
        self._slugs = slugs

    async def list_all_slugs(self):
        return list(self._slugs)


@pytest.mark.asyncio
async def test_inventory_agent_slugs_uses_dedicated_method():
    repo = _FakeAgentRepo(["script_ai", "summarize", "storyboard"])
    slugs = await inventory_agent_slugs(repo)
    assert slugs == frozenset({"script_ai", "summarize", "storyboard"})


@pytest.mark.asyncio
async def test_inventory_agent_slugs_contract_on_real_repo():
    """The REAL AgentRepository must expose list_all_slugs — the old
    supabase-py fallback masked its absence and the advertisement
    silently degraded to empty (found 2026-07-13)."""
    from app.repositories.agent_repository import AgentRepository

    assert callable(getattr(AgentRepository, "list_all_slugs", None))


@pytest.mark.asyncio
async def test_inventory_agent_slugs_none_repo_returns_empty():
    """No repo wired up (gateway-only) → empty advertisement, never crash."""
    assert await inventory_agent_slugs(None) == frozenset()


@pytest.mark.asyncio
async def test_inventory_agent_slugs_swallows_errors():
    """Best-effort — DB error must not block worker startup."""

    class _Broken:
        async def list_all_slugs(self):
            raise RuntimeError("db down")

    assert await inventory_agent_slugs(_Broken()) == frozenset()


# ─── providers ────────────────────────────────────────────────────────


def _catalog_repo(rows):
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=rows)
    return repo


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inventory_providers_reads_enabled_catalog_rows():
    """DB-only credentials (铁律 2026-07-07): availability = enabled catalog
    rows' actual_provider values; disabled rows and blanks are excluded."""
    rows = [
        {"actual_provider": "doubao", "is_enabled": True},
        {"actual_provider": "deepseek", "is_enabled": True},
        {"actual_provider": "qwen", "is_enabled": False},  # disabled → out
        {"actual_provider": "", "is_enabled": True},  # blank → out
    ]
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        return_value=_catalog_repo(rows),
    ):
        providers = await inventory_providers()
    assert providers == frozenset({"doubao", "deepseek"})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inventory_providers_settings_arg_is_ignored():
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        return_value=_catalog_repo([{"actual_provider": "doubao", "is_enabled": True}]),
    ):
        providers = await inventory_providers(
            SimpleNamespace(OPENAI_API_KEY="sk-env-leak")
        )
    assert providers == frozenset({"doubao"})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inventory_providers_empty_on_catalog_failure():
    """Best-effort: a broken catalog read degrades to no provider capability."""
    repo = MagicMock()
    repo.list_all = AsyncMock(side_effect=RuntimeError("db down"))
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        return_value=repo,
    ):
        assert await inventory_providers() == frozenset()


# ─── merge_lane_capacity ──────────────────────────────────────────────


@pytest.mark.unit
def test_merge_lane_capacity_last_wins():
    base = {"chat": 5, "transcription": 2}
    override = {"chat": 10, "analysis": 3}
    merged = merge_lane_capacity(base, override)
    assert merged == {"chat": 10, "transcription": 2, "analysis": 3}


@pytest.mark.unit
def test_merge_lane_capacity_accepts_iterables():
    pairs = [("a", 1), ("b", 2)]
    merged = merge_lane_capacity(pairs)
    assert merged == {"a": 1, "b": 2}
