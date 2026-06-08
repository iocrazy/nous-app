# Issue Chat Runtime — Spec-1b (reply-triggered turns) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A human reply on an issue that has an assigned agent triggers another agent turn on the issue's existing `ai_session`, serialized one-at-a-time per issue, without changing the issue's status.

**Architecture:** Reuse Spec-1a's `ai_session` + `AILibraryChatService.run_session_turn`. A new DBOS workflow `respond_to_issue_reply(issue_id, user_id, reply_text)` carries the reply text as durable workflow input, acquires a per-issue **turn lock** (`issues.execution_locked_at`, the same column `execute_issue` uses, so dispatch and reply turns mutually exclude), runs one turn (`trigger="issue_reply"`), then releases. `post_issue_message` (the existing POST endpoint) routes session-backed issues to this workflow instead of the legacy `issue_messages` placeholder. The reply turn does NOT touch issue status (that is Spec-2).

**Tech Stack:** Python 3.12, FastAPI, DBOS (`@DBOS.workflow` / `@DBOS.step` / `DBOS.start_workflow` / `DBOS.sleep`), SQLAlchemy async engine (`app.db.engine`), Supabase admin client, pytest.

**Design source:** `docs/superpowers/specs/2026-05-25-issue-chat-runtime-design.md` Component 5. Spec-1a (#345) already shipped: `issues.ai_session_id`, `get_or_create_issue_session`, `run_session_turn`, messages endpoint reads `ai_messages`.

---

## Decisions locked (read before coding)

1. **Serialization = strict one-turn-per-issue via a CAS turn lock + bounded `DBOS.sleep` wait.** Each reply gets its own turn (responds to that reply's text). A reply arriving while a turn runs **waits** for the lock (durable, reply text in workflow input), then runs. If the lock can't be acquired within the cap (`MAX_LOCK_ATTEMPTS × LOCK_WAIT_SECONDS = 20 × 6s = 120s`), the reply turn **defers** (logged; text is still durable in DBOS input, user can re-send). Coalescing/rerun-flag is a documented future upgrade, NOT in this spec.
2. **Reuse `issues.execution_locked_at` as the turn lock** so reply turns and `execute_issue` dispatch mutually exclude on the same `ai_session`. Use a **dedicated** `acquire_turn_lock` step that sets ONLY `execution_locked_at` (NOT `dbos_workflow_id`, which the dispatch-status UI subscribes to). Release via the existing `clear_lock` step.
3. **No migration, no chat-service change.** `run_session_turn(content=reply_text, trigger="issue_reply")` appends the user message and runs — same as dispatch but with the reply text instead of the issue title.
4. **Agent self-comments are excluded by construction** — `post_issue_message` is a human-authenticated endpoint; agents never call it (their replies are written as `role="assistant"` ai_messages by `run_session_turn`). We additionally gate the trigger on `issue.assignee_agent_id` being set.
5. **`user_id` for the turn = the issue's owner** (`created_by_user_id` or `assignee_user_id`), mirroring `get_or_create_issue_session`, so BYO-key/adapter resolution uses the agent owner's keys. The replying human's identity is not separately threaded (single-owner-issue assumption; documented limitation).
6. **POST response comment is synthesized** for optimistic display; the canonical thread is the GET (`ai_messages`) path from Spec-1a. The frontend already refetches; Task 4 verifies it replaces (no dup).
7. **No status change** on a reply turn (Spec-1b constraint). `execute_issue`'s status machine is untouched.

---

## File Structure

- **Modify** `backend/app/workflows/issue_lifecycle.py` — add `acquire_turn_lock` step, `run_issue_reply_step` step, `ensure_issue_session_step` step, and `respond_to_issue_reply` workflow. (Reuses existing `clear_lock`.)
- **Modify** `backend/app/api/issue_messages_router.py` — `post_issue_message` gains a session-path branch that dispatches `respond_to_issue_reply`.
- **Create** `backend/tests/test_issue_reply_workflow.py` — unit tests for the new steps + workflow lock logic.
- **Create** `backend/tests/test_post_issue_message_reply.py` — unit tests for the endpoint routing.
- **Verify (likely no change)** `frontend/components/Todolist/IssueChatThread.tsx` / `IssueDetailView.tsx` / `frontend/services/issueMessageService.ts` — confirm reply POST + refetch renders the agent's turn.

---

## Task 1: Turn-lock + reply steps in issue_lifecycle.py

**Files:**
- Modify: `backend/app/workflows/issue_lifecycle.py`
- Test: `backend/tests/test_issue_reply_workflow.py`

- [ ] **Step 1: Write the failing test for `acquire_turn_lock` + `run_issue_reply_step`**

Create `backend/tests/test_issue_reply_workflow.py`:

```python
"""Spec-1b: reply-turn steps + workflow lock logic."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_run_issue_reply_step_calls_run_session_turn(monkeypatch):
    from app.workflows import issue_lifecycle as m

    fake_chat = AsyncMock()
    fake_chat.run_session_turn = AsyncMock(
        return_value={"assistant_message": {"content": "ok"}, "run_id": "r1"}
    )
    monkeypatch.setattr(m, "AILibraryChatService", lambda: fake_chat)

    out = await m.run_issue_reply_step.__wrapped__(
        session_id="11111111-1111-1111-1111-111111111111",
        user_id="22222222-2222-2222-2222-222222222222",
        reply_text="please continue",
    )

    assert out == "ok"
    kwargs = fake_chat.run_session_turn.call_args.kwargs
    assert kwargs["content"] == "please continue"
    assert kwargs["trigger"] == "issue_reply"


@pytest.mark.asyncio
async def test_acquire_turn_lock_true_when_free(monkeypatch):
    from app.workflows import issue_lifecycle as m

    fake_engine = AsyncMock()
    fake_engine.execute = AsyncMock(return_value=1)  # 1 row updated → acquired
    monkeypatch.setattr(m, "_engine", lambda: fake_engine, raising=False)

    got = await m.acquire_turn_lock.__wrapped__(99)
    assert got is True
    sql = fake_engine.execute.call_args.args[0]
    assert "execution_locked_at = now()" in sql
    assert "dbos_workflow_id" not in sql  # decision #2: do not touch wf id


@pytest.mark.asyncio
async def test_acquire_turn_lock_false_when_held(monkeypatch):
    from app.workflows import issue_lifecycle as m

    fake_engine = AsyncMock()
    fake_engine.execute = AsyncMock(return_value=0)  # 0 rows → already locked
    monkeypatch.setattr(m, "_engine", lambda: fake_engine, raising=False)

    assert await m.acquire_turn_lock.__wrapped__(99) is False
```

> Note: `__wrapped__` calls the underlying async function past the `@DBOS.step` decorator. `acquire_turn_lock` reads the engine via a tiny `_engine()` indirection so the test can monkeypatch it.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/test_issue_reply_workflow.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'run_issue_reply_step'` / `acquire_turn_lock` / `_engine`.

- [ ] **Step 3: Implement the steps + helper in `issue_lifecycle.py`**

Add near the top (after the existing imports) a tiny engine indirection so tests can patch it:

```python
def _engine():
    from app.db import engine as db_engine

    return db_engine
```

Add these steps after `clear_lock` (reuse `clear_lock` for release):

```python
@DBOS.step()
async def acquire_turn_lock(issue_id: int) -> bool:
    """Claim the per-issue turn lock for a reply turn. Reuses
    issues.execution_locked_at (shared with execute_issue dispatch) but does
    NOT touch dbos_workflow_id — the dispatch-status UI subscribes to that.
    Returns True if acquired, False if a turn is already in flight."""
    locked = await _engine().execute(
        "UPDATE public.issues SET execution_locked_at = now() "
        "WHERE id = :id AND execution_locked_at IS NULL",
        {"id": issue_id},
    )
    return locked > 0


@DBOS.step()
async def ensure_issue_session_step(issue_id: int) -> str:
    """Get-or-create the issue's ai_session; raise if the issue has no
    assignable agent (nothing to respond with)."""
    from app.services.issues.issue_session import get_or_create_issue_session

    session_id = await get_or_create_issue_session(issue_id)
    if not session_id:
        raise RuntimeError(f"issue {issue_id} has no assignable agent session")
    return session_id


@DBOS.step(retries_allowed=True, max_attempts=2)
async def run_issue_reply_step(
    *, session_id: str, user_id: str, reply_text: str
) -> Optional[str]:
    """Run one reply turn on the issue's session via the full chat runtime."""
    from uuid import UUID

    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    result = await AILibraryChatService().run_session_turn(
        UUID(session_id),
        user_id=UUID(user_id),
        content=reply_text,
        trigger="issue_reply",
    )
    return (result.get("assistant_message") or {}).get("content") or ""
```

Add the import for `AILibraryChatService` reference used by the monkeypatch (so `m.AILibraryChatService` exists at module scope). At the top of the module add:

```python
from app.services.ai.chat.ai_library_chat_service import AILibraryChatService  # noqa: F401  (module-level handle for run_issue_reply_step + tests)
```

Then change `run_issue_reply_step` to use the module-level name instead of the local import:

```python
@DBOS.step(retries_allowed=True, max_attempts=2)
async def run_issue_reply_step(
    *, session_id: str, user_id: str, reply_text: str
) -> Optional[str]:
    """Run one reply turn on the issue's session via the full chat runtime."""
    from uuid import UUID

    result = await AILibraryChatService().run_session_turn(
        UUID(session_id),
        user_id=UUID(user_id),
        content=reply_text,
        trigger="issue_reply",
    )
    return (result.get("assistant_message") or {}).get("content") or ""
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/test_issue_reply_workflow.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/workflows/issue_lifecycle.py backend/tests/test_issue_reply_workflow.py
git commit -m "feat(issues): turn-lock + reply turn steps for Spec-1b"
```

---

## Task 2: `respond_to_issue_reply` workflow

**Files:**
- Modify: `backend/app/workflows/issue_lifecycle.py`
- Test: `backend/tests/test_issue_reply_workflow.py`

- [ ] **Step 1: Write the failing test for the workflow body (lock acquired path + deferred path)**

The workflow orchestrates steps + `DBOS.sleep`. Extract the orchestration into a plain async helper `_run_reply_turns(issue_id, user_id, reply_text, *, acquire, run_turn, release, sleep)` so it is unit-testable without a live DBOS runtime; the `@DBOS.workflow` wraps it with the real step callables.

Append to `backend/tests/test_issue_reply_workflow.py`:

```python
@pytest.mark.asyncio
async def test_run_reply_turns_acquires_runs_releases():
    from app.workflows import issue_lifecycle as m

    calls = {"run": 0, "release": 0}
    acquire = AsyncMock(return_value=True)

    async def run_turn(**kw):
        calls["run"] += 1
        return "done"

    async def release(_id):
        calls["release"] += 1

    async def sleep(_s):
        raise AssertionError("should not sleep when lock is free")

    out = await m._run_reply_turns(
        7, "u", "hi",
        session_id="s",
        acquire=acquire, run_turn=run_turn, release=release, sleep=sleep,
    )

    assert out["executed"] is True
    assert calls == {"run": 1, "release": 1}
    acquire.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_run_reply_turns_waits_then_defers():
    from app.workflows import issue_lifecycle as m

    acquire = AsyncMock(return_value=False)  # never free
    slept = {"n": 0}

    async def run_turn(**kw):
        raise AssertionError("must not run when lock never acquired")

    async def release(_id):
        raise AssertionError("must not release a lock we never held")

    async def sleep(_s):
        slept["n"] += 1

    out = await m._run_reply_turns(
        7, "u", "hi",
        session_id="s",
        acquire=acquire, run_turn=run_turn, release=release, sleep=sleep,
        max_attempts=3, wait_seconds=1,
    )

    assert out["deferred"] is True
    assert slept["n"] == 3  # waited between each failed attempt
    assert acquire.await_count == 3


@pytest.mark.asyncio
async def test_run_reply_turns_releases_even_on_error():
    from app.workflows import issue_lifecycle as m

    acquire = AsyncMock(return_value=True)
    released = {"n": 0}

    async def run_turn(**kw):
        raise RuntimeError("llm blew up")

    async def release(_id):
        released["n"] += 1

    async def sleep(_s):
        pass

    with pytest.raises(RuntimeError, match="llm blew up"):
        await m._run_reply_turns(
            7, "u", "hi",
            session_id="s",
            acquire=acquire, run_turn=run_turn, release=release, sleep=sleep,
        )
    assert released["n"] == 1  # finally released
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_issue_reply_workflow.py -k run_reply_turns -v`
Expected: FAIL — `_run_reply_turns` does not exist.

- [ ] **Step 3: Implement `_run_reply_turns` + the `respond_to_issue_reply` workflow**

Add to `issue_lifecycle.py`:

```python
# Spec-1b: bounded wait for the per-issue turn lock. Replies are human-paced,
# so a turn almost always frees the lock within seconds; the cap only bounds
# the pathological "reply lands during a multi-minute turn" case.
REPLY_LOCK_MAX_ATTEMPTS = 20
REPLY_LOCK_WAIT_SECONDS = 6


async def _run_reply_turns(
    issue_id: int,
    user_id: str,
    reply_text: str,
    *,
    session_id: str,
    acquire,
    run_turn,
    release,
    sleep,
    max_attempts: int = REPLY_LOCK_MAX_ATTEMPTS,
    wait_seconds: int = REPLY_LOCK_WAIT_SECONDS,
) -> dict[str, Any]:
    """Acquire the per-issue turn lock (waiting if a turn is in flight), run
    exactly one reply turn, then release. No status change (Spec-1b)."""
    acquired = False
    for _ in range(max_attempts):
        if await acquire(issue_id):
            acquired = True
            break
        await sleep(wait_seconds)

    if not acquired:
        logger.warning(
            f"[issue_reply] issue {issue_id}: turn lock busy after "
            f"{max_attempts} attempts; deferring reply (text durable in input)"
        )
        return {"issue_id": issue_id, "deferred": True}

    try:
        await run_turn(session_id=session_id, user_id=user_id, reply_text=reply_text)
        return {"issue_id": issue_id, "executed": True}
    finally:
        await release(issue_id)


@DBOS.workflow()
async def respond_to_issue_reply(
    issue_id: int, user_id: str, reply_text: str
) -> dict[str, Any]:
    """Spec-1b: run one agent turn in response to a human reply on an issue.
    Serialized per issue via the turn lock; does NOT change issue status."""
    session_id = await ensure_issue_session_step(issue_id)
    return await _run_reply_turns(
        issue_id,
        user_id,
        reply_text,
        session_id=session_id,
        acquire=acquire_turn_lock,
        run_turn=run_issue_reply_step,
        release=clear_lock,
        sleep=DBOS.sleep,
    )
```

> `run_issue_reply_step` is called as `run_turn(session_id=..., user_id=..., reply_text=...)` — its signature is keyword-only and matches.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_issue_reply_workflow.py -v`
Expected: 6 passed (3 from Task 1 + 3 here).

- [ ] **Step 5: Commit**

```bash
git add backend/app/workflows/issue_lifecycle.py backend/tests/test_issue_reply_workflow.py
git commit -m "feat(issues): respond_to_issue_reply workflow (Spec-1b)"
```

---

## Task 3: Wire `post_issue_message` to dispatch the reply turn

**Files:**
- Modify: `backend/app/api/issue_messages_router.py:213-300` (`post_issue_message`)
- Test: `backend/tests/test_post_issue_message_reply.py`

- [ ] **Step 1: Write the failing test for the session-path branch**

Create `backend/tests/test_post_issue_message_reply.py`:

```python
"""Spec-1b: post_issue_message routes session-backed issues to a reply turn."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.schemas.issue_message import IssueMessageKind, IssueMessagePost


@pytest.mark.asyncio
async def test_session_issue_dispatches_reply_workflow(monkeypatch):
    from app.api import issue_messages_router as r

    issue_row = {
        "id": 5,
        "assignee_agent_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "created_by_user_id": "11111111-1111-1111-1111-111111111111",
        "assignee_user_id": None,
    }
    monkeypatch.setattr(
        r, "_assert_issue_visible", AsyncMock(return_value=issue_row)
    )
    monkeypatch.setattr(
        r, "get_or_create_issue_session", AsyncMock(return_value="sess-5")
    )

    started = {}

    def fake_start_workflow(fn, *args):
        started["fn"] = fn.__name__
        started["args"] = args

    auth = SimpleNamespace(user_id=UUID("11111111-1111-1111-1111-111111111111"))

    with patch.object(r, "DBOS", SimpleNamespace(start_workflow=fake_start_workflow)), \
         patch.object(r, "SetWorkflowID", lambda *_a, **_k: _nullctx()):
        resp = await r.post_issue_message(
            5, IssueMessagePost(body="please continue"), auth
        )

    assert started["fn"] == "respond_to_issue_reply"
    assert started["args"][0] == 5  # issue_id
    assert started["args"][1] == "11111111-1111-1111-1111-111111111111"  # owner
    assert started["args"][2] == "please continue"  # reply_text
    assert resp.comment.kind == IssueMessageKind.COMMENT
    assert resp.comment.body == "please continue"
    assert resp.agent_run is None


@pytest.mark.asyncio
async def test_legacy_issue_still_inserts_issue_messages(monkeypatch):
    from app.api import issue_messages_router as r

    issue_row = {"id": 6, "assignee_agent_id": None}
    monkeypatch.setattr(
        r, "_assert_issue_visible", AsyncMock(return_value=issue_row)
    )

    inserted = {"row": None}

    class _Tbl:
        def insert(self, row):
            inserted["row"] = row
            return self

        async def execute(self):
            return SimpleNamespace(
                data=[{
                    "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                    "issue_id": 6, "kind": "comment",
                    "author_user_id": "11111111-1111-1111-1111-111111111111",
                    "body": "hi", "meta": {},
                    "created_at": "2026-05-25T00:00:00+00:00",
                }]
            )

    fake_sb = MagicMock()
    fake_sb.table = MagicMock(return_value=_Tbl())
    monkeypatch.setattr(r, "get_async_supabase_admin", AsyncMock(return_value=fake_sb))

    auth = SimpleNamespace(user_id=UUID("11111111-1111-1111-1111-111111111111"))
    resp = await r.post_issue_message(6, IssueMessagePost(body="hi"), auth)

    assert inserted["row"]["kind"] == "comment"
    assert resp.comment.body == "hi"


def _nullctx():
    import contextlib

    return contextlib.nullcontext()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_post_issue_message_reply.py -v`
Expected: FAIL — current `post_issue_message` always inserts into `issue_messages`; `r.DBOS` / `r.SetWorkflowID` / `r.get_or_create_issue_session` don't exist yet.

- [ ] **Step 3: Rewrite `post_issue_message` with the session-path branch**

Add imports near the top of `issue_messages_router.py`:

```python
import uuid
from datetime import datetime, timezone

from dbos import DBOS, SetWorkflowID

from app.services.issues.issue_session import get_or_create_issue_session
from app.workflows.issue_lifecycle import respond_to_issue_reply
```

Replace the body of `post_issue_message` (the `@router.post("/{issue_id}/messages", ...)` handler) with:

```python
@router.post(
    "/{issue_id}/messages",
    response_model=IssueMessagePostResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_issue_message(
    issue_id: int, payload: IssueMessagePost, auth: AuthDep
) -> IssueMessagePostResponse:
    """Post a human comment on an issue.

    Session path (Spec-1b): when the issue has an assigned agent, the comment
    is treated as a reply that drives another agent turn on the issue's
    ai_session (respond_to_issue_reply workflow). The human message + the
    agent reply are persisted as ai_messages by run_session_turn and surface
    through GET /messages (Spec-1a). The returned comment is a synthesized
    optimistic row; the canonical thread comes from GET.

    Legacy path: issues with no assigned agent keep the issue_messages insert.
    """
    issue_row = await _assert_issue_visible(issue_id, auth)
    assignee_agent_id = issue_row.get("assignee_agent_id")

    # ── Session path (Spec-1b) ────────────────────────────────────────────
    if assignee_agent_id:
        session_id = await get_or_create_issue_session(issue_id)
        if not session_id:
            raise HTTPException(500, "issue has an agent but no resolvable session")

        # The turn runs as the issue OWNER (BYO-key/adapter context), mirroring
        # get_or_create_issue_session; the replying human's identity is not
        # separately threaded (single-owner-issue assumption).
        owner_id = issue_row.get("created_by_user_id") or issue_row.get(
            "assignee_user_id"
        )
        if not owner_id:
            raise HTTPException(500, "issue has no owner to run the turn as")

        wf_id = f"issue-reply-{issue_id}-{uuid.uuid4()}"
        try:
            with SetWorkflowID(wf_id):
                DBOS.start_workflow(
                    respond_to_issue_reply, issue_id, str(owner_id), payload.body
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                f"dispatch respond_to_issue_reply failed (issue_id={issue_id}): {exc}"
            )
            raise HTTPException(500, "failed to dispatch reply turn")

        # Optimistic comment for immediate render; GET (ai_messages) is canonical.
        comment = IssueMessage(
            id=uuid.uuid4(),
            issue_id=issue_id,
            kind=IssueMessageKind.COMMENT,
            author_user_id=auth.user_id,
            body=payload.body,
            meta={"optimistic": True},
            created_at=datetime.now(timezone.utc),
        )
        return IssueMessagePostResponse(comment=comment, agent_run=None)

    # ── Legacy path (no assigned agent) ───────────────────────────────────
    sb = await get_async_supabase_admin()
    comment_row = {
        "issue_id": issue_id,
        "kind": IssueMessageKind.COMMENT.value,
        "author_user_id": str(auth.user_id),
        "body": payload.body,
        "meta": {},
    }
    try:
        comment_resp = await sb.table("issue_messages").insert(comment_row).execute()
    except Exception as exc:
        logger.exception(f"insert comment failed (issue_id={issue_id}): {exc}")
        raise HTTPException(500, "comment insert failed")

    if not comment_resp.data:
        raise HTTPException(500, "comment insert returned no row")

    comment = IssueMessage.model_validate(comment_resp.data[0])
    return IssueMessagePostResponse(comment=comment, agent_run=None)
```

> This removes the old `payload.agent_id` placeholder-dispatch branch (it created an `agent_run` that never ran — superseded by the real reply turn). `simulate_agent_run_complete` is left as-is (dev tool, now redundant for session issues).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_post_issue_message_reply.py -v`
Expected: 2 passed.

- [ ] **Step 5: Run the full issue test set + lint**

Run:
```bash
cd backend && uv run pytest tests/ -k "issue" -q
uv run black app/api/issue_messages_router.py app/workflows/issue_lifecycle.py tests/test_issue_reply_workflow.py tests/test_post_issue_message_reply.py
uv run isort app/api/issue_messages_router.py app/workflows/issue_lifecycle.py
uv run flake8 app/api/issue_messages_router.py app/workflows/issue_lifecycle.py
```
Expected: issue tests pass; black reports "All done" (reformat if needed); isort/flake8 clean.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/issue_messages_router.py backend/tests/test_post_issue_message_reply.py
git commit -m "feat(issues): route session issues to reply-turn workflow (Spec-1b)"
```

---

## Task 4: Frontend verification (likely no change)

**Files:**
- Verify: `frontend/components/Todolist/IssueChatThread.tsx`, `frontend/components/Todolist/IssueDetailView.tsx`, `frontend/services/issueMessageService.ts`

- [ ] **Step 1: Trace the reply flow.** Read how the issue chat sends a reply (the POST to `/issues/{id}/messages`) and how it refreshes afterwards. Confirm: after a successful POST it **refetches** the thread via GET (which Spec-1a backs with `ai_messages`) and **replaces** its message list (so the synthesized optimistic comment from Task 3 is replaced by the real `ai_messages` rows — no duplicate). The agent's reply appears on the next refetch / Realtime tick.

- [ ] **Step 2: Decide.** If the component already refetches-and-replaces after posting → **no change**. If it optimistically appends and never reconciles by id → make the minimal change so it refetches after POST (or dedupes by `meta.optimistic`). Keep the change minimal.

- [ ] **Step 3: Build.**

Run: `cd frontend && npm run build`
Expected: ✓ built, 0 TypeScript errors.

- [ ] **Step 4: Commit (only if files changed)**

```bash
git add frontend/components/Todolist/IssueChatThread.tsx frontend/components/Todolist/IssueDetailView.tsx frontend/services/issueMessageService.ts
git commit -m "fix(todolist): refetch issue thread after reply (Spec-1b)"
```

If nothing changed, skip the commit and note "no frontend change needed".

---

## Task 5: Final verification

- [ ] **Step 1: Full backend suite.**

Run: `cd backend && uv run pytest tests/ -q`
Expected: all pass (≈ prior count + the new reply tests), 0 failures.

- [ ] **Step 2: Workflow registration import check.** `respond_to_issue_reply` must be importable so DBOS registers it. It is imported at module top of `issue_messages_router.py` (Task 3), which is mounted in `app/main.py` at startup — confirm the chain imports cleanly:

Run:
```bash
cd backend && uv run python -c "import app.api.issue_messages_router, app.workflows.issue_lifecycle; print('CHAIN_OK')"
```
Expected: `CHAIN_OK`.

- [ ] **Step 3: Confirm no migration / no chat-service edit.** `git diff --stat origin/master...HEAD` should touch only: `app/workflows/issue_lifecycle.py`, `app/api/issue_messages_router.py`, the two new test files, and (only if Task 4 needed it) the frontend files. No `supabase/migrations/*`, no `ai_library_chat_service.py`.

- [ ] **Step 4: Report to the user** before opening the PR (repo will need to be public for CI, per the recurring constraint). Summarize: serialization choice + the deferred-on-long-turn limitation + that status is unchanged (Spec-2 owns status).

---

## Self-Review (run after writing, before execution)

- **Spec coverage:** reply triggers a turn (Task 3 → workflow Task 2) ✓; reuses ai_session + run_session_turn (Task 1) ✓; per-issue single-in-flight (Task 1/2 lock) ✓; agent self-comments excluded (Task 3 gate + human-only endpoint) ✓; no status change (workflow has no set_status call) ✓.
- **Type consistency:** `run_issue_reply_step(session_id, user_id, reply_text)` keyword-only and called as such by `_run_reply_turns`'s `run_turn` ✓; `respond_to_issue_reply(issue_id:int, user_id:str, reply_text:str)` matches the `DBOS.start_workflow(respond_to_issue_reply, issue_id, str(owner_id), payload.body)` call ✓; `acquire`/`release` are `acquire_turn_lock(issue_id)` / `clear_lock(issue_id)` ✓.
- **No placeholders:** all steps have real code/commands ✓.
- **Known limitations (documented, intentional):** (1) reply during a >120s turn defers (text durable, user re-sends) — coalescing is the future fix; (2) stale `execution_locked_at` after a hard crash blocks turns until cleared — shared risk with `execute_issue`, no new reaper here; (3) non-owner human replies attribute to the issue owner — single-owner assumption.
