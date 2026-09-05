# Canvas Picker Follow-ups (Files Source Chips · Library-Only Default · Fixed Library Button) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close three real-machine findings on the canvas `@` picker and Library panel (2026-09-05): the "Uploads" shelf is really every file in the scope, the Assets shelf shows assets the user never added to the library, and Text-kind prompt nodes have no way to open the Library panel.

**Architecture:** All three are frontend changes inside `features/canvas-core/` plus one additive query parameter on `GET /api/v1/resources/search`. No migration. The `uploads` store keeps its internal key (it is persisted in localStorage as `mediaStore`) and only its label and filters change. Nothing here changes what a run delivers; only what the pickers offer.

**Tech Stack:** React 19 + TypeScript + Vitest (frontend), FastAPI + SQLAlchemy async ORM (backend, no `text()`).

**Spec:** `docs/superpowers/specs/2026-08-28-asset-library-loadout-design.md` (§6.5 pickers; mig 449 ruling "资产库成员 = 明确入库") and `docs/superpowers/specs/2026-09-03-canvas-library-panel-design.md` (panel). User rulings 2026-09-05: (a) Files shelf gets source chips All / Uploaded / Downloaded / Generated, panel segment renamed alongside; (b) Assets shelves default to In Library Only; (c) a fixed header button opens the Library panel for every prompt kind, and for Text-kind nodes the panel's primary action inserts mention chips instead of references.

## Global Constraints

- UI copy is English Title Case through i18n keys (`t('canvas.library.…')`), Chinese values in `public/locales/zh.json`, English in `en.json`. No emoji in UI; icons are lucide.
- Mocked HTTP bodies in tests use the REAL wire shape (`ResourceSearchResult.id` is a string; `AssetSummary.id` is a string).
- Backend: no new `text()` SQL; filters go through the existing ORM statements in `ResourcesRepository`.
- Never `git add -A`; add the files you changed by name.
- Each task ends with `cd frontend && npx vitest run <touched test files>` green and, for Task 1, `cd backend && uv run pytest tests/api/test_resources_search_router.py tests/api/test_resources_search_counts.py -q` green.
- Existing test ids stay (`library-segment-uploads`, `mention-tab-uploads`, `mention-library-toggle`, `mention-type-chip`, `add-reference`, `prompt-library-button`) — e2e-prod and unit tests key on them.

---

### Task 1: Files shelf — honest name + source chips (panel + picker + backend filter)

**Files:**
- Modify: `backend/app/api/resources_search_router.py` (add `sources` query param)
- Modify: `backend/app/repositories/resources_repository.py` (`list_accessible_for_user`, `count_accessible_by_kind_for_user` gain `sources: list[str] | None`)
- Test: `backend/tests/api/test_resources_search_router.py`
- Modify: `frontend/services/resourceSearchService.ts` (`sources?: string`)
- Modify: `frontend/features/canvas-core/library/librarySearch.ts` (`LibrarySearchOptions.uploadSources?: string`, forwarded by `fetchLibraryUploads`)
- Modify: `frontend/features/canvas-core/library/libraryStore.ts` (`uploadSource: UploadSource` state + `setUploadSource`, reset on `setMediaStore`, NOT persisted)
- Modify: `frontend/features/canvas-core/library/LibraryMediaPage.tsx` (source chip row for the `uploads` store)
- Modify: `frontend/features/canvas-core/smart/nodes/PromptMentionPicker.tsx` (source chip row on the `uploads` tab, local state)
- Modify: `frontend/public/locales/en.json`, `frontend/public/locales/zh.json`
- Test: `frontend/features/canvas-core/library/librarySearch.test.ts`, `frontend/features/canvas-core/library/LibraryPanel.test.tsx`, `frontend/features/canvas-core/smart/nodes/PromptMentionPicker.library.test.tsx`

