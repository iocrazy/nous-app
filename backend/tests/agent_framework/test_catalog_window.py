"""Context windows come from the provider catalog first (mig 500).

Production 2026-09-23: every model actually run in the last 30 days
(``doubao-seed-2-0-lite-260428`` ×194, ``deepseek-v4-*``) was missing from the
hardcoded ``_MODEL_WINDOWS`` table, so every tier decision divided by the
28000-token fallback and the step-end context gauge never fired (it skips when
the window is not known). The window is a model fact the admin owns, so it
lives on ``nous_models.context_window_tokens``; the table is the second layer.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

import app.agent_framework.catalog_windows as cwin
from app.agent_framework.context_window import _MODEL_WINDOWS, resolve_model_window
from app.core.config import settings


@pytest.fixture(autouse=True)
def _clean_catalog_cache():
    cwin._reset_for_tests()
    yield
    cwin._reset_for_tests()


def _rows(*triples: tuple[str, str, int | None]) -> list[dict]:
    return [
        {"name": n, "actual_model": m, "context_window_tokens": w}
        for n, m, w in triples
    ]


async def _load(monkeypatch, rows: list[dict]) -> None:
    async def _fetch():
        return rows

    monkeypatch.setattr(cwin, "_fetch_catalog_rows", _fetch)
    await cwin.refresh_catalog_windows()


@pytest.mark.unit
async def test_catalog_value_wins_and_is_known(monkeypatch):
    # gpt-4o is in the table at 128k; the catalog says otherwise and wins.
    await _load(monkeypatch, _rows(("nous-gpt4o", "gpt-4o", 64_000)))
    assert resolve_model_window("gpt-4o") == (64_000, True)


@pytest.mark.unit
async def test_catalog_answers_by_actual_model_and_by_name(monkeypatch):
    await _load(
        monkeypatch,
        _rows(("nous-deepseek-v4-pro", "deepseek-v4-pro", 131_072)),
    )
    assert resolve_model_window("deepseek-v4-pro") == (131_072, True)
    assert resolve_model_window("nous-deepseek-v4-pro") == (131_072, True)
    # the mediahub-/nous- rename alias resolves to the same row
    assert resolve_model_window("mediahub-deepseek-v4-pro") == (131_072, True)
    # case-insensitive, like the table lookup
    assert resolve_model_window("DeepSeek-V4-Pro") == (131_072, True)


@pytest.mark.unit
async def test_catalog_null_falls_through_to_the_table(monkeypatch):
    await _load(monkeypatch, _rows(("nous-gpt4o", "gpt-4o", None)))
    assert resolve_model_window("gpt-4o") == (128_000, True)


@pytest.mark.unit
async def test_neither_catalog_nor_table_is_the_fallback_and_unknown(monkeypatch):
    await _load(monkeypatch, _rows(("nous-x", "x-model", None)))
    assert resolve_model_window("x-model") == (settings.LLM_MAX_CONTEXT_TOKENS, False)


@pytest.mark.unit
async def test_conflicting_rows_for_one_model_take_the_smaller_window(monkeypatch):
    """Owner-scoped rows can share an actual_model. Compacting early is the
    safe side of a disagreement; overflowing the provider is not."""
    await _load(
        monkeypatch,
        _rows(("nous-a", "shared-model", 200_000), ("user-a", "shared-model", 64_000)),
    )
    assert resolve_model_window("shared-model") == (64_000, True)


@pytest.mark.unit
async def test_refresh_makes_a_new_value_take_effect(monkeypatch):
    await _load(monkeypatch, _rows(("nous-q", "qwen3-8-27b", 32_768)))
    assert resolve_model_window("qwen3-8-27b") == (32_768, True)
    await _load(monkeypatch, _rows(("nous-q", "qwen3-8-27b", 65_536)))
    assert resolve_model_window("qwen3-8-27b") == (65_536, True)


@pytest.mark.unit
async def test_ensure_loads_once_then_again_only_when_stale(monkeypatch):
    calls: list[int] = []

    async def _fetch():
        calls.append(1)
        return _rows(("nous-q", "qwen3-8-27b", 32_768))

    monkeypatch.setattr(cwin, "_fetch_catalog_rows", _fetch)
    await cwin.ensure_catalog_windows_loaded()
    await cwin.ensure_catalog_windows_loaded()
    assert len(calls) == 1
    # another process (the admin PUT landed on the gateway) changed the
    # catalog; the worker picks it up once the TTL lapses.
    monkeypatch.setattr(
        cwin, "_loaded_at", time.monotonic() - cwin.CATALOG_WINDOW_TTL_S - 1
    )
    await cwin.ensure_catalog_windows_loaded()
    assert len(calls) == 2


@pytest.mark.unit
async def test_a_failed_reload_keeps_the_previous_windows(monkeypatch):
    await _load(monkeypatch, _rows(("nous-q", "qwen3-8-27b", 32_768)))

    async def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(cwin, "_fetch_catalog_rows", _boom)
    await cwin.refresh_catalog_windows()
    assert resolve_model_window("qwen3-8-27b") == (32_768, True)


@pytest.mark.unit
def test_doubao_seed_lite_is_in_the_second_layer_table():
    assert _MODEL_WINDOWS["doubao-seed-2-0-lite-260428"] == 131_072


@pytest.mark.unit
def test_fetch_statement_reads_the_catalog_window_column():
    sql = str(cwin._catalog_windows_select_stmt())
    assert "context_window_tokens" in sql
    assert "nous_models" in sql
    assert "IS NOT NULL" in sql


@pytest.mark.unit
async def test_compactor_fallback_note_names_catalog_and_table(monkeypatch):
    from app.agent_framework.context_compactor import ContextCompactor

    await _load(monkeypatch, [])
    with (
        patch("app.agent_framework.context_compactor.count_tokens", return_value=0),
        patch(
            "app.agent_framework.context_compactor.count_messages_tokens",
            return_value=1,
        ),
    ):
        _, stats = await ContextCompactor().maybe_compact(
            system_message="",
            user_messages=[{"role": "user", "content": "x"}],
            model="made-up-xyz",
        )
    note = next(n for n in stats.notes if "window is a fallback" in n)
    assert "no catalog/table window" in note
    assert "_MODEL_WINDOWS" not in note


@pytest.mark.unit
async def test_catalog_known_model_feeds_the_context_gauge(monkeypatch):
    """The doubao runs had no gauge because the window was unknown."""
    from app.services.ai.runner.agent_runner import AgentRunner
    from app.services.ai.runner.step_hooks import StepHookChain

    await _load(
        monkeypatch,
        _rows(("nous-doubao-seed-2-0-pro", "doubao-seed-2-0-pro-260215", 131_072)),
    )

    class _Rec:
        def __init__(self):
            self.measured: list[tuple[int, int]] = []

        async def record_event(self, *a, **k):
            pass

        def record_usage(self, **kw):
            pass

        def cost_of(self, p, c, cached=0):
            return None

        def measure_context(self, used, window):
            self.measured.append((used, window))

    class _Composed:
        model = "doubao-seed-2-0-pro-260215"

    rec = _Rec()
    runner = AgentRunner(
        adapter=object(), skill_tool=object(), step_hooks=StepHookChain([])
    )
    await runner._step_ended(
        rec,
        _Composed(),
        1,
        0.0,
        {"prompt_tokens": 5000, "completion_tokens": 3},
        "stop",
    )
    assert rec.measured == [(5000, 131_072)]


@pytest.mark.unit
async def test_runner_preflight_refreshes_the_catalog_before_compacting(monkeypatch):
    """Turns run on the worker; an admin edit lands on the gateway. The
    preflight is the one async point every turn passes, so it keeps the
    worker's cache inside the TTL."""
    from app.services.ai.runner import agent_runner as ar

    order: list[str] = []

    async def _ensure():
        order.append("ensure")

    class _Compactor:
        async def maybe_compact(self, **kw):
            order.append("compact")
            return kw["user_messages"], None

    monkeypatch.setattr(cwin, "ensure_catalog_windows_loaded", _ensure)
    monkeypatch.setattr(ar, "_DEFAULT_COMPACTOR", _Compactor())

    class _Composed:
        system_message = "s"
        model = "m"
        tools = None

    runner = ar.AgentRunner(adapter=object(), skill_tool=object())
    await runner._preflight_compact_and_budget(
        _Composed(), [{"role": "user", "content": "hi"}], None
    )
    assert order[:2] == ["ensure", "compact"]


