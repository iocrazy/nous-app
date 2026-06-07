# IssueReplyBox @-reference resource — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users type `@` in the issue reply composer to reference an existing resource (scoped to the current issue's team + their personal library), so the agent can lazily read it — matching the main AI chat panel.

**Architecture:** Frontend-led. `IssueReplyBox` swaps its `<textarea>` for a tiptap editor and reuses the *already-decoupled* mention primitives (`createResourceMentionExtension`, `ResourcePickerSuggestion`, `collectRefAttachments`, `ResourceRefNode`) — NOT the whole `ChatInput` shell (which lacks IssueReplyBox's agent picker / attachment chips / `⌘↩` send). The backend issue-reply path already resolves `resource_ref` attachments end-to-end (verified — `run_session_turn` does it); the only backend change is an **optional** `team_id` scope filter on `GET /resources/search` so the picker shows team-A+personal instead of all-my-teams. Main chat passes no `team_id` → unchanged.

**Tech Stack:** Frontend React + tiptap (`@tiptap/react`, `@tiptap/starter-kit`) + vitest. Backend FastAPI + SQLAlchemy-Core-over-asyncpg (`db_engine`) + pytest.

**Spec:** `docs/plans/2026-05-31-issue-at-reference.md`.

---

## File Structure

**Backend (search scope filter — do first; frontend depends on the param existing):**
- Modify: `backend/app/repositories/resources_repository.py` — `list_accessible_for_user` gains optional `scope_team_id`.
- Modify: `backend/app/api/resources_search_router.py` — `GET /resources/search` gains optional `team_id` query param.
- Test: `backend/tests/repositories/test_resources_repository_accessible.py` (extend), `backend/tests/api/test_resources_search_router.py` (extend).

**Frontend (search plumbing → composer → wiring):**
- Modify: `frontend/services/resourceSearchService.ts` — `searchResources` accepts optional `teamId`.
- Modify: `frontend/hooks/useResourceSearch.ts` — accepts optional `teamId`, threads it through + cache key.
- Modify: `frontend/services/issueMessageService.ts` — widen `IssueMessageAttachment` to include the `resource_ref` shape.
- Modify: `frontend/components/Todolist/IssueReplyBox.tsx` — textarea → tiptap + @ picker; emit merged attachments; accept `teamId` prop.
- Modify: `frontend/components/Todolist/IssueDetailView.tsx` — pass `teamId` to IssueReplyBox; map `resource_ref` attachments in `handleReply`.
- Test: `frontend/hooks/useResourceSearch.test.ts` (extend), `frontend/components/Todolist/IssueReplyBox.test.tsx` (extend).

---

## Task 1: Backend — `scope_team_id` filter on `list_accessible_for_user`

**Files:**
- Modify: `backend/app/repositories/resources_repository.py` (`list_accessible_for_user`, ~line 1459-1535)
- Test: `backend/tests/repositories/test_resources_repository_accessible.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/repositories/test_resources_repository_accessible.py`:

```python
@pytest.mark.asyncio
async def test_scope_team_id_narrows_to_team_plus_personal():
    repo = ResourcesRepository()
    captured = {}

    async def _fake_fetch(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    with patch("app.db.engine.fetch_all", side_effect=_fake_fetch):
        await repo.list_accessible_for_user(
            user_id="user-1",
            scope_team_id="900123",
        )

    sql = captured["sql"].lower()
    # Still gated on membership (no escalation via a forged team_id)…
    assert "team_members" in sql
    # …but now narrowed to the passed team OR the caller's personal team.
    assert captured["params"]["scope_team_id"] == "900123"
    assert "kind = 'personal'" in sql


@pytest.mark.asyncio
async def test_scope_team_id_omitted_keeps_all_teams():
    repo = ResourcesRepository()
    captured = {}

    async def _fake_fetch(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    with patch("app.db.engine.fetch_all", side_effect=_fake_fetch):
        await repo.list_accessible_for_user(user_id="user-1")

    assert "scope_team_id" not in captured["params"]
    assert "team_members" in captured["sql"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/repositories/test_resources_repository_accessible.py::test_scope_team_id_narrows_to_team_plus_personal -v`
Expected: FAIL — `list_accessible_for_user()` got an unexpected keyword argument `scope_team_id`.

- [ ] **Step 3: Add the `scope_team_id` parameter + narrowed predicate**

In `backend/app/repositories/resources_repository.py`, change the signature of `list_accessible_for_user`:

```python
    async def list_accessible_for_user(
        self,
        *,
        user_id: str,
        q: str = "",
        kinds: list[str] | None = None,
        limit: int = 20,
        cursor: str | None = None,
        scope_team_id: str | None = None,
    ) -> list[dict]:
```

Then replace the membership-filter block (the three lines currently reading
`"  AND ri.scope_id::text IN ( "`, the `SELECT team_id::text FROM public.team_members WHERE user_id = :user_id`, and `"      ) "`) with a conditional. Keep `params` as-is up to that point, then:

```python
        if scope_team_id is not None:
            # Issue-scoped picker: narrow to the current team (only if the
            # caller is a member — no escalation) OR the caller's personal team.
            sql_parts.append(
                "  AND ri.scope_id::text IN ( "
                "        SELECT team_id::text FROM public.team_members "
                "          WHERE user_id = :user_id AND team_id::text = :scope_team_id "
                "        UNION "
                "        SELECT id::text FROM public.teams "
                "          WHERE owner_id::text = :user_id AND kind = 'personal' "
                "      ) "
            )
            params["scope_team_id"] = scope_team_id
        else:
            sql_parts.append(
                "  AND ri.scope_id::text IN ( "
                "        SELECT team_id::text FROM public.team_members WHERE user_id = :user_id "
                "      ) "
            )
```

(The `:user_id` param is already set earlier in `params`; do not duplicate it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/repositories/test_resources_repository_accessible.py -v`
Expected: PASS (all 4 — the 2 existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/resources_repository.py backend/tests/repositories/test_resources_repository_accessible.py
git commit -m "feat(resources): optional scope_team_id filter on list_accessible_for_user"
```

---

## Task 2: Backend — `team_id` query param on `GET /resources/search`

**Files:**
- Modify: `backend/app/api/resources_search_router.py`
- Test: `backend/tests/api/test_resources_search_router.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/api/test_resources_search_router.py` (mirror the existing `test_search_returns_results_and_counts` patch target + auth pattern; read that test first for the exact `client` fixture / auth override it uses, and reuse it):

```python
def test_search_forwards_team_id_to_repo():
    captured = {}

    async def _fake(**kwargs):
        captured.update(kwargs)
        return []

    with patch(
        "app.repositories.resources_repository.ResourcesRepository.list_accessible_for_user",
        side_effect=_fake,
    ):
        r = client.get("/api/v1/resources/search?q=story&team_id=900123")
        assert r.status_code == 200
    assert captured["scope_team_id"] == "900123"


def test_search_without_team_id_passes_none():
    captured = {}

    async def _fake(**kwargs):
        captured.update(kwargs)
        return []

    with patch(
        "app.repositories.resources_repository.ResourcesRepository.list_accessible_for_user",
        side_effect=_fake,
    ):
        r = client.get("/api/v1/resources/search?q=story")
        assert r.status_code == 200
    assert captured.get("scope_team_id") is None
```

(If the existing test file does NOT already construct `client` + auth at module level, copy that setup from `test_search_returns_results_and_counts` into these two tests so they run standalone.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/api/test_resources_search_router.py::test_search_forwards_team_id_to_repo -v`
Expected: FAIL — `KeyError: 'scope_team_id'` (router doesn't pass it yet).

- [ ] **Step 3: Add the `team_id` param + forward it**

In `backend/app/api/resources_search_router.py`, add the param to `search_resources` (after `kinds`):

```python
    team_id: Optional[str] = Query(None, description="narrow to this team + personal"),
```

And forward it in the `repo.list_accessible_for_user(...)` call:

```python
    rows = await repo.list_accessible_for_user(
        user_id=str(auth.user_id),
        q=q,
        kinds=kinds_list or None,
        limit=limit,
        scope_team_id=team_id,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/api/test_resources_search_router.py -v`
Expected: PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/resources_search_router.py backend/tests/api/test_resources_search_router.py
git commit -m "feat(resources): GET /resources/search accepts optional team_id scope"
```

---

## Task 3: Frontend — thread `teamId` through search service + hook

**Files:**
- Modify: `frontend/services/resourceSearchService.ts`
- Modify: `frontend/hooks/useResourceSearch.ts`
- Test: `frontend/hooks/useResourceSearch.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `frontend/hooks/useResourceSearch.test.ts` (it already imports `* as svc` and `renderHook` — reuse those):

```typescript
it('passes teamId through to searchResources', async () => {
  const spy = vi.spyOn(svc, 'searchResources').mockResolvedValue({
    results: [], counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 }, next_cursor: null,
  });
  renderHook(() => useResourceSearch('story', '', 'team-900'));
  await waitFor(() =>
    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ teamId: 'team-900' })),
  );
});
```

(If `waitFor` isn't imported in this test file, add it to the `@testing-library/react` import.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run hooks/useResourceSearch.test.ts -t "passes teamId"`
Expected: FAIL — `searchResources` called without `teamId` (hook signature has only 2 args).

