# Conversations Phase 1.5 — Group Agent Memory + Parallel Summons + agent_runs Link

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give group-chat agents memory (rolling conversation summary + agent_memory recall) so they stop being amnesiac beyond the 20-message window, run multi-agent summons in parallel, and give `agent_runs` a structural `conversation_id` column — all flag-dark.

**Architecture:** A `conversation_memory` sidecar row (one per conversation) holds a rolling head-summary maintained by a cheap LLM after agent turns; `run_conversation_agent_turn` injects "summary + recalled memories" into the composer's request instructions (AFTER the untrusted-channel guard, so the guard covers it). `dispatch_summons` switches from a serial loop to `asyncio.gather` under the existing per-conversation semaphore (cap 2), then fires a never-raise compaction. `agent_runs.conversation_id` becomes a real column (was metadata-json only) so run↔conversation queries stop JSON-scanning — this also pre-builds Phase 2's sidecar link.

**Tech Stack:** FastAPI + SQLAlchemy `db_engine` (asyncpg) + Postgres/Supabase; QwenAdapter (`qwen-turbo`) for the cheap summarizer (same precedent as `_harvest_summarizer` in `ai_library_chat_service.py:1083`); existing `agent_memory.recall()` for fact recall.

## Global Constraints

- **Branch:** `feature/conversations-p1.5` off latest `origin/master`. Commit this plan file as the first commit.
- **Flags dark:** new backend flag `FEATURE_GROUP_AGENT_MEMORY: bool = Field(default=False)`. All new read/write memory behavior is inert when off. (The whole path additionally only runs when `FEATURE_CONVERSATIONS` is on, because `dispatch_summons` lives on the conversations path — no extra gating needed for that.)
- **Privacy decision (user, 2026-07-02):** group recall reuses the 1:1 recall shape — **summoner-owned OR team-shared** memories (`_RECALL_SQL` predicate as-is). Rationale: the summoner @-mentioned the agent; that is treated as consent for their own memories to inform a group-visible reply. Document this in the service docstring verbatim.
- **Summon path never raises.** Every new call inside `dispatch_summons` / `run_conversation_agent_turn` must swallow-log (`logger.error`/`warning` with loguru **f-strings, never `%s`**). A memory/compaction failure must never break a send or a turn.
- **Migration numbering:** next free is **330** (master has ≤329 — verify with `ls supabase/migrations | sort` before writing; renumber if a parallel PR took 330/331).
- **PostgREST reload:** migration 331 adds a column to `agent_runs`, which is written via the supabase client (PostgREST) → the migration MUST end with `NOTIFY pgrst, 'reload schema';` (known trap: CI migration skips PostgREST reload).
- **asyncpg DoD:** every BIGINT bind coerced with `int(...)`/`_bigint(...)`; no NULL binds in comparisons without `CAST`. Real-DB smoke for each DB task (mocked tests miss asyncpg types). Local DB: `PGPASSWORD=postgres psql -h 127.0.0.1 -p 54322 -U postgres -d postgres` (conversations tables 327-329 already applied locally).
- **Lint:** `cd backend && uv run black <files> && uv run isort <files> && uv run flake8 <files>` on every touched `.py` before each commit.
- **No UI changes in this plan.** Backend-only.

## File Structure

- `supabase/migrations/330_conversation_memory.sql` — sidecar table (service-role-only RLS).
- `supabase/migrations/331_agent_runs_conversation_id.sql` — column + FK + partial index + pgrst reload.
- `backend/app/repositories/conversation_memory_repository.py` — NEW: `load` / `upsert`.
- `backend/app/repositories/conversation_repository.py` — MODIFY: add `messages_in_range`.
- `backend/app/services/chat/conversation_memory_service.py` — NEW: `build_memory_block` / `maybe_compact` / `_summarize`.
- `backend/app/services/chat/conversation_agent_turn.py` — MODIFY: inject memory block into `ComposerInput.request_instructions`; pass `conversation_id` to `RunRecorder`.
- `backend/app/services/conversation_service.py` — MODIFY: `dispatch_summons` parallel gather + post-turn compaction.
- `backend/app/services/ai/runner/run_recorder.py` — MODIFY: `conversation_id` dataclass field + insert payload key.
- `backend/app/core/config.py` — MODIFY: add `FEATURE_GROUP_AGENT_MEMORY`.
- Tests: `backend/tests/test_conversation_memory_repository.py` (NEW), `backend/tests/test_conversation_memory_service.py` (NEW), `backend/tests/test_conversation_agent_turn.py` (extend), `backend/tests/test_conversation_service.py` (extend), `backend/tests/test_run_recorder_conversation_id.py` (NEW).

---

### Task 1: Migration 330 — conversation_memory sidecar

**Files:**
- Create: `supabase/migrations/330_conversation_memory.sql`

**Interfaces:**
- Consumes: `public.conversations` (mig 327).
- Produces: table `public.conversation_memory(conversation_id PK→conversations, summary_md, last_seq_summarized, model, updated_at)` consumed by Tasks 3/4.

- [ ] **Step 1: Verify the migration number is still free**

