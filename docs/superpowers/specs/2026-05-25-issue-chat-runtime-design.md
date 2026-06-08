# Issue Conversations on the AI Chat Runtime — Design (Spec-1)

**Date:** 2026-05-25
**Status:** Approved (brainstorm) → ready for implementation plan
**Decomposition:** Spec-1 (this doc) = run issue agents on the full chat runtime, backed by an ai_session. **Spec-2 (follow-on, not here)** = liveness classification driving issue status (completed→in_review / blocked→blocked / advanced→auto-continue / needs_followup→human).

---

## Problem

The issue→agent executor shipped in #343/#344 runs a **stripped** runtime: `issue_agent_executor.run_issue_agent` builds `AgentRunner(adapter, skill_tool)` directly with a single-turn message and `hooks=None`. It has **no session, no conversation history, no compression, no memory (read or write), no sub-agents, no delegation, no budget guard, no fallback**.

Meanwhile mediahub's **chat stack already has all of these** (verified):
- Session history (`ai_sessions`/`ai_messages`, `get_messages(limit=200)`).
- Two-layer compaction: `AILibraryChatService._maybe_compact` (100k-token threshold, uses `ai_session_memory` cached summary) + `ContextCompactor.maybe_compact` inside every `AgentRunner.run_turn` (4-tier).
- Memory write (`MemoryHarvesterHook` → `write_memory_workflow`) + recall (`MemoryRetriever` pgvector+salience → injected into the prompt).
- Sub-agents (`SubAgentTaskService`) + delegation (`DelegateToolService`, depth/cycle/rate limits).
- Budget guard, cost auditor, LLM fallback chain, `ai_session_memory` rolling summary, commitment harvester.

All wired by `build_agent_runner_stack` (`app/services/ai/chat/ai_library_chat_wiring.py`) + driven by `AILibraryChatService.chat` (`app/services/ai/chat/ai_library_chat_service.py`).

## Goal

Make an issue (with an assigned agent) a first-class **conversation backed by an `ai_session`**, executed through the **full chat runtime** — so session management, conversation compression, memory, and sub-agents work for issues automatically (they are reused, not rebuilt). A human reply on the issue drives another agent turn.

## Non-goals (this spec)
- Liveness classification / automatic status transitions from agent output → **Spec-2**.
- Periodic monitor wakeups, stranded recovery, wakeup coalescing → later roadmap (see `project_issue_agent_executor` memory).
- Provider-side LLM session continuity (mediahub re-sends compacted history each turn; unchanged).
- Changing the agent-runs telemetry / Runs tab.

---

## Architecture

**One assigned issue ⇄ one `ai_session`. The issue's conversation IS that session's `ai_messages`. Issue execution = the same turn flow `AILibraryChatService.chat` runs.**

```
dispatch / human reply
   → get-or-create ai_session for the issue (issues.ai_session_id)
   → append the driving user message to ai_messages (issue title+desc on first turn; the reply on later turns)
   → run the SHARED chat-turn core:
        recall memory → build_agent_runner_stack(sub-agents/delegate/budget/fallback)
        → AgentRunner.run_turn (ContextCompactor inside) → persist assistant ai_message
        → update ai_session_memory + harvest memory + commitments (fire-and-forget)
   → (Spec-2 will classify the result → issue status)
```

### Component 1 — Data model: link issue ⇄ session
- **New migration** `2XX_issues_ai_session_id.sql`: `ALTER TABLE public.issues ADD COLUMN ai_session_id UUID REFERENCES public.ai_sessions(id) ON DELETE SET NULL;` (nullable — only set once an agent conversation starts).
- Reuse existing `ai_sessions.context_type` / `context_id` (mig 121/138): the issue's session is created with `context_type='issue'`, `context_id=str(issue_id)`. This gives a second lookup path + lets the chat UI/telemetry recognize issue sessions.

### Component 2 — Session get-or-create
- New helper (e.g. `app/services/issues/issue_session.py::get_or_create_issue_session(issue_id, agent_id, user_id) -> session_id`):
  - If `issues.ai_session_id` set → return it.
  - Else create an `ai_sessions` row (`user_id`, `agent_id`, `agent_slug` resolved from the agent, `context_type='issue'`, `context_id=str(issue_id)`, `title=issue.title`), write the id back to `issues.ai_session_id`, return it.
  - Idempotent + safe under concurrent dispatch (use the issue row / a uniqueness guard; document the race handling).

### Component 3 — Shared chat-turn core (the key refactor)
- Today `AILibraryChatService.chat(session_id, user_id, content, ...)` does: insert user msg → load history → recall memory → compose → build stack → run → persist assistant msg → update session/session_memory → harvest. We want issues to run **exactly this**.
- **Approach: extract the turn body into a reusable method** `AILibraryChatService.run_session_turn(session_id, user_id, content, *, trigger) -> {assistant_message, run_id, usage, tool_calls}` (or call `chat()` directly if its signature already fits — decide in the plan). Both the chat endpoint and the issue path call it. `trigger` distinguishes `chat` vs `issue_dispatch` vs `issue_reply` for the agent_runs row.
- The issue path passes the issue's `session_id` + the driving content (issue title+description on first turn; the human reply on subsequent turns). Everything else (memory/compaction/sub-agents) comes for free.

