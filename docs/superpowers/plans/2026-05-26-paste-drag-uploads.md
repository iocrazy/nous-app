# Paste / Drag-Drop Uploads (sub-plan 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** Let users paste an image OR drag-drop a file directly onto the chat composer (both AIChatPanel and the Issue reply box) — same allow-list + 50MB cap as the picker button, same temp-resource backend path (sub-plan 1).

**Architecture:**
- Extract `useChatAttachmentUpload` hook out of `ChatAttachmentPicker.tsx` so the same upload pipeline (validate → `aiLibraryService.uploadChatAttachment` → push StagedAttachment) is reusable from the composer's paste/drop handlers.
- New `useComposerDropzone` hook owns the drag-enter/leave/over/drop wiring + a `isDragActive` flag for the overlay UI. Works on any container element.
- New `useComposerPaste` helper attaches `onPaste` to the textarea, extracts `ClipboardEvent.clipboardData.files`, routes via the upload hook.
- **Backend**: `IssueMessagePost.attachments` is added (currently absent), and `respond_to_issue_reply` threads attachments through to `run_session_turn` (which already accepts them — sub-plan 1's session path).

**Tech Stack:** React 19 + Vite, existing `ChatAttachmentPicker` infrastructure (`ACCEPT_ATTR`, `validateFileBatch`, `MAX_FILES_AT_ONCE`), `aiLibraryService.uploadChatAttachment`, FastAPI Pydantic schema extension, DBOS workflow signature change.

**Spec:** `docs/superpowers/specs/2026-05-25-agent-media-context-and-assets-design.md` (gitignored, local). Sub-plan 3 of 5. Builds on sub-plan 1 (#351) and sub-plan 2 (#352).

---

## Locked design decisions (from brainstorm with user, 2026-05-26)

1. **Scope = AIChatPanel + IssueReplyBox.** IssueReplyBox currently has NO attachment UI (only an agent picker). This plan adds both the picker AND paste/drag to it. Backend issue-reply path is extended to accept attachments (otherwise the IssueReplyBox UI would be decorative).
2. **Drop zone = whole composer** (textarea + chip strip + buttons). Show a full-overlay "Drop files to attach" on dragenter.
3. **Reuse existing upload pipeline.** `ACCEPT_ATTR`, `MAX_FILES_AT_ONCE`, `validateFileBatch`, `aiLibraryService.uploadChatAttachment` are NOT touched — paste/drag funnels through the same `handleFiles(FileList)` logic.
4. **Eager upload** on paste/drop (same as picker today).
5. **Failure = same toast pattern** as picker (per-file error toast, valid files still upload).
6. **Backend payload** = `IssueMessagePost.attachments: Optional[List[AttachmentRequest]]` (same shape as chat sessions use). Threaded through `respond_to_issue_reply` → `_run_reply_turns` → `run_session_turn(attachments=...)`.

## Reuse these existing utilities (don't re-roll)

- `frontend/components/ChatAttachmentPicker.tsx` — the picker. The upload flow inside it (`handleFiles`) is what we extract.
- `frontend/components/ChatAttachmentPicker.helpers.ts` — `ACCEPT_ATTR`, `MAX_FILES_AT_ONCE`, `formatBytes`, `validateFileBatch`. UNCHANGED.
- `frontend/services/aiLibraryService.ts::uploadChatAttachment(file)` — UNCHANGED.
- `backend/app/schemas/ai_library_chat.py::AttachmentRequest` — UNCHANGED. The chat path already uses it.
- `backend/app/services/ai/chat/chat_attachment_resolver.py::resolve_attachments` — UNCHANGED. Both chat and issue session turns hit it via `run_session_turn`.

## File structure

- **Create** `frontend/hooks/useChatAttachmentUpload.ts` — `{ handleFiles, uploading }`.
- **Create** `frontend/hooks/useComposerDropzone.ts` — `{ rootProps: { onDragEnter, onDragOver, onDragLeave, onDrop }, isDragActive }`.
- **Create** `frontend/hooks/useComposerPaste.ts` — `{ onPaste }` handler factory.
- **Create** `frontend/hooks/useChatAttachmentUpload.test.ts`, `useComposerDropzone.test.ts`, `useComposerPaste.test.ts`.
- **Modify** `frontend/components/ChatAttachmentPicker.tsx` — replace inline `handleFiles` with the new hook (no behavior change).
- **Modify** `frontend/components/AIChatPanel.tsx` — wrap composer in dropzone container, wire paste on ChatInput, render overlay.
- **Modify** `frontend/components/chat/ChatInput.tsx` — add optional `onPaste` prop forwarded to the textarea.
- **Modify** `frontend/components/Todolist/IssueReplyBox.tsx` — add ChatAttachmentPicker + dropzone + paste + threading attachments via parent `onSubmit`.
- **Modify** the parent that owns IssueReplyBox's submission (find via grep; likely `IssueDetailView.tsx`) — accept attachments in its onSubmit + forward to issueService.
- **Modify** `frontend/services/issueService.ts` (or wherever `postIssueMessage` lives) — accept `attachments?: AttachmentRequest[]` and POST in the body.
- **Modify** `backend/app/schemas/issue_messages.py` (or wherever `IssueMessagePost` lives) — add `attachments: Optional[List[AttachmentRequest]] = None`.
- **Modify** `backend/app/api/issue_messages_router.py::post_issue_message` — forward attachments to `respond_to_issue_reply`.
- **Modify** `backend/app/workflows/issue_lifecycle.py::respond_to_issue_reply` (+ `_run_reply_turns`) — accept attachments, pass to `run_session_turn(attachments=...)`.
- Tests for the backend changes.

---

## Task 1: Extract `useChatAttachmentUpload` hook (no behavior change)

**Files:**
- Create: `frontend/hooks/useChatAttachmentUpload.ts`
- Modify: `frontend/components/ChatAttachmentPicker.tsx` (use the hook)
- Test: `frontend/hooks/useChatAttachmentUpload.test.ts`

- [ ] **Step 1: Read** `components/ChatAttachmentPicker.tsx` end-to-end (the `handleFiles` function is the unit to extract — 30-45 lines).
- [ ] **Step 2: Write the failing test** — mock `aiLibraryService.uploadChatAttachment` + `useToast`, render the hook with `renderHook` from `@testing-library/react`, call `handleFiles(fakeFileList)`, assert `attachments` callback was called with the staged result, `uploading` flips false at the end.
- [ ] **Step 3: Run → fail.**
- [ ] **Step 4: Implement** the hook. Signature:
  ```ts
  export function useChatAttachmentUpload(opts: {
    attachments: StagedAttachment[];
    onChange: (next: StagedAttachment[]) => void;
  }): { handleFiles: (files: FileList | File[] | null) => Promise<void>; uploading: boolean };
  ```
  Body: lift `handleFiles` verbatim from the picker; lift `_readAsDataUrl` (or inline) for image previews; depend on `useState<boolean>` for `uploading`.
- [ ] **Step 5: Refactor ChatAttachmentPicker** to use the hook. Remove the inline `handleFiles` + `useState<uploading>`. Picker's other state (input ref, pickerOpen, etc.) stays.
- [ ] **Step 6: Run** the existing picker tests AND the new hook tests — confirm zero regressions.
- [ ] **Step 7: Commit**: `git commit -m "refactor(chat): extract useChatAttachmentUpload hook from ChatAttachmentPicker"`.

---

## Task 2: `useComposerDropzone` hook + drop overlay

**Files:**
- Create: `frontend/hooks/useComposerDropzone.ts`
- Test: `frontend/hooks/useComposerDropzone.test.ts`

- [ ] **Step 1: Write failing test.** Render the hook, simulate `dragEnter` → assert `isDragActive=true`; simulate `dragLeave` (with related-target outside the container) → assert false; simulate `drop` → assert `onFiles(files)` called with the dropped files, `isDragActive` resets to false.
- [ ] **Step 2: Implement.** Signature:
  ```ts
  export function useComposerDropzone(opts: {
    onFiles: (files: FileList) => void | Promise<void>;
    disabled?: boolean;
  }): {
    rootProps: {
      onDragEnter: (e: React.DragEvent) => void;
      onDragOver: (e: React.DragEvent) => void;
      onDragLeave: (e: React.DragEvent) => void;
      onDrop: (e: React.DragEvent) => void;
    };
    isDragActive: boolean;
  };
  ```
  Tricky bits:
  - Use a ref-counter (`dragCounter`) for enter/leave because dragenter/leave fire per child element — without counting, isDragActive flickers.
  - `onDragOver`: `e.preventDefault()` + `e.dataTransfer.dropEffect = 'copy'`.
  - `onDrop`: `e.preventDefault()`, reset counter, call `onFiles(e.dataTransfer.files)`.
  - Respect `disabled` — no-op all handlers when disabled is true (but still preventDefault so browser doesn't navigate to the file URL).
- [ ] **Step 3: Commit.**

---

## Task 3: `useComposerPaste` hook (paste image from clipboard)

**Files:**
- Create: `frontend/hooks/useComposerPaste.ts`
- Test: `frontend/hooks/useComposerPaste.test.ts`

- [ ] **Step 1: Write failing test.** Simulate a `ClipboardEvent` with `clipboardData.files` containing an image File, call the returned `onPaste` → assert `onFiles(files)` called. Test the no-op path: paste with `clipboardData.files.length === 0` (text-only paste) → assert `onFiles` NOT called (so we don't intercept normal text paste).
- [ ] **Step 2: Implement.** Signature:
  ```ts
  export function useComposerPaste(opts: {
    onFiles: (files: FileList) => void | Promise<void>;
    disabled?: boolean;
  }): { onPaste: (e: React.ClipboardEvent) => void };
  ```
  Body: when `e.clipboardData.files.length > 0` AND not disabled, `e.preventDefault()` + call `onFiles(e.clipboardData.files)`. Else no-op (so text paste is unaffected).
- [ ] **Step 3: Commit.**

---

## Task 4: Wire AIChatPanel composer (paste + drag-drop + overlay)

**Files:**
- Modify: `frontend/components/AIChatPanel.tsx` (composer JSX + handlers)
- Modify: `frontend/components/chat/ChatInput.tsx` (add `onPaste` prop)
- Test: extend `frontend/components/AIChatPanel.test.tsx` IF an existing test file is present; else create a focused test for the composer's paste-flow.

- [ ] **Step 1: Read** `AIChatPanel.tsx` around the composer JSX (lines ~440-510 per recon). Identify the wrapper div around ChatInput + ChatAttachmentPicker — that becomes the dropzone container.
- [ ] **Step 2: Add `onPaste` prop to ChatInput.** Pure forwarding to the textarea: `<textarea ... onPaste={onPaste}>`. Update the prop interface.
- [ ] **Step 3: Wire AIChatPanel.**
  - Already has `stagedAttachments` + `setStagedAttachments`. Add `const { handleFiles, uploading } = useChatAttachmentUpload({ attachments: stagedAttachments, onChange: setStagedAttachments });`.
  - Add `const { rootProps, isDragActive } = useComposerDropzone({ onFiles: handleFiles, disabled: !activeSessionId });`.
  - Add `const { onPaste } = useComposerPaste({ onFiles: handleFiles, disabled: !activeSessionId });`.
  - Wrap the composer JSX in `<div {...rootProps} className="relative">` and pass `onPaste={onPaste}` to `<ChatInput ... />`.
  - Render the overlay conditionally inside the wrapper:
    ```tsx
    {isDragActive && (
      <div className="absolute inset-0 z-10 flex items-center justify-center pointer-events-none bg-blue-500/10 border-2 border-dashed border-blue-400 rounded-lg">
        <span className="text-sm font-medium text-blue-200">Drop files to attach</span>
      </div>
    )}
    ```
  - The overlay is `pointer-events-none` so the drop event still hits the wrapper.
- [ ] **Step 4: Test** — at minimum, verify `npx vitest run --reporter=default` passes with zero regressions. If AIChatPanel has an existing test, add ONE smoke test: render with mocked services + simulate dragEnter → confirm overlay text appears.
- [ ] **Step 5: Commit.**

---

## Task 5: Backend — `IssueMessagePost.attachments` + threading

**Files:**
- Modify: `backend/app/schemas/issue_messages.py` (or wherever `IssueMessagePost` lives — grep first)
- Modify: `backend/app/api/issue_messages_router.py::post_issue_message`
- Modify: `backend/app/workflows/issue_lifecycle.py::respond_to_issue_reply` (+ `_run_reply_turns`)
- Test: `backend/tests/test_issue_reply_attachments.py`

- [ ] **Step 1: Reuse-surface read.**
  - `grep -rn "class IssueMessagePost\|IssueMessagePost" backend/app/schemas/` — find the schema file.
  - Read `respond_to_issue_reply` + `_run_reply_turns` in `backend/app/workflows/issue_lifecycle.py` end-to-end. The plan assumes `_run_reply_turns` eventually calls `run_session_turn(attachments=...)` — confirm that name and that it accepts `attachments`. If `_run_reply_turns` constructs the turn differently, adapt the threading path.
  - Confirm `AttachmentRequest` is the schema to reuse: `grep -n "class AttachmentRequest" backend/app/schemas/ai_library_chat.py`.

- [ ] **Step 2: Write failing test.**
  ```python
  # backend/tests/test_issue_reply_attachments.py
  import pytest
  from app.schemas.ai_library_chat import AttachmentRequest
  from app.schemas.issue_messages import IssueMessagePost  # adapt import

  def test_issue_message_post_accepts_attachments():
      p = IssueMessagePost(
          body="hello",
          attachments=[
              AttachmentRequest(kind="image", url="personal/u1/temp/x.png", mime="image/png")
          ],
      )
      assert len(p.attachments) == 1
      assert p.attachments[0].kind == "image"


  def test_issue_message_post_attachments_optional():
      p = IssueMessagePost(body="hello")
      assert p.attachments is None or p.attachments == []


  # If respond_to_issue_reply is a coroutine you can test in isolation
  # (mock the session/turn primitives), add a test that asserts attachments
  # are forwarded into _run_reply_turns (or whichever lower fn accepts them).
  ```

- [ ] **Step 3: Implement schema:**
  ```python
  # in IssueMessagePost
  from typing import List, Optional
  from app.schemas.ai_library_chat import AttachmentRequest

  class IssueMessagePost(BaseModel):
      body: str = Field(..., min_length=1)
      attachments: Optional[List[AttachmentRequest]] = None
  ```

- [ ] **Step 4: Implement router forwarding.** In `post_issue_message`, change the start_workflow line to pass attachments:
  ```python
  DBOS.start_workflow(
      respond_to_issue_reply,
      issue_id,
      str(owner_id),
      payload.body,
      [a.model_dump() for a in (payload.attachments or [])],
  )
  ```
  (Serialize to dicts so DBOS can store the workflow input — DBOS doesn't like arbitrary Pydantic objects.)

- [ ] **Step 5: Implement workflow signature.** Update `respond_to_issue_reply(issue_id, user_id, reply_text, attachments=None)` and `_run_reply_turns(... attachments=None)` so the attachments list (raw dicts) gets passed to whichever turn primitive expects `attachments`. If `run_session_turn` takes `attachments: List[AttachmentRequest]`, reconstruct: `[AttachmentRequest(**a) for a in attachments]` at the boundary.

- [ ] **Step 6: Run** `cd backend && uv run pytest tests/test_issue_reply_attachments.py -v` + full backend suite to confirm no regressions in issue tests.

- [ ] **Step 7: Commit.**

---

## Task 6: Frontend — issueService + IssueReplyBox attachment integration

**Files:**
- Modify: `frontend/services/issueService.ts` (or wherever `postIssueMessage` lives)
- Modify: `frontend/components/Todolist/IssueReplyBox.tsx`
- Modify: the parent that supplies `onSubmit` to IssueReplyBox (likely `IssueDetailView.tsx` or `IssueChatThread.tsx`)
- Test: `frontend/components/Todolist/IssueReplyBox.test.tsx`

- [ ] **Step 1: Reuse-surface read.**
  - `grep -rn "postIssueMessage\|issueService\b" frontend/services/ frontend/components/Todolist/` — locate the service.
  - Read IssueReplyBox + the parent that calls `onSubmit` — confirm the data flow.
  - Confirm `StagedAttachment` shape (sub-plan 1's contract) maps to the AttachmentRequest the backend expects (`{kind, url}` → `{kind, url}` — the picker stores `url=file_path` after PR #351).

- [ ] **Step 2: Write failing test.** Render IssueReplyBox with a mocked onSubmit + a mocked tempTtlService/uploadChatAttachment, simulate user typing + paste an image File via the ClipboardEvent, confirm onSubmit is called with `(body, agentId, attachments)` where attachments has 1 entry.

- [ ] **Step 3: Implement issueService.** Add `attachments?: { kind, url }[]` to the post call body so the backend receives them.

- [ ] **Step 4: Implement IssueReplyBox.**
  - Add `stagedAttachments` state + the upload hook.
  - Wrap the textarea + chip strip in a dropzone container.
  - Render the drop overlay when `isDragActive`.
  - Render the ChatAttachmentPicker chips (when staged > 0) above the textarea.
  - Change `onSubmit` signature to `(body, agentId, attachments) => Promise<void>`.
  - In submit: forward attachments to onSubmit; clear stagedAttachments on success.

- [ ] **Step 5: Implement parent (IssueDetailView etc.)** — accept attachments in the onSubmit it provides, forward to `issueService.postIssueMessage`.

- [ ] **Step 6: Run tests.** Confirm new IssueReplyBox tests pass + no regression in Todolist tests.

- [ ] **Step 7: Commit.**

---

## Task 7: Final verification

- [ ] Backend full suite: `cd backend && uv run pytest tests/ -q` → all pass.
- [ ] Frontend full suite: `cd frontend && npx vitest run --reporter=default` → all pass.
- [ ] Import chain OK: `cd backend && uv run python -c "import app.api.issue_messages_router, app.workflows.issue_lifecycle, app.schemas.issue_messages; print('OK')"`.
- [ ] Lint clean (both sides).
- [ ] **Manual sanity** (post-deploy on the dev stack):
  - In AIChatPanel: type some text, then paste an image from clipboard → confirm chip appears + send → confirm the assistant sees the image.
  - In AIChatPanel: drag a video file onto the composer → confirm overlay shows "Drop files to attach", drop → confirm chip + send.
  - In an Issue with an assigned agent: paste an image into the reply box → confirm chip + reply → confirm worker turn responds AND can read the image (this is the sub-plan-1 regression scenario, now reachable via paste UX).
- [ ] **Deploy note for PR body:** no migration. Backend workflow signature changed (`respond_to_issue_reply` now takes a 4th positional arg) — DBOS will reject in-flight workflows started before deploy if they replay. Flag in PR; deploy timing should be when issue-reply queue is quiet (or accept the small churn).

## Self-review checklist

- Spec coverage: AIChatPanel paste (Task 3+4), AIChatPanel drag (Task 2+4), IssueReplyBox paste+drag+chip (Task 6), backend attachment threading (Task 5). Overlay UX (Task 2+4).
- Reuse: picker + helpers + service unchanged; new hooks compose the existing pipeline.
- Open for implementer to confirm by reading: `_run_reply_turns` signature (Task 5 Step 1), exact `IssueReplyBox` parent (Task 6 Step 1).
- Known limitation: text paste is unaffected (we only `preventDefault` when `clipboardData.files.length > 0`). Multi-file paste is supported up to `MAX_FILES_AT_ONCE`.
- Out of scope: paste/drag in any composer outside AIChatPanel and IssueReplyBox (e.g. agent skill editor, settings forms). Out of scope: previewing the file before upload (eager upload matches the picker today).