Run: `ls supabase/migrations/ | grep -E '^33[0-9]' || echo FREE`
Expected: `FREE` (if 330 is taken by a parallel PR, renumber this file and 331 accordingly and update all references in this plan's commits).

- [ ] **Step 2: Write the migration.** Exact content:

```sql
-- 330_conversation_memory.sql — Phase 1.5: rolling head-summary sidecar for
-- group agent turns (spec "conversation_memory", built early for group chat;
-- Phase 2 direct_agent reuses it). One row per conversation. Backend-only:
-- read/written via db_engine; RLS is service-role-only so PostgREST never
-- exposes it to anon/authenticated (same posture as generated_media, mig 307).

CREATE TABLE IF NOT EXISTS public.conversation_memory (
  conversation_id     BIGINT PRIMARY KEY
                      REFERENCES public.conversations(id) ON DELETE CASCADE,
  summary_md          TEXT        NOT NULL DEFAULT '',
  last_seq_summarized BIGINT      NOT NULL DEFAULT 0,
  model               TEXT,
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.conversation_memory ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS conversation_memory_service_all ON public.conversation_memory;
CREATE POLICY conversation_memory_service_all ON public.conversation_memory
  FOR ALL USING (
    current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role'
  );

COMMENT ON TABLE public.conversation_memory IS
  'Rolling compressed summary of a conversation''s older messages (head). '
  'Agents read summary + recent tail; compaction advances last_seq_summarized. '
  'Backend-only (service-role RLS).';
```

- [ ] **Step 3: Apply locally + verify**

Run: `PGPASSWORD=postgres psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -v ON_ERROR_STOP=1 -f supabase/migrations/330_conversation_memory.sql`
Expected: no error.
Verify: `PGPASSWORD=postgres psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -tAc "SELECT column_name FROM information_schema.columns WHERE table_name='conversation_memory' ORDER BY ordinal_position;"`
Expected: `conversation_id`, `summary_md`, `last_seq_summarized`, `model`, `updated_at`.

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/330_conversation_memory.sql
git commit -m "feat(chat): conversation_memory sidecar table (mig 330)"
```

---

### Task 2: Migration 331 + RunRecorder — structural agent_runs.conversation_id

**Files:**
- Create: `supabase/migrations/331_agent_runs_conversation_id.sql`
- Modify: `backend/app/services/ai/runner/run_recorder.py` (dataclass field ~line 91 area; `_insert_row` payload ~line 534)
- Modify: `backend/app/services/chat/conversation_agent_turn.py` (the `RunRecorder(...)` call, currently passing `metadata={"conversation_id": ...}`)
- Test: `backend/tests/test_run_recorder_conversation_id.py` (NEW)

**Interfaces:**
- Consumes: `RunRecorder` dataclass (fields `agent_id, user_id, trigger, session_id, team_id, project_id, model, provider, input_summary, metadata`); insert happens in `_insert_row` via `client.table("agent_runs").insert(payload)`.
- Produces: `RunRecorder(conversation_id: Optional[int] = None)` — later tasks/Phase 2 pass it; `agent_runs.conversation_id BIGINT NULL` column.

- [ ] **Step 1: Write the migration.** Exact content:

```sql
-- 331_agent_runs_conversation_id.sql — Phase 1.5: structural run→conversation
-- link (was metadata-json only). Nullable: non-chat triggers stay NULL.
-- Phase 2 (direct_agent) reuses this column per the unified-conversation spec.

ALTER TABLE public.agent_runs
  ADD COLUMN IF NOT EXISTS conversation_id BIGINT;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conname = 'agent_runs_conversation_id_fkey') THEN
    ALTER TABLE public.agent_runs
      ADD CONSTRAINT agent_runs_conversation_id_fkey
      FOREIGN KEY (conversation_id) REFERENCES public.conversations(id)
      ON DELETE SET NULL;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_agent_runs_conversation
  ON public.agent_runs (conversation_id) WHERE conversation_id IS NOT NULL;