### Component 4 — Replace the stripped executor
- `issue_agent_executor.run_issue_agent` (the `AgentRunner(adapter, skill_tool)` path) is **replaced** by: `get_or_create_issue_session` → `AILibraryChatService.run_session_turn(session_id, user_id, content=issue-task-or-reply, trigger=...)`.
- `issue_lifecycle.run_issue_agent_step` calls this. The first dispatch turn's content = the issue title+description (current `_build_user_message`).
- The mig-208 trigger bridge is **no longer needed for the conversation** — the chat flow writes the assistant `ai_message` itself. (Keep the agent_runs row issue-linked for telemetry; the `output_summary`→issue_messages trigger becomes redundant for the body.)

### Component 5 — Reply drives a turn
- `post_issue_message` (POST `/api/v1/issues/{id}/messages`) on a **human** comment, when the issue has `assignee_agent_id`: append the reply as a user `ai_message` to the issue's session, then `DBOS.start_workflow(respond_to_issue_reply, issue_id, user_id)` (async; don't block the HTTP response). The workflow runs `run_session_turn` with the reply content.
- Concurrency: one in-flight turn per issue — dedup via DBOS `workflow_id` keyed on the issue (e.g. `issue-turn-{issue_id}-{seq}` with single-in-flight guard); overlapping replies queue or are rejected (document the chosen guard).
- **Status effect (Spec-1):** a reply turn does NOT change the issue status (a chat reply isn't task completion). Only `execute_issue` (dispatch) keeps the #343/#344 status machine (success→`in_review`). Automatic status from agent output is Spec-2 (liveness).

### Component 6 — Issue chat surface = ai_messages
- `GET /api/v1/issues/{id}/messages` returns the issue session's `ai_messages` (mapped to the UI shape the frontend expects: role/author/body/agent/created_at). The frontend `IssueChatThread` / `IssueDetailView` consume this unchanged in shape (adapter maps `ai_messages` → the existing `IssueMessage` UI type).
- **Status-change events** (the old `issue_messages` kind='system_status' with from/to_status): keep these as a separate lightweight issue-event stream (small `issue_messages`-style table or `ai_messages` with role='system' + `metadata_json.kind='status'`). Decide in the plan; default: write status changes as `ai_messages` (role='system', metadata kind='status', from/to) so the one timeline shows everything.

### Component 7 — issue_messages / mig-208 disposition
- With the conversation on `ai_messages`, the `issue_messages` table + mig-208 bridge triggers are retired for the conversation. Plan: stop writing the dispatch placeholder + terminal bridge; migrate the messages endpoint to `ai_messages`. Keep `issue_messages` table only if the status-event stream stays there (Component 6). Document the cutover (no data migration needed — issues are early-stage; confirm row counts before dropping any triggers).

---

## What's reused vs new

**Reused unchanged (do NOT rebuild):** `ai_sessions`/`ai_messages`, `get_messages`, `_maybe_compact` + `ContextCompactor`, `MemoryRetriever` + `MemoryHarvesterHook`, `SubAgentTaskService` + `DelegateToolService`, `BudgetGuardHook`, `CostAuditorHook`, `LLMFallbackChain`, `ai_session_memory`, commitment harvester — all via `build_agent_runner_stack` + the chat-turn flow.

**New:** `issues.ai_session_id` migration; `get_or_create_issue_session`; extract `run_session_turn` from `chat()`; rewire `run_issue_agent` to it; `respond_to_issue_reply` workflow + the `post_issue_message` trigger; issue messages endpoint → `ai_messages`; status events as `ai_messages` (or kept stream).

**Reused from #343/#344:** Dispatch button (Todolist `IssueDetailView`), `POST /dispatch` endpoint, `execute_issue` workflow + status machine (`in_progress` on checkout, `in_review`/`blocked`/`done`), BYO adapter resolution (`get_adapter_for_user`).
**Superseded by this spec:** the stripped `run_issue_agent` executor + the mig-208 conversation bridge.

---

## Error handling
- Agent run failure inside `run_session_turn`: the existing chat flow records the failed `agent_runs` row + surfaces an error; `execute_issue`'s `except` sets the issue `blocked` (Spec-1 keeps the manual/dispatch status machine; Spec-2 automates from liveness).
- Session create race (two dispatches): get-or-create must converge on one session (guard on `issues.ai_session_id` / `context_id` uniqueness).
- BYO key missing for the agent's model: `get_adapter_for_user` falls back to platform per provider; if neither, the run fails → issue `blocked` with a clear error (same as #344).

## Testing strategy
- `get_or_create_issue_session`: creates once, returns existing on second call (idempotent); writes `issues.ai_session_id`.
- `run_session_turn` shared core: chat endpoint and issue path both invoke it; an issue turn loads history + recalls memory + builds the full stack (assert sub-agent/delegate/hooks wired, e.g. via the stack builder being called — mock the LLM).
- Reply path: `post_issue_message` on a human comment with an assignee → appends user ai_message + dispatches the turn workflow; agent self-comment does NOT trigger.
- Messages endpoint returns the session's ai_messages mapped to the UI shape.
- Regression: chat endpoint still works after the `run_session_turn` extraction (unchanged behavior).

## Open decisions (resolved)
- **Conversation surface:** unify on `ai_messages` (option A). `issue_messages` retired for the conversation.
- **Liveness/status automation:** out of scope → Spec-2.
- **Trigger model (reply):** every human reply on an assigned issue drives a turn (auto-respond), agent self-comments excluded.
