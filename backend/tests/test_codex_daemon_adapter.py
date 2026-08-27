from __future__ import annotations

from uuid import uuid4

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.adapters.codex_daemon import CodexDaemonAdapter
from app.services.codex.daemon_dispatch import DaemonOfflineError
from app.services.codex.errors import CodexLocalError


def _composed(tools=None, model="") -> ComposedSystemPrompt:
    return ComposedSystemPrompt(
        agent_id=uuid4(),
        agent_slug="a",
        model=model,
        temperature=0.7,
        max_tokens=1000,
        system_message="be brief",
        tools=tools or [],
        skill_manifest=[],
        cache_fingerprint="f",
    )


async def _scope(_uid: str) -> int:
    return 42


@pytest.mark.asyncio
async def test_call_dispatches_text_job_and_returns_openai_shape():
    seen: dict = {}

    async def fake_dispatch(**kw):
        seen.update(kw)
        return {
            "text": "hello",
            "usage": {
                "input_tokens": 10,
                "cached_input_tokens": 4,
                "output_tokens": 2,
            },
        }

    a = CodexDaemonAdapter(
        user_id="u1", model="gpt-5", dispatch=fake_dispatch, scope_resolver=_scope
    )
    resp = await a.call(_composed(), [{"role": "user", "content": "hi"}])

    assert seen["user_id"] == "u1" and seen["scope_id"] == 42 and seen["kind"] == "text"
    assert seen["timeout_s"] == 180
    assert seen["payload"]["model"] == "gpt-5"
    assert seen["payload"]["prompt"].startswith("[System]\nbe brief")
    assert seen["payload"]["image_urls"] == []
    assert resp["choices"][0]["message"] == {
        "role": "assistant",
        "content": "hello",
        "tool_calls": [],
    }
    assert resp["choices"][0]["finish_reason"] == "stop"
    assert resp["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 2,
        "total_tokens": 12,
        "prompt_tokens_details": {"cached_tokens": 4},
    }


@pytest.mark.asyncio
async def test_tools_present_is_rejected_before_any_dispatch():
    async def never(**_):
        raise AssertionError("must not dispatch")

    a = CodexDaemonAdapter(user_id="u1", dispatch=never, scope_resolver=_scope)
    with pytest.raises(CodexLocalError) as ei:
        await a.call(
            _composed(tools=[{"type": "function", "function": {"name": "Skill"}}]), []
        )
    assert ei.value.code == "tools_unsupported"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raised,code",
    [
        (DaemonOfflineError("no daemon"), "daemon_offline"),
        (TimeoutError("late"), "timeout"),
        (RuntimeError("codex_not_logged_in: run codex login"), "codex_not_logged_in"),
        (RuntimeError("cli_missing: codex not found"), "cli_missing"),
        (RuntimeError("codex_no_output: nothing"), "codex_no_output"),
        (RuntimeError("weird"), "codex_failed"),
    ],
)
async def test_dispatch_failures_become_typed_codes(raised, code):
    async def boom(**_):
        raise raised

    a = CodexDaemonAdapter(user_id="u1", dispatch=boom, scope_resolver=_scope)
    with pytest.raises(CodexLocalError) as ei:
        await a.call(_composed(), [{"role": "user", "content": "x"}])
    assert ei.value.code == code


@pytest.mark.asyncio
async def test_composed_model_wins_over_adapter_default_and_no_stream():
    seen: dict = {}

    async def fake_dispatch(**kw):
        seen.update(kw)
        return {"text": "ok", "usage": {}}

    a = CodexDaemonAdapter(
        user_id="u1", model="row-model", dispatch=fake_dispatch, scope_resolver=_scope
    )
    await a.call(_composed(model="composed-model"), [{"role": "user", "content": "x"}])
    assert seen["payload"]["model"] == "composed-model"
    assert not hasattr(a, "stream")
