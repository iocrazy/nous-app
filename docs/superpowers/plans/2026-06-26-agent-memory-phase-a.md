# Agent Memory Layer — Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Build the scoped agent-memory **read path** — a multi-tenant `agent_memory` table (scope columns + tsvector FTS + RLS) and a strictly-scoped `recall()` API wired into the agent prompt behind a default-off flag. No writers yet → recall returns empty → **behavior-neutral**. Proves the isolation model + injection wiring; B/C/D (consolidation, promotion, distill) build on it later.

**Architecture:** New `agent_memory` Postgres table holds curated, scoped memory entries with a GENERATED `search_tsv` (tsvector) + GIN index. `recall(ctx: MemoryContext, query)` is the ONLY read path — it builds a mandatory scope predicate (own private + my-team shared) and ranks by `ts_rank`. RLS double-guards. `prompt_composer` injects an `<agent_memory>` block (post cache-boundary, fingerprinted) when `FEATURE_AGENT_MEMORY` is on. See `docs/superpowers/specs/2026-06-26-agent-memory-layer-design.md`.

**Tech Stack:** Postgres (first tsvector/GIN-tsvector in the codebase) + SQLAlchemy 2.0 (asyncpg) + FastAPI + DBOS chat runtime; pytest. Backend lint = black + isort + flake8 (NOT ruff); loguru `{}`/f-strings.

## Global Constraints

- **Behavior-neutral.** `FEATURE_AGENT_MEMORY` defaults **off**; with it off, recall is never called and nothing changes. With it on but the table empty, recall returns `[]` and the prompt block is omitted. No existing path changes output until an admin enables the flag AND rows exist (Phase B+).
- **Isolation is mandatory.** `recall()` requires a `MemoryContext`; there is NO unscoped query path. The scope predicate is: `owner_user_id = ctx.user_id` OR (`visibility='shared'` AND `team_id = ANY(ctx.team_ids)`). RLS on the table is defense-in-depth.
- SQL belongs in `supabase/migrations/NNN_*.sql` (next number **323**). Migration must be CI-appliable (test `psql -U postgres` locally is the apply contract; avoid `ALTER PUBLICATION ... DROP TABLE IF EXISTS` style traps).
- Snowflake BIGINT ids carried as str → coerce with `_bigint()` on write paths (none here) and bind as int in queries.
- Lint = black + isort + flake8; loguru `{}`/f-strings.
- Reuse existing RLS helpers: `public.get_user_team_ids(uuid)` (SECURITY DEFINER) + `team_members`. Wrap `auth.uid()` as `(SELECT auth.uid())` (the established perf pattern).

---

## File Structure

| File | Responsibility |
|------|----------------|
| `supabase/migrations/323_agent_memory.sql` (new) | `agent_memory` table + enums + GENERATED `search_tsv` + GIN + scope indexes + RLS |
| `backend/app/models/ai.py` (modify) | `AgentMemory` ORM model matching the table |
| `backend/app/services/ai/memory/agent_memory.py` (new) | `MemoryContext`, `MemoryHit`, `recall()` scoped read API |
| `backend/app/repositories/agent_memory_repository.py` (new) | `AgentMemoryRepository` (ORM) — the scoped SELECT |
| `backend/app/services/ai/prompts/prompt_composer.py` (modify) | `ComposerInput.agent_memory_facts` + `<agent_memory>` render + fingerprint |
| `backend/app/services/ai/chat/ai_library_chat_wiring.py` (modify) | flag-gated recall in the concurrent recall budget |
| `backend/app/core/config.py` (modify) | `FEATURE_AGENT_MEMORY` flag (default False) |
| `backend/tests/memory/test_agent_memory_recall.py` (new) | scope predicate + ranking + flag-off no-op |
| `backend/tests/memory/test_agent_memory_prompt_wiring.py` (new) | composer block + fingerprint |

---

### Task 1: Migration 323 + ORM model

