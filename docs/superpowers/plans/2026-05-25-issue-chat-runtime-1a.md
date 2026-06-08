# Issue Chat Runtime — Spec-1a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) tracking.

**Goal:** An issue with an assigned agent runs through the **full AI chat runtime** (session + memory + 2-layer compaction + sub-agents + delegation + budget + fallback), backed by an `ai_session`, with the issue's conversation stored in `ai_messages`. Dispatch-only (reply trigger = Spec-1b).

**Architecture:** `issue.ai_session_id` → one `ai_session`. `execute_issue` (dispatch) creates/loads that session and runs the same turn flow `chat()` runs, via a shared `run_session_turn` extracted from `AILibraryChatService.chat`. The agent's output is persisted as an `ai_messages` row by the chat flow (no mig-208 trigger needed). The issue chat UI reads the session's `ai_messages`.

**Tech Stack:** Python 3.12 / FastAPI / DBOS; `AILibraryChatService` + `build_agent_runner_stack` + `RunRecorder`; React/TS frontend. Spec: `docs/superpowers/specs/2026-05-25-issue-chat-runtime-design.md`. Eng-review: split into 1a (this) + 1b (reply).

**Scope (1a):** session link, get-or-create, `run_session_turn` extraction (+chat regression), execute_issue rewire, messages endpoint → ai_messages, frontend adapter.
**NOT in 1a:** reply-triggered turns (1b), liveness auto-status (Spec-2), migrating old `issue_messages` rows (left as-is — issues were never really executed, no real conversations to preserve), monitor/recovery.

---

## Task 1: Migration — issues.ai_session_id

**Files:** Create `supabase/migrations/224_issues_ai_session_id.sql`

- [ ] **Step 1: Write the migration**
```sql
-- 224_issues_ai_session_id.sql
-- Spec-1a: back an issue's agent conversation with an ai_session so issue
-- execution runs on the full chat runtime (memory / compaction / sub-agents).
-- Nullable: only set once an agent conversation starts for the issue.
ALTER TABLE public.issues
  ADD COLUMN IF NOT EXISTS ai_session_id UUID
  REFERENCES public.ai_sessions(id) ON DELETE SET NULL;
```
- [ ] **Step 2: Verify SQL parses** (psql dry-run not required here; apply happens post-merge). Confirm `ai_sessions` is the correct referenced table + `id` UUID PK (mig 121/138).
- [ ] **Step 3: Commit**
```bash
git add supabase/migrations/224_issues_ai_session_id.sql
git commit -m "feat(issues): add issues.ai_session_id (Spec-1a)"
```

## Task 2: get_or_create_issue_session

**Files:** Create `backend/app/services/issues/issue_session.py`; Test `backend/tests/test_issue_session.py`

- [ ] **Step 1: Failing test**
```python
"""get_or_create_issue_session returns the issue's session, creating one on
first call and reusing it after."""
from __future__ import annotations
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import pytest


async def test_returns_existing_when_issue_has_session(monkeypatch):
    from app.services.issues import issue_session as m
    sid = str(uuid4())

    async def fake_fetch_one(sql, params=None):
        return {"ai_session_id": sid, "title": "t", "assignee_agent_id": str(uuid4()),
                "created_by_user_id": str(uuid4())}

    with patch("app.db.engine.fetch_one", fake_fetch_one):
        got = await m.get_or_create_issue_session(409)
    assert got == sid


async def test_creates_and_backfills_when_absent(monkeypatch):
    from app.services.issues import issue_session as m
    agent_uuid, user_uuid = str(uuid4()), str(uuid4())

    async def fake_fetch_one(sql, params=None):
        return {"ai_session_id": None, "title": "Write essay",
                "assignee_agent_id": agent_uuid, "created_by_user_id": user_uuid}

    created = {}
    chat_svc = AsyncMock()
    chat_svc.create_session = AsyncMock(return_value={"id": "new-sess"})
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat_svc)
    agent_repo = AsyncMock()
    agent_repo.get_by_id = AsyncMock(return_value={"slug": "writer"})
    monkeypatch.setattr(m, "AgentRepository", lambda: agent_repo)

    async def fake_execute(sql, params=None):
        created["sql"] = sql; created["params"] = params; return 1

    with patch("app.db.engine.fetch_one", fake_fetch_one), \
         patch("app.db.engine.execute", fake_execute):
        got = await m.get_or_create_issue_session(409)

    assert got == "new-sess"
    # backfilled onto the issue
    assert "UPDATE public.issues" in created["sql"]
    assert created["params"]["sid"] == "new-sess" and created["params"]["id"] == 409
```
Run `uv run pytest tests/test_issue_session.py -q` → FAIL (module missing). Confirm.