-- agent_runs is written via PostgREST (supabase client) — reload the schema
-- cache so the new column is insertable immediately (known trap).
NOTIFY pgrst, 'reload schema';
```

- [ ] **Step 2: Apply locally + verify**

Run: `PGPASSWORD=postgres psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -v ON_ERROR_STOP=1 -f supabase/migrations/331_agent_runs_conversation_id.sql`
Expected: no error. (Local snapshot note: if `public.agent_runs` is missing locally, first apply `supabase/migrations/145_agent_runs.sql` — the Phase 1 session hit this snapshot drift before.)
Verify: `... -tAc "SELECT confrelid::regclass FROM pg_constraint WHERE conname='agent_runs_conversation_id_fkey';"` → `conversations`.

- [ ] **Step 3: Write the failing test**

```python
"""RunRecorder carries conversation_id into the agent_runs insert payload."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.ai.runner.run_recorder import RunRecorder


@pytest.mark.unit
@pytest.mark.asyncio
async def test_insert_payload_includes_conversation_id(monkeypatch):
    rec = RunRecorder(
        agent_id=uuid4(),
        user_id=uuid4(),
        trigger="chat_summon",
        conversation_id=123456789,
    )
    captured: dict = {}

    class _Table:
        def insert(self, payload):
            captured.update(payload)
            m = MagicMock()
            m.execute = AsyncMock(
                return_value=MagicMock(data=[{"id": "00000000-0000-0000-0000-000000000001"}])
            )
            return m

    class _Client:
        def table(self, name):
            assert name == "agent_runs"
            return _Table()

    # Patch whatever client accessor _insert_row uses — read run_recorder.py
    # first and patch the exact symbol (e.g. the module-level get_supabase/
    # service client factory). The assertion below is the contract.
    monkeypatch.setattr(
        "app.services.ai.runner.run_recorder.RunRecorder._snapshot_price",
        AsyncMock(),
    )
    # ... patch the client factory used inside _insert_row (exact name per file read)
    await rec._insert_row()  # noqa: SLF001 — unit-testing the payload shape
    assert captured.get("conversation_id") == 123456789


@pytest.mark.unit
def test_conversation_id_defaults_none():
    rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
    assert rec.conversation_id is None
```

(The implementer must read `_insert_row` to patch the actual client symbol; the two assertions — payload carries the int, default is None — are the required contract. If an existing `test_run_recorder*.py` already has a payload-capture harness, extend it instead of hand-rolling this one.)

- [ ] **Step 4: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_run_recorder_conversation_id.py -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'conversation_id'`.

- [ ] **Step 5: Implement**

In `run_recorder.py`, add the field next to `session_id` (keep dataclass ordering — fields with defaults stay after non-default fields):

```python
    session_id: Optional[str] = None
    conversation_id: Optional[int] = None  # Phase 1.5: structural run→conversation link
    team_id: Optional[int] = None
```

In `_insert_row`'s payload dict, next to `"session_id"`:

```python
            "conversation_id": int(self.conversation_id) if self.conversation_id else None,
```

In `conversation_agent_turn.py`, add to the existing `RunRecorder(...)` call (keep the metadata key too — dashboards already read it):

```python
        async with RunRecorder(
            agent_id=composed.agent_id,
            user_id=UUID(summoner_user_id),
            trigger="chat_summon",
            session_id=None,
            conversation_id=int(conversation_id),
            team_id=int(scope_id) if scope_id is not None else None,
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_run_recorder_conversation_id.py tests/test_conversation_agent_turn.py -v`
Expected: all PASS (the agent-turn suite proves the extra kwarg didn't break the call).

- [ ] **Step 7: Lint + commit**

```bash
cd backend && uv run black app/services/ai/runner/run_recorder.py app/services/chat/conversation_agent_turn.py tests/test_run_recorder_conversation_id.py && uv run isort app/services/ai/runner/run_recorder.py app/services/chat/conversation_agent_turn.py tests/test_run_recorder_conversation_id.py && uv run flake8 app/services/ai/runner/run_recorder.py app/services/chat/conversation_agent_turn.py tests/test_run_recorder_conversation_id.py
git add supabase/migrations/331_agent_runs_conversation_id.sql backend/app/services/ai/runner/run_recorder.py backend/app/services/chat/conversation_agent_turn.py backend/tests/test_run_recorder_conversation_id.py
git commit -m "feat(ai): structural agent_runs.conversation_id (mig 331) + RunRecorder field"
```

---

### Task 3: Repositories — conversation_memory_repository + messages_in_range

**Files:**
- Create: `backend/app/repositories/conversation_memory_repository.py`
- Modify: `backend/app/repositories/conversation_repository.py` (add `messages_in_range` next to `recent_messages`, ~line 391)
- Test: `backend/tests/test_conversation_memory_repository.py` (NEW); extend `backend/tests/test_conversation_repository.py`

**Interfaces:**
- Consumes: `from app.db import engine as db_engine` — **match the exact call style `conversation_repository.recent_messages` uses** (read it first; same fetch helper, same `_bigint` coercion).
- Produces:
  - `ConversationMemoryRepository.load(conversation_id: int) -> Optional[dict]` (keys: `conversation_id, summary_md, last_seq_summarized, model, updated_at`)
  - `ConversationMemoryRepository.upsert(*, conversation_id: int, summary_md: str, last_seq_summarized: int, model: Optional[str]) -> None`
  - `get_conversation_memory_repository() -> ConversationMemoryRepository` singleton
  - `ConversationRepository.messages_in_range(*, conversation_id: int, from_seq: int, to_seq: int, limit: int = 200) -> list[dict]` (ascending, non-deleted, `seq BETWEEN from_seq AND to_seq`, rows carry `seq, sender_type, type, body, created_at`)

- [ ] **Step 1: Write the failing tests**

`tests/test_conversation_memory_repository.py` — use the same fake-engine SQL-capture harness as `tests/test_conversation_repository.py` (copy its fixture):

```python
@pytest.mark.unit
@pytest.mark.asyncio
async def test_upsert_uses_on_conflict(fake_engine):
    repo = ConversationMemoryRepository()
    await repo.upsert(
        conversation_id=1, summary_md="S", last_seq_summarized=40, model="qwen-turbo"
    )
    sql = fake_engine.last_sql()
    assert "INSERT INTO public.conversation_memory" in sql
    assert "ON CONFLICT (conversation_id) DO UPDATE" in sql
    assert fake_engine.last_params()["last_seq"] == 40


@pytest.mark.unit
@pytest.mark.asyncio
async def test_load_selects_by_conversation(fake_engine):
    repo = ConversationMemoryRepository()
    await repo.load(7)
    sql = fake_engine.last_sql()
    assert "FROM public.conversation_memory" in sql
    assert "conversation_id = :cid" in sql
```

Extend `tests/test_conversation_repository.py`:

```python
@pytest.mark.unit
@pytest.mark.asyncio
async def test_messages_in_range_bounds_and_excludes_deleted(fake_engine):
    repo = ConversationRepository()
    await repo.messages_in_range(conversation_id=1, from_seq=5, to_seq=40)
    sql = fake_engine.last_sql()
    assert "seq >= :from_seq" in sql and "seq <= :to_seq" in sql
    assert "deleted_at IS NULL" in sql
    assert "ORDER BY seq ASC" in sql
```

(Adapt assertion helpers to the file's actual fake-engine fixture API — read the existing tests first; the asserted SQL fragments are the contract.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_conversation_memory_repository.py tests/test_conversation_repository.py -v`
Expected: new tests FAIL (module/method missing); existing tests PASS.

- [ ] **Step 3: Implement**

`conversation_memory_repository.py`:

```python
"""Data access for conversation_memory (rolling head-summary sidecar).

