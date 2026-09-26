"""Adapter-side halves of the provider contract (fh4 T5 commit ②).

* Claude: ``stop_reason`` must survive into the envelope (``max_tokens`` →
  ``length``, ``refusal`` → content filter) and ``usage`` must be passed
  through. It used to collapse everything to ``"stop"`` and drop usage, so a
  truncated Claude answer looked complete and cost nothing.
* True stream: a connection that closes before any ``finish_reason`` is a
  transport failure, not a silent (cancelled-looking) end.
* Key rotation: only an auth / quota failure is the KEY's fault.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.adapters.claude import ClaudeAdapter
from app.services.ai.adapters.openai_compat import OpenAICompatibleAdapter
from app.services.ai.provider_contract import ProviderOutcome, ProviderResponseError

pytestmark = pytest.mark.unit


def _composed(model: str = "claude-opus-4-5") -> ComposedSystemPrompt:
    return ComposedSystemPrompt(
        agent_id=uuid4(),
        agent_slug="t",
        model=model,
        temperature=0.0,
        max_tokens=64,
        system_message="sys",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="f",
    )


def _anthropic(stop_reason: str, blocks: list, usage: dict | None) -> SimpleNamespace:
    """Shape of ``anthropic.types.Message`` (attribute access, real field names)."""
    return SimpleNamespace(
        id="msg_1",
        type="message",
        role="assistant",
        content=blocks,
        stop_reason=stop_reason,
        usage=SimpleNamespace(**usage) if usage is not None else None,
    )


def _text(t: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=t)


async def _claude_call(resp: SimpleNamespace):
    adapter = ClaudeAdapter(api_key="sk-ant-test")
    with patch.object(adapter, "_client") as client:
        client.messages.create = AsyncMock(return_value=resp)
        return await adapter.call(_composed(), [{"role": "user", "content": "hi"}])


# ── Claude ────────────────────────────────────────────────────────────────


async def test_claude_max_tokens_maps_to_length_and_keeps_usage():
    out = await _claude_call(
        _anthropic(
            "max_tokens",
            [_text("a truncated answ")],
            {
                "input_tokens": 120,
                "output_tokens": 64,
                "cache_read_input_tokens": 100,
                "cache_creation_input_tokens": 0,
            },
        )
    )
    assert out["choices"][0]["finish_reason"] == "length"
    assert out["usage"] == {
        "prompt_tokens": 120,
        "completion_tokens": 64,
        "total_tokens": 184,
        "prompt_tokens_details": {"cached_tokens": 100},
    }


async def test_claude_refusal_raises_content_filter():
    with pytest.raises(ProviderResponseError) as ei:
        await _claude_call(
            _anthropic("refusal", [], {"input_tokens": 10, "output_tokens": 0})
        )
    assert ei.value.error_code == "PROVIDER_CONTENT_FILTER"


@pytest.mark.parametrize(
    "stop_reason, expected",
    [("end_turn", "stop"), ("stop_sequence", "stop"), ("tool_use", "tool_calls")],
)
async def test_claude_normal_ends(stop_reason, expected):
    blocks = [_text("ok")]
    if stop_reason == "tool_use":
        blocks.append(
            SimpleNamespace(type="tool_use", id="tu_1", name="Skill", input={"a": 1})
        )
    out = await _claude_call(
        _anthropic(stop_reason, blocks, {"input_tokens": 5, "output_tokens": 3})
    )
    assert out["choices"][0]["finish_reason"] == expected


# ── true stream disconnect ────────────────────────────────────────────────


async def test_stream_that_closes_before_any_finish_raises_unreachable():
    """Deltas arrive, then the socket closes: no finish_reason, no [DONE].
    It used to just end — the runner filed it CANCELLED."""
    body = (
        'data: {"choices":[{"delta":{"content":"hel"},"finish_reason":null}]}\n\n'
        'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":null}]}\n\n'
    )
    real = httpx.AsyncClient

    def _client(*a, **kw):
        kw["transport"] = httpx.MockTransport(
            lambda req: httpx.Response(
                200, text=body, headers={"content-type": "text/event-stream"}
            )
        )
        return real(*a, **kw)

    adapter = OpenAICompatibleAdapter(
        api_url="https://ark.example/api/v3", api_key="k", default_model="m"
    )
    chunks = []
    with patch("app.services.ai.adapters.openai_compat.httpx.AsyncClient", _client):
        with pytest.raises(ProviderResponseError) as ei:
            async for c in adapter.stream(
                _composed("m"), [{"role": "user", "content": "q"}]
            ):
                chunks.append(c)
    assert ei.value.error_code == "PROVIDER_UNREACHABLE"
    assert ei.value.retryable
    assert "".join(c.delta_text or "" for c in chunks) == "hello"


# ── key rotation ──────────────────────────────────────────────────────────


class _Raising:
    def __init__(self, key: str, errors: dict, calls: list):
        self._key, self._errors, self._calls = key, errors, calls

    async def call(self, composed, messages):
        self._calls.append(self._key)
        if self._key in self._errors:
            raise self._errors[self._key]
        return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}


def _typed(code: str, detail: str, status: int | None = None) -> ProviderResponseError:
    return ProviderResponseError(
        ProviderOutcome.error(code, detail, status_code=status)
    )


@pytest.mark.parametrize(
    "err",
    [
        # a content filter is about the REQUEST, not the key — even when the
        # provider's text happens to contain a status-looking number
        _typed("PROVIDER_CONTENT_FILTER", "blocked by policy 403-b"),
        # a 200 body error carrying a 5xx — the key is fine
        _typed("PROVIDER_BAD_RESPONSE", "internal", status=500),
        _typed("PROVIDER_EMPTY_RESPONSE", "nothing"),
    ],
)
async def test_rotation_does_not_burn_keys_on_non_key_errors(err):
    from app.agent_framework import KeyRotator, RotatingAdapter

    calls: list = []
    rotator = KeyRotator(["k1", "k2"])
    adapter = RotatingAdapter(rotator, lambda k: _Raising(k, {"k1": err}, calls))
    with pytest.raises(ProviderResponseError):
        await adapter.call(None, [])
    assert calls == ["k1"]


@pytest.mark.parametrize(
    "code, status",
    [
        ("PROVIDER_AUTH", 401),
        ("PROVIDER_RATE_LIMIT", 429),
        ("PROVIDER_QUOTA_CAP", 429),
        # a cap reported inside a 200 body carries no numeric status
        ("PROVIDER_QUOTA_CAP", None),
    ],
)
async def test_rotation_still_rotates_on_key_errors(code, status):
    from app.agent_framework import KeyRotator, RotatingAdapter

    calls: list = []
    rotator = KeyRotator(["k1", "k2"])
    err = _typed(code, "key problem", status=status)
    adapter = RotatingAdapter(rotator, lambda k: _Raising(k, {"k1": err}, calls))
    out = await adapter.call(None, [])
    assert calls == ["k1", "k2"]
    assert out["choices"][0]["message"]["content"] == "ok"