- [ ] **Step 2: Implement** `issue_session.py`:
```python
"""Get-or-create the ai_session backing an issue's agent conversation (Spec-1a)."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from loguru import logger

from app.repositories.agent_repository import AgentRepository
from app.services.ai.chat.ai_library_chat_service import AILibraryChatService


async def get_or_create_issue_session(issue_id: int) -> Optional[str]:
    """Return the issue's ai_session_id, creating + backfilling one if absent.
    Returns None if the issue has no assignee_agent_id (nothing to run)."""
    from app.db import engine as db_engine

    row = await db_engine.fetch_one(
        "SELECT ai_session_id, title, assignee_agent_id, created_by_user_id, "
        "assignee_user_id FROM public.issues WHERE id = :id",
        {"id": issue_id},
    )
    if not row:
        raise RuntimeError(f"issue {issue_id} not found")
    if row.get("ai_session_id"):
        return str(row["ai_session_id"])

    agent_id = row.get("assignee_agent_id")
    user_id = row.get("created_by_user_id") or row.get("assignee_user_id")
    if not agent_id or not user_id:
        return None

    agent = await AgentRepository().get_by_id(UUID(str(agent_id)))
    if not agent:
        raise RuntimeError(f"assignee agent {agent_id} not found")

    session = await AILibraryChatService().create_session(
        user_id=str(user_id),
        agent_slug=agent["slug"],
        title=(row.get("title") or "Issue")[:200],
        context_type="issue",
        context_id=str(issue_id),
    )
    session_id = str(session["id"])
    # Backfill. Guard on ai_session_id IS NULL so a concurrent create loses
    # cleanly (the second writer's UPDATE matches 0 rows; caller re-reads).
    n = await db_engine.execute(
        "UPDATE public.issues SET ai_session_id = :sid "
        "WHERE id = :id AND ai_session_id IS NULL",
        {"sid": session_id, "id": issue_id},
    )
    if n == 0:
        # Lost the race — return whatever landed.
        winner = await db_engine.fetch_one(
            "SELECT ai_session_id FROM public.issues WHERE id = :id", {"id": issue_id}
        )
        return str(winner["ai_session_id"]) if winner and winner.get("ai_session_id") else session_id
    logger.info(f"[issue_session] created session {session_id} for issue {issue_id}")
    return session_id
```
VERIFY against the real `AILibraryChatService.create_session` signature (lines ~53) — match its kwargs (`user_id`, `agent_slug`, `title`, `context_type`, `context_id`, optional `project_id`/`team_id`). Adapt if they differ.

- [ ] **Step 3:** `uv run pytest tests/test_issue_session.py -q` → PASS. `uv run python -c "import app.services.issues.issue_session"` → OK.
- [ ] **Step 4: Commit** `feat(issues): get_or_create_issue_session (Spec-1a)`

## Task 3: Extract run_session_turn from chat() (CRITICAL — regression-guarded)

**Files:** Modify `backend/app/services/ai/chat/ai_library_chat_service.py`; Test `backend/tests/test_run_session_turn.py`

**Context:** `chat()` (line ~318) does: get_session(404 guard) → get_messages(history) → insert user ai_message → build_agent_runner_stack → compose → RunRecorder+run_turn → `_maybe_compact` → persist assistant ai_message → bump session counters → fire-and-forget session_memory + commitment harvest → return `{assistant_message, run_id, usage, tool_calls}`. We extract the body into `run_session_turn` so issues reuse it; `chat()` delegates.

