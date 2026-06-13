"""AI capability health — surfaces the invisible capability→agent→model
→provider→key mapping so users can see (and fix) what each feature uses.

Reuses the SAME resolver the runtime uses (resolve_task_provider_config),
so the panel can't drift from reality.
"""

from __future__ import annotations

import pytest

from app.services.ai import ai_health


def _patch(monkeypatch, *, settings, resolver, runtime=None):
    async def _get_ai_settings(uid):
        return settings

    async def _resolve(uid, task_key, default_slug):
        return resolver(task_key, default_slug)

    async def _runtime(uid, task_types):
        return runtime or {}

    monkeypatch.setattr(ai_health, "get_ai_settings", _get_ai_settings)
    monkeypatch.setattr(ai_health, "resolve_task_provider_config", _resolve)
    monkeypatch.setattr(ai_health, "fetch_runtime_summary", _runtime)


@pytest.mark.asyncio
async def test_ok_when_model_and_key_present(monkeypatch):
    _patch(
        monkeypatch,
        settings={
            "ai_providers": {"qwen": {"api_key": "sk-x"}},
            "task_assignment": {"summarization": "summarize"},
        },
        resolver=lambda tk, ds: (
            "qwen",
            {"model": "qwen-max", "api_key": "sk-x"},
            "qwen-max",
            "summarize",
        ),
    )
    rows = await ai_health.get_capability_health("u1")
    summ = next(r for r in rows if r["capability"] == "summarization")
    assert summ["status"] == "ok"
    assert summ["model"] == "qwen-max"
    assert summ["provider"] == "qwen"
    assert summ["agent_slug"] == "summarize"
    assert summ["assigned"] is True


@pytest.mark.asyncio
async def test_no_key_flags_missing_provider_key(monkeypatch):
    _patch(
        monkeypatch,
        settings={"ai_providers": {}, "task_assignment": {}},
        resolver=lambda tk, ds: ("doubao", {"model": "doubao-pro"}, "doubao-pro", ds),
    )
    rows = await ai_health.get_capability_health("u1")
    row = rows[0]
    assert row["status"] == "no_key"
    assert "doubao" in row["hint"].lower()
    assert row["assigned"] is False  # falling back to default


@pytest.mark.asyncio
async def test_no_model_when_agent_has_none(monkeypatch):
    _patch(
        monkeypatch,
        settings={"ai_providers": {"qwen": {"api_key": "sk-x"}}, "task_assignment": {}},
        resolver=lambda tk, ds: ("", {}, "", ds),
    )
    rows = await ai_health.get_capability_health("u1")
    assert rows[0]["status"] == "no_model"


@pytest.mark.asyncio
async def test_vision_capability_warns_on_text_model(monkeypatch):
    # caption/classify/visual_analysis need a vision model; a text-only
    # model (qwen-max) is configured but can't see images.
    _patch(
        monkeypatch,
        settings={"ai_providers": {"qwen": {"api_key": "sk-x"}}, "task_assignment": {}},
        resolver=lambda tk, ds: (
            "qwen",
            {"model": "qwen-max", "api_key": "sk-x"},
            "qwen-max",
            ds,
        ),
    )
    rows = await ai_health.get_capability_health("u1")
    caption = next(r for r in rows if r["capability"] == "caption")
    assert caption["status"] == "not_vision"
    assert "vision" in caption["hint"].lower()


@pytest.mark.asyncio
async def test_vision_capability_ok_with_vision_model(monkeypatch):
    _patch(
        monkeypatch,
        settings={
            "ai_providers": {"modelscope": {"api_key": "ms-x"}},
            "task_assignment": {},
        },
        resolver=lambda tk, ds: (
            "modelscope",
            {"model": "Qwen/Qwen3-VL-235B-A22B-Instruct", "api_key": "ms-x"},
            "Qwen/Qwen3-VL-235B-A22B-Instruct",
            ds,
        ),
    )
    rows = await ai_health.get_capability_health("u1")
    caption = next(r for r in rows if r["capability"] == "caption")
    assert caption["status"] == "ok"


@pytest.mark.asyncio
async def test_key_list_form_counts_as_present(monkeypatch):
    _patch(
        monkeypatch,
        settings={
            "ai_providers": {"qwen": {"api_key": ["sk-a", "sk-b"]}},
            "task_assignment": {},
        },
        resolver=lambda tk, ds: ("qwen", {"model": "qwen-max"}, "qwen-max", ds),
    )
    rows = await ai_health.get_capability_health("u1")
    assert rows[0]["status"] == "ok"


@pytest.mark.asyncio
async def test_all_capabilities_present(monkeypatch):
    _patch(
        monkeypatch,
        settings={"ai_providers": {"qwen": {"api_key": "sk"}}, "task_assignment": {}},
        resolver=lambda tk, ds: (
            "qwen",
            {"model": "qwen-max", "api_key": "sk"},
            "qwen-max",
            ds,
        ),
    )
    rows = await ai_health.get_capability_health("u1")
    caps = {r["capability"] for r in rows}
    assert caps == {
        "summarization",
        "visual_analysis",
        "caption",
        "classify",
        "translation",
    }