Backend-only table (service-role RLS); all access via the privileged
db_engine, mirroring conversation_repository's call style.
"""

from __future__ import annotations

from typing import Any, Optional

from app.db import engine as db_engine


class ConversationMemoryRepository:
    async def load(self, conversation_id: int) -> Optional[dict[str, Any]]:
        return await db_engine.fetch_one(
            "SELECT conversation_id, summary_md, last_seq_summarized, model, updated_at "
            "FROM public.conversation_memory WHERE conversation_id = :cid",
            {"cid": int(conversation_id)},
        )

    async def upsert(
        self,
        *,
        conversation_id: int,
        summary_md: str,
        last_seq_summarized: int,
        model: Optional[str] = None,
    ) -> None:
        await db_engine.execute(
            "INSERT INTO public.conversation_memory "
            "(conversation_id, summary_md, last_seq_summarized, model, updated_at) "
            "VALUES (:cid, :summary, :last_seq, :model, now()) "
            "ON CONFLICT (conversation_id) DO UPDATE SET "
            "summary_md = EXCLUDED.summary_md, "
            "last_seq_summarized = EXCLUDED.last_seq_summarized, "
            "model = EXCLUDED.model, updated_at = now()",
            {
                "cid": int(conversation_id),
                "summary": summary_md,
                "last_seq": int(last_seq_summarized),
                "model": model,
            },
        )


_repo: Optional[ConversationMemoryRepository] = None


def get_conversation_memory_repository() -> ConversationMemoryRepository:
    global _repo
    if _repo is None:
        _repo = ConversationMemoryRepository()
    return _repo
```

(**If** `db_engine` exposes different helper names — e.g. `fetch_one`/`execute` don't exist and `conversation_repository` uses `async with eng.begin()` — copy `recent_messages`'s exact pattern instead. The SQL strings above are the contract; the transport must match the codebase.)

`conversation_repository.py` — add below `recent_messages`, same style:

```python
    async def messages_in_range(
        self,
        *,
        conversation_id: int,
        from_seq: int,
        to_seq: int,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Ascending non-deleted messages with from_seq <= seq <= to_seq.

        Compaction feed (Phase 1.5): the caller summarizes this span and
        advances conversation_memory.last_seq_summarized to to_seq.
        """
        # (copy the surrounding fetch helper/transaction style verbatim)
        rows = await db_engine.fetch_all(
            "SELECT seq, sender_type, type, body, created_at "
            "FROM public.messages "
            "WHERE conversation_id = :cid AND seq >= :from_seq AND seq <= :to_seq "
            "  AND deleted_at IS NULL "
            "ORDER BY seq ASC LIMIT :limit",
            {
                "cid": _bigint(conversation_id),
                "from_seq": _bigint(from_seq),
                "to_seq": _bigint(to_seq),
                "limit": limit,
            },
        )
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_conversation_memory_repository.py tests/test_conversation_repository.py -v`
Expected: all PASS.

- [ ] **Step 5: Real-DB smoke** — add a skippable integration test (guard on `SUPAVISOR_DATABASE_URL`, mirror the existing smoke in `test_conversation_repository.py`): `upsert` twice for the same conversation (second call updates), `load` returns the updated row; `messages_in_range` over a conversation with 3 sent messages returns seq 1..3 ascending. Run it once for real:
`SUPAVISOR_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres uv run pytest tests/test_conversation_memory_repository.py -v -k smoke`
Expected: PASS (paste output in the report).

- [ ] **Step 6: Lint + commit**

```bash
cd backend && uv run black app/repositories/conversation_memory_repository.py app/repositories/conversation_repository.py tests/test_conversation_memory_repository.py tests/test_conversation_repository.py && uv run isort <same files> && uv run flake8 <same files>
git add backend/app/repositories/conversation_memory_repository.py backend/app/repositories/conversation_repository.py backend/tests/test_conversation_memory_repository.py backend/tests/test_conversation_repository.py
git commit -m "feat(chat): conversation_memory repository + messages_in_range"
```

---

### Task 4: Flag + conversation_memory_service (read block + compaction)

**Files:**
- Modify: `backend/app/core/config.py` (feature-flags block, near `FEATURE_AGENT_MEMORY` ~line 1022)
- Create: `backend/app/services/chat/conversation_memory_service.py`
- Test: `backend/tests/test_conversation_memory_service.py` (NEW)

**Interfaces:**
- Consumes: `get_conversation_memory_repository()` + `ConversationRepository.messages_in_range` (Task 3); `MemoryContext`/`recall` from `app.services.ai.memory.agent_memory`; `QwenAdapter` + `ComposedSystemPrompt` (summarizer precedent `ai_library_chat_service.py:1083`); `settings.FEATURE_GROUP_AGENT_MEMORY` / `settings.FEATURE_AGENT_MEMORY` / `settings.DASHSCOPE_API_KEY` / `settings.QWEN_API_KEY`.
- Produces:
  - `build_memory_block(*, conversation: dict, user_query: str, summoner_user_id: str, agent: dict) -> str` (empty string when flag off / nothing to inject; never raises)
  - `maybe_compact(*, conversation: dict, agent_id: Optional[str] = None) -> None` (never raises)
  - Module constants `COMPACT_TRIGGER = 30`, `COMPACT_KEEP_TAIL = 20`

- [ ] **Step 1: Add the flag** in `config.py`, in the feature-flags block:

```python
    FEATURE_GROUP_AGENT_MEMORY: bool = Field(
        default=False,
        description="Group-chat agent memory: inject conversation_memory "
        "summary + agent_memory recall into conversation agent turns, and "
        "compact after turns. Off (default) = Phase 1 behavior (20-msg tail).",
    )
