# Issue → Agent Executor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an issue assigned to an agent actually RUN that agent and write the result back into the issue chat — closing the A8.4 stub where dispatch only creates a "running" placeholder that never completes.

**Architecture:** Port the core of paperclip's `executeRun` (checkout → build task context → run agent → write result back → flip status) onto mediahub's existing DBOS `execute_issue` workflow. The hard part — getting agent output into the issue chat — is **already built** as DB triggers (mig 208): an issue-linked `agent_runs` row auto-emits a "Agent picking up…" `issue_messages` placeholder on INSERT, and the terminal trigger UPDATEs that row's `body` from `agent_runs.output_summary` when status flips to `completed`. So the whole executor reduces to: run the agent through the standard `AgentRunner` + `RunRecorder` path (mirroring `ai_summary`'s `run_summarize_agent` step), with the `agent_runs` row carrying `issue_id` so the existing triggers do the write-back.

**Tech Stack:** Python 3.12 / FastAPI / DBOS workflows + steps, `AgentRunner` + `RunRecorder` + `PromptComposer`, pgvector-free, React 19 + TS frontend. Backend tests: pytest (mock the runner/recorder, assert SQL/flow).

## Scope

**MVP (this plan):** dispatch an issue → the assigned agent runs once synchronously inside `execute_issue` → output lands in the issue chat → issue status flips. Plus the missing **Dispatch button** in the Todolist `IssueDetailView`.

**Deferred (paperclip Phase 2 — NOT in this plan):** periodic issue-monitor wakeups (`tickDueIssueMonitors`), multi-turn liveness continuation (`advanced`→re-enqueue), reply-triggered re-runs, wakeup coalescing, the `agent_wakeup_requests` queue, stranded-issue recovery beyond the existing `liveness_scanner`. mediahub already has `liveness_scanner` to mark dead runs; that covers the crash case for MVP.

