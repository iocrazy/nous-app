"""The provider contract, proven through its REAL consumer (fh4 T5).

CLAUDE.md 「公共契约两侧都要遵守」: every source shape a provider can hand back
has to be run through the real consumer, not just the adapter in isolation.
Production's only chat path is ``AgentRunner.stream_turn``'s buffered branch
(the chat wiring hands the runner an ``LLMFallbackChain``, which has no
``stream``), so every row here goes:

    real OpenAICompatibleAdapter.call() over a mocked socket → real
    LLMRetryMiddleware → real
    LLMFallbackChain → AgentRunner.stream_turn (buffered) → real RunRecorder
    (fake DB table)

and asserts what lands where it matters: the typed ``turn_end`` event and
``agent_runs.error_code``. One more row drives the issue executor to prove an
in-band provider error reaches its exception path instead of being filed as
EMPTY_OUTPUT.

Before the contract, a 200 error body surfaced as an untyped ``KeyError``, a
content-filtered or token-less empty reply was a *successful* turn, and every
raise was filed under its class name.
"""

from __future__ import annotations

from typing import Any, Callable
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.services.ai.error_catalog import ALL_ERROR_CODES
from app.services.ai.llm.llm_fallback_chain import LLMFallbackChain
from app.services.ai.runner.agent_runner import AgentRunner
from app.services.ai.runner.run_recorder import RunRecorder
from tests.test_run_recorder import _FakeTable, _patched

pytestmark = pytest.mark.unit

PRIMARY, FALLBACK = "primary-m", "fallback-m"


# ── source shapes (real wire forms) ───────────────────────────────────────


def _envelope(content: Any, finish: str, completion_tokens: int) -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": PRIMARY,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish,
            }
        ],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": completion_tokens,
            "total_tokens": 12 + completion_tokens,
        },
    }


def _json(status: int, body: Any) -> Callable[[httpx.Request], httpx.Response]:
    return lambda req: httpx.Response(status, json=body)


def _raise(exc_factory: Callable[[httpx.Request], BaseException]):
    def _go(req: httpx.Request) -> httpx.Response:
        raise exc_factory(req)

    return _go


# name → (wire behaviour, expected turn_end reason, expected code,
#          fallback allowed)
SHAPES: dict[str, tuple] = {
    "http_429_quota_cap": (
        _json(
            429,
            {
                "error": {
                    "code": "SetLimitExceeded",
                    "message": "Your account has reached the set inference limit",
                }
            },
        ),
        "error",
        "PROVIDER_QUOTA_CAP",
        True,
    ),
    "http_401": (
        _json(401, {"error": {"code": "AuthenticationError", "message": "bad key"}}),
        "error",
        "PROVIDER_AUTH",
        False,
    ),
    "body_200_error": (
        _json(
            200,
            {
                "error": {
                    "code": "InternalServiceError",
                    "message": "The service encountered an unexpected internal "
                    "error.",
                    "type": "InternalServiceError",
                }
            },
        ),
        "error",
        "PROVIDER_BAD_RESPONSE",
        True,
    ),
    "content_filter": (
        _json(200, _envelope("", "content_filter", 0)),
        "error",
        "PROVIDER_CONTENT_FILTER",
        True,
    ),
    "empty_unbilled": (
        _json(200, _envelope("", "stop", 0)),
        "error",
        "PROVIDER_EMPTY_RESPONSE",
        True,
    ),
    "stream_disconnect": (
        _raise(
            lambda req: httpx.RemoteProtocolError(
                "Server disconnected without sending a response.", request=req
            )
        ),
        "error",
        "PROVIDER_UNREACHABLE",
        True,
    ),
}

OK_SHAPES: dict[str, tuple] = {
    # provider cut the output: an honest partial answer, not an error
    "length": (_json(200, _envelope("abc", "length", 16)), "provider_length"),
    # billed but empty: fh2 T4's workflow-owned policy — stays a success,
    # with the empty_response evidence event
    "empty_billed": (_json(200, _envelope("", "stop", 649)), "completed"),
}


