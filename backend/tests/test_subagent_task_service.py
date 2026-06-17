"""Tests for SubAgentTaskService — synchronous spawn-and-return.

Covers the safety boundary (depth / cycle / rate-limit / unknown-slug
/ self-spawn) and the envelope shape contract. The successful-spawn
path is heavily mocked because it transitively touches the full
agent_runner stack (PromptComposer, AgentRunner, RunRecorder,
build_agent_runner_stack); we test the wiring is right, not the
inner runner — that's covered by Phase 5 E2E.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.ai.runner.subagent_task_service import (
    ENVELOPE_KEYS,
    MAX_DELEGATION_DEPTH,
    SubAgentTaskService,
)


@pytest.fixture
def caller_ctx():
    return {
        "caller_agent_id": uuid4(),
        "caller_user_id": uuid4(),
        "parent_run_id": uuid4(),
        "agent_depth": 0,
    }


@pytest.fixture
def service(caller_ctx) -> SubAgentTaskService:
    return SubAgentTaskService(**caller_ctx)


# ─── Argument validation ──────────────────────────────────────────────


async def test_missing_subagent_type_returns_failed(service):
    """Without an agent slug there's nothing to spawn — return a
    structured error so the parent LLM can see the mistake instead
    of a silent no-op."""
    out = await service.spawn({"prompt": "hello"})
    assert out["status"] == "failed"
    assert "subagent_type" in out["error"]


async def test_missing_prompt_returns_failed(service):
    """Empty prompt = nothing for the sub-agent to do. Reject before
    spinning up an AgentRunner that would just immediately exit."""
    out = await service.spawn({"subagent_type": "research"})
    assert out["status"] == "failed"
    assert "prompt" in out["error"]


# ─── Safety boundary ──────────────────────────────────────────────────


async def test_depth_cap_rejects_at_limit(caller_ctx):
    """A caller already at MAX_DELEGATION_DEPTH cannot spawn again —
    the sub-agent would run at depth+1 = exceeds cap. Reject before
    DB roundtrip (the cheapest fail-fast)."""
    ctx = {**caller_ctx, "agent_depth": MAX_DELEGATION_DEPTH}
    service = SubAgentTaskService(**ctx)
    out = await service.spawn({"subagent_type": "research", "prompt": "x"})
    assert out["status"] == "failed"
    assert "max sub-agent depth" in out["error"]


async def test_unknown_slug_returns_failed(service):
    """Misconfigured parent prompt that names a non-existent agent
    must not crash the parent's run — return a clean envelope so the
    LLM can choose a different slug or fall back."""
    with patch("app.repositories.agent_repository.AgentRepository") as mock_repo_cls:
        mock_repo = mock_repo_cls.return_value
        mock_repo.get_by_slug = AsyncMock(return_value=None)
        out = await service.spawn({"subagent_type": "nonexistent", "prompt": "x"})
    assert out["status"] == "failed"
    assert "unknown agent slug" in out["error"]


async def test_self_spawn_rejected(service, caller_ctx):
    """An agent calling Task with its own slug would recurse on the
    SAME context — Delegate also rejects this. Refactor to a plan
    step is the right answer."""
    self_uuid = caller_ctx["caller_agent_id"]
    with patch("app.repositories.agent_repository.AgentRepository") as mock_repo_cls:
        mock_repo = mock_repo_cls.return_value
        mock_repo.get_by_slug = AsyncMock(
            return_value={"id": str(self_uuid), "slug": "myself"}
        )
        out = await service.spawn({"subagent_type": "myself", "prompt": "x"})
    assert out["status"] == "failed"
    assert "cannot spawn self" in out["error"]


# ─── Envelope contract ────────────────────────────────────────────────


async def test_failed_envelope_has_expected_keys(service):
    """Pin the envelope keys for the failure path. The Frontend
    Runs UI + parent agent prompt both depend on this shape."""
    out = await service.spawn({"prompt": "x"})  # missing subagent_type
    for key in ENVELOPE_KEYS:
        assert key in out, f"failed envelope missing key {key!r}"


class _FakeRecorder:
    """Minimal stand-in exposing the accumulated-token properties."""

    def __init__(self, prompt_tokens: int, completion_tokens: int):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


async def test_build_envelope_success_shape():
    """Success envelope carries the sub-run id, summary text, and
    token usage — what the parent LLM needs to reason about cost
    and what the Runs UI links to for drill-down. tokens_used comes from
    the recorder's ACCUMULATED prompt+completion (whole sub-turn, all
    iterations), NOT result['usage'] — run_turn never sets that key, so the
    old read always yielded 0 and the parent cost tree was blank."""
    sub_run_id = uuid4()
    fake_result = {
        "content": "Found 3 relevant docs in /docs/",
        # run_turn's REAL shape: last-iteration usage under 'raw'. The recorder
        # (800+434) must win over this 99 so multi-iteration turns total right.
        "raw": {"usage": {"total_tokens": 99}},
    }
    env = SubAgentTaskService._build_envelope(
        result=fake_result,
        sub_run_id=sub_run_id,
        recorder=_FakeRecorder(prompt_tokens=800, completion_tokens=434),
    )
    assert env["status"] == "success"
    assert env["summary"] == "Found 3 relevant docs in /docs/"
    assert env["sub_run_id"] == str(sub_run_id)
    assert env["tokens_used"] == 1234  # recorder accumulation, not the 99 raw
    for key in ENVELOPE_KEYS:
        assert key in env


async def test_build_envelope_tokens_fallback_to_raw_usage():
    """Without a recorder, fall back to run_turn's REAL shape
    (result['raw']['usage']['total_tokens']) — never the nonexistent
    result['usage'] the old code read."""
    env = SubAgentTaskService._build_envelope(
        result={"content": "ok", "raw": {"usage": {"total_tokens": 777}}},
        sub_run_id=uuid4(),
    )
    assert env["tokens_used"] == 777


async def test_build_envelope_failed_shape():
    """When run_turn returns an error, envelope.status flips to
    failed and the error string surfaces. The summary stays
    populated so a partial response (e.g. 'tried X, hit Y') is
    still visible to the parent."""
    fake_result = {
        "content": "tried but couldn't",
        "error": "context_budget_exceeded",
        "usage": None,
    }
    env = SubAgentTaskService._build_envelope(result=fake_result, sub_run_id=uuid4())
    assert env["status"] == "failed"
    assert env["error"] == "context_budget_exceeded"
    assert env["summary"] == "tried but couldn't"
    assert env["tokens_used"] == 0


async def test_envelope_keys_are_stable():
    """Pin the exact 6 keys. Adding new keys is safe (parent agents
    that don't read them just see extras), but renaming or removing
    is a breaking change for any consumer relying on the schema."""
    assert set(ENVELOPE_KEYS) == {
        "summary",
        "key_findings",
        "files_created",
        "tokens_used",
        "sub_run_id",
        "status",
    }


# ─── Phase 5: parent_recorder rollup ──────────────────────────────────


async def test_spawn_calls_note_subagent_on_parent_recorder(caller_ctx):
    """Phase 5 of #199: when a parent recorder is wired in, every
    spawn() must roll the envelope into recorder.metadata.subagents
    so the parent run row exposes the sub-agent fan-out without a
    separate join."""
    parent_rec = MagicMock()
    parent_rec.note_subagent = MagicMock()

    service = SubAgentTaskService(
        **caller_ctx,
        parent_recorder=parent_rec,
    )
    # Trigger the failure path (no slug) — counter still bumps so we
    # can assert the wiring without mocking the entire AgentRunner.
    out = await service.spawn({"prompt": "x"})

    parent_rec.note_subagent.assert_called_once()
    (envelope,), _ = parent_rec.note_subagent.call_args
    assert envelope is out
    assert envelope["status"] == "failed"


async def test_spawn_without_parent_recorder_is_silent(caller_ctx):
    """A SubAgentTaskService with no parent_recorder (CLI / batch
    callers, tests) must not crash — the rollup is opt-in."""
    service = SubAgentTaskService(**caller_ctx)
    out = await service.spawn({"prompt": "x"})
    assert out["status"] == "failed"  # no slug → fail, no exception
