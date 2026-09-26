"""A1 — long-session continuation benchmark (framework hardening batch 5, T2).

Production has never compacted (0 ``compaction_*`` events all time), so no
organic traffic exercises what happens to a conversation AFTER the compactor
fires. This benchmark forces it: a conversation seeded with ``N_SEED`` rows in
the drift database, a model window monkeypatched down to ``WINDOW`` tokens,
then ``K_TURNS`` live turns through the real stack:

* the turn-history load and message assembly (the seam the chat service uses);
* a real ``AgentRunner.stream_turn`` over an adapter with NO ``stream``
  attribute — the buffered branch, production's only path (CLAUDE.md);
* a real ``RunRecorder`` writing ``agent_runs`` + transcript events;
* the real preflight + ``ContextCompactor`` (warm-prefix summary path);
* the real ``ConversationsAiStore`` appending each turn's user/assistant rows.

What it asserts (recon-a §4):

* **A1** the model-visible list is prefix-stable across turns: a turn's list
  starts with the previous turn's list unless that turn produced a fresh
  summary, and at most ``MAX_FRESH_SUMMARIES`` transitions may break it;
* **A2** after the first compaction every turn opens with exactly one summary
  frame, at index 0;
* **A3** after the first compaction every turn fits under orange;
* **A4** at most ``MAX_FRESH_SUMMARIES`` fresh summaries over ``K_TURNS``;
* **A5** a fork of the last run at its ``turn_end`` rebuilds exactly what the
  model saw in that run (minus the within-turn tool round) — I4;
* **A6** ``conversation_memory`` holds one row whose watermark never moved
  backwards and whose text is the last fresh summary.

The per-turn table (ms / fresh summaries / summary calls / tokens) is printed
and never fails; only the median-ms ceiling (``CEILING_FACTOR`` × the baked
baseline) does.

Skips cleanly without ``INTEGRATION_DATABASE_URL``; the schema-drift workflow
runs it through ``pytest-no-full-skip.sh`` against the ephemeral database.
"""

from __future__ import annotations

import json
import os
import random
import statistics
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.benchmark, pytest.mark.integration]

_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
pytest.importorskip("asyncpg")
_skip = pytest.mark.skipif(
    not _DSN,
    reason="INTEGRATION_DATABASE_URL not set — the long-session bench needs a DB.",
)

# Unknown to the tokenizer on purpose: the char heuristic is deterministic and
# fast, and the window is patched anyway.
MODEL = "bench-long-session"
WINDOW = 24_000
ORANGE_PCT = 0.80
N_SEED = 400
K_TURNS = 20
MAX_FRESH_SUMMARIES = 3
LIVE_USER_CHARS = 800
LIVE_ANSWER_CHARS = 4800
SUMMARY_CHARS = 1200
SYSTEM = "You are the long-session benchmark agent. Answer the user briefly."
# Median ms per turn measured on the development machine (Mac mini, drift DB on
# localhost) after A2. The ceiling is generous on purpose: this guards against
# an order-of-magnitude regression (a per-turn full-history re-read, a
# tokenizer pass per row), not against CI jitter.
BASELINE_MEDIAN_MS = 120.0
CEILING_FACTOR = 5


# ── fake adapter (no ``stream`` attribute: the buffered branch) ────────────


def _text(seed: int, n_chars: int) -> str:
    rng = random.Random(seed)
    words = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf")
    out: list[str] = []
    size = 0
    while size < n_chars:
        word = rng.choice(words)
        out.append(word)
        size += len(word) + 1
    return " ".join(out)[:n_chars].strip()


@dataclass
class _Call:
    kind: str  # "summary" | "turn"
    messages: list[dict[str, str]]


@dataclass
class _BenchAdapter:
    """A tool round then a text answer per turn; a canned summary whenever the
    request ends on the warm-prefix instruction. Records every request."""

    calls: list[_Call] = field(default_factory=list)
    summaries: int = 0
    answer_seed: int = 10_000

    async def call(self, composed: Any, messages: list[dict], *a: Any, **k: Any):
        from app.agent_framework.summarizer import WARM_PREFIX_INSTRUCTION

        last = messages[-1] if messages else {}
        if last.get("content") == WARM_PREFIX_INSTRUCTION:
            self.summaries += 1
            self.calls.append(_Call("summary", _plain(messages)))
            text = f"Summary {self.summaries}: " + _text(self.summaries, SUMMARY_CHARS)
            return _reply(content=text)
        self.calls.append(_Call("turn", _plain(messages)))
        if last.get("role") == "tool":
            self.answer_seed += 1
            return _reply(content=_text(self.answer_seed, LIVE_ANSWER_CHARS))
        call = {
            "id": f"call_{len(self.calls)}",
            "type": "function",
            "function": {"name": "Skill", "arguments": json.dumps({"skill": "probe"})},
        }
        return _reply(content="", tool_calls=[call])