**Files:**
- Create: `supabase/migrations/323_agent_memory.sql`
- Modify: `backend/app/models/ai.py` (add `AgentMemory`)
- Test: applied locally (see Step 2)

**Interfaces:**
- Produces: table `public.agent_memory` with columns: `id BIGINT PK (generate_snowflake_id())`, `scope TEXT CHECK IN ('session','user','agent_user','project','team')`, `owner_user_id UUID NOT NULL`, `team_id BIGINT NULL`, `project_id BIGINT NULL`, `agent_id UUID NULL`, `session_id BIGINT NULL`, `visibility TEXT CHECK IN ('private','shared') DEFAULT 'private'`, `kind TEXT CHECK IN ('fact','decision','preference','procedure') DEFAULT 'fact'`, `title TEXT NOT NULL DEFAULT ''`, `body_md TEXT NOT NULL DEFAULT ''`, `when_to_use TEXT NOT NULL DEFAULT ''`, `fingerprint TEXT NOT NULL DEFAULT ''`, `status TEXT CHECK IN ('active','archived','superseded') DEFAULT 'active'`, `reinforcement_count INT NOT NULL DEFAULT 0`, `last_recalled_at TIMESTAMPTZ`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`, and `search_tsv tsvector GENERATED ALWAYS AS (...) STORED`. Plus a GIN index on `search_tsv`, b-tree indexes on the scope dims, and RLS policies. ORM class `AgentMemory` mirrors it.

- [ ] **Step 1: Write the migration**

```sql
-- supabase/migrations/323_agent_memory.sql
-- Agent Memory Layer Phase A — curated, scoped agent memory + ranked FTS recall.
-- Read-path only; no writers in Phase A. The codebase's first tsvector/GIN-tsvector.

CREATE TABLE IF NOT EXISTS public.agent_memory (
    id                  BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    scope               TEXT NOT NULL CHECK (scope IN ('session','user','agent_user','project','team')),
    owner_user_id       UUID NOT NULL,
    team_id             BIGINT,
    project_id          BIGINT,
    agent_id            UUID,
    session_id          BIGINT,
    visibility          TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN ('private','shared')),
    kind                TEXT NOT NULL DEFAULT 'fact' CHECK (kind IN ('fact','decision','preference','procedure')),
    title               TEXT NOT NULL DEFAULT '',
    body_md             TEXT NOT NULL DEFAULT '',
    when_to_use         TEXT NOT NULL DEFAULT '',
    fingerprint         TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived','superseded')),
    reinforcement_count INT NOT NULL DEFAULT 0,
    last_recalled_at    TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Ranked recall index: weight title+when_to_use (A) above body (B).
    search_tsv tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '') || ' ' || coalesce(when_to_use, '')), 'A')
        || setweight(to_tsvector('english', coalesce(body_md, '')), 'B')
    ) STORED
);