**Interfaces:**
- Produces (backend): `GET /api/v1/resources/search?sources=upload,web` — csv over the allowlist `{"upload","web","generated","derived"}`; unknown values dropped; empty/absent = no filter. Applied to BOTH `list_accessible_for_user` (rows) and `count_accessible_by_kind_for_user` (badges) as `Resources.source_type.in_(sources)`.
- Produces (frontend):
  ```ts
  // librarySearch.ts
  export type UploadSource = 'all' | 'upload' | 'web' | 'generated';
  /** Wire csv per chip. `generated` folds `derived` in: cover frames cut from a
   *  video are neither uploaded nor downloaded, and a fourth chip for them would
   *  name an internal source_type nobody chose. */
  export const UPLOAD_SOURCE_CSV: Record<UploadSource, string> = {
    all: '', upload: 'upload', web: 'web', generated: 'generated,derived',
  };
  export const UPLOAD_SOURCES: readonly UploadSource[] = ['all', 'upload', 'web', 'generated'];
  ```
  `LibrarySearchOptions.uploadSources?: string` (the csv), forwarded as `sources` by `fetchLibraryUploads`.
- Label change: `canvas.library.storeUploads` → en `"Files"`, zh `"资源"`. New keys: `canvas.library.sourceAll` = "All" / "全部", `canvas.library.sourceUploaded` = "Uploaded" / "上传", `canvas.library.sourceDownloaded` = "Downloaded" / "下载", `canvas.library.sourceGenerated` = "Generated" / "生成".

- [ ] **Step 1: Backend failing test** — in `backend/tests/api/test_resources_search_router.py` add `test_search_forwards_sources_allowlisted`: call `/api/v1/resources/search?q=&sources=upload,web,bogus` with the existing auth fixture and the existing `list_accessible_for_user` patch pattern; assert the repo was called with `sources=["upload", "web"]`, and add a sibling asserting `sources` absent → `sources=None`. Run: `cd backend && uv run pytest tests/api/test_resources_search_router.py -q` → FAIL (unexpected kwarg).
- [ ] **Step 2: Backend implementation** — router: `sources: Optional[str] = Query(None, description="csv of upload,web,generated,derived")`; parse like `kinds` against `{"upload","web","generated","derived"}`; pass `sources=sources_list or None` to both repo calls. Repository: add keyword-only `sources: list[str] | None = None` to both methods; after the kinds filter add `if sources: stmt = stmt.where(Resources.source_type.in_(sources))`. Update the two docstrings' Args blocks. Run the two backend test files → PASS.
- [ ] **Step 3: Frontend service + search options** — `searchResources` gains `sources?: string` and sets `u.searchParams.set('sources', params.sources)` only when non-empty. `librarySearch.ts`: add the `UploadSource` exports above; `fetchLibraryUploads` passes `sources: opts.uploadSources ?? ''`. Test in `librarySearch.test.ts`: `fetchLibraryUploads('', { scopeId: 't1', uploadSources: 'web' })` calls `searchResources` with `sources: 'web'`; with no `uploadSources` the call carries `sources: ''` (and the service omits the param).
- [ ] **Step 4: Store + panel chips** — `libraryStore.ts`: `uploadSource: UploadSource` (initial `'all'`), `setUploadSource(s)` (also clears `selection`), and `setMediaStore` resets it to `'all'` alongside `kind`. It is NOT part of `Persisted`. `LibraryMediaPage.tsx`: pass `uploadSources: mediaStore === 'uploads' ? UPLOAD_SOURCE_CSV[uploadSource] : ''` into `useLibrarySearch`; render a second chip row (same `chipClass` as the segments, `data-testid="library-source-<value>"`, `aria-pressed`) directly under the segment row when `mediaStore === 'uploads'`, labels from the four new keys. Test in `LibraryPanel.test.tsx`: opening on Files shows four source chips with `library-source-all` pressed; clicking `library-source-web` makes the next `searchResources` call carry `sources: 'web'`; switching segment to Generated and back resets to All.
- [ ] **Step 5: Picker chips** — `PromptMentionPicker.tsx`: `const [uploadSource, setUploadSource] = useState<UploadSource>('all')`; pass `uploadSources: UPLOAD_SOURCE_CSV[uploadSource]` into its `useLibrarySearch`; when `tab === 'uploads'` render the same four chips (`data-testid="mention-source-chip"`, `data-source=<value>`, `aria-pressed`, `pillClass`) in a row above the `LibraryGrid`, and reset `active` to 0 on change. Test in `PromptMentionPicker.library.test.tsx`: the Files tab shows four chips; clicking Downloaded re-queries with `sources: 'web'`.
- [ ] **Step 6: Labels** — change `storeUploads` values, add the four source keys in both locale files (keep JSON key order tidy under `canvas.library`). Update any test asserting the literal "Uploads" label (`libraryI18n.test.ts`, `PromptMentionPicker.tabs.test.tsx`, `e2e/canvas-attached-mention.spec.ts` if they match on text; test ids are unchanged).
- [ ] **Step 7: Run** — `cd frontend && npx vitest run features/canvas-core/library features/canvas-core/smart/nodes/PromptMentionPicker` and `npx tsc --noEmit -p tsconfig.json`; backend two files. All green.
- [ ] **Step 8: Commit** — `git add` the named files; message `feat(canvas): Files shelf — source chips (Uploaded / Downloaded / Generated) on panel and @ picker, honest label`.