def _reply(*, content: str, tool_calls: list | None = None) -> dict:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "choices": [
            {
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10},
    }


def _plain(messages: list[dict]) -> list[dict[str, str]]:
    """What the comparisons read: role + text content of every message."""
    return [
        {"role": str(m.get("role")), "content": str(m.get("content") or "")}
        for m in messages
    ]


class _SkillTool:
    recorder = None

    async def execute(self, args: dict) -> dict:
        return {"content": "probe ok"}


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
async def orm_dsn():
    from app.core.config import settings
    from app.db import engine as engine_mod
    from app.db import session as session_mod

    old = settings.SUPAVISOR_DATABASE_URL
    settings.SUPAVISOR_DATABASE_URL = _DSN
    await engine_mod.dispose_engine()
    session_mod.dispose_sessionmaker()
    try:
        yield _DSN
    finally:
        await engine_mod.dispose_engine()
        session_mod.dispose_sessionmaker()
        settings.SUPAVISOR_DATABASE_URL = old


@pytest.fixture
async def pg():
    import asyncpg

    conn = await asyncpg.connect(_DSN)
    try:
        yield conn
    finally:
        await conn.close()


@dataclass(frozen=True)
class _Ctx:
    store: Any
    sid: int
    user_id: str
    agent_id: str
    team_id: int


@pytest.fixture
async def bench_ctx(orm_dsn, pg):
    """A throwaway user / team / agent / direct_agent conversation seeded with
    ``N_SEED`` alternating rows; everything is deleted in a finally."""
    from app.services.ai.chat.conversations_ai_store import ConversationsAiStore

    user_id = uuid.uuid4()
    await pg.execute("INSERT INTO auth.users (id) VALUES ($1)", user_id)
    team_id = await pg.fetchval(
        "INSERT INTO teams (name, owner_id, invite_code) VALUES ($1, $2, $3) RETURNING id",
        "Long Session Bench Team",
        user_id,
        uuid.uuid4().hex[:10],
    )
    slug = f"bench-long-session-{uuid.uuid4().hex[:8]}"
    agent_id = await pg.fetchval(
        "INSERT INTO ai_agents (name, slug) VALUES ($1, $2) RETURNING id",
        "Long Session Bench Agent",
        slug,
    )
    store = ConversationsAiStore()
    session = await store.create_session(
        user_id=str(user_id),
        agent_slug=slug,
        agent_id=str(agent_id),
        title="Long Session Bench",
        project_id=None,
        team_id=team_id,
        context_type=None,
        context_id=None,
    )
    sid = int(session["id"])
    try:
        await _seed_rows(pg, sid, user_id)
        yield _Ctx(store, sid, str(user_id), str(agent_id), int(team_id))
    finally:
        await _cleanup(pg, sid, agent_id, team_id, user_id)


async def _seed_rows(pg: Any, sid: int, user_id: uuid.UUID) -> None:
    rows = []
    for seq in range(1, N_SEED + 1):
        rng = random.Random(seq)
        is_user = seq % 2 == 1
        size = rng.randint(160, 1600) if is_user else rng.randint(600, 4800)
        body = {"text": f"seed {seq}: " + _text(seq, size)}
        rows.append(
            (
                sid,
                seq,
                "user" if is_user else "agent",
                user_id if is_user else None,
                json.dumps(body),
            )
        )
    await pg.executemany(
        "INSERT INTO messages (conversation_id, seq, sender_type, sender_id, type, body) "
        "VALUES ($1, $2, $3, $4, 'text', $5::jsonb)",
        rows,
    )
    await pg.execute(
        "UPDATE conversations SET last_seq = $1 WHERE id = $2", N_SEED, sid
    )


