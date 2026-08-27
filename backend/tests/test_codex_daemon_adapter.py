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
    # The two legs are deliberately unequal: the daemon caps `codex exec` at
    # 180s (payload), the backend waits 210s for the result. The daemon must
    # time out FIRST so its typed code arrives instead of a generic timeout.
    assert seen["timeout_s"] == 210
    assert seen["payload"]["timeout_s"] == 180
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
        # Already typed: it must pass through untouched. ``CodexLocalError`` is
        # itself a ``RuntimeError``, so without the dedicated except block it
        # would be re-parsed by ``from_daemon_error`` into ``codex_failed``
        # with one marker nested inside another.
        (CodexLocalError("codex_not_logged_in", "x"), "codex_not_logged_in"),
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
async def test_images_over_the_backstop_cap_are_trimmed_and_logged():
    """Trimming must never be silent: the flattened prompt still refers to the
    dropped images, so the model would answer about pictures it never got.

    Captured through a loguru sink rather than ``caplog`` because this module
    logs via loguru, which does not propagate into pytest's stdlib capture —
    ``caplog`` would sit empty regardless of whether the warning fired."""
    from loguru import logger

    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    seen: dict = {}

    async def fake_dispatch(**kw):
        seen.update(kw)
        return {"text": "ok", "usage": {}}

    try:
        a = CodexDaemonAdapter(
            user_id="u1", dispatch=fake_dispatch, scope_resolver=_scope
        )
        await a.call(
            _composed(),
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe these"},
                        *(
                            {
                                "type": "image_url",
                                "image_url": {"url": f"https://api.nous.ink/{i}.png"},
                            }
                            for i in range(10)
                        ),
                    ],
                }
            ],
        )
    finally:
        logger.remove(sink_id)

    assert len(seen["payload"]["image_urls"]) == 9
    assert seen["payload"]["image_urls"][-1] == "https://api.nous.ink/8.png"
    assert any("dropping 1 of 10 images" in r for r in records), records


def _data_url(decoded_bytes: int) -> str:
    """A base64 data URL whose payload decodes to roughly ``decoded_bytes``."""
    return "data:image/png;base64," + "A" * ((decoded_bytes * 4 + 2) // 3)


def _image_message(*urls: str) -> list[dict]:
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "describe these"},
                *({"type": "image_url", "image_url": {"url": u}} for u in urls),
            ],
        }
    ]


@pytest.mark.asyncio
async def test_one_small_inline_image_passes_through_untouched():
    seen: dict = {}

    async def fake_dispatch(**kw):
        seen.update(kw)
        return {"text": "ok", "usage": {}}

    url = _data_url(1024 * 1024)
    a = CodexDaemonAdapter(user_id="u1", dispatch=fake_dispatch, scope_resolver=_scope)
    await a.call(_composed(), _image_message(url, "https://api.nous.ink/x.png"))

    # http(s) URLs are not weighed at all — the daemon streams those itself;
    # only what we inline into the job counts against the budget.
    assert seen["payload"]["image_urls"] == [url, "https://api.nous.ink/x.png"]


@pytest.mark.asyncio
async def test_inline_images_over_the_byte_budget_are_rejected_before_dispatch():
    """The chat layer hands us `data:image/...;base64` URLs, so the job frame
    carries the pixels themselves. Oversized frames have to die HERE: past this
    point the bytes are already in Redis and on the socket, and the failure
    would surface as a transport error with no actionable code."""

    async def never(**_):
        raise AssertionError("must not dispatch")

    a = CodexDaemonAdapter(user_id="u1", dispatch=never, scope_resolver=_scope)
    with pytest.raises(CodexLocalError) as ei:
        await a.call(
            _composed(),
            _image_message(*(_data_url(1024 * 1024) for _ in range(7))),
        )
    assert ei.value.code == "ref_rejected"
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_row_model_wins_and_the_composed_display_name_is_ignored():
    """``composed.model`` is ``agents.model``, which on this path is the
    catalog row's DISPLAY name ("Codex (Local)") — a label, not a model codex
    can run. If it reached the payload the daemon would shell out
    ``codex exec --model "Codex (Local)"``. Only the row's ``actual_model``
    (passed at construction) may reach the payload; "" means "the user's own
    codex default", which is a legitimate answer for a local CLI."""
    seen: dict = {}

    async def fake_dispatch(**kw):
        seen.update(kw)
        return {"text": "ok", "usage": {}}

    blank = CodexDaemonAdapter(
        user_id="u1", model="", dispatch=fake_dispatch, scope_resolver=_scope
    )
    await blank.call(
        _composed(model="Codex (Local)"), [{"role": "user", "content": "x"}]
    )
    assert seen["payload"]["model"] == ""

    pinned = CodexDaemonAdapter(
        user_id="u1", model="gpt-5", dispatch=fake_dispatch, scope_resolver=_scope
    )
    await pinned.call(
        _composed(model="Codex (Local)"), [{"role": "user", "content": "x"}]
    )
    assert seen["payload"]["model"] == "gpt-5"
    assert not hasattr(pinned, "stream")