### Task 2: Assets shelves default to In Library Only (canvas picker, chat picker, panel Assets segment)

**Files:**
- Modify: `frontend/components/assets/AssetGridPicker.tsx` (`useState(false)` → prop `defaultInLibraryOnly = true`)
- Modify: `frontend/components/chat/ResourcePickerSuggestion.tsx` (pass `libraryToggle` so the chat user can widen; default stays `in`)
- Modify: `frontend/features/canvas-core/library/libraryStore.ts` (`assetsInLibraryOnly: boolean` initial `true`, `setAssetsInLibraryOnly`, not persisted)
- Modify: `frontend/features/canvas-core/library/librarySearch.ts` (`LibrarySearchOptions.assetsLibrary?: 'in' | 'all'`; `fetchLibraryAssets` sends it instead of the hard-coded `'all'`, default `'in'`)
- Modify: `frontend/features/canvas-core/library/LibraryMediaPage.tsx` (an "In Library Only" pill in the Assets scope-chip row, `data-testid="library-in-library-toggle"`, `aria-pressed`)
- Test: `frontend/components/assets/AssetGridPicker.test.tsx` (or the nearest existing test for it), `frontend/features/canvas-core/library/librarySearch.test.ts`, `frontend/features/canvas-core/library/LibraryPanel.test.tsx`, `frontend/components/chat/ResourcePickerSuggestion.test.tsx` if present

**Interfaces:**
- `AssetGridPicker` props gain `defaultInLibraryOnly?: boolean` (default `true`). The toggle label stays `labels.inLibraryOnly`. The first request a freshly mounted picker makes therefore carries `library: 'in'`; toggling sends `'all'`.
- `fetchLibraryAssets` uses `opts.assetsLibrary ?? 'in'` for BOTH branches (project-scoped `listAssets` and `searchAssets`). Rewrite the comment there: the shelf answers "my library" now; the widen toggle exists for imports and legacy-card migrations.
- Backend untouched: `/assets` already honours `library=in|out|all`; `/assets/search` too.

- [ ] **Step 1: Failing tests** — AssetGridPicker: mounting with `libraryToggle` and a spy `fetch` → first call has `library: 'in'` and the toggle has `aria-pressed="true"`; click → `library: 'all'`. librarySearch: `fetchLibraryAssets('', { scopeId: 't1' })` → `searchAssets` called with `library: 'in'`; with `assetsLibrary: 'all'` → `'all'`. LibraryPanel: Assets segment shows `library-in-library-toggle` pressed by default; clicking it re-queries with `library: 'all'`. Run → FAIL.
- [ ] **Step 2: Implement** as in Interfaces. In `ResourcePickerSuggestion.tsx` add `libraryToggle` to the `AssetGridPicker` element so the chat shelf can widen (its labels object already has `inLibraryOnly`; if the chat labels lack `libraryLabel`, add `t('chat.mentionPicker.library', 'Library')` with zh "库").
- [ ] **Step 3: Run** the touched test files + `npx tsc --noEmit -p tsconfig.json` → PASS. Also grep the repo for tests that asserted the OLD default (`library: 'all'` as the first request) and fix them to the new contract — `frontend/e2e/chat-assets.spec.ts` routes `/assets/search` and may assert the query string.
- [ ] **Step 4: Commit** — `feat(assets): pickers and panel default to In Library Only (user ruling: library = explicitly added)`.

### Task 3: Fixed Library button on every prompt node; Text-kind nodes get mentions, not references