@pytest.mark.unit
async def test_startup_warms_the_catalog_cache(monkeypatch):
    from fastapi import FastAPI

    from app.startup import agent_framework_init as init

    called: list[int] = []

    async def _refresh():
        called.append(1)

    monkeypatch.setattr(cwin, "refresh_catalog_windows", _refresh)
    await init.warm_catalog_windows(FastAPI())
    assert called == [1]


@pytest.mark.unit
@pytest.mark.parametrize("verb", ["create", "update", "delete"])
async def test_admin_catalog_edits_refresh_the_cache(monkeypatch, verb):
    import importlib

    # the package re-exports the APIRouter under the module's name
    r = importlib.import_module("app.api.admin.nous_model_router")

    called: list[int] = []

    async def _refresh():
        called.append(1)

    class _Repo:
        async def list_all(self):
            return []

        async def create(self, data):
            return {"id": 1, **data}

        async def update(self, model_id, updates):
            return {"id": 1, "name": "nous-x", **updates}

        async def delete(self, model_id):
            return True

    async def _no_collision(*a, **k):
        return None

    monkeypatch.setattr(r, "refresh_catalog_windows", _refresh)
    monkeypatch.setattr(r, "get_nous_model_repository", lambda: _Repo())
    monkeypatch.setattr(r, "_reject_name_collision", _no_collision)
    monkeypatch.setattr(r, "_to_response", lambda row, **k: row)

    if verb == "create":
        body = r.NousModelCreate(
            name="nous-x",
            display_name="X",
            type="llm",
            actual_provider="openai",
            actual_model="x",
            api_key="k",
        )
        await r.create_nous_model(body, auth=None)
    elif verb == "update":
        await r.update_nous_model(
            "1", r.NousModelUpdate(context_window_tokens=65_536), auth=None
        )
    else:
        await r.delete_nous_model("1", auth=None)
    assert called == [1]


@pytest.mark.unit
async def test_fetch_opens_its_own_session_even_inside_a_unit_of_work(monkeypatch):
    """A failed catalog read must not abort an ambient unit_of_work
    transaction, so the fetch never joins it."""
    from contextlib import asynccontextmanager

    import app.db.session as dbs

    used: list[str] = []

    class _Result:
        def mappings(self):
            return self

        def all(self):
            return [{"name": "n", "actual_model": "m", "context_window_tokens": 1}]

    class _Own:
        async def execute(self, stmt):
            used.append("own")
            return _Result()

    class _Ambient:
        async def execute(self, stmt):
            used.append("ambient")
            return _Result()

    @asynccontextmanager
    async def _session():
        yield _Own()

    monkeypatch.setattr(dbs, "get_sessionmaker", lambda: _session)
    token = dbs._request_session.set(_Ambient())
    try:
        rows = await cwin._fetch_catalog_rows()
    finally:
        dbs._request_session.reset(token)
    assert used == ["own"]
    assert rows == [{"name": "n", "actual_model": "m", "context_window_tokens": 1}]