async def _cleanup(pg: Any, sid: int, agent_id: Any, team_id: Any, user_id: Any):
    # The hourly usage rollup keys on (team, agent, model, …) with NULLS NOT
    # DISTINCT; deleting the team / agent first would null those columns and
    # collide with the previous run's row. The model name is the bench's own.
    await pg.execute("DELETE FROM ai_usage_hourly WHERE model = $1", MODEL)
    run_ids = [
        r["id"]
        for r in await pg.fetch(
            "SELECT id FROM agent_runs WHERE conversation_id = $1", sid
        )
    ]
    if run_ids:
        await pg.execute(
            "DELETE FROM agent_run_transcript_events WHERE run_id = ANY($1::bigint[])",
            run_ids,
        )
        await pg.execute("DELETE FROM agent_runs WHERE id = ANY($1::bigint[])", run_ids)
    await pg.execute("DELETE FROM messages WHERE conversation_id = $1", sid)
    await pg.execute("DELETE FROM conversations WHERE id = $1", sid)
    await pg.execute("DELETE FROM ai_agents WHERE id = $1", agent_id)
    await pg.execute("DELETE FROM teams WHERE id = $1", team_id)
    await pg.execute("DELETE FROM auth.users WHERE id = $1", user_id)


# ── the turn seam ───────────────────────────────────────────────────────────


async def _prepare_turn(ctx: _Ctx, runner: Any, user_text: str) -> list[dict]:
    """The chat service's history load + assembly, in its order: load history,
    persist the user message, build history messages, append the new user
    message, per-message cap. Master-equivalent path: newest-200 rows."""
    from app.agent_framework import cap_messages_tokens
    from app.services.ai.chat.history_image_replay import build_history_messages

    history = await ctx.store.get_messages(session_id=ctx.sid, newest=True)
    await ctx.store.append_user_message(
        session_id=ctx.sid, user_id=ctx.user_id, content=user_text
    )
    messages = await build_history_messages(
        history, user_id=ctx.user_id, supports_vision=False
    )
    messages.append({"role": "user", "content": user_text})
    return [o.message for o in cap_messages_tokens(messages)]


async def _after_turn(ctx: _Ctx, runner: Any, recorder: Any) -> None:
    """Post-turn bookkeeping inside the recorder block (master: none)."""
    return None


def _fork_seed(origin: list[dict], events: list[dict]) -> list[dict]:
    from app.services.ai.runner.replay import messages_from_events
    from app.services.issues.issue_fork import _seed

    return _seed(origin, messages_from_events(events))


# ── one turn ────────────────────────────────────────────────────────────────


@dataclass
class _TurnRecord:
    run_id: str
    ms: float
    seen: list[dict[str, str]]
    answer: str
    summary_calls: int
    fresh_summaries: int
    stored_summaries: int
    tokens: int
    watermark: int | None


def _composed(agent_id: str):
    from uuid import UUID

    from app.schemas.ai_library import ComposedSystemPrompt

    return ComposedSystemPrompt(
        agent_id=UUID(agent_id),
        agent_slug="bench",
        model=MODEL,
        temperature=0.0,
        max_tokens=512,
        system_message=SYSTEM,
        tools=[],
        skill_manifest=[],
        cache_fingerprint="bench",
    )


async def _run_one_turn(ctx: _Ctx, runner: Any, adapter: _BenchAdapter, t: int):
    from uuid import UUID

    from app.agent_framework.tokenizer import count_messages_tokens, count_tokens
    from app.services.ai.runner.run_recorder import RunRecorder

    user_text = f"turn {t}: " + _text(50_000 + t, LIVE_USER_CHARS)
    composed = _composed(ctx.agent_id)
    calls_before, summaries_before = len(adapter.calls), adapter.summaries
    started = time.perf_counter()
    user_messages = await _prepare_turn(ctx, runner, user_text)
    async with RunRecorder(
        agent_id=UUID(ctx.agent_id),
        user_id=UUID(ctx.user_id),
        trigger="chat",
        conversation_id=ctx.sid,
        team_id=ctx.team_id,
        model=MODEL,
        input_summary=user_text[:200],
    ) as recorder:
        chunks = [
            ch
            async for ch in runner.stream_turn(
                composed,
                user_messages=user_messages,
                recorder=recorder,
                auto_recorder=False,
            )
        ]
        answer = "".join(ch.delta_text or "" for ch in chunks)
        await _after_turn(ctx, runner, recorder)
        run_id = str(recorder.run_id)
    await ctx.store.append_assistant_message(
        session_id=ctx.sid,
        agent_id=ctx.agent_id,
        content=answer,
        prompt_tokens=0,
        completion_tokens=0,
        metadata={"run_id": run_id},
    )
    ms = (time.perf_counter() - started) * 1000
    turn_calls = [c for c in adapter.calls[calls_before:] if c.kind == "turn"]
    seen = turn_calls[0].messages
    tokens = count_tokens(SYSTEM, MODEL) + count_messages_tokens(seen, MODEL)
    fresh, stored = await _summary_events(run_id)
    return _TurnRecord(
        run_id=run_id,
        ms=ms,
        seen=seen,
        answer=answer,
        summary_calls=adapter.summaries - summaries_before,
        fresh_summaries=fresh,
        stored_summaries=stored,
        tokens=tokens,
        watermark=await _watermark(ctx),
    )