**Files:**
- Create: `frontend/features/canvas-core/library/mentionHandles.ts` (module registry `nodeId → MentionInserters`)
- Modify: `frontend/features/canvas-core/smart/nodes/PromptNodeView.tsx` (register/unregister the body editor handle; header button; rename `openLibraryForRefs` → `openLibraryForNode`)
- Modify: `frontend/features/canvas-core/library/LibraryMediaPage.tsx` (Text-kind target → primary action "Insert {{count}} Mentions", consequence line, `onItemActivate`)
- Modify: `frontend/public/locales/en.json`, `frontend/public/locales/zh.json`
- Test: `frontend/features/canvas-core/library/mentionHandles.test.ts`, `frontend/features/canvas-core/library/LibraryPanel.test.tsx`, `frontend/features/canvas-core/smart/nodes/PromptNodeView.test.tsx` (nearest existing PromptNodeView test file)

**Interfaces:**
```ts
// mentionHandles.ts — a Map, not store state: editor handles are live objects
// and must never be persisted or compared by value.
import type { MentionInserters } from './mentionLibraryItems';
const handles = new Map<string, MentionInserters>();
export function registerMentionHandle(nodeId: string, h: MentionInserters): () => void; // returns unregister
export function getMentionHandle(nodeId: string): MentionInserters | null;
```
- `PromptNodeView.tsx`: `useEffect(() => registerMentionHandle(id, { insertImage: (i, o) => bodyEditorRef.current?.insertImage(i, o), insertAsset: (a, o) => bodyEditorRef.current?.insertAsset(a, o) }), [id])` — wrappers, so a late-mounted editor is still reached. Header: a new button between the Split toggle and the Prompt Templates button: `data-testid="prompt-open-library"`, icon `Images` (lucide, size 12), `aria-label={t('canvas.library.openPanel', 'Open Library')}`, `className` same as the Prompt Templates button, `disabled={readOnly}`, `onClick={openLibraryForNode}`. Rendered for EVERY kind (text/image/video). The existing dashed "Add reference" button for gen kinds stays.
- `LibraryMediaPage.tsx`: `const mentionMode = inTargetMode && !targetData?.gen;` When `mentionMode`: primary action = `{ label: t('canvas.library.insertMentions', { count: chosen.length, defaultValue: 'Insert {{count}} Mentions' }), disabled: busy, onClick: doInsertMentions }`; `onItemActivate` → `doInsertMentions([item])`; consequence = `t('canvas.library.mentionConsequence', { title: target.title, defaultValue: 'Inserting mentions into {{title}} · a text prompt reads chips, not reference images' })`; `fileCount = null`. `doInsertMentions(picked)`: `const handle = getMentionHandle(target.nodeId)`; if null → toast `t('canvas.library.mentionNoEditor', 'Open the prompt node before inserting mentions')` (error) and return; else `mentionLibraryItems(picked, scopeId, handle)` and echo exactly as `useLibraryMention` does (`mentioned` count → `speakOutcome`-equivalent toast; failures → `canvas.library.mentionFailed`). Reuse `useLibraryMention` if its signature fits (it takes `(items, handle)`), otherwise factor the echo into a shared helper rather than copying it.
- New i18n keys: `canvas.library.openPanel` "Open Library"/"打开素材库", `canvas.library.insertMentions` "Insert {{count}} Mentions"/"插入 {{count}} 个提及", `canvas.library.mentionConsequence` (above)/"向 {{title}} 插入提及 · 文本提示词读取 chip，不读取参考图", `canvas.library.mentionNoEditor` "Open the prompt node before inserting mentions"/"先打开提示词节点再插入提及".

- [ ] **Step 1: Failing tests** — `mentionHandles.test.ts`: register returns an unregister; `getMentionHandle` null after it. `PromptNodeView` test: a Text-kind node renders `prompt-open-library`; clicking it calls `useLibraryStore.getState().openPanel` with `{ page: 'media', target: { nodeId, kind: 'prompt', … } }` (spy on the store). `LibraryPanel.test.tsx`: with a target whose node data has `gen: null` and a registered fake handle, the primary action reads "Insert 1 Mentions" after selecting one Files row and clicking it calls `handle.insertImage` once with `consumeMention: false`; with `gen: {kind:'image',…}` the action still reads "Add 1 References". Run → FAIL.
- [ ] **Step 2: Implement** as in Interfaces.
- [ ] **Step 3: Run** `npx vitest run features/canvas-core/library features/canvas-core/smart/nodes` + `npx tsc --noEmit -p tsconfig.json` → PASS.
- [ ] **Step 4: Commit** — `feat(canvas): fixed Open Library button on every prompt node; Text-kind targets insert mentions instead of references`.