- [ ] **Step 1: Regression test FIRST (chat must not change)**
```python
"""run_session_turn is the shared turn core; chat() delegates to it unchanged."""
from __future__ import annotations
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import pytest


async def test_chat_delegates_to_run_session_turn(monkeypatch):
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService
    svc = AILibraryChatService()
    # session has an agent_slug
    svc.get_session = AsyncMock(return_value={"agent_slug": "writer"})
    sentinel = {"assistant_message": {"role": "assistant", "content": "hi"},
                "run_id": "r1", "usage": {}, "tool_calls": []}
    svc.run_session_turn = AsyncMock(return_value=sentinel)
    sid, uid = uuid4(), uuid4()
    out = await svc.chat(sid, user_id=uid, content="hello")
    svc.run_session_turn.assert_awaited_once()
    call = svc.run_session_turn.await_args
    assert call.kwargs.get("trigger", "chat") == "chat" or "chat" in str(call)
    assert out == sentinel
```
Run → FAIL (`run_session_turn` doesn't exist). Confirm.

- [ ] **Step 2: Extract.** Read `chat()` in full. Move everything AFTER the `get_session`+`agent_slug` guard (the user-message insert through the return) into a new method:
```python
async def run_session_turn(
    self, session_id, user_id, content, *, trigger: str = "chat", **kwargs
) -> dict:
    """Shared turn core: persist user msg → recall+compose+run (full stack)
    → persist assistant msg → bump counters → fire-and-forget session_memory
    + commitments. Returns {assistant_message, run_id, usage, tool_calls}.
    The RunRecorder uses `trigger` (chat | issue_dispatch | issue_reply ...)."""
    # <moved body of chat(), with RunRecorder(trigger=trigger, ...)>
```
Then `chat()` becomes:
```python
async def chat(self, session_id, user_id, content, ...):
    session = await self.get_session(session_id, user_id=user_id)
    if not session.get("agent_slug"):
        raise HTTPException(status_code=400, detail="session has no agent_slug bound — cannot chat")
    return await self.run_session_turn(session_id, user_id, content, trigger="chat")
```
Keep the `streaming` (`chat_stream`) path as-is (out of scope; it can extract later). Pass through any kwargs `chat()` accepted that the body uses (e.g. project/team context) so behavior is identical. The `RunRecorder` `trigger` was hardcoded `"chat"` — now `trigger`.

- [ ] **Step 3:** Regression test PASS. Then run the FULL existing chat test suite: `uv run pytest tests/ -k "chat or ai_library or library_chat" -q` → all prior chat tests still pass (this is the IRON-RULE regression gate — chat behavior unchanged).
- [ ] **Step 4: Commit** `refactor(chat): extract run_session_turn from chat() for reuse (Spec-1a)`

## Task 4: execute_issue runs the session turn

**Files:** Modify `backend/app/services/issues/issue_agent_executor.py` + `backend/app/workflows/issue_lifecycle.py`; update `backend/tests/test_issue_agent_executor.py`

- [ ] **Step 1: Update the executor test** — `run_issue_agent` now: get-or-create session → `run_session_turn(session_id, user_id, content=issue task, trigger='issue_dispatch')`. Replace the old compose/AgentRunner mocks with:
```python
async def test_run_issue_agent_runs_session_turn(monkeypatch):
    from app.services.issues import issue_agent_executor as m
    monkeypatch.setattr(m, "get_or_create_issue_session", AsyncMock(return_value="sess-1"))
    chat_svc = AsyncMock()
    chat_svc.run_session_turn = AsyncMock(return_value={"assistant_message": {"content": "essay"}, "run_id": "r"})
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat_svc)
    out = await m.run_issue_agent(issue={"id": 409, "title": "写", "description": "春"},
                                  agent_id="a", user_id="u")
    chat_svc.run_session_turn.assert_awaited_once()
    kw = chat_svc.run_session_turn.await_args
    assert kw.kwargs["trigger"] == "issue_dispatch"
    joined = str(kw)
    assert "写" in joined  # issue task in content
```
(Drop the now-obsolete adapter/compose tests; keep `_build_user_message` test if present.)

- [ ] **Step 2: Rewrite `run_issue_agent`:**
```python
async def run_issue_agent(*, issue, agent_id, user_id):
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService
    from app.services.issues.issue_session import get_or_create_issue_session

    session_id = await get_or_create_issue_session(int(issue["id"]))
    if not session_id:
        raise RuntimeError(f"issue {issue['id']} has no assignable agent session")
    result = await AILibraryChatService().run_session_turn(
        session_id, user_id, _build_user_message(issue), trigger="issue_dispatch"
    )
    return (result.get("assistant_message") or {}).get("content") or ""
```
Remove the old `_build_runner` / `_load_user_providers` (the chat stack handles adapter/BYO/memory/sub-agents now). Keep `_build_user_message`.
- `issue_lifecycle.run_issue_agent_step` is unchanged (still calls `run_issue_agent`); `execute_issue` still sets `in_review` on success.

- [ ] **Step 3:** `uv run pytest tests/test_issue_agent_executor.py tests/test_issue_lifecycle_sql.py -q` → PASS. Import smoke the chain.
- [ ] **Step 4: Commit** `feat(issues): execute issues on the chat runtime via run_session_turn (Spec-1a)`

## Task 5: Issue messages endpoint → ai_messages

**Files:** Modify `backend/app/api/issue_messages_router.py` (`list_issue_messages`); Test `backend/tests/test_issue_messages_endpoint.py`

- [ ] **Step 1: Failing test** — `GET /issues/{id}/messages` returns the issue session's `ai_messages` mapped to the `IssueMessage` UI shape (id, kind, author, body, created_at). Mock the issue→session lookup + ai_messages fetch; assert mapping (assistant ai_message → kind='agent_run'/author_agent; user ai_message → kind='comment'; system → status).
- [ ] **Step 2: Implement** — `list_issue_messages(issue_id)`: read `issues.ai_session_id`; if set, fetch that session's `ai_messages` (ordered created_at ASC) and map each to the `IssueMessage` response shape the frontend expects (role→kind/author, content→body, metadata_json status→from/to_status). If `ai_session_id` is null (issue never dispatched), fall back to the legacy `issue_messages` read (so old issues still render). Document the mapping inline.
- [ ] **Step 3:** tests PASS; `uv run pytest tests/ -k "issue_messages or issue_endpoint" -q`.
- [ ] **Step 4: Commit** `feat(issues): issue messages endpoint reads the session's ai_messages (Spec-1a)`

## Task 6: Frontend adapter

**Files:** Modify `frontend/components/Todolist/IssueChatThread.tsx` / `IssueDetailView.tsx` / the messages-fetch in `frontend/services/issuesService.ts` (find the exact fetch first)

- [ ] **Step 1:** Read how the frontend currently fetches + renders issue messages (the `IssueMessage` type + the GET call). Confirm the Task-5 response keeps the SAME shape (so the frontend needs no change) OR adapt the mapper. Goal: the Todolist issue chat shows the agent's `ai_messages`-backed conversation.
- [ ] **Step 2:** If the response shape is preserved (Task 5 maps ai_messages → existing `IssueMessage`), no frontend change beyond verification. If not, update the adapter/type minimally.
- [ ] **Step 3:** `cd frontend && npm run build` → ✓ built, 0 TS errors. Manually confirm the type alignment.
- [ ] **Step 4: Commit** (only if files changed) `feat(todolist): render issue chat from session ai_messages (Spec-1a)`

---

## Final verification
- [ ] `cd backend && uv run pytest tests/ -q` → all pass (incl. the chat regression suite + new issue tests).
- [ ] `cd frontend && npm run build` → ✓.
- [ ] Chain import: `execute_issue → run_issue_agent → get_or_create_issue_session → run_session_turn`.
- [ ] Post-merge prod (needs public repo): apply migration 224; dispatch the MH-1 issue → agent runs on the full stack (memory recall + sub-agents available); response lands in the issue chat (ai_messages); status → in_review.

## Self-Review
**Spec coverage:** session link (T1), get-or-create (T2), run_session_turn extraction + chat regression (T3), execute_issue rewire (T4), messages endpoint → ai_messages (T5), frontend (T6). All 1a spec components covered. Reply (1b) + liveness (Spec-2) excluded.
**Placeholders:** none. T6 has a "verify shape, adapt if needed" branch — that's read-then-adapt (the frontend fetch must be read live), not a placeholder.
**Type consistency:** `run_session_turn(session_id, user_id, content, *, trigger)` used identically in T3 (def), T4 (issue call), chat() delegate. `get_or_create_issue_session(issue_id)->str|None` in T2/T4.
**Risk (from eng-review):** T3 extraction is the high-risk item — guarded by the chat-delegates regression test + the full chat suite gate (Step 3). T4 drops the stripped executor's BYO/adapter code because the chat stack owns it now (get_adapter_for_user is inside build_agent_runner_stack's adapter wiring — confirm during T4 that the chat stack resolves BYO for the agent's model; if not, that's a real finding to surface).