CREATE INDEX IF NOT EXISTS idx_agent_memory_search ON public.agent_memory USING gin (search_tsv);
CREATE INDEX IF NOT EXISTS idx_agent_memory_owner ON public.agent_memory (owner_user_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_memory_team_shared ON public.agent_memory (team_id, visibility) WHERE visibility = 'shared';
CREATE INDEX IF NOT EXISTS idx_agent_memory_agent ON public.agent_memory (agent_id) WHERE agent_id IS NOT NULL;

ALTER TABLE public.agent_memory ENABLE ROW LEVEL SECURITY;

-- Read: own rows (any visibility) OR shared rows of a team the caller belongs to.
-- Project-shared rows also carry their project's team_id, so the team check covers them.
DROP POLICY IF EXISTS agent_memory_readable ON public.agent_memory;
CREATE POLICY agent_memory_readable ON public.agent_memory FOR SELECT
    USING (
        owner_user_id = (SELECT auth.uid())
        OR (visibility = 'shared'
            AND team_id IN (SELECT public.get_user_team_ids((SELECT auth.uid()))))
    );

-- Writes are service-role only (Phase B+ consolidation runs as service role).
DROP POLICY IF EXISTS agent_memory_write_service_only ON public.agent_memory;
CREATE POLICY agent_memory_write_service_only ON public.agent_memory FOR ALL
    TO service_role USING (true) WITH CHECK (true);
```

- [ ] **Step 2: Apply locally to verify it's CI-appliable**

Run against the NAS dev DB (the reachable dev库; per project memory `192.168.50.9:55434` / or the configured dev DSN):
`psql "<DEV_DSN>" -f supabase/migrations/323_agent_memory.sql`
Expected: `CREATE TABLE` / `CREATE INDEX` ×4 / `ALTER TABLE` / `CREATE POLICY` ×2, no error. Then verify the generated column + GIN:
`psql "<DEV_DSN>" -c "INSERT INTO public.agent_memory (scope, owner_user_id, title, when_to_use, body_md) VALUES ('user', gen_random_uuid(), 'deploy runbook', 'when deploying backend', 'run docker compose up -d'); SELECT title, ts_rank(search_tsv, websearch_to_tsquery('english','deploy')) AS r FROM public.agent_memory ORDER BY r DESC LIMIT 1;"`
Expected: one row with `r > 0`. Then clean up: `DELETE FROM public.agent_memory;`

> If the dev DB is not reachable from the implementer's environment, SKIP the live apply and instead validate the SQL parses by reviewing it against the column list above; flag in the report that live-apply was not run. (CI's "Run SQL Migration" will apply it on merge.)

- [ ] **Step 3: Add the ORM model**

In `backend/app/models/ai.py`, add (near the other AI tables; mirror the `NousModels` style — `BigInteger` snowflake PK, `Uuid`, `DateTime(True)`):

```python
class AgentMemory(Base):
    __tablename__ = "agent_memory"
    __table_args__ = (
        CheckConstraint(
            "scope = ANY (ARRAY['session','user','agent_user','project','team']::text[])",
            name="agent_memory_scope_check",
        ),
        CheckConstraint(
            "visibility = ANY (ARRAY['private','shared']::text[])",
            name="agent_memory_visibility_check",
        ),
        CheckConstraint(
            "kind = ANY (ARRAY['fact','decision','preference','procedure']::text[])",
            name="agent_memory_kind_check",
        ),
        CheckConstraint(
            "status = ANY (ARRAY['active','archived','superseded']::text[])",
            name="agent_memory_status_check",
        ),
        PrimaryKeyConstraint("id", name="agent_memory_pkey"),
        Index("idx_agent_memory_owner", "owner_user_id", "status"),
        Index("idx_agent_memory_agent", "agent_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    team_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    project_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    session_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    visibility: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'private'::text")
    )
    kind: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'fact'::text")
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    body_md: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    when_to_use: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''::text")
    )
    fingerprint: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''::text")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'active'::text")
    )
    reinforcement_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    last_recalled_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