async def _events(run_id: str) -> list[dict]:
    from app.repositories.agent_runs_repository import get_agent_runs_repository
    from app.services.issues.issue_fork import REPLAY_EVENT_TYPES

    return await get_agent_runs_repository().list_transcript_events(
        int(run_id), event_types=list(REPLAY_EVENT_TYPES)
    )


async def _summary_events(run_id: str) -> tuple[int, int]:
    events = [
        e for e in await _events(run_id) if e["event_type"] == "compaction_summary"
    ]
    paths = [(e.get("payload") or {}).get("path") for e in events]
    fresh = sum(1 for p in paths if p in ("warm", "legacy"))
    stored = sum(1 for p in paths if p == "stored")
    return fresh, stored


async def _watermark(ctx: _Ctx) -> int | None:
    from app.repositories.conversation_memory_repository import (
        get_conversation_memory_repository,
    )

    row = await get_conversation_memory_repository().load(ctx.sid)
    return int(row["last_seq_summarized"]) if row else None


# ── assertions ──────────────────────────────────────────────────────────────


def _is_frame(message: dict) -> bool:
    from app.boundary.summary_frame import SUMMARY_PREFIX

    return message.get("role") == "system" and str(
        message.get("content", "")
    ).startswith(SUMMARY_PREFIX)


def _check_prefix_stability(turns: list[_TurnRecord]) -> list[str]:
    """A1: a turn without a fresh summary sees the previous turn's list as its
    prefix; the number of broken transitions is capped."""
    broken = []
    for prev, cur in zip(turns, turns[1:]):
        if cur.seen[: len(prev.seen)] != prev.seen:
            broken.append((turns.index(cur), cur.fresh_summaries))
    errors = []
    unexplained = [i for i, fresh in broken if not fresh]
    if unexplained:
        errors.append(
            f"A1 prefix changed on turns {unexplained} without a fresh summary"
        )
    if len(broken) > MAX_FRESH_SUMMARIES:
        errors.append(
            f"A1 prefix broke on {len(broken)} of {len(turns) - 1} transitions "
            f"(cap {MAX_FRESH_SUMMARIES})"
        )
    return errors


def _check_frames_and_budget(turns: list[_TurnRecord]) -> list[str]:
    """A2 + A3 on every turn from the first compaction on."""
    first = next((i for i, t in enumerate(turns) if t.fresh_summaries), None)
    if first is None:
        return ["A2 no turn ever produced a fresh summary — the bench never compacted"]
    errors = []
    for i, turn in enumerate(turns[first:], start=first):
        frames = [j for j, m in enumerate(turn.seen) if _is_frame(m)]
        if frames != [0]:
            errors.append(f"A2 turn {i}: summary frames at {frames}, want [0]")
        if turn.tokens >= ORANGE_PCT * WINDOW:
            errors.append(f"A3 turn {i}: {turn.tokens} tokens ≥ orange")
    return errors


def _check_summary_count(turns: list[_TurnRecord]) -> list[str]:
    """A4: fresh summaries (adapter summary calls) stay capped."""
    calls = sum(t.summary_calls for t in turns)
    if calls > MAX_FRESH_SUMMARIES:
        return [
            f"A4 {calls} summary calls over {len(turns)} turns (cap {MAX_FRESH_SUMMARIES})"
        ]
    return []