**Key design decisions (locked):**
1. **One issue-linked run, managed by `RunRecorder`.** Add an optional `issue_id` to `RunRecorder` so its `_insert_row` carries it. This makes the existing mig-208 INSERT trigger emit the placeholder and the terminal trigger write the result — no separate `agent_runs` insert. The current `create_agent_run_for_issue` step is **removed** (its hand-rolled insert is replaced by the RunRecorder-managed row).
2. **Reuse the proven `ai_summary` step shape**: a `@DBOS.step()` that composes the agent, runs `AgentRunner.run_turn`, and sets `output_summary` on the recorder. Issue title + description become the user message.
3. **Status lifecycle (server-driven for MVP):** `execute_issue` sets `in_progress` on checkout (existing), and on agent success sets `in_review` (so a human confirms the output) — NOT `done`. On agent failure → `blocked` (existing catch path). (Paperclip lets the agent self-set `done`; mediahub agents don't call back the API yet, so server sets `in_review`.)

---

## File Structure

| File | Responsibility | Phase |
|------|----------------|-------|
| `backend/app/services/ai/runner/run_recorder.py` | add optional `issue_id` field → include in `_insert_row` payload | 1 |
| `backend/app/services/issues/issue_agent_executor.py` | NEW — `run_issue_agent(issue, agent_id, user_id) -> str` (compose + AgentRunner + RunRecorder(issue_id=...)); builds the user message from the issue | 1 |
| `backend/app/workflows/issue_lifecycle.py` | replace `create_agent_run_for_issue` call with a `run_issue_agent_step`; set `in_review` on success | 1 |
| `backend/tests/test_issue_agent_executor.py` | NEW — executor builds correct prompt, sets output_summary, recorder carries issue_id | 1 |
| `backend/tests/test_issue_lifecycle_sql.py` | update for the new workflow shape | 1 |
| `frontend/components/Todolist/IssueDetailView.tsx` | add Dispatch button (shown when assignee_agent set + status in backlog/todo) calling `dispatchIssue` | 2 |
| `frontend/services/issuesService.ts` | already has `dispatchIssue` — reuse | 2 |

---

# Phase 1 — Backend executor

### Task 1.1: RunRecorder carries issue_id

**Files:**
- Modify: `backend/app/services/ai/runner/run_recorder.py`
- Test: `backend/tests/test_run_recorder_issue_id.py` (new)

- [ ] **Step 1: Write the failing test**

```python
"""RunRecorder must carry issue_id into the agent_runs INSERT so mig-208's
trigger emits + later updates the issue chat row."""
from __future__ import annotations
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import pytest


async def test_insert_row_includes_issue_id(monkeypatch):
    from app.services.ai.runner.run_recorder import RunRecorder

    captured = {}

    class _Tbl:
        def insert(self, payload):
            captured["payload"] = payload
            return self
        async def execute(self):
            class R: data = [{"id": str(uuid4())}]
            return R()

    class _Client:
        def table(self, name):
            return _Tbl()

    async def fake_client():
        return _Client()

    monkeypatch.setattr(
        "app.services.ai.runner.run_recorder.get_async_supabase_admin", fake_client
    )
    rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="issue_dispatch",
                      issue_id=409)
    await rec._insert_row()
    assert captured["payload"]["issue_id"] == 409
```

- [ ] **Step 2: Run — verify FAIL**

Run: `cd backend && uv run pytest tests/test_run_recorder_issue_id.py -q`
Expected: FAIL — `RunRecorder.__init__() got an unexpected keyword argument 'issue_id'`.

- [ ] **Step 3: Implement**

In `run_recorder.py`, add the dataclass field (near `trigger`):
```python
    issue_id: Optional[int] = None  # when set, mig-208 triggers bridge this run to the issue chat
```
In `_insert_row`'s `payload` dict, add (only when set, to avoid writing null for non-issue runs):
```python
        if self.issue_id is not None:
            payload["issue_id"] = self.issue_id
```
(Place the conditional right after the dict literal is built, before `client.table("agent_runs").insert(payload)`.)

- [ ] **Step 4: Run — verify PASS**

Run: `cd backend && uv run pytest tests/test_run_recorder_issue_id.py tests/ -k "run_recorder" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ai/runner/run_recorder.py backend/tests/test_run_recorder_issue_id.py
git commit -m "feat(agent-runs): RunRecorder carries issue_id for the mig-208 chat bridge (#A8.4)"
```

### Task 1.2: The issue agent executor

**Files:**
- Create: `backend/app/services/issues/issue_agent_executor.py`
- Test: `backend/tests/test_issue_agent_executor.py`

- [ ] **Step 1: Write the failing test**

```python
"""run_issue_agent composes the assigned agent, runs it on the issue's
title+description, and records output_summary on an issue-linked run."""
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest


async def test_run_issue_agent_builds_prompt_and_records_output(monkeypatch):
    from app.services.issues import issue_agent_executor as m

    agent_uuid = uuid4()

    # agent repo → returns a row with slug
    agent_repo = MagicMock()
    agent_repo.get_by_id = AsyncMock(return_value={"id": str(agent_uuid), "slug": "writer", "model": "qwen-max"})
    monkeypatch.setattr(m, "AgentRepository", lambda: agent_repo)

    composed = MagicMock(agent_id=agent_uuid, model="qwen-max")
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)
    monkeypatch.setattr(m, "PromptComposer", lambda *a, **k: composer)

    runner = MagicMock()
    runner.run_turn = AsyncMock(return_value={"content": "Here is the 200-word essay...", "tool_calls": []})
    monkeypatch.setattr(m, "_build_runner", lambda composed_, settings_: runner)

    # capture recorder usage
    rec = MagicMock()
    rec.__aenter__ = AsyncMock(return_value=rec)
    rec.__aexit__ = AsyncMock(return_value=False)
    rec.set_summaries = MagicMock()
    captured = {}
    def _rec_factory(**kwargs):
        captured.update(kwargs)
        return rec
    monkeypatch.setattr(m, "RunRecorder", _rec_factory)

    issue = {"id": 409, "title": "写一篇 200 字的短文", "description": "关于春天"}
    out = await m.run_issue_agent(issue=issue, agent_id=str(agent_uuid), user_id=str(uuid4()))

    # prompt carries the issue title + description
    user_msgs = runner.run_turn.call_args.args[1] if runner.run_turn.call_args.args else runner.run_turn.call_args.kwargs["user_messages"]
    joined = " ".join(msg["content"] for msg in user_msgs)
    assert "写一篇 200 字的短文" in joined and "关于春天" in joined
    # recorder is issue-linked + records output
    assert captured["issue_id"] == 409
    assert captured["trigger"] == "issue_dispatch"
    rec.set_summaries.assert_called_once()
    assert "essay" in rec.set_summaries.call_args.kwargs["output_summary"]
    assert out == "Here is the 200-word essay..."
```

- [ ] **Step 2: Run — verify FAIL**

Run: `cd backend && uv run pytest tests/test_issue_agent_executor.py -q`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement**

Create `backend/app/services/issues/issue_agent_executor.py`:
```python
"""Run the agent assigned to an issue and record its output.

The result write-back to the issue chat is handled by the mig-208 DB triggers:
the issue-linked agent_runs row (created by RunRecorder with issue_id set) emits
an "Agent picking up…" issue_messages placeholder on INSERT, and the terminal
trigger copies agent_runs.output_summary into that row's body when the run
completes. So this module only has to run the agent and set output_summary.

Mirrors the proven app/workflows/ai_summary.py::run_summarize_agent shape.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from loguru import logger

from app.repositories.agent_repository import AgentRepository
from app.repositories.skill_repository import SkillRepository
from app.services.ai.prompts.prompt_composer import ComposerInput, PromptComposer
from app.services.ai.runner.agent_runner import AgentRunner
from app.services.ai.runner.run_recorder import RunRecorder
from app.services.ai.skills.skill_tool_service import SkillToolService


def _build_runner(composed: Any, settings: Any) -> AgentRunner:
    """Adapter + AgentRunner for the composed agent. Mirrors SummarizeService."""
    from app.services.ai.adapters import get_adapter

    adapter = get_adapter(composed.model, settings)
    return AgentRunner(adapter=adapter, skill_tool=SkillToolService(SkillRepository()))


def _build_user_message(issue: dict[str, Any]) -> str:
    title = (issue.get("title") or "").strip()
    description = (issue.get("description") or "").strip()
    parts = [f"Task: {title}"] if title else []
    if description:
        parts.append(f"\nDetails:\n{description}")
    return "\n".join(parts) or "Complete the assigned task."


async def run_issue_agent(
    *, issue: dict[str, Any], agent_id: str, user_id: str
) -> Optional[str]:
    """Compose + run the assigned agent on the issue. Returns the agent's text
    output (also persisted via RunRecorder.output_summary → mig-208 bridge)."""
    from app.core.config import settings

    agent_repo = AgentRepository()
    agent_row = await agent_repo.get_by_id(UUID(agent_id))
    if not agent_row:
        raise RuntimeError(f"assignee agent {agent_id} not found")

    composer = PromptComposer(agent_repo, SkillRepository())
    composed = await composer.compose(
        ComposerInput(
            agent_slug=agent_row["slug"],
            request_instructions=_build_user_message(issue),
        )
    )
    runner = _build_runner(composed, settings)
    user_messages = [{"role": "user", "content": _build_user_message(issue)}]

    async with RunRecorder(
        agent_id=composed.agent_id,
        user_id=UUID(user_id),
        trigger="issue_dispatch",
        model=composed.model,
        issue_id=int(issue["id"]),
    ) as recorder:
        result = await runner.run_turn(composed, user_messages, recorder=recorder)
        content = result.get("content") or ""
        recorder.set_summaries(
            input_summary=_build_user_message(issue)[:500],
            output_summary=content[:500] if content else "(no output)",
        )

    logger.info(
        f"[issue_agent] issue={issue['id']} agent={agent_id} produced "
        f"{len(content)} chars"
    )
    return content
```

- [ ] **Step 4: Run — verify PASS**

Run: `cd backend && uv run pytest tests/test_issue_agent_executor.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/issues/issue_agent_executor.py backend/tests/test_issue_agent_executor.py
git commit -m "feat(issues): issue_agent_executor — run assigned agent on an issue (#A8.4)"
```

### Task 1.3: Wire execute_issue to actually run the agent

**Files:**
- Modify: `backend/app/workflows/issue_lifecycle.py`
- Modify: `backend/tests/test_issue_lifecycle_sql.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_issue_lifecycle_sql.py`:
```python
async def test_run_issue_agent_step_records_and_sets_in_review(monkeypatch):
    import app.workflows.issue_lifecycle as il

    ran = {}

    async def fake_run_issue_agent(*, issue, agent_id, user_id):
        ran["issue_id"] = issue["id"]
        ran["agent_id"] = agent_id
        return "essay output"

    monkeypatch.setattr(
        "app.services.issues.issue_agent_executor.run_issue_agent",
        fake_run_issue_agent,
    )
    out = await il.run_issue_agent_step(
        {"id": 409, "title": "t", "description": "d"}, "agent-uuid", "user-uuid"
    )
    assert ran["issue_id"] == 409 and ran["agent_id"] == "agent-uuid"
    assert out == "essay output"
```

- [ ] **Step 2: Run — verify FAIL**

Run: `cd backend && uv run pytest tests/test_issue_lifecycle_sql.py -k run_issue_agent_step -q`
Expected: FAIL — `run_issue_agent_step` not defined.

- [ ] **Step 3: Implement**

In `issue_lifecycle.py`:

(a) Add the new step (after `load_issue`):
```python
@DBOS.step(retries_allowed=True, max_attempts=2)
async def run_issue_agent_step(
    issue: dict[str, Any], agent_id: str, user_id: str
) -> Optional[str]:
    """Run the assigned agent on the issue. The RunRecorder (issue_id-linked)
    + mig-208 triggers write the result into the issue chat; we just return the
    text. Retryable: each attempt is a fresh agent_runs row + LLM call."""
    from app.services.issues.issue_agent_executor import run_issue_agent

    return await run_issue_agent(issue=issue, agent_id=agent_id, user_id=user_id)
```

(b) In `execute_issue`, REPLACE the `create_agent_run_for_issue` block:
```python
# OLD
        agent_run_id: Optional[str] = None
        if agent_id and user_id:
            agent_run_id = await create_agent_run_for_issue(
                issue_id, agent_id, user_id, workflow_id
            )
        result: dict[str, Any] = {"issue_id": issue_id, "agent_run_id": agent_run_id}
        if agent_run_id is None:
            await set_status(issue_id, "done")
        return result
# NEW
        if agent_id and user_id:
            await run_issue_agent_step(issue_row, agent_id, user_id)
            # Agent output is in the chat via mig-208; human confirms via in_review.
            await set_status(issue_id, "in_review")
            return {"issue_id": issue_id, "executed": True}
        # No agent assigned → nothing to run; close it out.
        await set_status(issue_id, "done")
        return {"issue_id": issue_id, "executed": False}
```

(c) DELETE the now-unused `create_agent_run_for_issue` step + its test (`test_create_agent_run_for_issue_inserts_fields`, `test_create_agent_run_returns_none_on_db_error`) — the RunRecorder path replaces it. (The mig-208 INSERT trigger now fires off the RunRecorder insert instead.)

- [ ] **Step 4: Run — verify PASS**

Run: `cd backend && uv run pytest tests/test_issue_lifecycle_sql.py -q`
Expected: PASS (remove the two deleted-step tests).

- [ ] **Step 5: Compile + commit**

Run: `cd backend && uv run python -c "import app.workflows.issue_lifecycle; print('OK')"`
```bash
git add backend/app/workflows/issue_lifecycle.py backend/tests/test_issue_lifecycle_sql.py
git commit -m "feat(issues): execute_issue runs the assigned agent + sets in_review (#A8.4)"
```

---

# Phase 2 — Frontend Dispatch button

### Task 2.1: Add Dispatch action to the Todolist IssueDetailView

**Files:**
- Modify: `frontend/components/Todolist/IssueDetailView.tsx`

- [ ] **Step 1: Read the component**

Read `IssueDetailView.tsx` to find its props (it must receive the `issue` + a refresh/update callback) and where the action buttons (New Sub-Issue / Upload attachment / New document) are rendered.

- [ ] **Step 2: Add the dispatch handler + button**

Import the service:
```tsx
import { dispatchIssue } from '../../services/issuesService';
```
Add a handler (place near other handlers; use the component's existing issue + refresh props — adapt names to the actual props):
```tsx
const [dispatching, setDispatching] = useState(false);
const handleDispatch = async () => {
  if (!issue?.id) return;
  setDispatching(true);
  try {
    await dispatchIssue(issue.id);
    await onRefresh?.();   // re-fetch so status/chat update
  } catch (e) {
    console.error('[IssueDetailView] dispatch failed', e);
  } finally {
    setDispatching(false);
  }
};
```
Render the button next to the existing action buttons, shown only when an agent is assigned and the issue isn't already running:
```tsx
{issue.assignee_agent_id && ['backlog', 'todo'].includes(issue.status) && (
  <button
    onClick={handleDispatch}
    disabled={dispatching}
    className="<match the existing action-button classes>"
  >
    {dispatching ? t('issues.dispatching') : t('issues.dispatchToAgent')}
  </button>
)}
```
Add i18n keys `issues.dispatchToAgent` ("Dispatch to Agent") + `issues.dispatching` ("Dispatching…") to `frontend/public/locales/en.json` and `zh.json`.

- [ ] **Step 3: Verify build + typecheck**

Run: `cd frontend && npm run build`
Expected: ✓ built, no TS errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/Todolist/IssueDetailView.tsx frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(todolist): Dispatch-to-agent button on the issue detail view (#A8.4)"
```

---

## Verification (end-to-end, post-merge on prod)

- [ ] **Step 1: Full backend suite** — `cd backend && uv run pytest tests/ -q` → all pass.
- [ ] **Step 2: Dispatch a real issue** — assign an agent to a fresh issue, click Dispatch. Then check prod:
  - `application_logs`: `[issue_agent] issue=… produced N chars` appears.
  - `agent_runs`: a row with `issue_id=…`, `trigger='issue_dispatch'`, `status='completed'`, `output_summary` set.
  - `issue_messages`: the "Agent picking up…" row UPDATEd in place to the agent's output (body = essay).
  - `issues.status` = `in_review`.
- [ ] **Step 3: Failure path** — point at a misconfigured agent; confirm the run goes `failed` → issue `blocked` → chat row shows the error (terminal trigger handles failed too).

---

## Self-Review

**Spec coverage:** The A8.4 gap = "dispatch creates a running placeholder that never executes." Task 1.1–1.3 fill it (RunRecorder issue_id → run the agent → bridge writes result → status). Task 2.1 fills the UI gap (no Dispatch button in Todolist). Both gaps from the diagnosis are covered.

**Placeholder scan:** no TBD/TODO. The one read-then-adapt point is Task 2.1 Step 1 (the exact prop names of `IssueDetailView` must be read from the file — it's a real component with unknown-until-read prop names), which is investigation-then-edit, not a placeholder.

**Type consistency:** `run_issue_agent(issue, agent_id, user_id)` signature matches between executor (1.2), the DBOS step (1.3), and the tests. `RunRecorder(issue_id=...)` matches the field added in 1.1. `dispatchIssue(issue.id)` matches the existing `issuesService.ts:197` export.

**Reuse over rebuild:** mediahub does NOT need paperclip's full heartbeat/wakeup-queue for MVP — it already has DBOS workflows + the mig-208 trigger bridge + a liveness_scanner. We port only paperclip's `executeRun` core (run agent → write comment → status), reusing mediahub's `AgentRunner`/`RunRecorder`/`PromptComposer` exactly as `ai_summary` does. Paperclip's monitor/continuation/coalescing/recovery are deferred Phase 2.

**Risk notes:** (1) `run_issue_agent_step` is `@DBOS.step(max_attempts=2)` — a retry creates a second agent_runs row + a second placeholder chat row; acceptable (matches ai_summary) but note it. (2) `in_review` (not `done`) on success is deliberate — mediahub agents don't call back to self-close, so a human confirms. (3) The agent runs synchronously inside the DBOS workflow; a long agent run holds the workflow — fine for MVP (DBOS handles durability), revisit if issues need parallel/long runs (that's where paperclip's queue would come in).