```

> Implementer: confirm `BigInteger, CheckConstraint, Index, PrimaryKeyConstraint, Integer, Text, DateTime, Uuid, Mapped, mapped_column, text` are already imported at the top of `ai.py` (they are — used by NousModels/AiSessions). `search_tsv` is a DB-generated column; do NOT map it (the ORM never writes/reads it directly — recall uses raw SQL for `@@`/`ts_rank`). Export `AgentMemory` from `app.models` if the package re-exports model names (check `backend/app/models/__init__.py` and add it there if the others are listed).

- [ ] **Step 4: Commit**

```bash
cd /Volumes/program/project-code/repos/nous/.worktrees/feature-orm-2-migration
cd backend && uv run black app/models/ai.py && uv run isort app/models/ai.py && uv run flake8 app/models/ai.py
cd .. && git add supabase/migrations/323_agent_memory.sql backend/app/models/ai.py backend/app/models/__init__.py
git commit -m "feat(memory): agent_memory table (scoped, tsvector FTS, RLS) + ORM (Phase A)"
```

---

### Task 2: `MemoryContext` + scoped `recall()`

**Files:**
- Create: `backend/app/services/ai/memory/agent_memory.py`
- Create: `backend/app/repositories/agent_memory_repository.py`
- Test: `backend/tests/memory/test_agent_memory_recall.py`

**Interfaces:**
- Consumes: `AgentMemory` ORM (Task 1); `app.db.session.read_scope`.
- Produces:
  - `@dataclass(frozen=True) class MemoryContext`: `user_id: str`, `team_ids: tuple[int, ...] = ()`, `project_id: Optional[int] = None`, `agent_id: Optional[str] = None`, `session_id: Optional[int] = None`.
  - `@dataclass(frozen=True) class MemoryHit`: `id: int`, `title: str`, `body_md: str`, `kind: str`, `score: float`.
  - `async def recall(ctx: MemoryContext, query: str, *, limit: int = 5) -> list[MemoryHit]` — the ONLY read path. Builds the scope predicate + `search_tsv @@ websearch_to_tsquery('english', :q)`, ranks by `ts_rank`, returns top `limit`. Returns `[]` on blank query / no rows / any error (never raises — recall must never break a chat turn).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/memory/test_agent_memory_recall.py
"""Agent memory scoped recall (Phase A) — isolation predicate + ranking."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.memory.agent_memory import MemoryContext, recall


@pytest.mark.asyncio
async def test_recall_returns_empty_on_blank_query():
    ctx = MemoryContext(user_id="u1")
    assert await recall(ctx, "   ") == []


@pytest.mark.asyncio
async def test_recall_builds_scoped_query_and_maps_hits():
    # Capture the SQL + params the repo runs; return one fake row.
    captured = {}

    class _Result:
        def mappings(self):
            return self

        def all(self):
            return [
                {"id": 7, "title": "deploy", "body_md": "run x", "kind": "fact", "score": 0.9}
            ]

    class _Session:
        async def execute(self, stmt, params=None):
            captured["sql"] = str(stmt)
            captured["params"] = params
            return _Result()

    class _Scope:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *a):
            return False

    ctx = MemoryContext(user_id="u1", team_ids=(10, 20))
    with patch(
        "app.repositories.agent_memory_repository.read_scope", return_value=_Scope()
    ):
        hits = await recall(ctx, "deploy backend", limit=5)

    assert len(hits) == 1
    assert hits[0].id == 7 and hits[0].kind == "fact"
    # isolation: the query binds the caller's user_id + team_ids
    assert captured["params"]["user_id"] == "u1"
    assert captured["params"]["team_ids"] == [10, 20]
    # uses websearch_to_tsquery + ts_rank (ranked FTS, not ILIKE)
    assert "websearch_to_tsquery" in captured["sql"]
    assert "ts_rank" in captured["sql"]


@pytest.mark.asyncio
async def test_recall_swallows_errors():
    ctx = MemoryContext(user_id="u1")
    with patch(
        "app.repositories.agent_memory_repository.read_scope",
        side_effect=RuntimeError("db down"),
    ):
        assert await recall(ctx, "anything") == []
```

- [ ] **Step 2: Run → fail** (`ModuleNotFoundError: app.services.ai.memory.agent_memory`).

Run: `cd backend && uv run pytest tests/memory/test_agent_memory_recall.py -q`

- [ ] **Step 3: Implement the repository**

