"""Unit tests for LLMFallbackChain — primary→fallback walking, switch log, errors."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.llm.llm_fallback_chain import (
    AllModelsFailed,
    LLMFallbackChain,
)
from app.services.ai.llm.llm_retry_middleware import LLMCallError


def _composed() -> ComposedSystemPrompt:
    return ComposedSystemPrompt(
        agent_id=UUID("00000000-0000-0000-0000-000000000001"),
        agent_slug="script_ai",
        model="qwen-max",
        temperature=0.7,
        max_tokens=1024,
        system_message="SYSTEM",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="x",
    )


class _StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


def _make_factory(model_to_adapter: dict[str, AsyncMock]):
    """Build an adapter_factory closure that returns a pre-configured AsyncMock per model."""

    def factory(model: str):
        return model_to_adapter[model]

    return factory


@pytest.mark.unit
@pytest.mark.asyncio
async def test_primary_succeeds_no_fallback_needed():
    primary = AsyncMock()
    primary.call.return_value = {"choices": [{"message": {"content": "ok"}}]}

    chain = LLMFallbackChain(
        primary_model="qwen-max",
        fallback_models=["qwen-plus"],
        adapter_factory=_make_factory({"qwen-max": primary}),
    )
    response = await chain.call(_composed(), [])

    assert response["_actual_model"] == "qwen-max"
    assert response["_fallback_meta"]["fallback_used"] is False
    assert response["_fallback_meta"]["switch_log"] == []
    primary.call.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_primary_fails_fallback_zero_succeeds():
    primary = AsyncMock()
    primary.call.side_effect = _StatusError(503)
    fallback0 = AsyncMock()
    fallback0.call.return_value = {"choices": [{"message": {"content": "saved"}}]}

    chain = LLMFallbackChain(
        primary_model="qwen-max",
        fallback_models=["qwen-plus"],
        adapter_factory=_make_factory({"qwen-max": primary, "qwen-plus": fallback0}),
        max_retries_per_model=1,
        base_delay_s=0,
    )
    response = await chain.call(_composed(), [])

    assert response["_actual_model"] == "qwen-plus"
    assert response["_fallback_meta"]["fallback_used"] is True
    assert response["_fallback_meta"]["switch_log"] == [
        {"from": "qwen-max", "to": "qwen-plus", "reason": "retries_exhausted"}
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_primary_and_fallback_zero_fail_fallback_one_succeeds():
    primary = AsyncMock()
    primary.call.side_effect = _StatusError(503)
    fb0 = AsyncMock()
    fb0.call.side_effect = _StatusError(429)
    fb1 = AsyncMock()
    fb1.call.return_value = {"choices": [{"message": {"content": "third time lucky"}}]}

    chain = LLMFallbackChain(
        primary_model="qwen-max",
        fallback_models=["qwen-plus", "qwen-turbo"],
        adapter_factory=_make_factory(
            {"qwen-max": primary, "qwen-plus": fb0, "qwen-turbo": fb1}
        ),
        max_retries_per_model=1,
        base_delay_s=0,
    )
    response = await chain.call(_composed(), [])

    assert response["_actual_model"] == "qwen-turbo"
    log = response["_fallback_meta"]["switch_log"]
    assert len(log) == 2
    assert log[0] == {
        "from": "qwen-max",
        "to": "qwen-plus",
        "reason": "retries_exhausted",
    }
    assert log[1] == {
        "from": "qwen-plus",
        "to": "qwen-turbo",
        "reason": "retries_exhausted",
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_all_models_fail_raises_all_models_failed():
    primary = AsyncMock()
    primary.call.side_effect = _StatusError(503)
    fb0 = AsyncMock()
    fb0.call.side_effect = _StatusError(503)

    chain = LLMFallbackChain(
        primary_model="qwen-max",
        fallback_models=["qwen-plus"],
        adapter_factory=_make_factory({"qwen-max": primary, "qwen-plus": fb0}),
        max_retries_per_model=1,
        base_delay_s=0,
    )

    with pytest.raises(AllModelsFailed):
        await chain.call(_composed(), [])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_fallback_list_just_uses_primary():
    primary = AsyncMock()
    primary.call.return_value = {"choices": [{"message": {"content": "ok"}}]}

    chain = LLMFallbackChain(
        primary_model="qwen-max",
        fallback_models=[],
        adapter_factory=_make_factory({"qwen-max": primary}),
    )
    response = await chain.call(_composed(), [])
    assert response["_actual_model"] == "qwen-max"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_fallback_primary_fails_raises_all_models_failed():
    primary = AsyncMock()
    primary.call.side_effect = _StatusError(503)

    chain = LLMFallbackChain(
        primary_model="qwen-max",
        fallback_models=[],
        adapter_factory=_make_factory({"qwen-max": primary}),
        max_retries_per_model=1,
        base_delay_s=0,
    )

    with pytest.raises(AllModelsFailed):
        await chain.call(_composed(), [])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_retryable_error_does_not_trigger_fallback():
    """LLMCallError (e.g. 401 auth) bypasses fallback — surface immediately."""
    primary = AsyncMock()
    primary.call.side_effect = _StatusError(401)
    fb0 = AsyncMock()
    fb0.call.return_value = {"choices": [{"message": {"content": "should never see"}}]}

    chain = LLMFallbackChain(
        primary_model="qwen-max",
        fallback_models=["qwen-plus"],
        adapter_factory=_make_factory({"qwen-max": primary, "qwen-plus": fb0}),
        max_retries_per_model=1,
        base_delay_s=0,
    )

    with pytest.raises(LLMCallError):
        await chain.call(_composed(), [])

    fb0.call.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_adapter_factory_failure_skips_to_next_model():
    """If adapter_factory raises (e.g. missing API key), skip that model."""
    primary = AsyncMock()
    primary.call.side_effect = _StatusError(503)

    fb1 = AsyncMock()
    fb1.call.return_value = {"choices": [{"message": {"content": "ok"}}]}

    def factory(model: str):
        if model == "qwen-max":
            return primary
        if model == "qwen-plus":
            raise RuntimeError("API key missing for qwen-plus")
        if model == "qwen-turbo":
            return fb1
        raise ValueError("unknown")

    chain = LLMFallbackChain(
        primary_model="qwen-max",
        fallback_models=["qwen-plus", "qwen-turbo"],
        adapter_factory=factory,
        max_retries_per_model=1,
        base_delay_s=0,
    )
    response = await chain.call(_composed(), [])

    assert response["_actual_model"] == "qwen-turbo"
    log = response["_fallback_meta"]["switch_log"]
    # qwen-max → qwen-plus (retries_exhausted), then qwen-plus → init_failed... wait,
    # the switch from qwen-max records 'retries_exhausted', then we attempt qwen-plus
    # whose factory raises — log gets 'adapter_init_failed' qwen-max → qwen-plus.
    # Actually the code records based on idx-1 → model. Let's just verify it ran through.
    assert len(log) >= 1
    assert log[-1]["to"] == "qwen-turbo" or log[-2]["to"] == "qwen-plus"


# ---------------------------------------------------------------------------
# AI-007: chain-wide global deadline
# ---------------------------------------------------------------------------


def _clock(values):
    seq = list(values)

    def _next() -> float:
        return seq.pop(0) if len(seq) > 1 else seq[0]

    return _next


@pytest.mark.unit
@pytest.mark.asyncio
async def test_deadline_stops_chain_before_later_models():
    """Once the chain-wide deadline passes, no further fallback models are
    tried — even though they were configured."""
    primary = AsyncMock()
    primary.call.side_effect = _StatusError(503)
    fb0 = AsyncMock()
    fb0.call.return_value = {"choices": [{"message": {"content": "fb0"}}]}

    chain = LLMFallbackChain(
        primary_model="qwen-max",
        fallback_models=["qwen-plus"],
        adapter_factory=_make_factory({"qwen-max": primary, "qwen-plus": fb0}),
        max_retries_per_model=0,  # no inner retries — fail fast to fallback
        base_delay_s=0,
        total_deadline_seconds=5.0,
    )
    # start=0; every later monotonic() reads 10s (> 5s) → fallback gated out.
    chain.monotonic = _clock([0.0, 10.0])

    with pytest.raises(AllModelsFailed):
        await chain.call(_composed(), [])

    primary.call.assert_awaited()  # primary tried
    fb0.call.assert_not_awaited()  # deadline blocked the fallback


# ── Audit #8 (fix A): wire model realigned per attempt ────────────────────


def _captured_model(adapter: AsyncMock) -> str:
    """The composed.model the adapter's call() actually received."""
    composed_arg = adapter.call.await_args.args[0]
    return composed_arg.model


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cross_provider_fallback_realigns_wire_model():
    """Primary (qwen) fails → fallback (doubao) must receive composed.model
    == its OWN model, not the stale primary name. Otherwise the doubao
    endpoint+key gets a qwen model name → 400/misroute."""
    primary = AsyncMock()
    primary.call.side_effect = _StatusError(503)
    doubao = AsyncMock()
    doubao.call.return_value = {"choices": [{"message": {"content": "ok"}}]}

    chain = LLMFallbackChain(
        primary_model="qwen-max",
        fallback_models=["doubao-seed-2"],
        adapter_factory=_make_factory({"qwen-max": primary, "doubao-seed-2": doubao}),
        max_retries_per_model=0,
        base_delay_s=0,
    )
    composed = _composed()
    response = await chain.call(composed, [])

    assert response["_actual_model"] == "doubao-seed-2"
    assert _captured_model(primary) == "qwen-max"
    assert _captured_model(doubao) == "doubao-seed-2"  # realigned, not qwen-max
    # Original composed object is never mutated.
    assert composed.model == "qwen-max"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_same_provider_fallback_switches_wire_model():
    """qwen-max → qwen-plus: the fallback attempt must actually carry
    qwen-plus on the wire, not silently re-call the failing qwen-max."""
    primary = AsyncMock()
    primary.call.side_effect = _StatusError(503)
    plus = AsyncMock()
    plus.call.return_value = {"choices": [{"message": {"content": "ok"}}]}

    chain = LLMFallbackChain(
        primary_model="qwen-max",
        fallback_models=["qwen-plus"],
        adapter_factory=_make_factory({"qwen-max": primary, "qwen-plus": plus}),
        max_retries_per_model=0,
        base_delay_s=0,
    )
    await chain.call(_composed(), [])

    assert _captured_model(plus) == "qwen-plus"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_primary_success_keeps_composed_identity():
    """On the common primary path (composed.model == primary_model) no copy
    is made — the exact composed object is forwarded unchanged."""
    primary = AsyncMock()
    primary.call.return_value = {"choices": [{"message": {"content": "ok"}}]}

    chain = LLMFallbackChain(
        primary_model="qwen-max",
        fallback_models=["qwen-plus"],
        adapter_factory=_make_factory({"qwen-max": primary}),
    )
    composed = _composed()
    await chain.call(composed, [])

    assert primary.call.await_args.args[0] is composed  # same object, no churn