@pytest.mark.asyncio
async def test_visual_analysis_has_task_type_and_runtime_fields(monkeypatch):
    # visual_analysis is the one capability with a task_tracking task_type
    # (ai_extract); it gains runtime fields even when healthy.
    _patch(
        monkeypatch,
        settings={"ai_providers": {"doubao": {"api_key": "k"}}, "task_assignment": {}},
        resolver=lambda tk, ds: (
            "doubao",
            {"model": "doubao-seed-vl"},
            "doubao-seed-vl",
            ds,
        ),
        runtime={
            "ai_extract": {
                "recent_runs": 3,
                "recent_failures": 0,
                "last_error": "",
                "latest_failed": False,
            }
        },
    )
    rows = await ai_health.get_capability_health("u1")
    va = next(r for r in rows if r["capability"] == "visual_analysis")
    assert va["task_type"] == "ai_extract"
    assert va["recent_runs"] == 3
    assert va["status"] == "ok"


@pytest.mark.asyncio
async def test_runtime_failing_when_config_ok_but_latest_run_failed(monkeypatch):
    # The ark-key case: vision model + key resolve fine, but the most recent
    # call failed (e.g. AccessDenied). Config is "ok" yet the capability is
    # currently broken — surfaced as runtime_failing with the real error.
    _patch(
        monkeypatch,
        settings={"ai_providers": {"doubao": {"api_key": "k"}}, "task_assignment": {}},
        resolver=lambda tk, ds: (
            "doubao",
            {"model": "doubao-seed-vl"},
            "doubao-seed-vl",
            ds,
        ),
        runtime={
            "ai_extract": {
                "recent_runs": 4,
                "recent_failures": 2,
                "last_error": "AccessDenied: model not granted for this key",
                "latest_failed": True,
            }
        },
    )
    rows = await ai_health.get_capability_health("u1")
    va = next(r for r in rows if r["capability"] == "visual_analysis")
    assert va["status"] == "runtime_failing"
    assert "AccessDenied" in va["hint"]
    assert va["recent_failures"] == 2


@pytest.mark.asyncio
async def test_config_problem_takes_priority_over_runtime(monkeypatch):
    # No key is the actionable root cause; a runtime failure must not mask it.
    _patch(
        monkeypatch,
        settings={"ai_providers": {}, "task_assignment": {}},
        resolver=lambda tk, ds: (
            "doubao",
            {"model": "doubao-seed-vl"},
            "doubao-seed-vl",
            ds,
        ),
        runtime={
            "ai_extract": {
                "recent_runs": 1,
                "recent_failures": 1,
                "last_error": "boom",
                "latest_failed": True,
            }
        },
    )
    rows = await ai_health.get_capability_health("u1")
    va = next(r for r in rows if r["capability"] == "visual_analysis")
    assert va["status"] == "no_key"


@pytest.mark.asyncio
async def test_inline_capability_has_no_runtime_fields(monkeypatch):
    # summarization runs inline (no task_tracking task_type) — no runtime
    # enrichment, and stray runtime data for other types never leaks onto it.
    _patch(
        monkeypatch,
        settings={"ai_providers": {"qwen": {"api_key": "sk"}}, "task_assignment": {}},
        resolver=lambda tk, ds: ("qwen", {"model": "qwen-max"}, "qwen-max", ds),
        runtime={
            "ai_extract": {
                "recent_runs": 9,
                "recent_failures": 9,
                "last_error": "x",
                "latest_failed": True,
            }
        },
    )
    rows = await ai_health.get_capability_health("u1")
    summ = next(r for r in rows if r["capability"] == "summarization")
    assert summ["status"] == "ok"
    assert summ.get("task_type") is None
    assert "recent_runs" not in summ


@pytest.mark.asyncio
async def test_resolver_failure_is_isolated(monkeypatch):
    async def _get_ai_settings(uid):
        return {"ai_providers": {}, "task_assignment": {}}

    calls = {"n": 0}

    async def _resolve(uid, task_key, default_slug):
        calls["n"] += 1
        if task_key == "caption":
            raise RuntimeError("boom")
        return (
            "qwen",
            {"model": "qwen-max", "api_key": "sk"},
            "qwen-max",
            default_slug,
        )

    monkeypatch.setattr(ai_health, "get_ai_settings", _get_ai_settings)
    monkeypatch.setattr(ai_health, "resolve_task_provider_config", _resolve)

    rows = await ai_health.get_capability_health("u1")
    # The crashing capability becomes an 'error' row; siblings unaffected.
    caption = next(r for r in rows if r["capability"] == "caption")
    assert caption["status"] == "error"
    assert all(r["status"] != "error" for r in rows if r["capability"] != "caption")