# ── harness ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _wire(monkeypatch):
    """The REAL ``OpenAICompatibleAdapter.call()`` runs; only the socket is
    replaced. The handler is looked up per request by the model in the body,
    so primary and fallback can answer differently."""
    import json as _json_mod

    from app.services.ai.adapters import openai_compat

    real_client = httpx.AsyncClient
    state: dict[str, Any] = {"behaviours": {}, "calls": []}

    def _handler(req: httpx.Request) -> httpx.Response:
        model = _json_mod.loads(req.content)["model"]
        state["calls"].append(model)
        return state["behaviours"][model](req)

    def _client(*a, **kw):
        kw["transport"] = httpx.MockTransport(_handler)
        return real_client(*a, **kw)

    monkeypatch.setattr(openai_compat.httpx, "AsyncClient", _client)
    return state


def _chain(wire: dict, primary, fallback) -> LLMFallbackChain:
    from app.services.ai.adapters.openai_compat import OpenAICompatibleAdapter

    wire["behaviours"] = {PRIMARY: primary, FALLBACK: fallback}
    return LLMFallbackChain(
        primary_model=PRIMARY,
        fallback_models=[FALLBACK],
        adapter_factory=lambda m: OpenAICompatibleAdapter(
            api_url="https://ark.example/api/v3", api_key="k", default_model=m
        ),
        max_retries_per_model=2,
        base_delay_s=0.0,
        max_delay_s=0.0,
    )


class _Tool:
    recorder = None

    async def execute(self, args):
        return {}


def _composed():
    from app.schemas.ai_library import ComposedSystemPrompt

    return ComposedSystemPrompt(
        agent_id=uuid4(),
        agent_slug="t",
        model=PRIMARY,
        temperature=0.0,
        max_tokens=64,
        system_message="sys",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="f",
    )


async def _drive(chain) -> tuple[_FakeTable, list, BaseException | None]:
    """stream_turn inside a real RunRecorder; returns the table, the chunks
    and whatever escaped."""
    table = _FakeTable()
    p_read, p_write = _patched(table)
    runner = AgentRunner(adapter=chain, skill_tool=_Tool())
    chunks: list = []
    escaped: BaseException | None = None
    with p_read, p_write:
        try:
            async with RunRecorder(
                agent_id=uuid4(), user_id=uuid4(), trigger="chat"
            ) as rec:
                async for ch in runner.stream_turn(
                    _composed(),
                    [{"role": "user", "content": "q"}],
                    recorder=rec,
                    auto_recorder=False,
                ):
                    chunks.append(ch)
        except Exception as exc:  # noqa: BLE001 — the row asserts on it
            escaped = exc
    return table, chunks, escaped


def _turn_ends(table: _FakeTable) -> list[dict]:
    return [e["payload"] for e in table.event_inserts if e["event_type"] == "turn_end"]


def _finish(table: _FakeTable) -> dict:
    finishes = [u for u in table.update_calls if "status" in u]
    assert finishes, f"run row never finished: {table.update_calls}"
    return finishes[-1]


_HEALTHY = _json(200, _envelope("fine", "stop", 3))


# ── every model fails with the shape → typed error on both surfaces ───────


@pytest.mark.parametrize("name", sorted(SHAPES))
async def test_error_shape_files_a_catalog_code_on_turn_end_and_agent_runs(name, _wire):
    behaviour, reason, code, _fallback = SHAPES[name]
    table, _chunks, escaped = await _drive(_chain(_wire, behaviour, behaviour))

    assert escaped is not None, f"{name}: the turn must raise, not succeed"
    assert code in ALL_ERROR_CODES
    ends = _turn_ends(table)
    assert len(ends) == 1, ends
    assert ends[0]["reason"] == reason
    assert ends[0].get("error_code") == code, ends[0]
    finish = _finish(table)
    assert finish["status"] == "failed"
    assert finish["error_code"] == code, finish


# ── primary fails with the shape, fallback healthy → fallback decision ────


