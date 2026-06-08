# Issue Chat Polish + @-Reference Resource (sub-plan 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** Fix 4 issue-chat UX papercuts (toast, alignment, live tick) + ship the `@`-reference feature that lets the agent inject an existing resource's transcript/summary into a chat turn. Tool-call inline rows (the paperclip-style "🔧 Executing command" row) are intentionally **deferred** to a separate PR — they need a structured event stream redesign, not just UI work.

**Architecture:** Each polish item is a small surgical edit (1-2 files). The `@`-reference feature reuses existing scope context (`useResourcesContext` + `useAuth`), introduces ONE new picker component, ONE new service helper, and ONE new server-side resolver path that fetches `parsed_media.ai_extract_text` / `.summary` by resource id and prepends it as an extra paragraph to the user message before `run_session_turn` calls the model.

**Tech Stack:** React 19 + Vite, existing `IssueChatThread` / `IssueReplyBox` / `AIChatPanel` composers, FastAPI Pydantic schema extension on `AttachmentRequest` (new `kind="resource_ref"` OR a sibling `resource_refs` field), `parsed_media` reads via existing repo.

**Spec:** `docs/superpowers/specs/2026-05-25-agent-media-context-and-assets-design.md`. Sub-plan 4 of 5. Builds on sub-plans 1+2+3 (PRs #351 #352 #353) and the vision fix (PR #354).

---

## Locked design decisions (from session 2026-05-26)

1. **5 polish items + @-ref in this plan, tool-call rows deferred.** Tool calls live in `ai_messages.tool_calls` JSONB + as "→ Running skill..." text deltas in the stream. Rendering them as discrete inline rows needs a structured event channel (separate from text deltas) — out of scope here.
2. **@-ref injection format: text-prepend, not new Attachment kind.** Simpler, no schema fork. The picker sends `{resource_id, label}` in a new optional `resource_refs` field on the chat/issue post body; backend resolves each id → `parsed_media.ai_extract_text` (or `.summary` if no transcript) → prepends as a `# Referenced resource: <label>\n\n<text>` block before the user's typed message. The model gets it as part of the user message body, not a separate multimodal part.
3. **@-ref picker scope = current scope only** (personal OR team, whichever the chat session is bound to). No cross-scope picking. Reuse `useResourcesContext()` for scope context, filter resources by `scope_type/scope_id` matching the session.
4. **@-ref picker UX = inline autocomplete dropdown**, not a full modal. Triggered by typing `@` in the textarea; the dropdown lists matching resources from the current scope by `filename` prefix match (debounced). Pick with Enter or click → inserts `@<filename>` into the textarea + pushes the resource_id into local state.
5. **Token-budget guard for @-ref injection:** truncate any single resource's `.ai_extract_text` to 8000 chars before prepending. Multiple resources are concatenated with `---` separators. Total injection bytes capped at 24000 chars (≈ 6000 tokens). Anything over gets a `[truncated — full text 100k chars]` marker. The cap is conservative; admin can raise it later via a Settings dropdown (out of scope here).
6. **Default agent for @-ref resolution = the chat session's bound agent.** No multi-tenant complexity.

## Reuse facts (verified)
- `IssueChatThread`'s `AgentRunEvent` already renders `worked for {duration_seconds}` when set + `LivenessPill` + `metaStatus === 'running'` (line 113). Live tick = add a `useEffect` that re-renders every 1s while `isRunning && duration_seconds == null` (i.e., the agent is still in flight).
- `CommentEvent` (line 172) already computes `isSelf` (line 174); JSX needs alignment + bg-color split.
- `SystemStatusEvent` (line 70) is wired into the message kind dispatch (line 230); per the user's reference screenshot it should look like a gray inline row "STATUS done → todo".
- `IssueDetailView.tsx:179` fires `addToast('Reply posted; agent dispatched')` — redundant after sub-plan 3 because the reply streams in via Realtime + appears in the thread. Delete.
- Backend reply path: `respond_to_issue_reply → _run_reply_turns → run_issue_reply_step → run_session_turn(content=reply_text, attachments=...)`. The `content` string is what the agent sees as the user message. Prepending the @-ref text means concatenating before `content` is passed.
- `parsed_media.ai_extract_text` column (videos table): per CLAUDE.md, this is where transcripts live. Verify via `\d+ parsed_media` or read `app/repositories/parsed_media_repository.py`.
- The picker autocomplete needs a debounced server call `GET /api/v1/resources?scope_type=&scope_id=&q=<prefix>` — verify this endpoint exists or add a `q=` param.

## File structure

- **Modify** `frontend/components/Todolist/IssueDetailView.tsx` — drop the redundant toast (Task 1).
- **Modify** `frontend/components/Todolist/IssueChatThread.tsx::CommentEvent` — right-align self bubbles + bg-color split (Task 2).
- **Read/maybe-modify** same file `SystemStatusEvent` (Task 3) — verify rendering matches reference; cosmetic tweak if needed.
- **Create** `frontend/hooks/useElapsedSeconds.ts` — 1s-tick hook (Task 4).
- **Modify** `frontend/components/Todolist/IssueChatThread.tsx::AgentRunEvent` — render live "Working for Xs" via the new hook when `isRunning` (Task 4).
- **Create** `frontend/components/Todolist/AtReferencePicker.tsx` — autocomplete dropdown (Task 5).
- **Create** `frontend/hooks/useAtReference.ts` — textarea integration: track `@` trigger, debounced search, manage selected refs state (Task 5).
- **Modify** `frontend/services/issueMessageService.ts` — add `resource_refs?: { id: string; label: string }[]` to `IssueMessagePostPayload` (Task 5).
- **Modify** `frontend/services/aiLibraryService.ts` — same field on `ChatRequest`-equivalent (Task 5).
- **Modify** `frontend/components/Todolist/IssueReplyBox.tsx` + `frontend/components/AIChatPanel.tsx` — wire the picker into both composers (Task 5).
- **Create** `backend/app/services/ai/chat/at_reference_resolver.py` — takes `[{id, label}, ...]`, fetches `parsed_media.ai_extract_text` / `.summary`, returns prepend text (Task 6).
- **Modify** `backend/app/schemas/issue_message.py::IssueMessagePost` + `backend/app/schemas/ai_library_chat.py::ChatRequest` (or equivalent) — add `resource_refs` field (Task 6).
- **Modify** `backend/app/api/issue_messages_router.py::post_issue_message` + the chat router — forward to workflow / chat service (Task 6).
- **Modify** `backend/app/services/ai/chat/ai_library_chat_service.py::run_session_turn` — prepend resolved @-ref text to `content` before building the user message (Task 6).
- **Modify** `backend/app/workflows/issue_lifecycle.py::respond_to_issue_reply` + `_run_reply_turns` + `run_issue_reply_step` — thread `resource_refs` through (Task 6).
- Tests at every layer.

---

## Task 1: Remove redundant reply toast

**File:** `frontend/components/Todolist/IssueDetailView.tsx`

- [ ] **Step 1: Read** lines 175-200 to confirm the toast call site and that no other behavior depends on it.
- [ ] **Step 2: Delete** the `addToast(agentId ? 'Reply posted; agent dispatched' : 'Comment posted', 'success');` call at line 179. The toast is purely a confirmation — the reply already streams into the thread via Realtime, making the toast noise. KEEP the error toast at line 181.
- [ ] **Step 3: Run** `cd frontend && npx vitest run --reporter=default 2>&1 | tail -3`. Expect zero regressions (no test depended on the toast text).
- [ ] **Step 4: Commit** `git add components/Todolist/IssueDetailView.tsx && git commit -m "polish(issues): drop redundant 'Reply posted' toast (thread streams it already)"`.

---

## Task 2: Right-align self messages + bg color

**File:** `frontend/components/Todolist/IssueChatThread.tsx::CommentEvent`

- [ ] **Step 1: Read** the current CommentEvent body (~line 172-195). Note the current JSX outer wrapper className.
- [ ] **Step 2: Write failing test** in `frontend/components/Todolist/IssueChatThread.test.tsx` (create if absent):
  ```tsx
  // ... renders CommentEvent for self → expect wrapper to have justify-end / self-end + bg-blue-600 class
  // ... renders CommentEvent for other → expect wrapper to have justify-start / self-start + bg-zinc-800
  ```
  Use `@testing-library/react` `render` + `container.querySelector` for the wrapper class assertion (because comment bodies are plain text, not roles).
- [ ] **Step 3: Run → fail.**
- [ ] **Step 4: Implement** — change the outer wrapper:
  ```tsx
  <div className={`flex my-2 ${isSelf ? 'justify-end' : 'justify-start'}`}>
    <div className={`max-w-[80%] rounded-lg px-3 py-2 ${
      isSelf
        ? 'bg-blue-600/15 border border-blue-700/40 text-zinc-100'
        : 'bg-zinc-800 border border-zinc-700 text-zinc-200'
    }`}>
      {/* existing body — author label, time, content */}
    </div>
  </div>
  ```
  Preserve the existing author label / time / content render — only wrap with the new flex+bubble divs.
- [ ] **Step 5: Run → pass + commit** `git commit -m "polish(issues): right-align self messages with distinct bubble color"`.

---

## Task 3: Verify SystemStatusEvent rendering matches reference UX

**File:** `frontend/components/Todolist/IssueChatThread.tsx::SystemStatusEvent` (line 70)

- [ ] **Step 1: Read** the full SystemStatusEvent body (line 70-95). The reference UX is a small centered gray-italic inline row "STATUS done → todo".
- [ ] **Step 2:** if the current styling matches (e.g. centered, gray text, small font, italic), DONE — write a 1-line "no change needed" report. If it doesn't match (e.g. left-aligned, normal font), update the className to:
  ```tsx
  <div className="text-center text-[11px] text-zinc-500 italic my-2">
    <span className="text-zinc-400">{author}</span> updated this task —{' '}
    STATUS <span className="font-medium">{from}</span> → <span className="font-medium">{to}</span>
  </div>
  ```
- [ ] **Step 3: Smoke** render a SystemStatusEvent test (add to IssueChatThread.test.tsx) — confirm text contains "STATUS X → Y" and the className includes `italic` + `text-center`.
- [ ] **Step 4: Commit** if changed; else skip.

---

## Task 4: Live "Working for Xs" tick for in-flight agent runs

**Files:**
- Create: `frontend/hooks/useElapsedSeconds.ts`
- Create: `frontend/hooks/useElapsedSeconds.test.ts`
- Modify: `frontend/components/Todolist/IssueChatThread.tsx::AgentRunEvent`

- [ ] **Step 1: Reuse-surface read.** Read AgentRunEvent's current header rendering (lines ~128-142). Note where `worked for {duration_seconds}` is rendered + the conditions (`msg.duration_seconds != null`, `isRunning = metaStatus === 'running'`). The current render is static; we want a live tick while `isRunning`.
- [ ] **Step 2: Write failing test for the hook:**
  ```ts
  // frontend/hooks/useElapsedSeconds.test.ts
  import { renderHook, act } from '@testing-library/react';
  import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
  import { useElapsedSeconds } from './useElapsedSeconds';

  describe('useElapsedSeconds', () => {
    beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-05-26T12:00:00Z')); });
    afterEach(() => { vi.useRealTimers(); });

    it('returns 0 immediately', () => {
      const { result } = renderHook(() =>
        useElapsedSeconds('2026-05-26T12:00:00Z', { enabled: true })
      );
      expect(result.current).toBe(0);
    });

    it('increments each second when enabled', () => {
      const { result } = renderHook(() =>
        useElapsedSeconds('2026-05-26T12:00:00Z', { enabled: true })
      );
      act(() => { vi.advanceTimersByTime(3500); });
      expect(result.current).toBe(3);
    });

    it('freezes when disabled', () => {
      const { result, rerender } = renderHook(
        ({ enabled }) => useElapsedSeconds('2026-05-26T12:00:00Z', { enabled }),
        { initialProps: { enabled: true } }
      );
      act(() => { vi.advanceTimersByTime(2000); });
      expect(result.current).toBe(2);
      rerender({ enabled: false });
      act(() => { vi.advanceTimersByTime(5000); });
      expect(result.current).toBe(2); // frozen
    });

    it('returns 0 for null startedAt', () => {
      const { result } = renderHook(() => useElapsedSeconds(null, { enabled: true }));
      expect(result.current).toBe(0);
    });
  });
  ```
- [ ] **Step 3: Run → fail.**
- [ ] **Step 4: Implement:**
  ```ts
  // frontend/hooks/useElapsedSeconds.ts
  /**
   * Live-ticking seconds counter from `startedAt` to now, in whole seconds.
   * Cancelled when `enabled` flips false (timer cleared) or on unmount.
   * Returns 0 when startedAt is missing or malformed.
   */
  import { useEffect, useState } from 'react';

  export function useElapsedSeconds(
    startedAt: string | null | undefined,
    opts: { enabled: boolean },
  ): number {
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
      if (!opts.enabled || !startedAt) return;
      const id = setInterval(() => setNow(Date.now()), 1000);
      return () => clearInterval(id);
    }, [opts.enabled, startedAt]);
    if (!startedAt) return 0;
    const t = Date.parse(startedAt);
    if (Number.isNaN(t)) return 0;
    return Math.max(0, Math.floor((now - t) / 1000));
  }
  ```
- [ ] **Step 5: Wire into AgentRunEvent.** Replace the static duration render:
  ```tsx
  const elapsed = useElapsedSeconds(msg.started_at, { enabled: isRunning });
  // ... in header:
  {isRunning ? (
    <span className="text-[12px] text-zinc-500">working for {formatDuration(elapsed)}</span>
  ) : msg.duration_seconds != null ? (
    <span className="text-[12px] text-zinc-500">worked for {formatDuration(msg.duration_seconds)}</span>
  ) : null}
  ```
  The completed case keeps using `duration_seconds` from the DB (canonical). The in-flight case ticks live. Confirm `msg.started_at` is exposed on the IssueMessage type — if not, add it (grep `started_at` on the schema; the agent_run row should have it).
- [ ] **Step 6: Run + commit:**
  ```bash
  cd frontend && npx vitest run hooks/useElapsedSeconds.test.ts --reporter=default
  npx vitest run --reporter=default 2>&1 | tail -3
  git add hooks/useElapsedSeconds.ts hooks/useElapsedSeconds.test.ts components/Todolist/IssueChatThread.tsx
  git commit -m "feat(issues): live 'working for Xs' tick for in-flight agent runs"
  ```

---

## Task 5: `@`-reference picker — frontend autocomplete

**Files:**
- Create: `frontend/components/Todolist/AtReferencePicker.tsx`
- Create: `frontend/hooks/useAtReference.ts`
- Create: tests for both
- Modify: `frontend/services/issueMessageService.ts` + `frontend/services/aiLibraryService.ts`
- Modify: `frontend/components/Todolist/IssueReplyBox.tsx` + `frontend/components/AIChatPanel.tsx`

- [ ] **Step 1: Reuse-surface read.**
  - `grep -n "GET.*resources\|listResources\|fetchResources" frontend/services/resourceService.ts` to see the existing list endpoint. Confirm it supports a `q=` prefix filter; if not, that's a backend mini-task (~10 lines in resources_router).
  - Read `IssueReplyBox` and `AIChatPanel` composer JSX to find the textarea ref/handler — the picker needs to overlay it.
  - Confirm `useResourcesContext` exposes `scopeType` + `scopeId`.

- [ ] **Step 2: Write failing tests** for `useAtReference` (the textarea integration) + `AtReferencePicker` (the dropdown component). Pin: detect `@` insertion, open dropdown, debounced search, Enter/click adds to refs state, Escape closes.

- [ ] **Step 3: Implement `useAtReference`** (~80 lines):
  - Track `triggerOffset: number | null` — position in textarea value where the active `@` lives.
  - Track `query: string` — substring from `@` to caret.
  - Track `selectedRefs: { id, label }[]` — confirmed picks.
  - `onChange` / `onKeyDown` / `onSelect` handlers.
  - Returns `{ pickerOpen, query, items, onPickItem, refs, clearRefs }`.

- [ ] **Step 4: Implement `AtReferencePicker`** (~60 lines): absolute-positioned dropdown anchored to textarea bottom-left, lists items by filename, keyboard-navigable, click or Enter to pick. Closed on outside-click or Escape.

- [ ] **Step 5: Wire into composers.** Both IssueReplyBox + AIChatPanel:
  - `const { pickerOpen, query, items, onPickItem, refs, clearRefs } = useAtReference({ scopeType, scopeId });`
  - Add the picker conditionally below the textarea when `pickerOpen`.
  - On submit: pass `refs` to onSubmit / service.
  - Clear `refs` after successful send.

- [ ] **Step 6: Update services** — add `resource_refs?: { id: string; label: string }[]` to `IssueMessagePostPayload` and the chat-request equivalent. Serialize in the JSON body.

- [ ] **Step 7: Run + commit:** `feat(chat): @-reference picker (autocomplete in composer) + ref payload field`.

---

## Task 6: Backend `@`-reference resolver + thread through to model

**Files:**
- Create: `backend/app/services/ai/chat/at_reference_resolver.py`
- Test: `backend/tests/test_at_reference_resolver.py`
- Modify: `backend/app/schemas/issue_message.py::IssueMessagePost` + `backend/app/schemas/ai_library_chat.py::ChatRequest` — add `resource_refs: Optional[List[ResourceRef]] = None`.
- Modify: `backend/app/api/issue_messages_router.py::post_issue_message` + chat router — forward refs.
- Modify: `backend/app/workflows/issue_lifecycle.py::respond_to_issue_reply` + `_run_reply_turns` + `run_issue_reply_step` — thread refs.
- Modify: `backend/app/services/ai/chat/ai_library_chat_service.py::run_session_turn` — prepend resolved text to `content`.

- [ ] **Step 1: Define `ResourceRef`** in `app/schemas/ai_library_chat.py`:
  ```python
  class ResourceRef(BaseModel):
      id: str = Field(..., min_length=1)
      label: str = Field(..., min_length=1, max_length=200)
  ```

- [ ] **Step 2: Write resolver tests** for the prepend logic + truncation cap + missing-resource fallback. Use mocks for the parsed_media repo.

- [ ] **Step 3: Implement resolver:**
  ```python
  # backend/app/services/ai/chat/at_reference_resolver.py
  """Resolve @-referenced resources into a text-prepend block.

  Reads parsed_media.ai_extract_text (transcript) or .summary; truncates
  each at MAX_PER_RESOURCE_CHARS; total cap MAX_TOTAL_CHARS. Missing
  resources become a `[reference X.Y unavailable]` marker so the model
  knows the user intended a ref but it couldn't be resolved.
  """
  from typing import Optional
  from app.schemas.ai_library_chat import ResourceRef

  MAX_PER_RESOURCE_CHARS = 8000
  MAX_TOTAL_CHARS = 24000

  async def resolve_resource_refs(refs: list[ResourceRef]) -> str:
      """Return the prepend block. Empty string when refs is empty."""
      if not refs:
          return ""
      from app.repositories.parsed_media_repository import ParsedMediaRepository  # noqa: PLC0415

      repo = ParsedMediaRepository()
      blocks: list[str] = []
      remaining = MAX_TOTAL_CHARS
      for ref in refs:
          if remaining <= 0:
              break
          row = await repo.get_by_id(ref.id)
          if not row:
              blocks.append(f"[Reference {ref.label!r} unavailable — resource not found]")
              continue
          text = row.get("ai_extract_text") or row.get("summary") or ""
          if not text:
              blocks.append(f"[Reference {ref.label!r} has no transcript/summary yet]")
              continue
          truncated = text[:MAX_PER_RESOURCE_CHARS]
          if len(text) > MAX_PER_RESOURCE_CHARS:
              truncated += f"\n[truncated — full text {len(text)} chars]"
          if len(truncated) > remaining:
              truncated = truncated[:remaining]
          blocks.append(f"# Referenced resource: {ref.label}\n\n{truncated}")
          remaining -= len(truncated)
      return "\n\n---\n\n".join(blocks)
  ```

- [ ] **Step 4: Wire through the issue reply chain.** Schema additions + router forwarding + `_run_reply_turns(... resource_refs=...)` + `run_issue_reply_step(... resource_refs=...)`. At the bottom: `prepend = await resolve_resource_refs(resource_refs); content = (prepend + "\n\n" + reply_text) if prepend else reply_text` before calling `run_session_turn`.

- [ ] **Step 5: Wire through the chat router.** Same pattern for the AIChatPanel chat path. ChatRequest gains `resource_refs`; `run_session_turn` prepends.

- [ ] **Step 6: Tests + commit.** `feat(chat): @-reference text injection + per-resource + total char cap`.

---

## Task 7: Final verification

- [ ] Backend full suite passes.
- [ ] Frontend full suite passes.
- [ ] Import chain OK.
- [ ] **Manual sanity** (post-deploy):
  - Send a regular reply on an issue — confirm NO toast pops, reply shows in thread.
  - Send a reply as self — confirm right-aligned with blue tint.
  - Trigger a status change (e.g., dispatch agent) — confirm "STATUS X → Y" inline row appears.
  - Send a reply that takes >5s for the agent — confirm "working for Xs" tick visible and incrementing.
  - Type `@` in composer — confirm dropdown opens, lists resources, picking inserts label + ref.
  - Send with a picked @-ref — confirm the model's response references the resource content.
- [ ] **Deploy note:** no migration. DBOS workflow signature change (adding `resource_refs` kwarg) — in-flight workflows replay safely with default `None`.

## Self-review checklist

- Spec coverage: items 1-3, 5 (UX polish) + @-ref ✓. Item 6 (tool call inline rows) intentionally deferred — track as a follow-up PR.
- Reuse: every polish item touches 1-2 files. @-ref reuses ResourcesContext + parsed_media repo + run_session_turn.
- Open for implementer: confirm `parsed_media` exact column names (`ai_extract_text` vs `transcript`) before writing the resolver query.
- Token-budget caps are conservative defaults; admin tuning UI = separate work.