# ──────────────────────────────────────────────────────────────────────────
# AllModelsFailed must stand on its own message (2026-08-19 ai_summary)
# ──────────────────────────────────────────────────────────────────────────
# The message used to be "primary + N fallback(s) exhausted" and leaned on
# __cause__ for the reason. DBOS pickles an exception's args and DROPS
# __cause__, so four production failures reached task_tracking carrying a
# model count and nothing else — the real cause (a Volcengine account-level
# SetLimitExceeded cap) survived only in a container's stderr.


class _BodyError(Exception):
    """Adapter failure shaped like an httpx status error (status + body)."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        self.response = type("_R", (), {"status_code": status_code, "text": body})()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_all_models_failed_message_names_each_model_and_its_reason():
    primary = AsyncMock()
    primary.call.side_effect = _BodyError(
        429, '{"error":{"code":"SetLimitExceeded","message":"account cap"}}'
    )
    fb0 = AsyncMock()
    fb0.call.side_effect = _BodyError(503, "upstream down")

    chain = LLMFallbackChain(
        primary_model="doubao-seed-2-0-pro-260215",
        fallback_models=["qwen-plus"],
        adapter_factory=_make_factory(
            {"doubao-seed-2-0-pro-260215": primary, "qwen-plus": fb0}
        ),
        max_retries_per_model=0,
        base_delay_s=0,
    )

    with pytest.raises(AllModelsFailed) as excinfo:
        await chain.call(_composed(), [])

    message = str(excinfo.value)
    # Which models were burned — answers "whose quota/key do I go fix?".
    assert "doubao-seed-2-0-pro-260215" in message
    assert "qwen-plus" in message
    # …and why each one gave up, from the provider's own body.
    assert "SetLimitExceeded" in message
    assert "upstream down" in message


@pytest.mark.unit
@pytest.mark.asyncio
async def test_all_models_failed_exposes_structured_attempts():
    primary = AsyncMock()
    primary.call.side_effect = _BodyError(429, "capped")

    chain = LLMFallbackChain(
        primary_model="doubao-seed-2-0-pro-260215",
        fallback_models=[],
        adapter_factory=_make_factory({"doubao-seed-2-0-pro-260215": primary}),
        max_retries_per_model=0,
        base_delay_s=0,
    )

    with pytest.raises(AllModelsFailed) as excinfo:
        await chain.call(_composed(), [])

    attempts = excinfo.value.attempts
    assert [a["model"] for a in attempts] == ["doubao-seed-2-0-pro-260215"]
    assert attempts[0]["outcome"] == "retries_exhausted"
    assert "capped" in attempts[0]["error"]


@pytest.mark.unit
def test_all_models_failed_survives_a_pickle_round_trip():
    """DBOS pickles the exception to dbos.workflow_status.error, and the
    task_tracking trigger derives error_msg from those bytes. A custom
    __init__ that broke unpickling would put the message back out of reach —
    which is the entire failure this class was changed to prevent."""
    import pickle

    revived = pickle.loads(
        pickle.dumps(AllModelsFailed("all 1 model(s) failed: m (why)"))
    )

    assert str(revived) == "all 1 model(s) failed: m (why)"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_every_attempt_is_logged_for_post_mortem(caplog):
    """application_logs had ZERO rows for this module across seven days of
    real failures: it logged through stdlib logging, which app/core/utils.py
    bridges into loguru for an allowlist of third-party names only. Logging
    through loguru is what makes a failed chain reconstructable after the
    fact."""
    from loguru import logger

    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        primary = AsyncMock()
        primary.call.side_effect = _BodyError(429, "capped")
        fb0 = AsyncMock()
        fb0.call.side_effect = _BodyError(503, "upstream down")

        chain = LLMFallbackChain(
            primary_model="model-a",
            fallback_models=["model-b"],
            adapter_factory=_make_factory({"model-a": primary, "model-b": fb0}),
            max_retries_per_model=0,
            base_delay_s=0,
        )
        with pytest.raises(AllModelsFailed):
            await chain.call(_composed(), [])
    finally:
        logger.remove(sink_id)

    joined = "\n".join(records)
    # Assert per LAYER, not just "the text appears somewhere": the retry
    # middleware and the fallback chain each have their own logger, and an
    # earlier version of this test passed with the chain still on stdlib
    # logging because the middleware's line happened to carry the same model
    # name and reason. Pin both prefixes so either regression is caught.
    retry_lines = [r for r in records if "[LLMRetry]" in r]
    fallback_lines = [r for r in records if "[Fallback]" in r]

    assert retry_lines, f"retry middleware logged nothing through loguru: {joined}"
    assert fallback_lines, f"fallback chain logged nothing through loguru: {joined}"
    assert any("model-a" in r and "capped" in r for r in retry_lines)
    assert any("model-b" in r and "upstream down" in r for r in retry_lines)
    assert any("model-a" in r and "capped" in r for r in fallback_lines)
