"""Sprint 5.5 — bounds inventory introspection."""
from __future__ import annotations

from types import ModuleType, SimpleNamespace
from typing import Any

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

    def _private_helper():
        ...
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
    def stranger():
        ...
    stranger.__module__ = "third_party.utils"

    mod = _fake_workflow_module(stranger)
    assert inventory_workflow_names(mod) == frozenset()


# ─── agent slugs ──────────────────────────────────────────────────────


class _FakeAgentRepo:
    def __init__(self, slugs):
        self._slugs = slugs

    async def list_all_slugs(self):
        return list(self._slugs)


class _FakeAgentRepoFallback:
    """Simulates an older repo without list_all_slugs — exercises the
    raw-client fallback path."""

    def __init__(self, rows):
        self._rows = rows

    async def _get_client(self):
        return _FakeClient(self._rows)


class _FakeClient:
    def __init__(self, rows):
        self._rows = rows
        self._calls: list[tuple] = []

    def table(self, name):
        self._calls.append(("table", name))
        return self

    def select(self, *args):
        return self

    async def execute(self):
        return SimpleNamespace(data=self._rows)


@pytest.mark.asyncio
async def test_inventory_agent_slugs_uses_dedicated_method():
    repo = _FakeAgentRepo(["script_ai", "summarize", "storyboard"])
    slugs = await inventory_agent_slugs(repo)
    assert slugs == frozenset({"script_ai", "summarize", "storyboard"})


@pytest.mark.asyncio
async def test_inventory_agent_slugs_fallback_path():
    repo = _FakeAgentRepoFallback(
        [{"slug": "script_ai"}, {"slug": "summarize"}, {"slug": None}]
    )
    slugs = await inventory_agent_slugs(repo)
    assert slugs == frozenset({"script_ai", "summarize"})


@pytest.mark.asyncio
async def test_inventory_agent_slugs_none_repo_returns_empty():
    """No repo wired up (gateway-only) → empty advertisement, never crash."""
    assert await inventory_agent_slugs(None) == frozenset()


@pytest.mark.asyncio
async def test_inventory_agent_slugs_swallows_errors():
    """Best-effort — DB error must not block worker startup."""
    class _Broken:
        async def _get_client(self):
            raise RuntimeError("supabase down")

    assert await inventory_agent_slugs(_Broken()) == frozenset()


# ─── providers ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_inventory_providers_detects_configured():
    settings = SimpleNamespace(
        QWEN_API_KEY="sk-qwen",
        OPENAI_API_KEY="sk-openai",
        DEEPSEEK_API_KEY="",  # configured but empty → not available
        DOUBAO_API_KEY=None,
    )
    providers = inventory_providers(settings)
    assert providers == frozenset({"qwen", "openai"})


@pytest.mark.unit
def test_inventory_providers_alternate_attr_name():
    """Qwen accepts QWEN_API_KEY OR DASHSCOPE_API_KEY (legacy)."""
    settings = SimpleNamespace(DASHSCOPE_API_KEY="sk-dash")
    providers = inventory_providers(settings)
    assert "qwen" in providers


@pytest.mark.unit
def test_inventory_providers_empty_when_nothing_configured():
    settings = SimpleNamespace()
    assert inventory_providers(settings) == frozenset()


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