```python
# backend/app/repositories/agent_memory_repository.py
"""Scoped read path for agent_memory (Phase A). The ONLY recall entry point —
the scope predicate is mandatory, so no caller can issue an unscoped query."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import text

from app.db.session import read_scope

# Ranked, scope-isolated recall. Isolation predicate:
#   own rows (any visibility) OR shared rows of a team the caller belongs to.
# search_tsv is the GENERATED tsvector; ts_rank gives BM25-like relevance.
_RECALL_SQL = text(
    """
    SELECT id, title, body_md, kind,
           ts_rank(search_tsv, websearch_to_tsquery('english', :q)) AS score
    FROM public.agent_memory
    WHERE status = 'active'
      AND search_tsv @@ websearch_to_tsquery('english', :q)
      AND (
        owner_user_id = :user_id
        OR (visibility = 'shared' AND team_id = ANY(:team_ids))
      )
    ORDER BY score DESC
    LIMIT :limit
    """
)


async def recall_rows(
    *, query: str, user_id: str, team_ids: List[int], limit: int
) -> List[Dict[str, Any]]:
    """Run the scoped recall SELECT. Returns raw mapping dicts (or [])."""
    async with read_scope() as session:
        result = await session.execute(
            _RECALL_SQL,
            {
                "q": query,
                "user_id": user_id,
                "team_ids": team_ids,
                "limit": limit,
            },
        )
        return [dict(m) for m in result.mappings().all()]


__all__ = ["recall_rows"]
```

> Implementer: confirm `app.db.session.read_scope` is the async read context manager used elsewhere (it is — the ORM repos use it). `team_id = ANY(:team_ids)` binds a Python list → asyncpg array; an empty list makes the OR branch false (correct: no team → no shared rows). Bind `user_id` as the str UUID (the column is UUID; asyncpg coerces a str UUID literal fine in a parameter — if it errors, cast `:user_id::uuid` in the SQL).

- [ ] **Step 4: Implement the service**

```python
# backend/app/services/ai/memory/agent_memory.py
"""Agent memory recall (Phase A). MemoryContext is mandatory — there is no
unscoped read path. recall() never raises (a memory miss must never break a
chat turn)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from loguru import logger

from app.repositories.agent_memory_repository import recall_rows


@dataclass(frozen=True)
class MemoryContext:
    user_id: str
    team_ids: Tuple[int, ...] = ()
    project_id: Optional[int] = None
    agent_id: Optional[str] = None
    session_id: Optional[int] = None


@dataclass(frozen=True)
class MemoryHit:
    id: int
    title: str
    body_md: str
    kind: str
    score: float


async def recall(ctx: MemoryContext, query: str, *, limit: int = 5) -> List[MemoryHit]:
    """Scope-isolated, ranked recall. Empty on blank query / no rows / any error."""
    if not query or not query.strip():
        return []
    try:
        rows = await recall_rows(
            query=query.strip(),
            user_id=ctx.user_id,
            team_ids=list(ctx.team_ids),
            limit=limit,
        )
    except Exception:  # noqa: BLE001 — recall must never break a chat turn
        logger.warning(f"[agent_memory] recall failed for user={ctx.user_id}")
        return []
    return [
        MemoryHit(
            id=int(r["id"]),
            title=str(r.get("title") or ""),
            body_md=str(r.get("body_md") or ""),
            kind=str(r.get("kind") or "fact"),
            score=float(r.get("score") or 0.0),
        )
        for r in rows
    ]


__all__ = ["MemoryContext", "MemoryHit", "recall"]
```

- [ ] **Step 5: Run → pass.** `cd backend && uv run pytest tests/memory/test_agent_memory_recall.py -q` (3 passed).

- [ ] **Step 6: Lint + commit**

```bash
cd backend && uv run black app/services/ai/memory/agent_memory.py app/repositories/agent_memory_repository.py tests/memory/test_agent_memory_recall.py && uv run isort <same> && uv run flake8 <same>
cd .. && git add backend/app/services/ai/memory/agent_memory.py backend/app/repositories/agent_memory_repository.py backend/tests/memory/test_agent_memory_recall.py
git commit -m "feat(memory): scoped agent-memory recall API (Phase A)"
```

---

### Task 3: Flag + prompt_composer wiring + chat recall

**Files:**
- Modify: `backend/app/core/config.py` (add `FEATURE_AGENT_MEMORY: bool = False`)
- Modify: `backend/app/services/ai/prompts/prompt_composer.py`
- Modify: `backend/app/services/ai/chat/ai_library_chat_wiring.py`
- Test: `backend/tests/memory/test_agent_memory_prompt_wiring.py`