```

- [ ] **Step 2: Write the failing tests**

```python
"""conversation_memory_service — flag gating, block rendering, compaction."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.chat import conversation_memory_service as svc

CONV = {"id": 10, "scope_id": 99, "last_seq": 60}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_block_empty_when_flag_off(monkeypatch):
    monkeypatch.setattr(svc.settings, "FEATURE_GROUP_AGENT_MEMORY", False)
    out = await svc.build_memory_block(
        conversation=CONV, user_query="q", summoner_user_id="u1", agent={"id": "a1"}
    )
    assert out == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_block_renders_summary_and_memories(monkeypatch):
    monkeypatch.setattr(svc.settings, "FEATURE_GROUP_AGENT_MEMORY", True)
    monkeypatch.setattr(svc.settings, "FEATURE_AGENT_MEMORY", True)
    with patch.object(
        svc,
        "get_conversation_memory_repository",
        return_value=AsyncMock(
            load=AsyncMock(
                return_value={"summary_md": "OLD STUFF", "last_seq_summarized": 30}
            )
        ),
    ), patch.object(
        svc,
        "recall",
        AsyncMock(
            return_value=[
                svc.MemoryHit(id=1, title="T", body_md="B", kind="fact", score=1.0)
            ]
        ),
    ):
        out = await svc.build_memory_block(
            conversation=CONV, user_query="q", summoner_user_id="u1", agent={"id": "a1"}
        )
    assert "## Conversation summary (older messages)" in out and "OLD STUFF" in out
    assert "## Relevant memories" in out and "fact: T — B" in out


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_block_never_raises(monkeypatch):
    monkeypatch.setattr(svc.settings, "FEATURE_GROUP_AGENT_MEMORY", True)
    with patch.object(
        svc,
        "get_conversation_memory_repository",
        side_effect=Exception("db down"),
    ):
        out = await svc.build_memory_block(
            conversation=CONV, user_query="q", summoner_user_id="u1", agent={"id": "a1"}
        )
    assert out == ""  # degraded, not raised


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compact_below_threshold_is_noop(monkeypatch):
    monkeypatch.setattr(svc.settings, "FEATURE_GROUP_AGENT_MEMORY", True)
    summarize = AsyncMock()
    with patch.object(svc, "_summarize", summarize), patch.object(
        svc,
        "get_conversation_memory_repository",
        return_value=AsyncMock(
            load=AsyncMock(return_value={"summary_md": "", "last_seq_summarized": 20})
        ),
    ), patch.object(
        svc,
        "_fresh_conversation",
        AsyncMock(return_value={"id": 10, "last_seq": 60}),
    ):
        # 60 - 20 = 40 unsummarized < TRIGGER(30) + KEEP_TAIL(20) = 50 → no-op
        await svc.maybe_compact(conversation=CONV)
    summarize.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compact_above_threshold_upserts(monkeypatch):
    monkeypatch.setattr(svc.settings, "FEATURE_GROUP_AGENT_MEMORY", True)
    mem_repo = AsyncMock(
        load=AsyncMock(return_value={"summary_md": "PREV", "last_seq_summarized": 0})
    )
    conv_repo = AsyncMock(
        messages_in_range=AsyncMock(
            return_value=[
                {"seq": 1, "sender_type": "user", "type": "text", "body": {"text": "hi"}}
            ]
        )
    )
    with patch.object(
        svc, "get_conversation_memory_repository", return_value=mem_repo
    ), patch.object(
        svc, "get_conversation_repository", return_value=conv_repo
    ), patch.object(
        svc, "_fresh_conversation", AsyncMock(return_value={"id": 10, "last_seq": 60})
    ), patch.object(svc, "_summarize", AsyncMock(return_value="NEW SUMMARY")):
        # 60 - 0 = 60 unsummarized >= 50 → compact span 1..40
        await svc.maybe_compact(conversation=CONV)
    conv_repo.messages_in_range.assert_awaited_once_with(
        conversation_id=10, from_seq=1, to_seq=40
    )
    mem_repo.upsert.assert_awaited_once()
    kwargs = mem_repo.upsert.await_args.kwargs
    assert kwargs["last_seq_summarized"] == 40
    assert kwargs["summary_md"] == "NEW SUMMARY"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compact_summarizer_empty_means_no_upsert(monkeypatch):
    monkeypatch.setattr(svc.settings, "FEATURE_GROUP_AGENT_MEMORY", True)
    mem_repo = AsyncMock(load=AsyncMock(return_value=None))
    conv_repo = AsyncMock(
        messages_in_range=AsyncMock(
            return_value=[{"seq": 1, "sender_type": "user", "type": "text", "body": {}}]
        )
    )
    with patch.object(
        svc, "get_conversation_memory_repository", return_value=mem_repo
    ), patch.object(
        svc, "get_conversation_repository", return_value=conv_repo
    ), patch.object(
        svc, "_fresh_conversation", AsyncMock(return_value={"id": 10, "last_seq": 60})
    ), patch.object(svc, "_summarize", AsyncMock(return_value="")):
        await svc.maybe_compact(conversation=CONV)
    mem_repo.upsert.assert_not_awaited()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_conversation_memory_service.py -v`
Expected: FAIL (module missing).

- [ ] **Step 4: Implement** `conversation_memory_service.py`:

```python
"""Group-agent conversation memory (Phase 1.5).

Read side — build_memory_block(): rolling summary (conversation_memory) +
agent_memory recall, rendered as a markdown block the agent turn appends to
its request instructions. Write side — maybe_compact(): after agent turns,
summarize the un-summarized head (keeping the newest COMPACT_KEEP_TAIL
messages verbatim, since recent_messages already feeds those to the turn)
via a cheap LLM, and advance last_seq_summarized.

Privacy (user decision 2026-07-02): group recall reuses the 1:1 shape —
summoner-owned OR team-shared memories. The summoner @-mentioned the agent;
that is treated as consent for their own memories to inform a group-visible
reply.