@pytest.mark.parametrize("name", sorted(SHAPES))
async def test_error_shape_falls_back_only_when_the_contract_allows(name, _wire):
    behaviour, _reason, code, fallback_allowed = SHAPES[name]
    table, chunks, escaped = await _drive(_chain(_wire, behaviour, _HEALTHY))
    calls = _wire["calls"]

    if fallback_allowed:
        assert escaped is None, f"{name}: fallback should have served: {escaped!r}"
        assert FALLBACK in calls
        assert chunks and chunks[-1].delta_text == "fine"
        assert _turn_ends(table)[0]["reason"] == "completed"
    else:
        assert escaped is not None
        assert FALLBACK not in calls
        assert _finish(table)["error_code"] == code


async def test_content_filter_is_not_retried_on_the_same_model(_wire):
    await _drive(_chain(_wire, SHAPES["content_filter"][0], _HEALTHY))
    assert _wire["calls"] == [PRIMARY, FALLBACK], _wire["calls"]


async def test_bad_response_body_is_retried_once_on_the_same_model(_wire):
    await _drive(_chain(_wire, SHAPES["body_200_error"][0], _HEALTHY))
    assert _wire["calls"] == [PRIMARY, PRIMARY, FALLBACK], _wire["calls"]


# ── success shapes stay successes, byte-identical ─────────────────────────


@pytest.mark.parametrize("name", sorted(OK_SHAPES))
async def test_ok_shapes_are_not_errors(name, _wire):
    behaviour, reason = OK_SHAPES[name]
    table, chunks, escaped = await _drive(_chain(_wire, behaviour, _HEALTHY))

    assert escaped is None, escaped
    assert _wire["calls"] == [PRIMARY]
    assert _turn_ends(table)[0]["reason"] == reason
    assert _finish(table)["status"] == "completed"
    if name == "empty_billed":
        kinds = [
            e["payload"].get("kind")
            for e in table.event_inserts
            if e["event_type"] == "error"
        ]
        assert "empty_response" in kinds


# ── issue side: an in-band error reaches the executor's exception path ────


async def test_issue_executor_sees_the_raise_not_an_empty_output(monkeypatch, _wire):
    """Before the contract a content-filtered reply was a successful turn with
    empty content — ``run_issue_agent`` returned ``content=""`` and the
    workflow typed the run EMPTY_OUTPUT (``mark_empty_output``). Now the raise
    reaches the executor, whose caller routes it to ``blocked``."""
    from app.services.issues import issue_agent_executor as m

    behaviour = SHAPES["content_filter"][0]

    async def fake_run_session_turn(session_id, *, chunk_callback=None, **kw):
        # What the chat service does on the chunk_callback path, minus
        # persistence: drive stream_turn and fold the chunks.
        _t, chunks, escaped = await _drive(_chain(_wire, behaviour, behaviour))
        if escaped is not None:
            raise escaped
        text = "".join(c.delta_text or "" for c in chunks)
        return {"assistant_message": {"content": text}, "run_id": "1"}

    fake_chat = type("C", (), {"run_session_turn": staticmethod(fake_run_session_turn)})
    monkeypatch.setattr(m, "get_or_create_issue_session", AsyncMock(return_value="s"))
    monkeypatch.setattr(m, "AILibraryChatService", lambda: fake_chat())
    monkeypatch.setattr(m, "publish_chunk", AsyncMock())
    monkeypatch.setattr(m, "publish_message", AsyncMock())
    monkeypatch.setattr(m, "publish_status", AsyncMock())
    forced = AsyncMock(return_value=(None, None))
    monkeypatch.setattr(m, "attempt_forced_finish_declaration", forced)

    with pytest.raises(Exception) as ei:
        await m.run_issue_agent(
            issue={"id": 4242, "title": "t", "description": "d"},
            agent_id="a",
            user_id="u",
        )
    from app.services.ai.error_catalog import classify_ai_error

    assert classify_ai_error(ei.value) == "PROVIDER_CONTENT_FILTER"
    forced.assert_not_awaited()