**Interfaces:**
- Consumes: `recall` + `MemoryContext` (Task 2).
- Produces: `ComposerInput.agent_memory_facts: list[str]` (default `[]`); a post-cache-boundary `<agent_memory>` block rendered only when non-empty; the facts included in `_dynamic_fingerprint`. A flag-gated `_safe_recall_agent_memory(ctx, query)` in the chat wiring that returns `[]` when `FEATURE_AGENT_MEMORY` is off.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/memory/test_agent_memory_prompt_wiring.py
from unittest.mock import AsyncMock, patch

import pytest


def test_composer_renders_agent_memory_block_and_fingerprints():
    from app.services.ai.prompts.prompt_composer import ComposerInput, PromptComposer

    base = dict(
        identity_md="id", soul_md="", agent_md="", skills_xml="", workers_xml="",
        graph_facts=[], user_context=None,
    )
    composer = PromptComposer()
    out_without = composer.compose(ComposerInput(**base, agent_memory_facts=[]))
    out_with = composer.compose(
        ComposerInput(**base, agent_memory_facts=["fact: deploy runs compose up"])
    )
    assert "deploy runs compose up" in out_with.system_message
    assert "deploy runs compose up" not in out_without.system_message
    # the memory set changes the dynamic fingerprint (cache safety)
    assert out_with.dynamic_fingerprint != out_without.dynamic_fingerprint


@pytest.mark.asyncio
async def test_chat_recall_is_flag_gated_off_by_default():
    from app.services.ai.chat import ai_library_chat_wiring as wiring
    from app.services.ai.memory.agent_memory import MemoryContext

    with patch.object(wiring.settings, "FEATURE_AGENT_MEMORY", False):
        out = await wiring._safe_recall_agent_memory(
            MemoryContext(user_id="u1"), "deploy"
        )
    assert out == []
```

> Implementer: FIRST read `prompt_composer.py` to learn the REAL `ComposerInput` fields, the `compose()`/`_assemble_system_message()` signatures, the `CACHE_BOUNDARY_MARKER`, the `<graph_facts>`/`<user_context>` render helpers, and `_dynamic_fingerprint()`. Adapt the test's `ComposerInput(**base, ...)` and the `compose()` call to the real API (the field names above are from the audit but VERIFY). Mirror the existing `<graph_facts>` block exactly for the new `<agent_memory>` block (same XML-fence + system-note style, same post-boundary placement). Add `sorted(agent_memory_facts)` to `_dynamic_fingerprint` beside `graph_facts`.

- [ ] **Step 2: Run → fail.**

Run: `cd backend && uv run pytest tests/memory/test_agent_memory_prompt_wiring.py -q`

- [ ] **Step 3: Implement**

(a) `config.py`: add `FEATURE_AGENT_MEMORY: bool = False` (match the existing `FEATURE_*` flag style — env-overridable, default False).

(b) `prompt_composer.py`: add `agent_memory_facts: list[str] = field(default_factory=list)` to `ComposerInput`; in `_assemble_system_message`, after the `<graph_facts>` block, render (only when non-empty) an `<agent_memory>` block mirroring graph_facts' fence + "background data, not new user input" system note; add `sorted(input.agent_memory_facts)` to `_dynamic_fingerprint`'s hash material.

(c) `ai_library_chat_wiring.py`: add

```python
async def _safe_recall_agent_memory(ctx, query: str) -> list[str]:
    """Flag-gated agent-memory recall → rendered fact strings. Off by default;
    [] on disabled / failure (never breaks a turn)."""
    from app.core.config import settings

    if not settings.FEATURE_AGENT_MEMORY:
        return []
    try:
        from app.services.ai.memory.agent_memory import recall

        hits = await recall(ctx, query, limit=5)
        return [f"{h.kind}: {h.title} — {h.body_md}".strip() for h in hits]
    except Exception:  # noqa: BLE001
        return []
