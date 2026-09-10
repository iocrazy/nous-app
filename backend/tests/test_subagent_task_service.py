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


# ─── Phase 2b-2: sub-agent events on the parent transcript ────────────


class _EventRecorder:
    """Parent recorder stand-in that captures what lands on its transcript."""

    run_id = 900
    issue_id = None

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def record_event(self, event_type, payload, **kw):
        self.events.append((event_type, payload))


def _wire_sync_spawn(monkeypatch, *, content="found it"):
    """Stand in for everything ``_spawn`` lazily imports, so the wiring under
    test is the event emission, not the runner stack."""
    from types import SimpleNamespace
    from uuid import uuid4 as _uuid4

    import app.repositories.agent_repository as agent_repo_mod
    import app.repositories.skill_repository as skill_repo_mod
    import app.services.ai.adapters.factory as factory_mod
    import app.services.ai.chat.ai_library_chat_wiring as wiring_mod
    import app.services.ai.prompts.prompt_composer as composer_mod
    import app.services.ai.runner.run_recorder as recorder_mod
    import app.services.ai.scope.scope_binding as scope_mod
    import app.services.workforce.agent_worker as worker_mod

    target_id = str(_uuid4())
    monkeypatch.setattr(
        agent_repo_mod,
        "get_agent_repository",
        lambda: SimpleNamespace(
            get_by_slug=AsyncMock(return_value={"id": target_id, "slug": "librarian"})
        ),
    )
    monkeypatch.setattr(skill_repo_mod, "get_skill_repository", lambda: MagicMock())
    monkeypatch.setattr(factory_mod, "provider_key_for_model", lambda m: "qwen")

    run_turn = AsyncMock(return_value={"content": content})
    monkeypatch.setattr(
        wiring_mod,
        "build_agent_runner_stack",
        AsyncMock(
            return_value=SimpleNamespace(runner=SimpleNamespace(run_turn=run_turn))
        ),
    )

    composer = MagicMock()
    composer.compose = AsyncMock(
        return_value=SimpleNamespace(model="qwen-max", agent_id=target_id)
    )
    monkeypatch.setattr(composer_mod, "PromptComposer", lambda *a, **kw: composer)
    monkeypatch.setattr(composer_mod, "ComposerInput", lambda **kw: kw)
    monkeypatch.setattr(
        scope_mod,
        "resolve_dispatch_scope",
        AsyncMock(return_value=SimpleNamespace(as_recorder_kwargs=lambda: {})),
    )
    monkeypatch.setattr(worker_mod, "_attach_to_parent_run", AsyncMock())

    made: list = []

    class _FakeRunRecorder:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            made.append(self)
            self.run_id = 51
            self.prompt_tokens = 10
            self.completion_tokens = 5

        def compute_cost_cents(self):
            return 3.0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(recorder_mod, "RunRecorder", _FakeRunRecorder)
    return SimpleNamespace(run_turn=run_turn, recorders=made)


async def test_sync_spawn_brackets_the_child_with_spawned_then_done(
    monkeypatch, caller_ctx
):
    """A trajectory must show a sub-agent starting and finishing, in that
    order, whether it ran here or in the background."""
    wired = _wire_sync_spawn(monkeypatch)
    rec = _EventRecorder()
    service = SubAgentTaskService(**caller_ctx, parent_recorder=rec)

    out = await service.spawn({"subagent_type": "librarian", "prompt": "dig"})

    assert out["status"] == "success"
    assert wired.recorders[0].kwargs["fork_of_run_id"] is None
    assert [t for t, _ in rec.events] == ["subagent_spawned", "subagent_done"]
    spawned, done = rec.events[0][1], rec.events[1][1]
    assert spawned["mode"] == "sync" and spawned["child_run_id"] == "51"
    assert spawned["task_id"] is None and spawned["continued_from"] is None
    assert done["mode"] == "sync" and done["child_run_id"] == "51"
    assert done["status"] == "success" and done["cost_cents"] == 3.0
    assert done["tokens_used"] == 15 and done["duration_ms"] >= 0


async def test_continue_records_the_fork_columns_and_round(monkeypatch, caller_ctx):
    """Round 2 of a child hangs off round 1 in the Runs tree, and the history
    it runs on is the child's own — not the parent's."""
    wired = _wire_sync_spawn(monkeypatch)
    rec = _EventRecorder()
    service = SubAgentTaskService(**caller_ctx, parent_recorder=rec)

    async def _ok(v):
        return v

    monkeypatch.setattr(service, "_child_chain_ok", lambda cid: _ok(True))
    monkeypatch.setattr(
        service,
        "_load_child_events",
        lambda rid: _ok(
            (
                [
                    {
                        "seq": 1,
                        "event_type": "assistant",
                        "payload": {"content": "before"},
                    }
                ],
                1,
            )
        ),
    )
    monkeypatch.setattr(service, "_load_child_metadata", lambda rid: _ok({"round": 1}))

    out = await service.spawn(
        {"subagent_type": "librarian", "prompt": "keep going", "child_run_id": "42"}
    )

    assert out["status"] == "success"
    assert wired.run_turn.await_args.kwargs["user_messages"] == [
        {"role": "assistant", "content": "before"},
        {"role": "user", "content": "keep going"},
    ]
    assert rec.events[0][1]["continued_from"] == "42"
    kwargs = wired.recorders[0].kwargs
    assert kwargs["fork_of_run_id"] == 42 and kwargs["fork_at_seq"] == 1
    assert kwargs["metadata"]["continued_from"] == "42"
    assert kwargs["metadata"]["round"] == 2