Every public function is flag-gated on FEATURE_GROUP_AGENT_MEMORY and NEVER
raises — a memory failure must not break a summon.
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from app.core.config import settings
from app.repositories.conversation_memory_repository import (
    get_conversation_memory_repository,
)
from app.repositories.conversation_repository import get_conversation_repository
from app.services.ai.memory.agent_memory import MemoryContext, MemoryHit, recall

COMPACT_TRIGGER = 30  # un-summarized messages (beyond the tail) that trigger compaction
COMPACT_KEEP_TAIL = 20  # newest messages stay verbatim (= recent_messages window)
_SUMMARY_MODEL = "qwen-turbo"
_SUMMARY_MAX_TOKENS = 700

_SUMMARY_SYSTEM = (
    "You maintain a rolling summary of a team chat conversation. Merge the "
    "PREVIOUS SUMMARY with the NEW MESSAGES into one concise markdown summary "
    "(<= 300 words): decisions, open questions, facts, who said what that "
    "still matters. Drop chit-chat. Output ONLY the summary markdown."
)


def _render_line(msg: dict[str, Any]) -> str:
    body = msg.get("body") or {}
    mtype = msg.get("type", "text")
    if mtype == "text":
        content = str(body.get("text", ""))
    else:
        content = f"[{mtype}]"
    return f"[{msg.get('sender_type', 'user')}] {content}"


async def _fresh_conversation(conversation_id: int) -> Optional[dict[str, Any]]:
    """Re-read the conversation for a fresh last_seq (the dict the summon path
    holds was loaded before the turn wrote replies)."""
    return await get_conversation_repository().get_conversation(
        conversation_id=conversation_id
    )