```

and call it in the existing concurrent recall gather beside `_safe_recall_graph_facts` / `_safe_recall_honcho_context`, building the `MemoryContext` from the session (user_id + resolved team_ids + project_id + agent_id + session_id). Feed the result into the composer's `agent_memory_facts`. **Because `FEATURE_AGENT_MEMORY` defaults off, this path returns `[]` and the prompt is unchanged — behavior-neutral.**

> Implementer: read how `_safe_recall_graph_facts` builds its scope (user-/project-) and how the gather + `ComposerInput` are assembled, and follow that exactly. For `team_ids`, reuse the team resolution already present (`_resolve_team_workspace` resolves a single team; for Phase A pass `(team_id,)` when present else `()`).

- [ ] **Step 4: Run → pass** (2 passed). Then run the existing composer + wiring tests to prove behavior-neutral:

Run: `cd backend && uv run pytest tests/memory/ tests/ -k "composer or prompt or chat_wiring or inject or graph_facts" -q`
Expected: all pass (the new block is off by default + empty list renders nothing).

- [ ] **Step 5: Lint + commit**

```bash
cd backend && uv run black app/core/config.py app/services/ai/prompts/prompt_composer.py app/services/ai/chat/ai_library_chat_wiring.py tests/memory/test_agent_memory_prompt_wiring.py && uv run isort <same> && uv run flake8 <same>
cd .. && git add backend/app/core/config.py backend/app/services/ai/prompts/prompt_composer.py backend/app/services/ai/chat/ai_library_chat_wiring.py backend/tests/memory/test_agent_memory_prompt_wiring.py
git commit -m "feat(memory): flag-gated agent-memory recall in prompt composer (Phase A)"
```

---

### Task 4: Regression + PR

**Files:** none.

- [ ] **Step 1: Regression**

Run: `cd backend && uv run pytest tests/ -k "memory or composer or prompt or chat or honcho or graph" -q`
Expected: PASS (new + all unchanged memory/prompt tests — proves flag-off neutrality).

- [ ] **Step 2: Lint full set**

Run black --check / isort --check-only / flake8 over all changed `.py`.

- [ ] **Step 3: PR**

```bash
git push -u origin feature/agent-memory-phase-a
gh pr create --base master --head feature/agent-memory-phase-a \
  --title "feat(memory): agent-memory layer Phase A — scoped recall infra (flag-off)" \
  --body "Phase A of the agent-memory layer (spec: docs/superpowers/specs/2026-06-26-agent-memory-layer-design.md). Adds the scoped read path: agent_memory table (multi-tenant scope cols + tsvector FTS + RLS), a mandatory-scoped recall() API (own private OR my-team shared), and flag-gated prompt-composer injection. FEATURE_AGENT_MEMORY defaults OFF + no writers yet → recall empty → behavior-neutral. Proves the isolation model; Phase B (/dream consolidation, private-only), C (promotion gate), D (/distill→skills) build on this."
```

---

## Self-Review

**Spec coverage (Phase A subset):** scoped table + RLS (Task 1) ✓; mandatory-scoped recall, no unscoped path (Task 2) ✓; flag-gated prompt injection + cache-safe fingerprint (Task 3) ✓; behavior-neutral via default-off flag + empty table (all tasks) ✓. Phase B/C/D explicitly out.

**Placeholder scan:** none. The two "verify against real API" notes (prompt_composer fields; read_scope) carry explicit read-first instructions, not placeholders.

**Type consistency:** `MemoryContext{user_id, team_ids, project_id, agent_id, session_id}` (Task 2) is what Task 3's wiring constructs. `MemoryHit{id,title,body_md,kind,score}` matches `recall_rows` mapping keys and the table columns (Task 1). `agent_memory_facts: list[str]` is consistent across ComposerInput (Task 3), the fingerprint, and the wiring's rendered strings. The RLS predicate (owner OR shared+team) matches the `recall_rows` SQL WHERE exactly — the two isolation guards agree.