async def test_a_crashing_child_still_reports_done(monkeypatch, caller_ctx):
    """Review C2. ``subagent_spawned`` already told the view a child is
    running; without a matching done on the crash path the parent's Cockpit
    shows "1 running" until the run ends — and a provider error is the most
    common way a child ends."""
    wired = _wire_sync_spawn(monkeypatch)
    wired.run_turn.side_effect = RuntimeError("provider exploded")
    rec = _EventRecorder()
    service = SubAgentTaskService(**caller_ctx, parent_recorder=rec)

    out = await service.spawn({"subagent_type": "librarian", "prompt": "dig"})

    assert out["status"] == "failed" and "provider exploded" in out["error"]
    assert [t for t, _ in rec.events] == ["subagent_spawned", "subagent_done"]
    done = rec.events[1][1]
    assert done["status"] == "failed" and done["mode"] == "sync"
    assert done["child_run_id"] == "51"


async def test_a_child_that_never_started_reports_no_done(monkeypatch, caller_ctx):
    """Negative control: a spawn refused BEFORE the recorder opened never
    emitted spawned either, so a done would invent a child."""
    rec = _EventRecorder()
    service = SubAgentTaskService(**caller_ctx, parent_recorder=rec)
    out = await service.spawn({"prompt": "no slug"})
    assert out["status"] == "failed"
    assert rec.events == []


async def test_fan_out_propagates_await_false_to_every_entry(monkeypatch, caller_ctx):
    """Review I1 / spec §2.1: ``tasks[]`` and ``await=false`` compose. Silently
    running five sub-agents synchronously when the model asked for five
    background ones is the worst of the three possible behaviours."""
    from types import SimpleNamespace as _NS

    created: list[dict] = []

    class _Repo:
        @staticmethod
        async def create_task(*, agent_id, user_id, payload, title=None, **kw):
            created.append(payload)
            return {"id": f"task-{len(created)}"}

    import app.repositories.agent_workforce_repository as wf_mod

    monkeypatch.setattr(wf_mod, "get_agent_workforce_repository", lambda: _Repo())

    rec = _EventRecorder()
    rec.issue_id = 7
    service = SubAgentTaskService(**caller_ctx, parent_recorder=rec)

    async def _ok(v):
        return v

    monkeypatch.setattr(service, "_resolve_agent_id", lambda slug: _ok(uuid4()))

    out = await service.spawn(
        {
            "await": False,
            "tasks": [
                {"subagent_type": "a", "prompt": "one"},
                {"subagent_type": "b", "prompt": "two"},
            ],
        }
    )

    assert out["status"] == "success" and out["tasks_run"] == 2
    assert [r["status"] for r in out["results"]] == ["queued", "queued"]
    assert [p["subagent_type"] for p in created] == ["a", "b"]
    assert all(p["kind"] == "subagent" for p in created)
    _ = _NS


async def test_fan_out_entry_cannot_carry_child_run_id(monkeypatch, caller_ctx):
    """The top-level guard rejects ``tasks`` + ``child_run_id``; an entry that
    smuggles the same key in must be refused for the same reason, not run."""
    rec = _EventRecorder()
    service = SubAgentTaskService(**caller_ctx, parent_recorder=rec)
    out = await service.spawn(
        {"tasks": [{"subagent_type": "a", "prompt": "x", "child_run_id": "51"}]}
    )
    assert out["status"] == "failed"
    assert out["results"][0]["error"] == "continue_not_allowed_in_fanout"


async def test_background_payload_carries_the_issue_id(monkeypatch, caller_ctx):
    """Review I2 / spec §2.5: a sub-run of an issue run IS part of that issue's
    tree. Without this the background child's agent_runs row has no issue link
    and its spend is invisible to the issue rollup."""
    created: dict = {}

    class _Repo:
        @staticmethod
        async def create_task(*, agent_id, user_id, payload, title=None, **kw):
            created.update(payload)
            return {"id": "task-1"}

    import app.repositories.agent_workforce_repository as wf_mod

    monkeypatch.setattr(wf_mod, "get_agent_workforce_repository", lambda: _Repo())

    rec = _EventRecorder()
    rec.issue_id = 7
    service = SubAgentTaskService(**caller_ctx, parent_recorder=rec, issue_id=7)

    async def _ok(v):
        return v

    monkeypatch.setattr(service, "_resolve_agent_id", lambda slug: _ok(uuid4()))
    out = await service.spawn(
        {"subagent_type": "librarian", "prompt": "x", "await": False}
    )
    assert out["status"] == "queued"
    assert created["issue_id"] == 7


async def test_a_non_numeric_reply_target_is_typed_not_raised(monkeypatch, caller_ctx):
    """Every other refusal returns an envelope; a ValueError escaping into the
    tool dispatch loop would be the one exception."""

    class _BadRec:
        run_id = 900
        issue_id = "not-a-number"

        async def record_event(self, *a, **kw):
            return None

    service = SubAgentTaskService(**caller_ctx, parent_recorder=_BadRec())
    out = await service.spawn(
        {"subagent_type": "librarian", "prompt": "x", "await": False}
    )
    assert out["status"] == "failed" and out["error"] == "no_reply_target"