async def _summarize(previous: str, transcript: str) -> str:
    """Cheap-LLM rolling summary. '' on any failure / missing key
    (precedent: _harvest_summarizer in ai_library_chat_service)."""
    try:
        from uuid import uuid4

        from app.schemas.ai_library import ComposedSystemPrompt
        from app.services.ai.providers.ai_provider import QwenAdapter

        api_key = getattr(settings, "DASHSCOPE_API_KEY", None) or getattr(
            settings, "QWEN_API_KEY", None
        )
        if not api_key:
            logger.info("[conv_memory] no qwen key — skipping compaction")
            return ""
        adapter = QwenAdapter(api_key=api_key, model=_SUMMARY_MODEL)
        cs = ComposedSystemPrompt(
            agent_id=uuid4(),
            agent_slug="conversation_compactor",
            model=_SUMMARY_MODEL,
            temperature=0.0,
            max_tokens=_SUMMARY_MAX_TOKENS,
            system_message=_SUMMARY_SYSTEM,
            tools=[],
            skill_manifest=[],
            cache_fingerprint="conversation_compactor_v1",
        )
        prompt = (
            f"PREVIOUS SUMMARY:\n{previous or '(none)'}\n\n"
            f"NEW MESSAGES:\n{transcript}"
        )
        resp = await adapter.call(cs, [{"role": "user", "content": prompt}])
        return (resp.get("content") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[conv_memory] summarize failed: {exc!r}")
        return ""
```

(**ComposedSystemPrompt note:** the implementer must check its `agent_id` field type — `_harvest_summarizer` passed a real composed.agent_id. If a random uuid4 is rejected downstream (e.g. FK'd telemetry), thread the summoned agent's id through `maybe_compact(agent_id=...)` and use that instead. The harvest precedent shows adapter.call does not persist agent_id, so uuid4 is expected to be fine.)

```python
async def build_memory_block(
    *,
    conversation: dict[str, Any],
    user_query: str,
    summoner_user_id: str,
    agent: dict[str, Any],
) -> str:
    if not settings.FEATURE_GROUP_AGENT_MEMORY:
        return ""
    parts: list[str] = []
    cid = int(conversation["id"])
    try:
        mem = await get_conversation_memory_repository().load(cid)
        if mem and (mem.get("summary_md") or "").strip():
            parts.append(
                "## Conversation summary (older messages)\n" + mem["summary_md"]
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[conv_memory] summary load failed conv={cid}: {exc!r}")
    try:
        if settings.FEATURE_AGENT_MEMORY:
            ctx = MemoryContext(
                user_id=summoner_user_id,
                team_ids=(int(conversation["scope_id"]),),
                agent_id=str(agent.get("id")) if agent.get("id") else None,
            )
            hits = await recall(ctx, user_query, limit=5)
            if hits:
                parts.append(
                    "## Relevant memories\n"
                    + "\n".join(f"- {h.kind}: {h.title} — {h.body_md}" for h in hits)
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[conv_memory] recall failed conv={cid}: {exc!r}")
    return "\n\n".join(parts)


async def maybe_compact(
    *, conversation: dict[str, Any], agent_id: Optional[str] = None
) -> None:
    if not settings.FEATURE_GROUP_AGENT_MEMORY:
        return
    try:
        cid = int(conversation["id"])
        fresh = await _fresh_conversation(cid)
        if not fresh:
            return
        last_seq = int(fresh.get("last_seq") or 0)
        mem = await get_conversation_memory_repository().load(cid)
        done = int(mem["last_seq_summarized"]) if mem else 0
        if last_seq - done < COMPACT_TRIGGER + COMPACT_KEEP_TAIL:
            return
        to_seq = last_seq - COMPACT_KEEP_TAIL
        msgs = await get_conversation_repository().messages_in_range(
            conversation_id=cid, from_seq=done + 1, to_seq=to_seq
        )
        if not msgs:
            return
        transcript = "\n".join(_render_line(m) for m in msgs)
        previous = (mem or {}).get("summary_md") or ""
        summary = await _summarize(previous, transcript)
        if not summary:
            return
        await get_conversation_memory_repository().upsert(
            conversation_id=cid,
            summary_md=summary,
            last_seq_summarized=to_seq,
            model=_SUMMARY_MODEL,
        )
        logger.info(f"[conv_memory] compacted conv={cid} through seq={to_seq}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[conv_memory] compaction failed: {exc!r}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_conversation_memory_service.py -v`
Expected: all PASS.

- [ ] **Step 6: Lint + commit**

```bash
cd backend && uv run black app/core/config.py app/services/chat/conversation_memory_service.py tests/test_conversation_memory_service.py && uv run isort <same> && uv run flake8 <same>
git add backend/app/core/config.py backend/app/services/chat/conversation_memory_service.py backend/tests/test_conversation_memory_service.py
git commit -m "feat(chat): group agent memory service (summary block + rolling compaction) + flag"
```

---

### Task 5: Inject memory into the agent turn

**Files:**
- Modify: `backend/app/services/chat/conversation_agent_turn.py` (between `user_query` computation ~line 202 and `composer.compose` ~line 224)
- Test: extend `backend/tests/test_conversation_agent_turn.py`

**Interfaces:**
- Consumes: `build_memory_block` (Task 4); existing `_UNTRUSTED_CHANNEL_INSTRUCTION` + `ComposerInput(request_instructions=...)`.
- Produces: agent turns whose `request_instructions` = untrusted-guard + optional memory block. **Placement is load-bearing:** the memory block derives from user messages (untrusted) — it must come AFTER `_UNTRUSTED_CHANNEL_INSTRUCTION` so the guard covers it.

- [ ] **Step 1: Write the failing test** (extend the existing suite; reuse its mock harness):

```python
@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_block_appended_to_request_instructions(monkeypatch):
    """When build_memory_block returns text, compose() receives
    untrusted-guard + blank line + block; when '', instructions unchanged."""
    captured = {}
    # ...reuse the existing happy-path harness (mocks for agent repo, caps,
    # conv repo, build_agent_runner_stack); additionally:
    monkeypatch.setattr(
        "app.services.chat.conversation_agent_turn.build_memory_block",
        AsyncMock(return_value="## Conversation summary (older messages)\nS"),
    )
    # capture ComposerInput via the mocked PromptComposer.compose
    # assert captured_input.request_instructions.startswith(_UNTRUSTED_CHANNEL_INSTRUCTION)
    # assert "## Conversation summary (older messages)" in captured_input.request_instructions
```

(The existing `test_happy_path_returns_content` already mocks `PromptComposer` — extend that harness to capture the `ComposerInput` and assert both the prefix and the appended block. Add a second case: `build_memory_block` returns `""` → `request_instructions == _UNTRUSTED_CHANNEL_INSTRUCTION` exactly.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_conversation_agent_turn.py -v`
Expected: new tests FAIL (`build_memory_block` not imported / instructions unchanged).

- [ ] **Step 3: Implement** — in `conversation_agent_turn.py`:

Add import at top:

```python
from app.services.chat.conversation_memory_service import build_memory_block
```

After `user_query` is computed and `agent` is resolved (before the composer call), build the instructions:

```python
    # Phase 1.5 — group agent memory (flag-dark). The block derives from user
    # content, so it goes AFTER the untrusted-channel guard.
    memory_block = await build_memory_block(
        conversation=conversation,
        user_query=user_query,
        summoner_user_id=summoner_user_id,
        agent=agent,
    )
    request_instructions = _UNTRUSTED_CHANNEL_INSTRUCTION
    if memory_block:
        request_instructions = f"{_UNTRUSTED_CHANNEL_INSTRUCTION}\n\n{memory_block}"
```

And change the `ComposerInput(...)` call to use it:

```python
            ComposerInput(
                agent_slug=agent_slug,
                request_instructions=request_instructions,
                graph_facts=stack.graph_facts,
                user_context=stack.user_context,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_conversation_agent_turn.py -v`
Expected: all PASS (including the pre-existing 8).

- [ ] **Step 5: Lint + commit**

```bash
cd backend && uv run black app/services/chat/conversation_agent_turn.py tests/test_conversation_agent_turn.py && uv run isort <same> && uv run flake8 <same>
git add backend/app/services/chat/conversation_agent_turn.py backend/tests/test_conversation_agent_turn.py
git commit -m "feat(chat): inject conversation memory block into group agent turns"
```

---

### Task 6: Parallel summons + post-turn compaction

**Files:**
- Modify: `backend/app/services/conversation_service.py` (`dispatch_summons`, the serial `for slug in mentioned:` loop)
- Test: extend `backend/tests/test_conversation_service.py`

**Interfaces:**
- Consumes: `maybe_compact` (Task 4); existing `_get_conversation_semaphore` (cap 2), `run_conversation_agent_turn`, `self._repo.send_message`.
- Produces: `dispatch_summons` returns the same `list[str]` of replied slugs; agents run concurrently (bounded by the semaphore); one `maybe_compact` call per dispatch when at least one agent replied. All failure modes stay swallow-logged.

- [ ] **Step 1: Write the failing tests** (extend the existing suite; reuse its repo/caps/turn mocks):

```python
@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_two_agents_runs_concurrently(...):
    """Two mentioned agents: with run_conversation_agent_turn stubbed to
    `await asyncio.sleep(0.05); return 'r'`, total dispatch wall-time must be
    < 0.09s (parallel), not ~0.1s (serial). Both slugs in the returned list."""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_calls_maybe_compact_once_after_replies(...):
    """maybe_compact (patched) awaited exactly once when >=1 reply; NOT
    awaited when the anti-loop guard returns early or nothing replied."""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_one_agent_failure_does_not_sink_the_other(...):
    """First agent's turn raises, second returns text → returned list is
    exactly [second]; no exception escapes dispatch_summons."""
```

(Write these fully against the existing test harness in `test_conversation_service.py` — copy its `_make_service`/mock fixtures. The three docstrings above are the required behaviors; the existing tests for anti-loop and reply-write-failure must keep passing unchanged.)

- [ ] **Step 2: Run to verify the new tests fail**

Run: `cd backend && uv run pytest tests/test_conversation_service.py -v`
Expected: new tests FAIL (serial timing / maybe_compact not called); existing 14 PASS.

- [ ] **Step 3: Implement** — replace the serial loop in `dispatch_summons` (keep every log message text identical):

```python
        sem = _get_conversation_semaphore(conversation_id)

        async def _summon_one(slug: str) -> Optional[str]:
            agent = slug_to_agent[slug]
            async with sem:
                try:
                    reply = await run_conversation_agent_turn(
                        agent_slug=slug,
                        summoner_user_id=summoner_user_id,
                        conversation=conversation,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        f"[dispatch_summons] agent_turn_failed: "
                        f"conversation={conversation_id} agent={slug} error={exc!r}"
                    )
                    return None
            if not reply:
                return None
            try:
                await self._repo.send_message(
                    conversation_id=conversation_id,
                    sender_id=None,
                    sender_type="agent",
                    type="text",
                    body={"text": reply},
                    parent_id=None,
                    from_agent_id=agent["id"],
                )
                return slug
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    f"[dispatch_summons] reply_write_failed: "
                    f"agent={slug} error={exc!r}"
                )
                return None

        results = await asyncio.gather(*(_summon_one(s) for s in mentioned))
        replied = [s for s in results if s]

        # Phase 1.5 — rolling summary upkeep. Never raises; flag-gated inside.
        if replied:
            try:
                await maybe_compact(
                    conversation=conversation,
                    agent_id=slug_to_agent[replied[0]].get("id"),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[dispatch_summons] compact_failed: {exc!r}")

        return replied
```

Add the import at top of `conversation_service.py`:

```python
from app.services.chat.conversation_memory_service import maybe_compact
```

(`asyncio` is already imported for the semaphore. `gather` without `return_exceptions` is safe here because `_summon_one` swallows internally — nothing can raise out of it; the belt-and-braces try around `maybe_compact` covers the one remaining call.)

- [ ] **Step 4: Run the suite**

Run: `cd backend && uv run pytest tests/test_conversation_service.py -v`
Expected: all PASS (old 14 + new 3).

- [ ] **Step 5: Real-DB smoke (end-to-end memory loop)** — extend the skippable smoke in `test_conversation_memory_repository.py` (or a new smoke in the service test): with the flag monkeypatched ON and `_summarize` monkeypatched to return `"ROLLING"`, create a conversation, `send_message` × 55, call `maybe_compact(conversation=...)` → assert `conversation_memory` row exists with `last_seq_summarized == 35` and `summary_md == "ROLLING"`. Run once for real:
`SUPAVISOR_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres uv run pytest -v -k "smoke and compact"`
Expected: PASS (paste output).

- [ ] **Step 6: Lint + commit**

```bash
cd backend && uv run black app/services/conversation_service.py tests/test_conversation_service.py && uv run isort <same> && uv run flake8 <same>
git add backend/app/services/conversation_service.py backend/tests/test_conversation_service.py backend/tests/test_conversation_memory_repository.py
git commit -m "feat(chat): parallel agent summons + post-turn conversation compaction"
```

---

## Self-Review

- **Scope coverage:** group agent memory read (T4/T5) + write/compaction (T4/T6) + agent_memory recall (T4, privacy decision documented) + parallel summons (T6) + agent_runs structural link (T2) + sidecar table (T1). All flag-dark (`FEATURE_GROUP_AGENT_MEMORY` default False; T2's column is passive).
- **Placeholder scan:** all SQL/code blocks complete; the three T6 test docstrings and T2/T5 test sketches name exact required assertions against existing harnesses (the harness fixtures already exist in the named files — extending, not inventing).
- **Type consistency:** `build_memory_block(*, conversation, user_query, summoner_user_id, agent) -> str` used identically in T4 (def) and T5 (call); `maybe_compact(*, conversation, agent_id=None)` in T4 (def) and T6 (call); `messages_in_range(*, conversation_id, from_seq, to_seq, limit=200)` in T3 (def) and T4 (call); `COMPACT_TRIGGER=30`/`COMPACT_KEEP_TAIL=20` consistent with T4/T6 test math (60-0=60 ≥ 50 → to_seq=40; 55 msgs → to_seq=35).
- **Known limitation (accepted v1):** compaction runs AFTER turns only — the first summon after a long agent-silent stretch sees just the 20-message tail; the summary catches up for subsequent turns. Documented here; revisit if it bites.
- **Risks:** `ComposedSystemPrompt.agent_id` type (noted inline, T4); `db_engine` helper-name drift (noted inline, T3 — copy `recent_messages` style); local snapshot missing `agent_runs` (noted inline, T2).

## Execution Handoff

Execute via superpowers:subagent-driven-development (fresh implementer + reviewer per task; final whole-branch review). After T6: ship via PR → master (flags stay dark; zero behavior change until `FEATURE_GROUP_AGENT_MEMORY` flips). Rollout order at go-live: apply 330/331 → flip `FEATURE_CONVERSATIONS` (Phase 1 go-live, separate decision) → flip `FEATURE_GROUP_AGENT_MEMORY` → verify an @-mention in a >50-message group produces a `conversation_memory` row and the reply reflects older context.