async def _check_fork(ctx: _Ctx, pg: Any, last: _TurnRecord) -> list[str]:
    """A5 (I4): fork the last run at its turn_end → what the model saw + the
    final answer."""
    from app.services.issues.issue_fork import _RealDeps

    events = await _events(last.run_id)
    started_at = await pg.fetchval(
        "SELECT started_at FROM agent_runs WHERE id = $1", int(last.run_id)
    )
    origin = await _RealDeps().list_origin_messages(ctx.sid, started_at)
    seed = _fork_seed(origin, events)
    want = last.seen + [{"role": "assistant", "content": last.answer.strip()}]
    if seed == want:
        return []
    return [
        f"A5 fork seed differs from the model view: {len(seed)} vs {len(want)} "
        f"messages; first mismatch at {_first_mismatch(seed, want)}"
    ]


def _first_mismatch(a: list, b: list) -> int:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


async def _check_memory(ctx: _Ctx, pg: Any, turns: list[_TurnRecord]) -> list[str]:
    """A6: one sidecar row, monotonic watermark, text = last fresh summary."""
    rows = await pg.fetch(
        "SELECT summary_md, last_seq_summarized FROM conversation_memory "
        "WHERE conversation_id = $1",
        ctx.sid,
    )
    if len(rows) != 1:
        return [f"A6 conversation_memory has {len(rows)} rows, want 1"]
    marks = [t.watermark for t in turns if t.watermark is not None]
    errors = []
    if marks != sorted(marks):
        errors.append(f"A6 watermark moved backwards: {marks}")
    last_fresh = await _last_fresh_summary(turns)
    if rows[0]["summary_md"] != last_fresh:
        errors.append("A6 stored summary is not the last fresh compaction_summary")
    return errors


async def _last_fresh_summary(turns: list[_TurnRecord]) -> str | None:
    for turn in reversed(turns):
        for ev in reversed(await _events(turn.run_id)):
            payload = ev.get("payload") or {}
            if ev["event_type"] == "compaction_summary" and payload.get("path") in (
                "warm",
                "legacy",
            ):
                return payload.get("summary")
    return None


def _report(turns: list[_TurnRecord]) -> str:
    lines = [
        "turn |     ms | fresh | stored | summary calls | tokens | watermark",
        "-----+--------+-------+--------+---------------+--------+----------",
    ]
    for i, t in enumerate(turns):
        lines.append(
            f"{i:4d} | {t.ms:6.0f} | {t.fresh_summaries:5d} | {t.stored_summaries:6d} "
            f"| {t.summary_calls:13d} | {t.tokens:6d} | {t.watermark}"
        )
    median = statistics.median(t.ms for t in turns)
    total_calls = sum(t.summary_calls for t in turns)
    lines.append(f"median ms {median:.0f}; summary calls {total_calls}")
    return "\n".join(lines)


# ── the benchmark ───────────────────────────────────────────────────────────


@_skip
async def test_long_session_continuation(bench_ctx: _Ctx, pg, record_property):
    from app.services.ai.runner.agent_runner import AgentRunner
    from app.services.ai.runner.step_hooks import StepHookChain

    adapter = _BenchAdapter()
    runner = AgentRunner(
        adapter=adapter, skill_tool=_SkillTool(), step_hooks=StepHookChain([])
    )
    window = lambda model: (WINDOW, "builtin")  # noqa: E731
    turns: list[_TurnRecord] = []
    with (
        patch("app.agent_framework.context_compactor.resolve_model_window", window),
        patch("app.agent_framework.context_window.resolve_model_window", window),
    ):
        for t in range(K_TURNS):
            turns.append(await _run_one_turn(bench_ctx, runner, adapter, t))

    report = _report(turns)
    print("\n" + report)
    record_property("long_session_report", report)

    errors = (
        _check_prefix_stability(turns)
        + _check_frames_and_budget(turns)
        + _check_summary_count(turns)
        + await _check_fork(bench_ctx, pg, turns[-1])
        + await _check_memory(bench_ctx, pg, turns)
    )
    median = statistics.median(t.ms for t in turns)
    if median > CEILING_FACTOR * BASELINE_MEDIAN_MS:
        errors.append(
            f"ceiling: median {median:.0f} ms > {CEILING_FACTOR}× baseline "
            f"{BASELINE_MEDIAN_MS:.0f} ms"
        )
    assert not errors, "\n".join(errors) + "\n\n" + report