- [ ] **Step 3: Add `teamId` to the service**

In `frontend/services/resourceSearchService.ts`, extend the params + set the query param:

```typescript
export async function searchResources(params: {
  q: string;
  kinds: string;
  limit?: number;
  teamId?: string;
  signal?: AbortSignal;
}): Promise<ResourceSearchResponse> {
  const headers = await getAuthHeaders();
  const u = new URL(`${API}/api/v1/resources/search`, window.location.origin);
  u.searchParams.set('q', params.q);
  if (params.kinds) u.searchParams.set('kinds', params.kinds);
  if (params.limit) u.searchParams.set('limit', String(params.limit));
  if (params.teamId) u.searchParams.set('team_id', params.teamId);
  const res = await fetch(u.toString(), { headers, signal: params.signal });
  if (!res.ok) throw new Error(`search failed: ${res.status}`);
  return res.json();
}
```

- [ ] **Step 4: Add `teamId` to the hook (signature + cache key + call)**

In `frontend/hooks/useResourceSearch.ts`:

```typescript
export function useResourceSearch(query: string, kinds: string, teamId?: string) {
```

Change the cache key to include `teamId`:

```typescript
    const key = `${query}|${kinds}|${teamId ?? ''}`;
```

Pass `teamId` in the `searchResources` call:

```typescript
        const resp = await searchResources({ q: query, kinds, limit: 20, teamId, signal: ctrl.signal });
```

And add `teamId` to the effect deps array:

```typescript
  }, [query, kinds, teamId]);
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run hooks/useResourceSearch.test.ts`
Expected: PASS (existing 2 + new 1 — existing tests call `useResourceSearch(q, '')` with `teamId` undefined, still valid).

- [ ] **Step 6: Commit**

```bash
git add frontend/services/resourceSearchService.ts frontend/hooks/useResourceSearch.ts frontend/hooks/useResourceSearch.test.ts
git commit -m "feat(resources): useResourceSearch + searchResources accept optional teamId"
```

---

## Task 4: Frontend — widen `IssueMessageAttachment` for `resource_ref`

**Files:**
- Modify: `frontend/services/issueMessageService.ts` (`IssueMessageAttachment`, ~line 42-45)

No test (pure type change; exercised by Task 6's IssueReplyBox test).

- [ ] **Step 1: Widen the type**

In `frontend/services/issueMessageService.ts`, replace the `IssueMessageAttachment` interface:

```typescript
export type IssueMessageAttachment =
  | { kind: 'image' | 'video' | 'pdf'; url: string; mime?: string }
  | {
      kind: 'resource_ref';
      resource_id: string;
      name: string;
      mime: string;
      scope: { type: 'personal' | 'team'; id: string };
    };
```

(If `attachments?: IssueMessageAttachment[];` at line 51 referenced the old interface name, it still resolves — same name, now a union.)

- [ ] **Step 2: Typecheck (no new errors in this file)**

Run: `cd frontend && npm run typecheck 2>&1 | grep issueMessageService || echo "no new errors in issueMessageService"`
Expected: `no new errors in issueMessageService` (pre-existing baseline errors elsewhere are unrelated).

- [ ] **Step 3: Commit**

```bash
git add frontend/services/issueMessageService.ts
git commit -m "feat(issues): IssueMessageAttachment union adds resource_ref shape"
```

---

## Task 5: Frontend — IssueReplyBox textarea → tiptap + @ picker

**Files:**
- Modify: `frontend/components/Todolist/IssueReplyBox.tsx`

This is the largest task. It (a) adds a `teamId` prop, (b) replaces the textarea with a tiptap editor wired to the existing mention primitives, (c) renders `ResourcePickerSuggestion` as an overlay, (d) keeps `⌘↩` send / agent picker / attachment chips / paste / drag-drop, (e) merges file attachments + resource refs into the `onSubmit` third arg. Test wiring is Task 6.

- [ ] **Step 1: Update imports + props**

At the top of `frontend/components/Todolist/IssueReplyBox.tsx`, add imports:

```typescript
import React, { useEffect, useRef, useState } from 'react';
import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Placeholder from '@tiptap/extension-placeholder';
import type { Editor } from '@tiptap/core';
import { createResourceMentionExtension } from '../chat/ChatInputResourceMention';
import { ResourcePickerSuggestion } from '../chat/ResourcePickerSuggestion';
import { useResourceSearch } from '../../hooks/useResourceSearch';
import type { ResourceSearchResult, ResourceRefAttachment } from '../../types';
```

(Keep the existing imports: `useTranslation`, `Send`, `ChevronDown`, `AgentRef`, `ChatAttachmentPicker`, `StagedAttachment`, `useChatAttachmentUpload`, `useComposerDropzone`, `useComposerPaste`.)

Extend the props interface + the `onSubmit` attachment arg to the merged union:

```typescript
type ComposerAttachment = StagedAttachment | ResourceRefAttachment;

interface IssueReplyBoxProps {
  agents: AgentRef[];
  defaultAgentId?: string | null;
  /** Current issue's team id — narrows the @ picker to team + personal. */
  teamId?: string;
  /** Parent owns submission. Third arg carries staged files AND resource refs. */
  onSubmit: (body: string, agentId: string | null, attachments: ComposerAttachment[]) => Promise<void>;
  disabled?: boolean;
}
```

Update the component signature to destructure `teamId`:

```typescript
export const IssueReplyBox: React.FC<IssueReplyBoxProps> = ({ agents, defaultAgentId, teamId, onSubmit, disabled }) => {
```

- [ ] **Step 2: Add mention picker state + the editor (place AFTER the rewritten `submit` from Step 3 — the editor's `handleKeyDown` closes over `submit`, so `submit` must be declared above the `useEditor` call to avoid a TDZ reference. Do Step 3 first, then insert this block immediately below it.)**

```typescript
  // --- @-mention picker state (mirrors AIChatPanel) ---
  const editorRef = useRef<Editor | null>(null);
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionQuery, setMentionQuery] = useState('');
  const [mentionActiveKind, setMentionActiveKind] = useState<
    '' | 'video' | 'image' | 'doc' | 'audio' | 'pdf'
  >('');
  const [mentionActiveIndex, setMentionActiveIndex] = useState(0);
  const { data: mentionData, loading: mentionLoading } = useResourceSearch(
    mentionQuery,
    mentionActiveKind,
    teamId,
  );

  const editor = useEditor({
    extensions: [
      StarterKit.configure({ hardBreak: false }),
      Placeholder.configure({ placeholder: 'Reply' }),
      createResourceMentionExtension({ onPick: () => Promise.resolve(null) }),
    ],
    editorProps: {
      attributes: {
        class: 'w-full bg-transparent px-3 py-2 text-[14px] text-zinc-200 focus:outline-none min-h-[72px] tiptap-composer',
      },
      handleKeyDown(_view, event) {
        // ⌘↩ / Ctrl+↩ → send (NOT plain Enter; issue replies are multi-line)
        if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
          event.preventDefault();
          void submit();
          return true;
        }
        // '@' opens the picker (let the char insert first, then notify)
        if (event.key === '@') {
          setTimeout(() => { setMentionQuery(''); setMentionActiveIndex(0); setMentionOpen(true); }, 0);
          return false;
        }
        return false;
      },
      handlePaste(_view, event) {
        onPaste(event as unknown as React.ClipboardEvent);
        return false;
      },
    },
    editable: !inputBlocked,
  });

  useEffect(() => { editorRef.current = editor; }, [editor]);

  // Live query tracking after '@' (mirror ChatInput: track chars until whitespace)
  useEffect(() => {
    if (!editor) return;
    const handleUpdate = () => {
      const { from } = editor.state.selection;
      const before = editor.state.doc.textBetween(Math.max(0, from - 80), from);
      const atIdx = before.lastIndexOf('@');
      if (atIdx === -1) { setMentionOpen(false); return; }
      const q = before.slice(atIdx + 1);
      if (/[\s\n,;]/.test(q)) { setMentionOpen(false); return; }
      setMentionQuery(q);
    };
    editor.on('update', handleUpdate);
    return () => { editor.off('update', handleUpdate); };
  }, [editor]);

  // Esc closes the picker
  useEffect(() => {
    if (!mentionOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setMentionOpen(false); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [mentionOpen]);

  const handleMentionSelect = (item: ResourceSearchResult) => {
    (editor?.commands as unknown as {
      insertResourceRef: (i: ResourceSearchResult) => boolean;
    } | undefined)?.insertResourceRef(item);
    setMentionOpen(false);
    setMentionQuery('');
    editor?.commands.focus();
  };

  function collectRefs(): ResourceRefAttachment[] {
    const refs: ResourceRefAttachment[] = [];
    editor?.state.doc.descendants((node) => {
      if (node.type.name === 'resourceRef') {
        refs.push({
          kind: 'resource_ref',
          resource_id: String(node.attrs.resourceId ?? ''),
          name: String(node.attrs.name ?? ''),
          mime: String(node.attrs.mime ?? ''),
          scope: node.attrs.scope ?? { type: 'personal', id: '' },
        });
      }
    });
    return refs;
  }
```

- [ ] **Step 3: Rewrite `submit()` to read from the editor + merge attachments**

Replace the existing `submit` function with:

```typescript
  const submit = async () => {
    if (!editor) return;
    const trimmed = editor.getText().trim();
    const refs = collectRefs();
    if ((!trimmed && refs.length === 0) || submitting || uploading) return;
    setSubmitting(true);
    try {
      await onSubmit(trimmed, agentId, [...stagedAttachments, ...refs]);
      editor.commands.clearContent(true);
      setStagedAttachments([]);
    } catch {
      // parent toasts; keep content + chips for retry
    } finally {
      setSubmitting(false);
    }
  };
```

Also keep the editor's editable state in sync with `inputBlocked` (add after the editor-ref effect):

```typescript
  useEffect(() => { editor?.setEditable(!inputBlocked); }, [editor, inputBlocked]);
```

- [ ] **Step 4: Replace the `<textarea>` JSX with the editor + picker overlay**

Replace the `<textarea ... />` element (the whole `<textarea>` block) with:

```tsx
      {mentionOpen && (
        <div className="absolute bottom-full left-0 z-20 px-2 pb-1">
          <ResourcePickerSuggestion
            items={mentionData.results}
            query={mentionQuery}
            loading={mentionLoading}
            counts={mentionData.counts}
            activeKind={mentionActiveKind}
            onKindChange={setMentionActiveKind}
            onSelect={handleMentionSelect}
            activeIndex={mentionActiveIndex}
          />
        </div>
      )}
      <EditorContent editor={editor} />
```

(The `relative` wrapper div already exists on the outer container, so the overlay's `absolute` positions correctly. Leave the `{ ...rootProps }` wrapper, the chip strip, the footer row with the agent picker + send button, and the drag overlay unchanged. The footer hint text "⌘↩ to send" stays accurate.)

- [ ] **Step 5: Typecheck**

Run: `cd frontend && npm run typecheck 2>&1 | grep "IssueReplyBox" || echo "no new IssueReplyBox errors"`
Expected: `no new IssueReplyBox errors`.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/Todolist/IssueReplyBox.tsx
git commit -m "feat(issues): IssueReplyBox @-reference picker (tiptap + reused mention parts)"
```

---

## Task 6: Frontend — IssueReplyBox tests (existing pass on tiptap + new @-ref test)

**Files:**
- Modify: `frontend/components/Todolist/IssueReplyBox.test.tsx`

The existing tests use `container.querySelector('textarea')` — that selector breaks now that it's a tiptap `contenteditable`. Update them to drive the editor, and add a resource-ref test.

- [ ] **Step 1: Update existing tests to target the tiptap editor**

In `frontend/components/Todolist/IssueReplyBox.test.tsx`:

- Replace each `container.querySelector('textarea')!` with the editor element:
  ```typescript
  const editor = container.querySelector('.tiptap-composer, [contenteditable="true"]') as HTMLElement;
  ```
- Replace `fireEvent.change(textarea, { target: { value: 'hello' } })` (tiptap has no `value`) with a `beforeEach` mock of `editor.getText()`. Simplest robust approach: mock the tiptap module so `getText()` returns a fixed body and `descendants` yields no refs. Add at the top (after the existing `vi.mock` calls):
  ```typescript
  vi.mock('@tiptap/react', async (orig) => {
    const actual = await orig<typeof import('@tiptap/react')>();
    return {
      ...actual,
      useEditor: () => ({
        getText: () => 'hello',
        commands: { clearContent: vi.fn(), focus: vi.fn() },
        setEditable: vi.fn(),
        state: { doc: { descendants: vi.fn(), textBetween: () => '' }, selection: { from: 0 } },
        on: vi.fn(),
        off: vi.fn(),
      }),
      EditorContent: () => null,
    };
  });
  ```
- The "type a body" steps become no-ops (body is mocked to `'hello'`); keep the paste-file → chip → send-click → assert-onSubmit flow. Update the body assertion in "passes attachments" from `expect(args[0]).toBe('hello')` (still `'hello'`). The attachments assertion stays (file chip still maps to `{kind:'image', url:...}`).
- The "renders the attachment picker" and "shows the drop-files overlay" tests don't touch the textarea — leave them.

- [ ] **Step 2: Add the resource-ref test**

```typescript
it('emits a resource_ref attachment when a resource is referenced', async () => {
  // Re-mock useEditor for THIS test so descendants yields one resourceRef node.
  const { useEditor } = await import('@tiptap/react');
  vi.mocked(useEditor).mockReturnValueOnce({
    getText: () => 'see this',
    commands: { clearContent: vi.fn(), focus: vi.fn() },
    setEditable: vi.fn(),
    state: {
      doc: {
        descendants: (cb: (n: unknown) => void) =>
          cb({ type: { name: 'resourceRef' }, attrs: { resourceId: '900', name: 'demo.mp4', mime: 'video/mp4', scope: { type: 'team', id: 't1' } } }),
        textBetween: () => '',
      },
      selection: { from: 0 },
    },
    on: vi.fn(),
    off: vi.fn(),
  } as never);

  const onSubmit = vi.fn().mockResolvedValue(undefined);
  const { container } = render(
    <IssueReplyBox agents={_agents as never} defaultAgentId="a1" teamId="t1" onSubmit={onSubmit} />,
  );
  const sendBtn = container.querySelector('button[type="submit"], button[aria-label*="send" i]')!;
  fireEvent.click(sendBtn);

  await waitFor(() => {
    const atts = onSubmit.mock.calls[0][2];
    const ref = atts.find((a: { kind: string }) => a.kind === 'resource_ref');
    expect(ref).toMatchObject({ kind: 'resource_ref', resource_id: '900', name: 'demo.mp4' });
  });
});
```

(If `vi.mocked(useEditor).mockReturnValueOnce` doesn't compose with the module-level `vi.mock`, instead make the module-level `useEditor` mock read from a mutable `let editorState` variable that each test sets in its `beforeEach`/body. Pick whichever the test runner accepts; the assertion — onSubmit's 3rd arg contains a `resource_ref` with `resource_id:'900'` — is the fixed contract.)

- [ ] **Step 3: Run the tests**

Run: `cd frontend && npx vitest run components/Todolist/IssueReplyBox.test.tsx`
Expected: PASS (all — updated existing + new resource_ref test).

- [ ] **Step 4: Commit**

```bash
git add frontend/components/Todolist/IssueReplyBox.test.tsx
git commit -m "test(issues): IssueReplyBox tiptap editor + resource_ref attachment"
```

---

## Task 7: Frontend — wire IssueDetailView (pass teamId + map resource_ref)

**Files:**
- Modify: `frontend/components/Todolist/IssueDetailView.tsx` (`handleReply` ~line 165-183; `<IssueReplyBox>` render ~line 329-333)

- [ ] **Step 1: Map resource_ref in `handleReply`**

Replace the `attachmentPayload` mapping inside `handleReply` (currently maps only `{kind, url, mime}`):

```typescript
  const handleReply = async (
    body: string,
    agentId: string | null,
    attachments: import('./IssueReplyBox').ComposerAttachment[] = [],
  ) => {
    try {
      const attachmentPayload = attachments.length > 0
        ? attachments.map((a) =>
            a.kind === 'resource_ref'
              ? { kind: a.kind, resource_id: a.resource_id, name: a.name, mime: a.mime, scope: a.scope }
              : { kind: a.kind, url: a.url, mime: a.mime ?? undefined },
          )
        : undefined;
      await postIssueMessage(issue.id, { body, agent_id: agentId ?? undefined, attachments: attachmentPayload });
      await refresh();
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Send failed', 'error');
      throw err;
    }
  };
```

If `ComposerAttachment` is not exported from IssueReplyBox, add `export` to its `type ComposerAttachment = ...` declaration in Task 5 Step 1 (change `type ComposerAttachment` → `export type ComposerAttachment`), and import it normally at the top of IssueDetailView instead of the inline `import('./IssueReplyBox')`:
```typescript
import { IssueReplyBox, type ComposerAttachment } from './IssueReplyBox';
```

- [ ] **Step 2: Pass `teamId` to the composer**

In the `<IssueReplyBox ... />` render, add the `teamId` prop (the component already reads `const { teamId } = useParams(...)` at line 46):

```tsx
        <IssueReplyBox
          agents={agents}
          defaultAgentId={defaultAgentId}
          teamId={teamId}
          onSubmit={handleReply}
        />
```

(Match the existing prop list — keep whatever props are already passed; only add `teamId`.)

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npm run typecheck 2>&1 | grep "IssueDetailView" || echo "no new IssueDetailView errors"`
Expected: `no new IssueDetailView errors`.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/Todolist/IssueDetailView.tsx frontend/components/Todolist/IssueReplyBox.tsx
git commit -m "feat(issues): wire IssueReplyBox @-ref — pass teamId + map resource_ref payload"
```

---

## Task 8: Full verification + regression sweep

**Files:** none (verification only)

- [ ] **Step 1: Backend suite**

Run: `cd backend && uv run pytest -q`
Expected: all pass (no regressions; the 4 new search/repo tests included).

- [ ] **Step 2: Frontend suite + typecheck**

Run: `cd frontend && npx vitest run && npm run typecheck 2>&1 | grep -c "error TS"`
Expected: vitest all pass; `error TS` count == the pre-existing baseline (108 — record before starting; no NEW errors introduced).

- [ ] **Step 3: Manual prod-preview verification (after deploy, document results)**

In an issue under team A:
- Type `@` → picker opens, shows only team-A + personal resources (a known team-B resource must NOT appear).
- Pick one → an inline chip renders; `⌘↩` sends.
- The dispatched agent's run shows the `<available_resources>` block and can `ResourceFetch` the resource (read transcript/summary).
- Regression: file attach via paperclip, paste-image, drag-drop, agent picker, and `⌘↩` send all still work.
- Main AI chat: `@` still returns global results (no `team_id` sent) — pick + send unaffected.

- [ ] **Step 4: Update memory**

Update `project_resume_media_context.md` + the MEMORY.md index line: IssueReplyBox @-ref shipped; note backend got an optional `team_id` search scope (issue picker = team+personal, main chat = global).

---

## Self-Review Notes

- **Spec coverage:** Q1 (reuse mention parts, not ChatInput shell) → Task 5. Q3 (`⌘↩` send, `@` Enter-select/Esc-close) → Task 5 Step 2 `handleKeyDown` + Esc effect. Q4 (team+personal scope) → Tasks 1-3. Backend resource_ref resolution = unchanged (verified; no task — correct). Both frontend files + both backend files covered.
- **Deploy order:** backend (Tasks 1-2) ships first so the `team_id` param exists before the frontend sends it; but it's an *optional* param, so even out-of-order there's no break (old backend ignores unknown query params). No hard ordering constraint.
- **Type consistency:** `ComposerAttachment = StagedAttachment | ResourceRefAttachment` (Task 5) is the `onSubmit` 3rd-arg type consumed in Task 7; `ResourceRefAttachment` is the existing `types.ts` shape; `IssueMessageAttachment` union (Task 4) matches the `handleReply` payload mapping (Task 7). `scope_team_id` (repo, Task 1) ↔ `team_id` (router/query, Task 2) ↔ `teamId` (FE service/hook/prop, Tasks 3/5/7) — names differ by layer intentionally (DB param vs HTTP param vs JS prop), each mapping is explicit at its boundary.
- **Known fragility:** Task 6's tiptap mock is the riskiest spot (mocking `useEditor` shape). The plan gives a fallback approach; the fixed contract is the assertion, not the mock mechanism. If the real editor is easier to drive than mock in jsdom, the executor may instead render real tiptap and dispatch real input events — either satisfies the test intent.
