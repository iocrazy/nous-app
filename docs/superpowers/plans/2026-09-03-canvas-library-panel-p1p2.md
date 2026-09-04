# 画布 Library 面板 — P1 + P2 实施计划

**For agentic workers**: execute with `superpowers:subagent-driven-development`. One subagent per `### Task N`, implementers and reviewers on **opus**. Every task is TDD: the failing test is written and RUN first, and its failure text is quoted in the ledger before any implementation line is typed. Ledger at `.superpowers/sdd/2026-09-03-canvas-library-panel-p1p2/progress.md`.

**Goal**: give the canvas ONE thing called Library — an island panel with a Media page (Assets / Uploads / Generated) reachable from the top bar, the `L` key and the node image button — plus a single grid component that every canvas picker reuses, so "search, multi-select, see the consequence, drag onto the board" replaces today's six single-pick popovers. P1 lands the shared pieces inside the existing popovers; P2 lands the panel and drag-and-drop. The Prompts page is P3 and is out of scope here.

**Architecture**: all new logic lives in a new directory `frontend/features/canvas-core/library/`. Nothing new goes into the six over-budget files (§Global Constraints). One frontend hook (`useLibrarySearch`) fans out to the three existing list endpoints in parallel and normalises the rows into `LibraryItem`; one presentational component (`LibraryGrid`) renders that list with search, kind chips, multi-select, a consequence header and an action footer; one zustand store (`libraryStore`) holds panel state, independent of `canvasCoreStore`. The only backend change in the whole plan is one additive query parameter.

**Tech Stack**: React 19 + TypeScript + Vite 7 + TailwindCSS + zustand + `@tanstack/react-virtual` + lucide-react + i18next on the frontend; FastAPI + SQLAlchemy on the backend. Tests: vitest (frontend), pytest + httpx `ASGITransport` (backend).

**Spec**: `docs/superpowers/specs/2026-09-03-canvas-library-panel-design.md` — §6 defines P1 / P2 / P3. Every "现状修正" paragraph in that spec is binding.

---

## Plan-time rulings

Six decisions this plan makes that a reader of the spec alone would not predict. Each names the fact that forced it.

1. **The backend `canvas_id` parameter is pulled forward to Task 1, ahead of everything in P1.** The spec stages it in P2 (§6), but P1 §3.4 requires an `@`-palette group labelled **Generated · this canvas**, and facts §0 row 1 proves `GET /api/v1/generated` has no such filter at `b6fa8380` (params are `state / origin_kind[] / project_id / media_kind / model / since / source_asset_id / include_intermediate / cursor / limit`, and `project_id` resolves through `canvases.project_id`, so it is a PROJECT filter). Shipping the P1 label over a scope-wide list would put a lie on screen. The change is additive and independently deployable, so it goes first.
2. **`useLibrarySearch` returns per-store results, not one merged list.** Spec §3.7 requires that one failing store shows an error row while the other two keep working. A merged array cannot express "assets failed, uploads returned 12". The hook therefore returns `Record<LibraryStore, LibraryStoreResult>` and every consumer decides how to lay the three out.
3. **`LibraryGrid` is fed one store at a time.** It takes `items: LibraryItem[]`, not the whole `LibrarySearchResult`. The `@` palette (Task 5) renders four groups by mounting the grid's cell list per group; the panel (Task 7) renders one segment at a time. This keeps the component free of store-specific branching and is why the same file serves the compact popover and the full panel.
4. **`aspect` is optional on `LibraryItem` and defaults to 1 until a thumbnail measures in.** None of the three list endpoints returns pixel dimensions (facts §1a/§1b/§1c: `ResourceSearchResult`, `AssetSummary` and `GeneratedItem` all lack width/height). The grid therefore reuses `useMeasuredAspectRatios` (`frontend/hooks/useMeasuredAspectRatios.ts`) exactly as `ResourceGrid.tsx:406-409` does — cells report their natural ratio on load, batched per animation frame — and packs at ratio 1 before that lands. The ripple this causes is inherent to justified layout and documented at the top of `frontend/utils/justifiedLayout.ts`.
5. **The bookshelf button keeps opening `AssetPromptPicker` through the end of P2.** Task 7 adds a Prompts page to the panel that renders one line, "Prompts arrive with P3", and the node's bookshelf button is NOT rewired to it. Deleting the working prompt picker before its replacement exists would remove a shipped affordance in exchange for a stub. `AssetPromptPicker`, `MentionImageGrid` and the Prompts page itself are P3 (spec §6).


6. **Task 7 supersedes Task 4's popover and deletes it.** P1 has to leave the Add-reference affordance usable on its own, so Task 4 rebuilds it as a compact `LibraryGrid` popover. P2's whole premise is that there is ONE Media surface, so Task 7 points that same button at the panel in target mode and removes the popover component. `addReferences.ts`, which is where the work actually happens, survives both. This is staging, not churn, and it is called out here so a reviewer does not read Task 7 as undoing Task 4.

---

## Post-merge corrections (2026-09-04)

Two places where this plan no longer describes what shipped in PR #2102. The plan text above is left as written and corrected here, so the record of what was planned stays readable.

1. **`lite` did NOT stay behind.** Task 7 mounts the panel behind `isSmartFamily(kind)`, and that predicate already lists `lite` (`frontend/features/canvas-core/types.ts`), so a lite canvas got the Library panel with Task 7. Only `storyboard` and `classic` — the two kinds outside the smart family — are still P3. The Spec-coverage row and Gap 2 above have been corrected to say so.
2. **`⌥` mention ships as CHIPS, not plain text.** The code in Task 6's block writes `insertText('@' + title + ' ')`; final review ruling R31 replaced that with `mentionLibraryItems.ts`, which inserts a real asset chip or image chip per item. Plain text delivered nothing to the run (a run reads chips, not prose) and, routed through the editor's `insertAtMention`, destroyed text before the caret. `insertText` itself survives on the editor handle but has no production caller left.

---

## Hand-off for a new session

This plan was written in a docs-only worktree. Implementation needs a fresh one.

```bash
cd /media/heygo/program/projects-code/repos/nous-app
bash scripts/worktree-manager.sh create feat/canvas-library-panel
cd .worktrees/feat-canvas-library-panel
# worktrees are cut from the LOCAL master, which may be stale
git fetch origin master
git reset --hard origin/master
cd frontend && npm install
```

Then run the plan with `superpowers:subagent-driven-development`, implementers and reviewers on **opus**, ledger at `.superpowers/sdd/2026-09-03-canvas-library-panel-p1p2/progress.md`.

Before Task 2, record the TypeScript baseline once and write the number into the ledger:

```bash
cd frontend && npx tsc --noEmit -p . 2>&1 | grep -c "error TS"
```

Every later task quotes that number as its ceiling. The count must not grow.

---

## Global Constraints

Apply to every task. A task that violates one is not done, even if its own tests pass.

**UI language and i18n**
- All UI text is English, Title Case (`Add References`, not `Add references`). Chinese appears only in commit messages and code comments.
- Every new string goes through `t('canvas.library.<camelCaseKey>', 'English Default')` and is added to BOTH `frontend/public/locales/en.json` and `frontend/public/locales/zh.json`. Keys are camelCase under `canvas.library.*`.
- No emoji anywhere in UI or code. Icons come from `lucide-react`.
- Colours use semantic tokens only: `ok / warn / danger / info / agent`, the `canvas-*` family (`canvas-line`, `canvas-card`, `canvas-text`, `canvas-muted`) and `var(--accent-border)` / `var(--accent-text)`. Never `indigo` / `amber` / `emerald` / `rose` / `sky`.

**Test discipline**
- Boundary mocks use REAL wire shapes. Copy the fixtures from the facts file; do not idealise. Snowflake ids are JSON **strings** on `assets`, `generated` and `resources/search` rows (facts §1a: the search router emits `"id": str(row["id"])`). The rule and the distinction that decides which side you are on is quoted at the top of `frontend/e2e/helpers/realShapes.ts`: an HTTP response body gets the wire shape, a `vi.mock` of an already-normalised service function gets that function's documented return type.
- A vitest run counts only if the output carries a `Tests N passed` line AND the exit code is 0. `--reporter=basic` exits 0 having run zero tests and must never be used as a gate.
- Test paths are passed as separate quoted arguments: `npx vitest run "a.test.ts" "b.test.ts"`.
- `npx tsc --noEmit -p .` (from `frontend/`) error count must not exceed the recorded baseline.
- Every pinning test is mutation-verified: break the implementation in the stated way, watch the test go red, restore. The mutation is named in each task's steps.

**React Flow**
- All node views are `memo`-wrapped. Anything this feature injects into node data must be primitive or a stable reference — never a fresh object or array per render.
- Any interactive element rendered INSIDE a React Flow node needs the `nodrag nowheel nopan` classes. React Flow listens for mousedown on the node and stops propagation, so without them a real user's clicks silently do nothing while a synthetic dispatch in a test still passes (`MentionImageGrid.tsx:5-10` documents this exact trap).
- Popovers opened from inside a node `createPortal` to `document.body`.

**File-size budget** (facts §11) — these files may gain only small wiring hunks, never new logic:

| File | Lines at HEAD | Allowed change |
|---|---:|---|
| `smart/nodes/PromptNodeView.tsx` | 1085 | net NEGATIVE (Task 4 removes more than it adds) |
| `ui/CanvasPage.tsx` | 1033 | ≤ 6 lines (panel mount + one callback) |
| `store/canvasCoreStore.ts` | 1087 | 0 lines — panel state lives in `library/libraryStore.ts` |
| `ui/CanvasSurface.tsx` | 734 | ≤ 15 lines (one drop dispatch calling `library/dropLibraryItems.ts`) |
| `smart/nodes/AssetNodeView.tsx` | 540 | 0 lines |
| `ui/TopNodeBar.tsx` | 277 | net NEGATIVE (Task 7 merges two chips into one) |

**Backend**
- Format with `black` and `isort`; lint with `flake8`. This repo does NOT use ruff.
- No new `text()` raw SQL.
- Router tests drive the real ASGI app through `httpx` `ASGITransport`, as `backend/tests/api/test_generated_router.py` and `backend/tests/test_generated_media_cover.py` do.
- The `canvas_id` scope check is identical to `project_id`'s: validated by `OptSnowflakeQuery`, cast with `int(...) if ... else None`, and gated by the existing `_gate(scope_id, auth)` team-membership check. No second authorisation path.

**Commits**
- One commit per task, at the end of that task, after its tests pass.
- Exactly: `git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "..."`.
- `git add` names only the files that task touched. Never `git add -A`.
- Chinese commit messages are fine and are the house style.

---

### Task 1: `GET /api/v1/generated` 加 `canvas_id` 过滤（后端 + 前端 service 参数）

Spec §3.3 现状修正 + §5 决策表最后一行. Pulled ahead of P1 — see Plan-time ruling 1.

**Files**
- Modify `backend/app/repositories/generated_media_repository.py` — `_inbox_filters` signature at `:228-241`, filter body at `:258-278`, `list_inbox` signature at `:583-596` and its `_inbox_filters(...)` call at `:605-616`.
- Modify `backend/app/services/library/generated_inbox_service.py` — `list()` signature at `:159-174` and the `list_inbox(...)` call at `:175-187`.
- Modify `backend/app/api/generated_router.py` — `list_generated` params at `:78-95` and the `_service().list(...)` call at `:100-121`.
- Modify (test) `backend/tests/repositories/test_generated_media_inbox_repo.py` — all 7 existing `_inbox_filters(...)` call sites gain `canvas_id=None`.
- Test `backend/tests/api/test_generated_router.py` — one new case.
- Test `backend/tests/repositories/test_generated_media_inbox_repo.py` — one new case.
- Modify `frontend/services/generatedService.ts` — `GeneratedListOptions` at `:139-165`, `fetchGenerated`'s `query(...)` at `:220-233`.
- Test `frontend/services/generatedService.test.ts` — one new case.

**Interfaces**

Consumes (verbatim, facts §1c):

```python
def _inbox_filters(
    *,
    scope_id: int,
    state: Optional[str],
    origin_kinds: Optional[list[str]],
    project_id: Optional[int],
    media_kind: Optional[str],
    model: Optional[str],
    since: Optional[datetime.datetime],
    source_asset_id: Optional[int],
    include_intermediate: bool,
) -> list:
```

Produces — one new keyword-only parameter in each of the three layers, threaded straight through:

```python
# repositories/generated_media_repository.py
def _inbox_filters(
    *,
    scope_id: int,
    state: Optional[str],
    origin_kinds: Optional[list[str]],
    project_id: Optional[int],
    canvas_id: Optional[int],
    media_kind: Optional[str],
    model: Optional[str],
    since: Optional[datetime.datetime],
    source_asset_id: Optional[int],
    include_intermediate: bool,
) -> list: ...

async def list_inbox(
    self,
    scope_id: int,
    *,
    state: Optional[str] = None,
    origin_kinds: Optional[list[str]] = None,
    project_id: Optional[int] = None,
    canvas_id: Optional[int] = None,
    media_kind: Optional[str] = None,
    model: Optional[str] = None,
    since: Optional[datetime.datetime] = None,
    source_asset_id: Optional[int] = None,
    include_intermediate: bool = False,
    cursor: Optional[str] = None,
    limit: int = 60,
) -> dict: ...

# services/library/generated_inbox_service.py
async def list(
    self,
    scope_id: int,
    team_id: str,
    *,
    state: Optional[str] = None,
    origin_kinds: Optional[list[str]] = None,
    project_id: Optional[int] = None,
    canvas_id: Optional[int] = None,
    media_kind: Optional[str] = None,
    model: Optional[str] = None,
    since: Optional[datetime.datetime] = None,
    source_asset_id: Optional[int] = None,
    include_intermediate: bool = False,
    cursor: Optional[str] = None,
    limit: int = 60,
) -> dict[str, Any]: ...
```

```ts
// frontend/services/generatedService.ts
export interface GeneratedListOptions {
  // …every existing field unchanged…
  /** Only this canvas's generations. Resolved server-side against
   *  `generated_media.canvas_id` — NOT client-side: under keyset pagination a
   *  client filter silently under-fills every page. */
  canvasId?: string;
}
```

`_inbox_filters` keeps its no-default discipline (its own docstring: *"Every parameter here is required with no default on purpose: a filter that can be forgotten silently widens the page to the whole scope, which reads exactly like a correct answer."*), so `canvas_id` is required there and every existing test call site must pass `canvas_id=None`. The two public methods above it keep `= None` defaults, matching `project_id`.

**Steps**

- [ ] Write the failing repository test. Append to `backend/tests/repositories/test_generated_media_inbox_repo.py`:

```python
def test_canvas_id_filters_on_the_column_not_through_canvases():
    """`canvas_id` is a DIRECT column predicate.

    `project_id` has to go through a subquery on `canvases.project_id`; this one
    does not, and the difference is the point of the parameter. A subquery here
    would answer the project's whole inbox for every canvas in it.
    """
    sql = _sql(
        _inbox_filters(
            scope_id=7,
            state=None,
            origin_kinds=None,
            project_id=None,
            canvas_id=900000000000000001,
            media_kind=None,
            model=None,
            since=None,
            source_asset_id=None,
            include_intermediate=False,
        )
    )
    assert "canvas_id = 900000000000000001" in sql
    assert "SELECT canvases.id" not in sql


def test_canvas_id_none_adds_no_predicate():
    sql = _sql(
        _inbox_filters(
            scope_id=7,
            state=None,
            origin_kinds=None,
            project_id=None,
            canvas_id=None,
            media_kind=None,
            model=None,
            since=None,
            source_asset_id=None,
            include_intermediate=False,
        )
    )
    assert "canvas_id" not in sql
```

- [ ] Run it. Expected failure: `TypeError: _inbox_filters() got an unexpected keyword argument 'canvas_id'`.

```bash
cd backend && uv run pytest "tests/repositories/test_generated_media_inbox_repo.py" -q
```

- [ ] Write the failing router test. Append to `backend/tests/api/test_generated_router.py`:

```python
@pytest.mark.asyncio
async def test_canvas_id_is_cast_to_int_and_passed_through(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(
            f"/api/v1/generated?scope_id={SCOPE}&canvas_id=900000000000000001"
        )
    assert r.status_code == 200, r.text
    f = _filters(app)
    assert f["canvas_id"] == 900000000000000001
    assert isinstance(f["canvas_id"], int)


@pytest.mark.asyncio
async def test_canvas_id_absent_passes_none(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"/api/v1/generated?scope_id={SCOPE}")
    assert r.status_code == 200, r.text
    assert _filters(app)["canvas_id"] is None


@pytest.mark.asyncio
async def test_canvas_id_rejects_a_non_snowflake(app):
    """A bad id is a 422, never a scope-wide page wearing a canvas's name."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"/api/v1/generated?scope_id={SCOPE}&canvas_id=not-an-id")
    assert r.status_code == 422, r.text
```

- [ ] Run it. Expected failure: `KeyError: 'canvas_id'` on the first two cases and `assert 200 == 422` on the third.

```bash
cd backend && uv run pytest "tests/api/test_generated_router.py" -q
```

- [ ] Implement the repository. In `_inbox_filters`, add `canvas_id: Optional[int],` immediately after `project_id: Optional[int],` in the signature, extend the docstring with one line, and add the predicate immediately after the `project_id` block:

```python
    if canvas_id is not None:
        # A DIRECT column predicate, unlike ``project_id`` above, which has to
        # resolve through ``canvases.project_id``. "This canvas" is a question
        # the row can answer by itself, and routing it through the subquery
        # would answer the whole project's inbox under one canvas's name.
        crit.append(GeneratedMedia.canvas_id == int(canvas_id))
```

Add `canvas_id: Optional[int] = None,` to `list_inbox`'s keyword-only block after `project_id`, and `canvas_id=canvas_id,` to its `_inbox_filters(...)` call.

- [ ] Add `canvas_id=None,` to all 7 existing `_inbox_filters(...)` call sites in `backend/tests/repositories/test_generated_media_inbox_repo.py` (each already passes every other parameter explicitly — insert the new line after `project_id=...`).

- [ ] Implement the service. In `generated_inbox_service.py::list`, add `canvas_id: Optional[int] = None,` after `project_id` in the signature and `canvas_id=canvas_id,` to the `self.gen_repo.list_inbox(...)` call.

- [ ] Implement the router. In `generated_router.py::list_generated`, add after the `project_id` parameter:

```python
    canvas_id: OptSnowflakeQuery = None,
```

and inside the `_service().list(...)` call, after the `project_id` line:

```python
            # "This canvas", the Library panel's Generated default. Validated as
            # a snowflake like every other id query param, and resolved server
            # side: filtering a keyset page client-side under-fills it.
            canvas_id=int(canvas_id) if canvas_id else None,
```

- [ ] Run both backend tests. Expected: `Tests` all pass, e.g. `... passed` with zero failures.

```bash
cd backend && uv run pytest "tests/repositories/test_generated_media_inbox_repo.py" "tests/api/test_generated_router.py" -q
```

- [ ] Mutation-verify: change the new predicate to `GeneratedMedia.project_id == int(canvas_id)`. Expected: `test_canvas_id_filters_on_the_column_not_through_canvases` fails on `assert "canvas_id = 900000000000000001" in sql`. Restore.

- [ ] Write the failing frontend service test. Append to `frontend/services/generatedService.test.ts`, following the file's existing fetch-stub idiom:

```ts
it('sends canvas_id when asked, and omits it otherwise', async () => {
  const seen: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    seen.push(url);
    return new Response(
      JSON.stringify({ success: true, data: { items: [], next_cursor: null } }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    );
  }));
  await fetchGenerated('727145299382534100', { canvasId: '900000000000000001' });
  await fetchGenerated('727145299382534100', {});
  expect(seen[0]).toContain('canvas_id=900000000000000001');
  expect(seen[1]).not.toContain('canvas_id');
});
```

- [ ] Run it. Expected failure: `expected '…scope_id=727145299382534100' to contain 'canvas_id=900000000000000001'`.

```bash
cd frontend && npx vitest run "services/generatedService.test.ts"
```

- [ ] Implement. Add the `canvasId?: string;` field with its comment to `GeneratedListOptions` (after `projectId`), and add `canvas_id: opts.canvasId,` to the `query(scopeId, {...})` object in `fetchGenerated` (the helper already drops `undefined` values, so "not supplied" stays the same request as before).

- [ ] Run it. Expected: `Tests 1 passed` for the new case and the file's existing cases still green.

- [ ] Mutation-verify: rename the query key to `canvasId`. Expected failure on `toContain('canvas_id=…')`. Restore.

- [ ] Format and lint the backend:

```bash
cd backend && uv run black app/api/generated_router.py app/services/library/generated_inbox_service.py app/repositories/generated_media_repository.py tests/api/test_generated_router.py tests/repositories/test_generated_media_inbox_repo.py
uv run isort app/api/generated_router.py app/services/library/generated_inbox_service.py app/repositories/generated_media_repository.py
uv run flake8 app/api/generated_router.py app/services/library/generated_inbox_service.py app/repositories/generated_media_repository.py
```

- [ ] Commit:

```bash
git add backend/app/api/generated_router.py backend/app/services/library/generated_inbox_service.py backend/app/repositories/generated_media_repository.py backend/tests/api/test_generated_router.py backend/tests/repositories/test_generated_media_inbox_repo.py frontend/services/generatedService.ts frontend/services/generatedService.test.ts
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(generated): 列表加 canvas_id 过滤 —— 「本画布」是行自己能回答的问题，不走 project 子查询"
```

---

### Task 2: `library/librarySearch.ts` — 三库并行索引 `useLibrarySearch`

Spec §3.4 最后一段（前端并行三路，不新增后端聚合端点）+ §3.7 第一条（三库任一失败只影响自己）.

**Files**
- Create `frontend/features/canvas-core/library/librarySearch.ts`
- Test `frontend/features/canvas-core/library/librarySearch.test.ts`

**Interfaces**

Consumes (verbatim signatures, facts §1a–§1c):

```ts
export async function searchAssets(scopeId: string, opts: AssetSearchOptions = {}): Promise<AssetSummary[]>
export async function listAssets(scopeId: string, opts: AssetListOptions = {}): Promise<AssetRow[]>
export async function searchResources(params: { q: string; kinds: string; limit?: number; teamId?: string; signal?: AbortSignal }): Promise<ResourceSearchResponse>
export async function fetchGenerated(scopeId: string, opts: GeneratedListOptions = {}): Promise<GeneratedPage>
export function getResourceCoverUrl(resourceId: string, token?: string, version?: string | number): string
export function generatedMediaCoverUrl(id: string): string
```

Produces:

```ts
export type LibraryStore = 'assets' | 'uploads' | 'generated';
export const LIBRARY_STORES: readonly LibraryStore[] = ['assets', 'uploads', 'generated'];

export interface LibraryItem {
  store: LibraryStore;
  /** Snowflake as a STRING in all three stores — the wire shape. */
  id: string;
  title: string;
  /** Absolute, ready for a bare <img src>. '' when the row has no cover. */
  thumbUrl: string;
  /** assets: AssetType. uploads: video|image|doc|audio|pdf. generated: media_kind. */
  kind: string;
  /** w/h. Absent until a thumbnail measures in — no list endpoint returns it. */
  aspect?: number;
  /** Only meaningful for assets (draft vs ready); true for the other stores. */
  ready?: boolean;
}

export type GeneratedScope = 'this-canvas' | 'today' | 'all';
export type AssetScope = 'this-project' | 'all';

export interface LibrarySearchOptions {
  /** `teams.id`. '' means the route has no team segment — a refusal, not an
   *  unscoped query. Every store reports that as an error rather than []. */
  scopeId: string;
  stores?: readonly LibraryStore[];
  assetType?: AssetType | null;
  assetScope?: AssetScope;
  projectId?: string | null;
  /** csv for `/resources/search?kinds=`; '' means every kind. */
  uploadKinds?: string;
  generatedScope?: GeneratedScope;
  canvasId?: string | null;
  limit?: number;
}

export interface LibraryStoreResult {
  items: LibraryItem[];
  loading: boolean;
  error: Error | null;
  /** Re-run THIS store only. The other two are untouched. */
  reload: () => void;
}
export type LibrarySearchResult = Record<LibraryStore, LibraryStoreResult>;

export function assetToLibraryItem(row: { id: string; name: string; asset_type: AssetType; cover_file_id: string | null; readiness?: { state: 'ready' | 'draft'; missing: string[] } }): LibraryItem;
export function uploadToLibraryItem(row: ResourceSearchResult): LibraryItem;
export function generatedToLibraryItem(row: GeneratedItem): LibraryItem;
export function startOfTodayIso(now?: Date): string;
export async function fetchLibraryAssets(query: string, opts: LibrarySearchOptions): Promise<LibraryItem[]>;
export async function fetchLibraryUploads(query: string, opts: LibrarySearchOptions, signal?: AbortSignal): Promise<LibraryItem[]>;
export async function fetchLibraryGenerated(query: string, opts: LibrarySearchOptions): Promise<LibraryItem[]>;
export function useLibrarySearch(query: string, opts: LibrarySearchOptions): LibrarySearchResult;
```

**Steps**

- [ ] Write the failing test. Create `frontend/features/canvas-core/library/librarySearch.test.ts`:

```ts
// features/canvas-core/library/librarySearch.test.ts
//
// The three-store index. Fixtures are REAL wire shapes: every Snowflake is a
// JSON string, because that is what all three routers emit (the resources
// search router builds its row by hand with `"id": str(row["id"])`).

import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const searchAssets = vi.fn();
const listAssets = vi.fn();
const searchResources = vi.fn();
const fetchGenerated = vi.fn();

vi.mock('../../../services/assetsService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  searchAssets: (...a: unknown[]) => searchAssets(...a),
  listAssets: (...a: unknown[]) => listAssets(...a),
}));
vi.mock('../../../services/resourceSearchService', () => ({
  searchResources: (...a: unknown[]) => searchResources(...a),
}));
vi.mock('../../../services/generatedService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  fetchGenerated: (...a: unknown[]) => fetchGenerated(...a),
}));

import {
  assetToLibraryItem,
  generatedToLibraryItem,
  startOfTodayIso,
  uploadToLibraryItem,
  useLibrarySearch,
} from './librarySearch';

const SCOPE = '727145299382534100';
const CANVAS = '900000000000000001';

/** `GET /api/v1/assets` row (AssetSummary). */
const ASSET_ROW = {
  id: '727145299382534300',
  scope_id: SCOPE,
  asset_type: 'character' as const,
  name: 'Cole Bannon',
  role_tag: 'lead',
  readiness: { state: 'draft' as const, missing: ['portrait'] },
  cover_file_id: '600000000000000001',
  is_system_preset: false,
};

/** `GET /api/v1/resources/search` row — id is a STRING (`str(row["id"])`). */
const UPLOAD_ROW = {
  id: '655000000000000001',
  name: 'harbour-dusk.png',
  kind: 'image' as const,
  mime: 'image/png',
  size: 812345,
  scope: { type: 'team' as const, id: SCOPE },
  updated_at: '2026-09-01T10:11:12Z',
  thumbnail_url: '/api/v1/resources/655000000000000001/cover',
  transcript_status: null,
  summary_status: null,
};

/** `GET /api/v1/generated` item, as `GeneratedItem` documents it. */
const GENERATED_ROW = {
  id: '800000000000000001',
  scope_id: SCOPE,
  media_kind: 'image',
  mime: 'image/png',
  prompt: 'A wide shot of the harbour.',
  model: 'doubao-seedream',
  provider: 'volcengine',
  origin_kind: 'canvas_run',
  canvas_id: CANVAS,
  node_id: 'node-7',
  created_at: '2026-09-02T12:00:00Z',
  promoted_resource_id: null,
  review_state: 'unreviewed' as const,
  source_asset_id: null,
  source: {
    kind: 'canvas_run',
    label: 'Canvas · Harbour',
    canvas_id: CANVAS,
    node_id: 'node-7',
    shot_id: null,
    conversation_id: null,
    deep_link: `/projects/1/canvas/${CANVAS}`,
  },
  title: 'A wide shot of the harbour',
};

const OPTS = { scopeId: SCOPE, canvasId: CANVAS, generatedScope: 'this-canvas' as const };

beforeEach(() => {
  vi.useFakeTimers();
  searchAssets.mockReset().mockResolvedValue([ASSET_ROW]);
  listAssets.mockReset().mockResolvedValue([ASSET_ROW]);
  searchResources.mockReset().mockResolvedValue({
    results: [UPLOAD_ROW],
    counts: { all: 1, video: 0, image: 1, doc: 0, audio: 0, pdf: 0 },
    next_cursor: null,
  });
  fetchGenerated.mockReset().mockResolvedValue({
    items: [GENERATED_ROW],
    next_cursor: null,
  });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('normalisers', () => {
  it('an asset keeps its string id and reports draft readiness', () => {
    expect(assetToLibraryItem(ASSET_ROW)).toEqual({
      store: 'assets',
      id: '727145299382534300',
      title: 'Cole Bannon',
      thumbUrl: expect.stringContaining('/api/v1/resources/600000000000000001/cover'),
      kind: 'character',
      ready: false,
    });
  });

  it('an upload with no cover gets an empty thumbUrl, never a broken one', () => {
    expect(uploadToLibraryItem({ ...UPLOAD_ROW, thumbnail_url: null }).thumbUrl).toBe('');
  });

  it('a generation points at its cover endpoint and carries its media kind', () => {
    const item = generatedToLibraryItem(GENERATED_ROW);
    expect(item.id).toBe('800000000000000001');
    expect(item.kind).toBe('image');
    expect(item.thumbUrl).toContain('/api/v1/generated-media/800000000000000001/cover');
  });
});

describe('useLibrarySearch', () => {
  it('asks all three stores in parallel and reports each one separately', async () => {
    const { result } = renderHook(() => useLibrarySearch('harbour', OPTS));
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    await waitFor(() => {
      expect(result.current.assets.items).toHaveLength(1);
      expect(result.current.uploads.items).toHaveLength(1);
      expect(result.current.generated.items).toHaveLength(1);
    });
    expect(searchAssets).toHaveBeenCalledWith(SCOPE, expect.objectContaining({
      q: 'harbour',
      library: 'all',
    }));
    expect(searchResources).toHaveBeenCalledWith(
      expect.objectContaining({ q: 'harbour', teamId: SCOPE }),
    );
    expect(fetchGenerated).toHaveBeenCalledWith(SCOPE, expect.objectContaining({
      canvasId: CANVAS,
      state: 'all',
    }));
  });

  it('one store failing leaves the other two with their rows', async () => {
    searchAssets.mockRejectedValue(new Error('assets down'));
    const { result } = renderHook(() => useLibrarySearch('', OPTS));
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    await waitFor(() => {
      expect(result.current.assets.error?.message).toBe('assets down');
    });
    expect(result.current.assets.items).toEqual([]);
    expect(result.current.uploads.items).toHaveLength(1);
    expect(result.current.generated.items).toHaveLength(1);
  });

  it('an empty scopeId is a refusal, not an unscoped query', async () => {
    const { result } = renderHook(() => useLibrarySearch('', { scopeId: '' }));
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    await waitFor(() => {
      expect(result.current.assets.error).not.toBeNull();
    });
    expect(searchAssets).not.toHaveBeenCalled();
    expect(searchResources).not.toHaveBeenCalled();
    expect(fetchGenerated).not.toHaveBeenCalled();
  });

  it('a superseded query never lands: only the last one is kept', async () => {
    let release: (v: unknown) => void = () => {};
    searchAssets.mockImplementationOnce(
      () => new Promise((r) => { release = r; }),
    );
    const { rerender, result } = renderHook(
      ({ q }) => useLibrarySearch(q, OPTS),
      { initialProps: { q: 'a' } },
    );
    await act(async () => { vi.advanceTimersByTime(300); });
    rerender({ q: 'b' });
    await act(async () => { vi.advanceTimersByTime(300); });
    // The FIRST call resolves late with a row that must be discarded.
    await act(async () => {
      release([{ ...ASSET_ROW, id: '1', name: 'Stale' }]);
    });
    await waitFor(() => expect(result.current.assets.items).toHaveLength(1));
    expect(result.current.assets.items[0].title).toBe('Cole Bannon');
  });

  it('generated is narrowed client-side, because that endpoint has no q', async () => {
    fetchGenerated.mockResolvedValue({
      items: [GENERATED_ROW, { ...GENERATED_ROW, id: '2', title: 'A quiet forest' }],
      next_cursor: null,
    });
    const { result } = renderHook(() => useLibrarySearch('harbour', OPTS));
    await act(async () => { vi.advanceTimersByTime(300); });
    await waitFor(() => expect(result.current.generated.items).toHaveLength(1));
    expect(result.current.generated.items[0].title).toBe('A wide shot of the harbour');
  });

  it('the Today scope sends a since instant, not a client filter', async () => {
    renderHook(() => useLibrarySearch('', { scopeId: SCOPE, generatedScope: 'today' }));
    await act(async () => { vi.advanceTimersByTime(300); });
    await waitFor(() => expect(fetchGenerated).toHaveBeenCalled());
    expect(fetchGenerated.mock.calls[0][1].since).toBe(startOfTodayIso());
  });

  it('the This project asset scope uses listAssets, which takes a projectId', async () => {
    renderHook(() => useLibrarySearch('', {
      scopeId: SCOPE,
      assetScope: 'this-project',
      projectId: '500000000000000001',
    }));
    await act(async () => { vi.advanceTimersByTime(300); });
    await waitFor(() => expect(listAssets).toHaveBeenCalled());
    expect(listAssets).toHaveBeenCalledWith(SCOPE, expect.objectContaining({
      projectId: '500000000000000001',
      library: 'all',
    }));
    expect(searchAssets).not.toHaveBeenCalled();
  });

  it('reload re-runs one store without touching the others', async () => {
    const { result } = renderHook(() => useLibrarySearch('', OPTS));
    await act(async () => { vi.advanceTimersByTime(300); });
    await waitFor(() => expect(result.current.assets.items).toHaveLength(1));
    const uploadCalls = searchResources.mock.calls.length;
    await act(async () => { result.current.assets.reload(); });
    await act(async () => { vi.advanceTimersByTime(300); });
    await waitFor(() => expect(searchAssets).toHaveBeenCalledTimes(2));
    expect(searchResources.mock.calls.length).toBe(uploadCalls);
  });
});
```

- [ ] Run it. Expected failure: `Error: Failed to resolve import "./librarySearch"`.

```bash
cd frontend && npx vitest run "features/canvas-core/library/librarySearch.test.ts"
```

- [ ] Implement. Create `frontend/features/canvas-core/library/librarySearch.ts`:

```ts
// features/canvas-core/library/librarySearch.ts
//
// One index over the three libraries a canvas can pull from — assets, uploads
// and generations — fanned out in PARALLEL from the client.
//
// There is deliberately no backend aggregate endpoint. The three stores have
// three different permission models (assets are scope+preset, uploads are
// per-resource ACL, generations are scope-only), and a server-side union would
// have to invent a fourth filtering rule that agreed with all three. Fanning
// out here keeps each store answering in its own terms, and it is what makes
// "assets failed, uploads is fine" representable — see LibraryStoreResult.
//
// ⚠️ `GET /api/v1/generated` HAS NO TEXT SEARCH. The other two narrow
// server-side; generations are narrowed here, over the fetched page only. So a
// query can hide a match that lives past the page boundary. That is a real
// limitation, stated rather than papered over: the alternative is a backend
// search parameter, which is a separate change.

import { useCallback, useEffect, useMemo, useState } from 'react';

import {
  listAssets,
  searchAssets,
  type AssetType,
} from '../../../services/assetsService';
import { generatedMediaCoverUrl } from '../../../services/generatedMediaService';
import { fetchGenerated, type GeneratedItem } from '../../../services/generatedService';
import { getResourceCoverUrl } from '../../../services/resourceService';
import { searchResources } from '../../../services/resourceSearchService';
import type { ResourceSearchResult } from '../../../types';

const DEBOUNCE_MS = 300;
const LIMIT = 60;

export type LibraryStore = 'assets' | 'uploads' | 'generated';
export const LIBRARY_STORES: readonly LibraryStore[] = ['assets', 'uploads', 'generated'];

export interface LibraryItem {
  store: LibraryStore;
  id: string;
  title: string;
  thumbUrl: string;
  kind: string;
  aspect?: number;
  ready?: boolean;
}

export type GeneratedScope = 'this-canvas' | 'today' | 'all';
export type AssetScope = 'this-project' | 'all';

export interface LibrarySearchOptions {
  scopeId: string;
  stores?: readonly LibraryStore[];
  assetType?: AssetType | null;
  assetScope?: AssetScope;
  projectId?: string | null;
  uploadKinds?: string;
  generatedScope?: GeneratedScope;
  canvasId?: string | null;
  limit?: number;
}

export interface LibraryStoreResult {
  items: LibraryItem[];
  loading: boolean;
  error: Error | null;
  reload: () => void;
}

export type LibrarySearchResult = Record<LibraryStore, LibraryStoreResult>;

/** Raised when a store cannot be asked at all, so the UI shows a reason
 *  instead of an empty shelf that reads like "you own nothing". */
export class LibraryScopeError extends Error {
  constructor() {
    super('This canvas has no workspace scope');
    this.name = 'LibraryScopeError';
  }
}

export function assetToLibraryItem(row: {
  id: string;
  name: string;
  asset_type: AssetType;
  cover_file_id: string | null;
  readiness?: { state: 'ready' | 'draft'; missing: string[] };
}): LibraryItem {
  return {
    store: 'assets',
    id: row.id,
    title: row.name,
    thumbUrl: row.cover_file_id ? getResourceCoverUrl(row.cover_file_id) : '',
    kind: row.asset_type,
    ready: row.readiness?.state === 'ready',
  };
}

export function uploadToLibraryItem(row: ResourceSearchResult): LibraryItem {
  // `thumbnail_url` is RELATIVE and may be null. Building the absolute form
  // from the id is the same URL the search router would have produced, and it
  // keeps a null from becoming the string "null" in an <img src>.
  return {
    store: 'uploads',
    id: row.id,
    title: row.name,
    thumbUrl: row.thumbnail_url ? getResourceCoverUrl(row.id) : '',
    kind: row.kind,
    ready: true,
  };
}

export function generatedToLibraryItem(row: GeneratedItem): LibraryItem {
  return {
    store: 'generated',
    id: row.id,
    title: row.title,
    thumbUrl: generatedMediaCoverUrl(row.id),
    kind: row.media_kind,
    ready: true,
  };
}

/** Midnight local time, as the instant the router parses. */
export function startOfTodayIso(now: Date = new Date()): string {
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0, 0);
  return d.toISOString();
}

export async function fetchLibraryAssets(
  query: string,
  opts: LibrarySearchOptions,
): Promise<LibraryItem[]> {
  if (!opts.scopeId) throw new LibraryScopeError();
  const q = query.trim() || undefined;
  // `library: 'all'` — EXPLICIT, and the explicitness is the point. The
  // server's default is `in` (library members only), which hides script
  // imports and every asset the P4 legacy-card migration created — exactly the
  // population a canvas points at. Same reasoning as AssetPickerDialog.
  if (opts.assetScope === 'this-project' && opts.projectId) {
    const rows = await listAssets(opts.scopeId, {
      q,
      type: opts.assetType ?? undefined,
      projectId: opts.projectId,
      library: 'all',
      limit: opts.limit ?? LIMIT,
    });
    return rows.map(assetToLibraryItem);
  }
  const rows = await searchAssets(opts.scopeId, {
    q,
    type: opts.assetType ?? undefined,
    library: 'all',
    limit: opts.limit ?? LIMIT,
  });
  return rows.map(assetToLibraryItem);
}

export async function fetchLibraryUploads(
  query: string,
  opts: LibrarySearchOptions,
  signal?: AbortSignal,
): Promise<LibraryItem[]> {
  if (!opts.scopeId) throw new LibraryScopeError();
  const resp = await searchResources({
    q: query.trim(),
    kinds: opts.uploadKinds ?? '',
    limit: opts.limit ?? 50, // 50 is the backend's own ceiling for this route
    teamId: opts.scopeId,
    signal,
  });
  const results = Array.isArray(resp?.results) ? resp.results : [];
  return results.map(uploadToLibraryItem);
}

export async function fetchLibraryGenerated(
  query: string,
  opts: LibrarySearchOptions,
): Promise<LibraryItem[]> {
  if (!opts.scopeId) throw new LibraryScopeError();
  const scope = opts.generatedScope ?? 'this-canvas';
  const page = await fetchGenerated(opts.scopeId, {
    // `all` rather than the router's `unreviewed` default: this is a picker,
    // not a triage queue. A generation the user already saved is still a
    // picture they want to reuse, and the inbox default would hide it.
    state: 'all',
    canvasId: scope === 'this-canvas' ? (opts.canvasId ?? undefined) : undefined,
    since: scope === 'today' ? startOfTodayIso() : undefined,
    limit: opts.limit ?? LIMIT,
    // `include_intermediate` is deliberately omitted: absent and false are the
    // same request, and the "hide masks and brush composites" rule lives
    // server-side in generated_roles.py. Spelling it out here would be a
    // second copy of it.
  });
  const needle = query.trim().toLowerCase();
  const items = (page.items ?? []).map(generatedToLibraryItem);
  if (!needle) return items;
  return items.filter((i) => i.title.toLowerCase().includes(needle));
}

const EMPTY: LibraryItem[] = [];

/** One store's effect. Split out so the three cannot share a loading flag,
 *  an error, or an abort — which is what §3.7 asks for. */
function useOneStore(
  store: LibraryStore,
  enabled: boolean,
  query: string,
  opts: LibrarySearchOptions,
  key: string,
): LibraryStoreResult {
  const [items, setItems] = useState<LibraryItem[]>(EMPTY);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    // Only the uploads route takes a signal. The other two services expose no
    // abort, so a superseded call is DISCARDED on arrival rather than
    // cancelled — the request still costs a round trip, but a late answer can
    // never overwrite a newer one.
    const ctrl = new AbortController();
    const timer = setTimeout(() => {
      setLoading(true);
      setError(null);
      const run =
        store === 'assets'
          ? fetchLibraryAssets(query, opts)
          : store === 'uploads'
            ? fetchLibraryUploads(query, opts, ctrl.signal)
            : fetchLibraryGenerated(query, opts);
      run
        .then((rows) => {
          if (cancelled) return;
          setItems(rows);
          setLoading(false);
        })
        .catch((err: unknown) => {
          if (cancelled || (err as { name?: string }).name === 'AbortError') return;
          console.error(`[useLibrarySearch] ${store} failed:`, err);
          setItems(EMPTY);
          setError(err instanceof Error ? err : new Error(String(err)));
          setLoading(false);
        });
    }, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      ctrl.abort();
      clearTimeout(timer);
    };
    // `key` carries every option this store reads; see useLibrarySearch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, store, query, key, nonce]);

  return { items, loading, error, reload };
}

export function useLibrarySearch(
  query: string,
  opts: LibrarySearchOptions,
): LibrarySearchResult {
  const stores = opts.stores ?? LIBRARY_STORES;
  // One string per store holding exactly the options that store reads, so a
  // kind chip on Uploads does not re-fetch Assets.
  const assetKey = `${opts.scopeId}|${opts.assetType ?? ''}|${opts.assetScope ?? 'all'}|${opts.projectId ?? ''}|${opts.limit ?? ''}`;
  const uploadKey = `${opts.scopeId}|${opts.uploadKinds ?? ''}|${opts.limit ?? ''}`;
  const generatedKey = `${opts.scopeId}|${opts.generatedScope ?? 'this-canvas'}|${opts.canvasId ?? ''}|${opts.limit ?? ''}`;

  const assets = useOneStore('assets', stores.includes('assets'), query, opts, assetKey);
  const uploads = useOneStore('uploads', stores.includes('uploads'), query, opts, uploadKey);
  const generated = useOneStore('generated', stores.includes('generated'), query, opts, generatedKey);

  return useMemo(
    () => ({ assets, uploads, generated }),
    [assets, uploads, generated],
  );
}
```

- [ ] Run it. Expected: `Tests 11 passed`.

```bash
cd frontend && npx vitest run "features/canvas-core/library/librarySearch.test.ts"
```

- [ ] Mutation-verify three properties, one at a time, restoring after each:
  1. Delete the `if (cancelled) return;` guard in the `.then`. Expected: `a superseded query never lands` fails with `expected 'Stale' to be 'Cole Bannon'`.
  2. Replace the three `useOneStore` calls with one shared effect that `Promise.all`s the three fetchers. Expected: `one store failing leaves the other two with their rows` fails — uploads and generated come back empty.
  3. Change `library: 'all'` to omitted in `fetchLibraryAssets`. Expected: `asks all three stores in parallel` fails on the `objectContaining({ library: 'all' })` assertion.

- [ ] Confirm the TypeScript baseline has not grown:

```bash
cd frontend && npx tsc --noEmit -p . 2>&1 | grep -c "error TS"
```

- [ ] Commit:

```bash
git add frontend/features/canvas-core/library/librarySearch.ts frontend/features/canvas-core/library/librarySearch.test.ts
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(canvas): 三库并行索引 useLibrarySearch —— 每库独立 loading/error/retry，一库挂了不拖垮另外两库"
```

---

### Task 3: `library/LibraryGrid.tsx` + `LibraryCell.tsx` + `librarySelection.ts` — 一处实现的网格

Spec §3.3（网格 / 多选 / 页脚）+ §2 目标 6（每一处拾取都在界面上说明后果）+ §3.7（配额已满）.

**Files**
- Create `frontend/features/canvas-core/library/librarySelection.ts`
- Create `frontend/features/canvas-core/library/LibraryCell.tsx`
- Create `frontend/features/canvas-core/library/LibraryGrid.tsx`
- Test `frontend/features/canvas-core/library/librarySelection.test.ts`
- Test `frontend/features/canvas-core/library/LibraryGrid.test.tsx`
- Test `frontend/features/canvas-core/library/libraryI18n.test.ts`
- Modify `frontend/public/locales/en.json` — add the `canvas.library` object
- Modify `frontend/public/locales/zh.json` — same keys, real Chinese values

**Interfaces**

Consumes (verbatim, facts §7 and `frontend/hooks/useMeasuredAspectRatios.ts`):

```ts
export function computeJustifiedRows(
  aspectRatios: number[],
  containerWidth: number,
  { targetRowHeight = 170, gap = 8 }: JustifiedOptions = {},
  reuse?: JustifiedReuse,
): JustifiedRow[]                                   // JustifiedRow = { start, end, height }

export function useJustifiedVirtualizer({
  scrollRef, aspectRatios, targetRowHeight = 170, gap = 8,
}: UseJustifiedVirtualizerOpts): {
  rows: JustifiedRow[];
  rowVirtualizer: ReturnType<typeof useVirtualizer>;
  containerRef: (node: HTMLElement | null) => void;
  gap: number;
}

export function useMeasuredAspectRatios(): {
  measured: Record<string, number>;
  report: (id: string, aspect: number) => void;
}
```

Produces:

```ts
// librarySelection.ts
export type LibraryItemKey = string;                       // `${store}:${id}`
export function libraryKey(item: { store: LibraryStore; id: string }): LibraryItemKey;
export interface PickModifiers { meta: boolean; shift: boolean }
export interface SelectionState { keys: LibraryItemKey[]; anchor: number | null }
export function applyPick(
  state: SelectionState,
  items: readonly LibraryItem[],
  index: number,
  mods: PickModifiers,
): SelectionState;
export function selectedItems(
  keys: readonly LibraryItemKey[],
  items: readonly LibraryItem[],
): LibraryItem[];

// LibraryCell.tsx
export interface LibraryCellProps {
  item: LibraryItem;
  width: number;
  height: number;
  selected: boolean;
  active: boolean;
  onPick: (e: React.MouseEvent) => void;
  onActivate: () => void;
  onMeasure: (aspect: number) => void;
  onHoverStart?: (rect: DOMRect) => void;
  onHoverEnd?: () => void;
}
export function LibraryCell(props: LibraryCellProps): React.ReactElement;

// LibraryGrid.tsx
export interface LibraryKindChip { value: string | null; label: string }
export interface LibraryGridAction {
  label: string;
  disabled?: boolean;
  onClick: (items: LibraryItem[]) => void;
}
export interface LibraryGridProps {
  testId?: string;
  items: LibraryItem[];
  loading?: boolean;
  error?: Error | null;
  onRetry?: () => void;
  query: string;
  onQueryChange: (q: string) => void;
  searchPlaceholder: string;
  kinds?: readonly LibraryKindChip[];
  activeKind?: string | null;
  onKindChange?: (kind: string | null) => void;
  selection: readonly LibraryItemKey[];
  onSelectionChange: (keys: LibraryItemKey[]) => void;
  /** First line: what picking these will DO. NOT optional — spec §2 goal 6. */
  consequence: string;
  /** Files that will really be sent after the model's ceiling, or null when
   *  there is no target and the question has no answer. */
  fileCount?: number | null;
  /** A refusal in words, e.g. "3 / 3 references used · remove one on the node". */
  note?: string | null;
  primaryAction?: LibraryGridAction;
  secondaryAction?: LibraryGridAction;
  onItemActivate?: (item: LibraryItem) => void;
  onItemHover?: (item: LibraryItem | null, rect?: DOMRect) => void;
  onItemDragStart?: (item: LibraryItem, e: React.DragEvent) => void;
  emptyLabel: string;
  targetRowHeight?: number;
  className?: string;
}
export function LibraryGrid(props: LibraryGridProps): React.ReactElement;
```

Selection rules, fixed here so every caller behaves identically:

| Gesture | Result | Anchor after |
|---|---|---|
| click | selection becomes exactly that one item | this index |
| `⌘` / `Ctrl` click | toggle that item, keep the rest | this index |
| `⇧` click | selection becomes the inclusive range from the anchor to this index (the anchor, if unset, is this index) | unchanged |
| double click | `onItemActivate(item)`; selection untouched | unchanged |

**Steps**

- [ ] Write the failing selection test. Create `frontend/features/canvas-core/library/librarySelection.test.ts`:

```ts
import { describe, expect, it } from 'vitest';

import { applyPick, libraryKey, selectedItems } from './librarySelection';
import type { LibraryItem } from './librarySearch';

const items: LibraryItem[] = ['a', 'b', 'c', 'd'].map((n, i) => ({
  store: 'uploads',
  id: `65500000000000000${i}`,
  title: n,
  thumbUrl: '',
  kind: 'image',
}));
const key = (i: number) => libraryKey(items[i]);
const EMPTY = { keys: [] as string[], anchor: null };

describe('applyPick', () => {
  it('a plain click replaces the whole selection', () => {
    const first = applyPick(EMPTY, items, 2, { meta: false, shift: false });
    expect(first).toEqual({ keys: [key(2)], anchor: 2 });
    const second = applyPick(first, items, 0, { meta: false, shift: false });
    expect(second).toEqual({ keys: [key(0)], anchor: 0 });
  });

  it('meta-click toggles one without disturbing the rest', () => {
    const one = applyPick(EMPTY, items, 1, { meta: false, shift: false });
    const two = applyPick(one, items, 3, { meta: true, shift: false });
    expect(two.keys).toEqual([key(1), key(3)]);
    const back = applyPick(two, items, 1, { meta: true, shift: false });
    expect(back.keys).toEqual([key(3)]);
    expect(back.anchor).toBe(1);
  });

  it('shift-click takes the inclusive range from the anchor, in either direction', () => {
    const anchored = applyPick(EMPTY, items, 3, { meta: false, shift: false });
    const ranged = applyPick(anchored, items, 1, { meta: false, shift: true });
    expect(ranged.keys).toEqual([key(1), key(2), key(3)]);
    // The anchor SURVIVES, so a second shift-click re-ranges from the same
    // origin instead of walking the selection along.
    expect(ranged.anchor).toBe(3);
    const wider = applyPick(ranged, items, 0, { meta: false, shift: true });
    expect(wider.keys).toEqual([key(0), key(1), key(2), key(3)]);
  });

  it('shift with no anchor yet selects just the clicked row', () => {
    expect(applyPick(EMPTY, items, 2, { meta: false, shift: true })).toEqual({
      keys: [key(2)],
      anchor: 2,
    });
  });

  it('selectedItems resolves keys back to rows, in list order, dropping stale keys', () => {
    expect(selectedItems([key(3), key(1), 'uploads:gone'], items).map((i) => i.title)).toEqual([
      'b',
      'd',
    ]);
  });
});
```

- [ ] Run it. Expected failure: `Failed to resolve import "./librarySelection"`.

```bash
cd frontend && npx vitest run "features/canvas-core/library/librarySelection.test.ts"
```

- [ ] Implement `frontend/features/canvas-core/library/librarySelection.ts`:

```ts
// features/canvas-core/library/librarySelection.ts
//
// Multi-select arithmetic for the library grid, kept OUT of the component so
// the four gestures can be pinned without a DOM. A selection is a list of
// `${store}:${id}` keys plus an anchor index; the keys survive a re-query that
// changes the list under them, and `selectedItems` drops the ones that no
// longer resolve rather than inventing rows for them.

import type { LibraryItem, LibraryStore } from './librarySearch';

export type LibraryItemKey = string;

export function libraryKey(item: { store: LibraryStore; id: string }): LibraryItemKey {
  return `${item.store}:${item.id}`;
}

export interface PickModifiers {
  meta: boolean;
  shift: boolean;
}

export interface SelectionState {
  keys: LibraryItemKey[];
  /** Where a ⇧ range starts. Null before the first click. */
  anchor: number | null;
}

export function applyPick(
  state: SelectionState,
  items: readonly LibraryItem[],
  index: number,
  mods: PickModifiers,
): SelectionState {
  const item = items[index];
  if (!item) return state;
  const key = libraryKey(item);

  if (mods.shift) {
    // The anchor SURVIVES a range pick: a second ⇧-click must re-range from
    // the same origin, not walk the selection along one row at a time.
    const anchor = state.anchor ?? index;
    const lo = Math.min(anchor, index);
    const hi = Math.max(anchor, index);
    return {
      keys: items.slice(lo, hi + 1).map(libraryKey),
      anchor,
    };
  }

  if (mods.meta) {
    const has = state.keys.includes(key);
    return {
      keys: has ? state.keys.filter((k) => k !== key) : [...state.keys, key],
      anchor: index,
    };
  }

  return { keys: [key], anchor: index };
}

/** Keys back to rows, IN LIST ORDER — which is delivery order for everything
 *  downstream (references ship in the order they are added). A key with no row
 *  is dropped: the list was re-queried and that item is simply not here. */
export function selectedItems(
  keys: readonly LibraryItemKey[],
  items: readonly LibraryItem[],
): LibraryItem[] {
  const wanted = new Set(keys);
  return items.filter((i) => wanted.has(libraryKey(i)));
}
```

- [ ] Run it. Expected: `Tests 5 passed`.

- [ ] Write the failing grid test. Create `frontend/features/canvas-core/library/LibraryGrid.test.tsx`:

```tsx
// features/canvas-core/library/LibraryGrid.test.tsx
//
// jsdom lays nothing out, so `useContainerWidth` would measure 0 and the
// justified pre-pass would return no rows. The stub below gives the container
// a real width so the REAL layout path runs — the alternative (a
// non-virtualized fallback for tests only) would test something the user never
// sees.

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown) =>
      typeof d === 'string'
        ? d
        : typeof d === 'object' && d !== null && 'defaultValue' in (d as object)
          ? String((d as { defaultValue: string }).defaultValue).replace(
              /\{\{count\}\}/g,
              String((d as { count?: number }).count ?? ''),
            )
          : k,
  }),
}));

import { LibraryGrid } from './LibraryGrid';
import { libraryKey } from './librarySelection';
import type { LibraryItem } from './librarySearch';

const items: LibraryItem[] = ['Alpha', 'Bravo', 'Charlie', 'Delta'].map((t, i) => ({
  store: 'uploads',
  id: `65500000000000000${i}`,
  title: t,
  thumbUrl: `/api/v1/resources/65500000000000000${i}/cover`,
  kind: 'image',
}));

let realRect: () => DOMRect;
beforeEach(() => {
  realRect = HTMLElement.prototype.getBoundingClientRect;
  HTMLElement.prototype.getBoundingClientRect = function () {
    return { width: 640, height: 480, top: 0, left: 0, right: 640, bottom: 480, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
  };
});
afterEach(() => {
  HTMLElement.prototype.getBoundingClientRect = realRect;
  cleanup();
});

function renderGrid(over: Partial<Parameters<typeof LibraryGrid>[0]> = {}) {
  const onSelectionChange = vi.fn();
  const primary = vi.fn();
  const utils = render(
    <LibraryGrid
      items={items}
      query=""
      onQueryChange={() => {}}
      searchPlaceholder="Search library…"
      selection={[]}
      onSelectionChange={onSelectionChange}
      consequence="Reference images · sent to doubao-seedream · 0 / 3 used"
      primaryAction={{ label: 'Add 0 References', onClick: primary }}
      emptyLabel="Nothing here yet"
      {...over}
    />,
  );
  return { ...utils, onSelectionChange, primary };
}

describe('LibraryGrid', () => {
  it('renders every item as a cell', () => {
    renderGrid();
    expect(screen.getAllByTestId('library-cell')).toHaveLength(4);
  });

  it('states the consequence before anything is picked', () => {
    renderGrid();
    expect(screen.getByTestId('library-consequence').textContent).toContain(
      'Reference images · sent to doubao-seedream',
    );
  });

  it('a plain click replaces the selection; ⌘ adds; ⇧ takes the range', () => {
    const { onSelectionChange, rerender } = renderGrid();
    fireEvent.click(screen.getAllByTestId('library-cell')[1]);
    expect(onSelectionChange).toHaveBeenLastCalledWith([libraryKey(items[1])]);

    rerender(
      <LibraryGrid
        items={items}
        query=""
        onQueryChange={() => {}}
        searchPlaceholder="Search library…"
        selection={[libraryKey(items[1])]}
        onSelectionChange={onSelectionChange}
        consequence="c"
        emptyLabel="e"
      />,
    );
    fireEvent.click(screen.getAllByTestId('library-cell')[3], { metaKey: true });
    expect(onSelectionChange).toHaveBeenLastCalledWith([
      libraryKey(items[1]),
      libraryKey(items[3]),
    ]);
    fireEvent.click(screen.getAllByTestId('library-cell')[3], { shiftKey: true });
    expect(onSelectionChange).toHaveBeenLastCalledWith([
      libraryKey(items[1]),
      libraryKey(items[2]),
      libraryKey(items[3]),
    ]);
  });

  it('the footer counts the selection, and the file count only when there is one', () => {
    const { rerender } = renderGrid({
      selection: [libraryKey(items[0]), libraryKey(items[2])],
      fileCount: 3,
    });
    expect(screen.getByTestId('library-footer-count').textContent).toBe(
      '2 selected · 3 files',
    );
    rerender(
      <LibraryGrid
        items={items}
        query=""
        onQueryChange={() => {}}
        searchPlaceholder="s"
        selection={[libraryKey(items[0])]}
        onSelectionChange={() => {}}
        consequence="c"
        emptyLabel="e"
        fileCount={null}
      />,
    );
    expect(screen.getByTestId('library-footer-count').textContent).toBe('1 selected');
  });

  it('a disabled primary action says WHY in the note', () => {
    renderGrid({
      selection: [libraryKey(items[0])],
      note: '3 / 3 references used · remove one on the node',
      primaryAction: { label: 'Add 1 Reference', disabled: true, onClick: vi.fn() },
    });
    expect(screen.getByTestId('library-primary')).toBeDisabled();
    expect(screen.getByTestId('library-note').textContent).toBe(
      '3 / 3 references used · remove one on the node',
    );
  });

  it('the primary action receives the selected ROWS, in list order', () => {
    const primary = vi.fn();
    renderGrid({
      selection: [libraryKey(items[2]), libraryKey(items[0])],
      primaryAction: { label: 'Add 2 References', onClick: primary },
    });
    fireEvent.click(screen.getByTestId('library-primary'));
    expect(primary.mock.calls[0][0].map((i: LibraryItem) => i.title)).toEqual([
      'Alpha',
      'Charlie',
    ]);
  });

  it('double click activates one item without changing the selection', () => {
    const onItemActivate = vi.fn();
    const { onSelectionChange } = renderGrid({ onItemActivate });
    fireEvent.doubleClick(screen.getAllByTestId('library-cell')[2]);
    expect(onItemActivate).toHaveBeenCalledWith(expect.objectContaining({ title: 'Charlie' }));
    expect(onSelectionChange).not.toHaveBeenCalled();
  });

  it('arrow keys move the active cell and Enter runs the primary action', () => {
    const primary = vi.fn();
    renderGrid({
      selection: [libraryKey(items[0])],
      primaryAction: { label: 'Add 1 Reference', onClick: primary },
    });
    const root = screen.getByTestId('library-grid');
    fireEvent.keyDown(root, { key: 'ArrowRight' });
    fireEvent.keyDown(root, { key: 'ArrowRight' });
    expect(screen.getAllByTestId('library-cell')[2]).toHaveAttribute('data-active', 'true');
    fireEvent.keyDown(root, { key: 'Enter' });
    expect(primary).toHaveBeenCalled();
  });

  it('typing in the search box reports up, and does not reach the canvas', () => {
    const onQueryChange = vi.fn();
    renderGrid({ onQueryChange });
    fireEvent.change(screen.getByTestId('library-search'), { target: { value: 'harbour' } });
    expect(onQueryChange).toHaveBeenCalledWith('harbour');
  });

  it('an error shows a retry instead of an empty shelf', () => {
    const onRetry = vi.fn();
    renderGrid({ items: [], error: new Error('boom'), onRetry });
    expect(screen.queryByTestId('library-empty')).toBeNull();
    fireEvent.click(screen.getByTestId('library-retry'));
    expect(onRetry).toHaveBeenCalled();
  });

  it('a draft asset is visible and labelled, not hidden', () => {
    renderGrid({
      items: [{ store: 'assets', id: '727145299382534300', title: 'Cole Bannon', thumbUrl: '', kind: 'character', ready: false }],
    });
    expect(screen.getByTestId('library-cell-not-ready')).toBeInTheDocument();
  });
});
```

- [ ] Run it. Expected failure: `Failed to resolve import "./LibraryGrid"`.

- [ ] Implement `frontend/features/canvas-core/library/LibraryCell.tsx`:

```tsx
// features/canvas-core/library/LibraryCell.tsx
//
// One justified cell. Presentational: it owns no selection state and no
// fetching, so the same cell serves the compact reference popover and the
// full panel.
//
// `nodrag nowheel nopan` are load-bearing, not decoration — this grid renders
// inside a React Flow node in its popover form, and React Flow stops mousedown
// propagation on the node, so without them every cell silently does nothing
// for a real user while a synthetic dispatch in a test still "works".

import React, { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { ImageOff } from 'lucide-react';

import type { LibraryItem } from './librarySearch';

export interface LibraryCellProps {
  item: LibraryItem;
  width: number;
  height: number;
  selected: boolean;
  active: boolean;
  onPick: (e: React.MouseEvent) => void;
  onActivate: () => void;
  /** The thumbnail's natural w/h, once the browser knows it. */
  onMeasure: (aspect: number) => void;
  onHoverStart?: (rect: DOMRect) => void;
  onHoverEnd?: () => void;
  onDragStart?: (e: React.DragEvent) => void;
}

export function LibraryCell({
  item,
  width,
  height,
  selected,
  active,
  onPick,
  onActivate,
  onMeasure,
  onHoverStart,
  onHoverEnd,
  onDragStart,
}: LibraryCellProps): React.ReactElement {
  const { t } = useTranslation();

  const handleLoad = useCallback(
    (e: React.SyntheticEvent<HTMLImageElement>) => {
      const img = e.currentTarget;
      if (img.naturalWidth > 0 && img.naturalHeight > 0) {
        onMeasure(img.naturalWidth / img.naturalHeight);
      }
    },
    [onMeasure],
  );

  return (
    <button
      type="button"
      data-testid="library-cell"
      data-library-store={item.store}
      data-library-id={item.id}
      data-active={active ? 'true' : undefined}
      aria-pressed={selected}
      draggable={onDragStart !== undefined}
      onDragStart={onDragStart}
      onClick={onPick}
      onDoubleClick={onActivate}
      onMouseEnter={(e) => onHoverStart?.(e.currentTarget.getBoundingClientRect())}
      onMouseLeave={() => onHoverEnd?.()}
      style={{ width, height }}
      className={`nodrag nowheel nopan group relative shrink-0 overflow-hidden rounded-md border text-left transition-colors ${
        selected
          ? 'border-[var(--accent-border)] ring-1 ring-[var(--accent-border)]'
          : 'border-canvas-line hover:border-[var(--accent-border)]'
      }`}
    >
      {item.thumbUrl ? (
        <img
          src={item.thumbUrl}
          alt=""
          loading="lazy"
          onLoad={handleLoad}
          className="h-full w-full object-cover"
        />
      ) : (
        <span className="flex h-full w-full items-center justify-center bg-canvas-card text-canvas-muted">
          <ImageOff size={14} />
        </span>
      )}
      <span className="pointer-events-none absolute inset-x-0 bottom-0 truncate bg-black/55 px-1 py-0.5 text-[10px] text-white">
        {item.title}
      </span>
      {item.ready === false && (
        <span
          data-testid="library-cell-not-ready"
          className="pointer-events-none absolute left-1 top-1 rounded bg-warn-soft px-1 text-[9px] text-warn"
        >
          {t('canvas.library.notReady', 'Not Ready')}
        </span>
      )}
    </button>
  );
}

export default LibraryCell;
```

- [ ] Implement `frontend/features/canvas-core/library/LibraryGrid.tsx`:

```tsx
// features/canvas-core/library/LibraryGrid.tsx
//
// THE canvas library grid — one implementation behind every picker.
//
// Search box, kind chips, justified multi-select grid, a consequence line and
// an action footer. It is deliberately fed ONE store's rows at a time: the `@`
// palette renders four groups by mounting it per group, the panel renders one
// segment at a time, and neither needs the component to know which store it is
// looking at.
//
// The consequence line is a REQUIRED prop, not an optional one. Spec §2 goal 6
// is that every pick says what it will do — "adds a reference" and "inserts a
// mention" and "replaces the body" are three different outcomes behind
// identically-shaped grids, and an optional prop is one a caller forgets.
//
// Layout reuses PR #2092's two item-agnostic pieces (`computeJustifiedRows`
// via `useJustifiedVirtualizer`). ⚠️ When the scroll container has no measured
// height — jsdom, and the first frame of a panel that has not laid out yet —
// the virtualizer reports ZERO virtual rows. Rendering the full row list in
// that case is what keeps the grid from painting blank; the lists here are
// capped at 60 rows by `useLibrarySearch`, so the fallback is cheap.

import React, { useCallback, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, RotateCw, Search } from 'lucide-react';

import { useJustifiedVirtualizer } from '../../../hooks/useJustifiedVirtualizer';
import { useMeasuredAspectRatios } from '../../../hooks/useMeasuredAspectRatios';
import { LibraryCell } from './LibraryCell';
import type { LibraryItem } from './librarySearch';
import {
  applyPick,
  libraryKey,
  selectedItems,
  type LibraryItemKey,
  type SelectionState,
} from './librarySelection';

const GAP = 6;

export interface LibraryKindChip {
  value: string | null;
  label: string;
}

export interface LibraryGridAction {
  label: string;
  disabled?: boolean;
  onClick: (items: LibraryItem[]) => void;
}

export interface LibraryGridProps {
  testId?: string;
  items: LibraryItem[];
  loading?: boolean;
  error?: Error | null;
  onRetry?: () => void;
  query: string;
  onQueryChange: (q: string) => void;
  searchPlaceholder: string;
  kinds?: readonly LibraryKindChip[];
  activeKind?: string | null;
  onKindChange?: (kind: string | null) => void;
  selection: readonly LibraryItemKey[];
  onSelectionChange: (keys: LibraryItemKey[]) => void;
  consequence: string;
  fileCount?: number | null;
  note?: string | null;
  primaryAction?: LibraryGridAction;
  secondaryAction?: LibraryGridAction;
  onItemActivate?: (item: LibraryItem) => void;
  onItemHover?: (item: LibraryItem | null, rect?: DOMRect) => void;
  onItemDragStart?: (item: LibraryItem, e: React.DragEvent) => void;
  emptyLabel: string;
  targetRowHeight?: number;
  className?: string;
}

export function LibraryGrid({
  testId = 'library-grid',
  items,
  loading = false,
  error = null,
  onRetry,
  query,
  onQueryChange,
  searchPlaceholder,
  kinds,
  activeKind = null,
  onKindChange,
  selection,
  onSelectionChange,
  consequence,
  fileCount = null,
  note = null,
  primaryAction,
  secondaryAction,
  onItemActivate,
  onItemHover,
  onItemDragStart,
  emptyLabel,
  targetRowHeight = 120,
  className = '',
}: LibraryGridProps): React.ReactElement {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);
  const anchorRef = useRef<number | null>(null);
  const [active, setActive] = useState(0);

  const { measured, report } = useMeasuredAspectRatios();
  const aspectRatios = useMemo(
    () => items.map((i) => measured[libraryKey(i)] ?? i.aspect ?? 1),
    [items, measured],
  );
  const { rows, rowVirtualizer, containerRef } = useJustifiedVirtualizer({
    scrollRef: scrollRef as React.RefObject<HTMLElement>,
    aspectRatios,
    targetRowHeight,
    gap: GAP,
  });
  const virtual = rowVirtualizer.getVirtualItems();
  const visibleRows = virtual.length > 0 ? virtual.map((v) => v.index) : rows.map((_, i) => i);

  const chosen = useMemo(() => selectedItems(selection, items), [selection, items]);
  const selectedSet = useMemo(() => new Set(selection), [selection]);

  const pick = useCallback(
    (index: number, e: React.MouseEvent) => {
      const state: SelectionState = { keys: [...selection], anchor: anchorRef.current };
      const next = applyPick(state, items, index, {
        meta: e.metaKey || e.ctrlKey,
        shift: e.shiftKey,
      });
      anchorRef.current = next.anchor;
      setActive(index);
      onSelectionChange(next.keys);
    },
    [selection, items, onSelectionChange],
  );

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault();
        setActive((i) =>
          Math.max(0, Math.min(items.length - 1, i + (e.key === 'ArrowRight' ? 1 : -1))),
        );
        return;
      }
      if (e.key === 'Enter') {
        e.preventDefault();
        if (chosen.length > 0 && primaryAction && !primaryAction.disabled) {
          primaryAction.onClick(chosen);
          return;
        }
        const item = items[active];
        if (item) onItemActivate?.(item);
      }
    },
    [items, active, chosen, primaryAction, onItemActivate],
  );

  return (
    <div
      data-testid={testId}
      onKeyDown={onKeyDown}
      className={`nodrag nowheel nopan flex min-h-0 flex-col ${className}`}
    >
      <div className="flex items-center gap-1.5 border-b border-canvas-line px-2 py-1.5">
        <Search size={13} className="shrink-0 text-canvas-muted" />
        <input
          data-testid="library-search"
          aria-label={t('canvas.library.searchLabel', 'Search Library')}
          placeholder={searchPlaceholder}
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          className="min-w-0 flex-1 bg-transparent text-xs text-canvas-text outline-none placeholder:text-canvas-muted"
        />
      </div>

      <p
        data-testid="library-consequence"
        className="border-b border-canvas-line px-2 py-1 text-[10px] leading-snug text-canvas-muted"
      >
        {consequence}
      </p>

      {kinds && kinds.length > 0 && (
        <div className="flex flex-wrap gap-1 border-b border-canvas-line px-2 py-1.5">
          {kinds.map((chip) => (
            <button
              key={chip.value ?? '__all'}
              type="button"
              data-testid={`library-kind-${chip.value ?? 'all'}`}
              aria-pressed={activeKind === chip.value}
              onClick={() => onKindChange?.(chip.value)}
              className={`nodrag rounded-full border px-2 py-0.5 text-[10px] ${
                activeKind === chip.value
                  ? 'border-[var(--accent-border)] text-[var(--accent-text)]'
                  : 'border-canvas-line text-canvas-muted hover:text-canvas-text'
              }`}
            >
              {chip.label}
            </button>
          ))}
        </div>
      )}

      <div ref={scrollRef} className="nowheel min-h-0 flex-1 overflow-y-auto p-1.5">
        {error ? (
          <div data-testid="library-error" className="p-3 text-[11px] text-warn">
            <p>{t('canvas.library.loadFailed', 'Could not load this library')}</p>
            {onRetry && (
              <button
                type="button"
                data-testid="library-retry"
                onClick={onRetry}
                className="nodrag mt-1 inline-flex items-center gap-1 rounded border border-canvas-line px-2 py-0.5 text-canvas-text"
              >
                <RotateCw size={11} />
                {t('canvas.library.retry', 'Retry')}
              </button>
            )}
          </div>
        ) : loading && items.length === 0 ? (
          <div className="flex items-center gap-2 p-3 text-[11px] text-canvas-muted">
            <Loader2 size={12} className="animate-spin" />
            {t('canvas.library.loading', 'Loading…')}
          </div>
        ) : items.length === 0 ? (
          <div data-testid="library-empty" className="p-3 text-[11px] text-canvas-muted">
            {emptyLabel}
          </div>
        ) : (
          <div ref={containerRef} className="flex flex-col" style={{ gap: GAP }}>
            {visibleRows.map((rowIndex) => {
              const row = rows[rowIndex];
              if (!row) return null;
              return (
                <div key={rowIndex} className="flex" style={{ gap: GAP, height: row.height }}>
                  {items.slice(row.start, row.end).map((item, offset) => {
                    const index = row.start + offset;
                    return (
                      <LibraryCell
                        key={libraryKey(item)}
                        item={item}
                        width={aspectRatios[index] * row.height}
                        height={row.height}
                        selected={selectedSet.has(libraryKey(item))}
                        active={index === active}
                        onPick={(e) => pick(index, e)}
                        onActivate={() => onItemActivate?.(item)}
                        onMeasure={(a) => report(libraryKey(item), a)}
                        onHoverStart={(rect) => onItemHover?.(item, rect)}
                        onHoverEnd={() => onItemHover?.(null)}
                        onDragStart={
                          onItemDragStart ? (e) => onItemDragStart(item, e) : undefined
                        }
                      />
                    );
                  })}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 border-t border-canvas-line px-2 py-1.5">
        <span data-testid="library-footer-count" className="text-[10px] text-canvas-muted">
          {fileCount === null || fileCount === undefined
            ? t('canvas.library.selected', {
                count: chosen.length,
                defaultValue: '{{count}} selected',
              })
            : `${t('canvas.library.selected', {
                count: chosen.length,
                defaultValue: '{{count}} selected',
              })} · ${t('canvas.library.files', {
                count: fileCount,
                defaultValue: '{{count}} files',
              })}`}
        </span>
        <span className="flex-1" />
        {secondaryAction && (
          <button
            type="button"
            data-testid="library-secondary"
            disabled={secondaryAction.disabled || chosen.length === 0}
            onClick={() => secondaryAction.onClick(chosen)}
            className="nodrag rounded border border-canvas-line px-2 py-0.5 text-[11px] text-canvas-text disabled:cursor-not-allowed disabled:opacity-50"
          >
            {secondaryAction.label}
          </button>
        )}
        {primaryAction && (
          <button
            type="button"
            data-testid="library-primary"
            disabled={primaryAction.disabled || chosen.length === 0}
            onClick={() => primaryAction.onClick(chosen)}
            className="nodrag rounded border border-[var(--accent-border)] px-2 py-0.5 text-[11px] text-[var(--accent-text)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {primaryAction.label}
          </button>
        )}
      </div>
      {note && (
        <p data-testid="library-note" className="px-2 pb-1.5 text-[10px] text-warn">
          {note}
        </p>
      )}
    </div>
  );
}

export default LibraryGrid;
```

- [ ] Add the i18n keys. In `frontend/public/locales/en.json`, inside `canvas`, add:

```json
"library": {
  "searchLabel": "Search Library",
  "loading": "Loading…",
  "loadFailed": "Could not load this library",
  "retry": "Retry",
  "notReady": "Not Ready",
  "selected_one": "{{count}} selected",
  "selected_other": "{{count}} selected",
  "files_one": "{{count}} file",
  "files_other": "{{count}} files"
}
```

and in `frontend/public/locales/zh.json`, inside `canvas`:

```json
"library": {
  "searchLabel": "搜索素材库",
  "loading": "加载中…",
  "loadFailed": "无法加载该库",
  "retry": "重试",
  "notReady": "未就绪",
  "selected_one": "已选 {{count}} 项",
  "selected_other": "已选 {{count}} 项",
  "files_one": "{{count}} 个文件",
  "files_other": "{{count}} 个文件"
}
```

- [ ] Write the i18n parity test that will guard every later task in this plan. Create `frontend/features/canvas-core/library/libraryI18n.test.ts`:

```ts
// features/canvas-core/library/libraryI18n.test.ts
//
// en/zh parity for `canvas.library.*`.
//
// Every t() on the canvas passes an English default, so a key missing from
// en.json still renders correctly and reports nothing, and a key missing from
// zh.json falls back to English for zh users only — which nobody on an English
// machine ever sees.
//
// The key list is scanned from the WHOLE `library/` directory rather than a
// hand-kept file list (which is what `canvasAssetEntryI18n.test.ts` uses and
// what makes that test go stale the day a file is added). A new file in this
// directory is covered the moment it exists.

import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

const LOCALES = path.resolve(__dirname, '../../../public/locales');
const DIR = __dirname;

function sources(dir: string): string[] {
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .flatMap((e) =>
      e.isDirectory()
        ? sources(path.join(dir, e.name))
        : /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name)
          ? [path.join(dir, e.name)]
          : [],
    );
}

function load(lang: string): Record<string, unknown> {
  return JSON.parse(fs.readFileSync(path.join(LOCALES, `${lang}.json`), 'utf8'));
}

function at(tree: Record<string, unknown>, key: string): unknown {
  return key.split('.').reduce<unknown>(
    (node, part) =>
      node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined,
    tree,
  );
}

const PATTERN = /'(canvas\.library\.[A-Za-z0-9_.]+)'/g;

// i18next resolves `foo` to `foo_one` / `foo_other` when a `count` is passed,
// so a plural key is DECLARED in two halves and USED as one. Expanding here is
// what lets the "nothing unused" case below stay exact.
const PLURAL_SUFFIXES = ['_one', '_other'];

const used = [
  ...new Set(
    sources(DIR).flatMap((f) =>
      [...fs.readFileSync(f, 'utf8').matchAll(PATTERN)].map((m) => m[1]),
    ),
  ),
].sort();

const en = load('en');
const zh = load('zh');

function resolved(tree: Record<string, unknown>, key: string): boolean {
  if (typeof at(tree, key) === 'string') return true;
  return PLURAL_SUFFIXES.every((s) => typeof at(tree, `${key}${s}`) === 'string');
}

describe('canvas.library i18n', () => {
  it('found the keys at all — an empty list would pass every case below', () => {
    expect(used.length).toBeGreaterThan(4);
  });

  it.each(used)('%s is translated in both locales', (key) => {
    expect(resolved(en, key), `${key} missing from en.json`).toBe(true);
    expect(resolved(zh, key), `${key} missing from zh.json`).toBe(true);
  });

  it('the zh values are actually translated, not copied English', () => {
    const copied = used.filter((k) => {
      const e = at(en, k) ?? at(en, `${k}_other`);
      const z = at(zh, k) ?? at(zh, `${k}_other`);
      return typeof e === 'string' && e === z;
    });
    expect(copied).toEqual([]);
  });

  it('no locale carries a canvas.library key nothing asks for', () => {
    const declared = Object.keys(
      (at(en, 'canvas.library') ?? {}) as Record<string, unknown>,
    )
      .map((k) => k.replace(/_(one|other)$/, ''))
      .filter((k, i, a) => a.indexOf(k) === i)
      .sort();
    expect(declared).toEqual(used.map((k) => k.replace('canvas.library.', '')).sort());
  });

  it('the two locales carry exactly the same canvas.library keys', () => {
    const keys = (tree: Record<string, unknown>) =>
      Object.keys((at(tree, 'canvas.library') ?? {}) as Record<string, unknown>).sort();
    expect(keys(zh)).toEqual(keys(en));
  });
});
```

- [ ] Run all three test files. Expected: `Tests 5 passed` (selection), `Tests 11 passed` (grid), and the i18n file green.

```bash
cd frontend && npx vitest run "features/canvas-core/library/librarySelection.test.ts" "features/canvas-core/library/LibraryGrid.test.tsx" "features/canvas-core/library/libraryI18n.test.ts"
```

- [ ] Mutation-verify, restoring after each:
  1. In `applyPick`, set `anchor: index` on the shift branch. Expected: `shift-click takes the inclusive range` fails on `expect(ranged.anchor).toBe(3)`, and the follow-up range comes back as two items instead of four.
  2. In `LibraryGrid`, change the primary button's `onClick` to `primaryAction.onClick(items)`. Expected: `the primary action receives the selected ROWS` fails with four titles instead of two.
  3. Delete the `canvas.library.notReady` key from `zh.json`. Expected: the i18n parity test fails on `canvas.library.notReady missing from zh.json`.

- [ ] Confirm the TypeScript baseline has not grown.

- [ ] Commit:

```bash
git add frontend/features/canvas-core/library/librarySelection.ts frontend/features/canvas-core/library/librarySelection.test.ts frontend/features/canvas-core/library/LibraryCell.tsx frontend/features/canvas-core/library/LibraryGrid.tsx frontend/features/canvas-core/library/LibraryGrid.test.tsx frontend/features/canvas-core/library/libraryI18n.test.ts frontend/public/locales/en.json frontend/public/locales/zh.json
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(canvas): LibraryGrid —— 搜索/类型 chip/多选/后果头/页脚，一处实现供所有拾取器复用"
```

---

### Task 4: 「加参考图」弹层换成 `LibraryGrid` 紧凑态 + `library/addReferences.ts`

Spec §1 row 2（`query` 写死为 `''`）+ §3.3 页脚 + §3.7 配额 + §4 验收 2 / 6. Deletes `CanvasMentionPicker`.

**Files**
- Create `frontend/features/canvas-core/library/addReferences.ts`
- Create `frontend/features/canvas-core/library/LibraryReferencePopover.tsx`
- Test `frontend/features/canvas-core/library/addReferences.test.ts`
- Test `frontend/features/canvas-core/library/LibraryReferencePopover.test.tsx`
- Modify `frontend/features/canvas-core/smart/nodes/PromptNodeView.tsx` — delete `:295-317` (`addManualRef`), `:163` (`activeKind` state), `:61` (`ActiveKind` type), `:367` (`useResourceSearch('')`), `:53` + `:36` imports, and replace the mount at `:806-823` with an 8-line hunk. Net change is NEGATIVE.
- Delete `frontend/features/canvas-core/smart/nodes/CanvasMentionPicker.tsx`
- Modify (test) `frontend/features/canvas-core/smart/nodes/PromptNodeView.inputimages.test.tsx` — the `Add reference key opens the picker` case at `:239-243` and the now-dead `vi.mock('../../../../hooks/useResourceSearch')` at `:26-29`.
- Modify `frontend/public/locales/en.json` / `zh.json`

**Interfaces**

Consumes (verbatim, facts §2c and §6):

```ts
export async function importResourceAsCanvasMedia(resourceId: string): Promise<{ url: string; kind: 'image' | 'video'; id?: string }>
export async function fetchAssetDetail(scopeId: string, id: string, opts?: AssetDetailOptions): Promise<AssetRowDetail>
export function primarySlotFileIds(asset: AssetNodeSeed, loadoutId: string | null): string[]
export function useModelCapabilities(model: string | null | undefined): ModelCapabilities | null   // max_refs is a plain number
export const MAX_REFERENCE_IMAGES = 20;   // smart/refOrder.ts — IC's manual-ref ceiling
export interface GeneratedImageRef { url: string; kind: OutputKind; name?: string; id?: string }
export const DURABLE_PREFIXES = ['/api/v1/generated-media/', '/api/v1/resources/'] as const;
```

Produces:

```ts
// library/addReferences.ts
export interface AddReferencesResult {
  added: number;
  /** Already on the node, deduped by url. */
  skipped: number;
  /** Typed, never silent — one entry per item we could not turn into a ref. */
  failed: Array<{ item: LibraryItem; reason: 'mint_failed' | 'no_image_file' | 'not_an_image' }>;
}
export async function resolveReferenceRefs(
  item: LibraryItem,
  scopeId: string,
): Promise<GeneratedImageRef[]>;
export async function addReferences(
  nodeId: string,
  items: readonly LibraryItem[],
  scopeId: string,
): Promise<AddReferencesResult>;

// library/LibraryReferencePopover.tsx
export interface LibraryReferencePopoverProps {
  nodeId: string;
  /** The prompt's own generation model, for the ceiling and the header line. */
  model: string | null;
  onClose: () => void;
}
export function LibraryReferencePopover(props: LibraryReferencePopoverProps): React.ReactElement;
```

Per-store URL resolution, stated once so nothing has to guess:

| store | how it becomes a `manual_refs` entry |
|---|---|
| `uploads` | `importResourceAsCanvasMedia(id)` — mints a durable `/api/v1/generated-media/…` row. This is exactly what the current Add-reference path does (facts §2c). |
| `generated` | already durable: `/api/v1/generated-media/{id}/file`, no round trip. Video rows are refused with `not_an_image`. |
| `assets` | `fetchAssetDetail(scopeId, id)` → `primarySlotFileIds(detail, null)` → one `/api/v1/resources/{rid}/cover` per file. An asset with no primary-slot file fails with `no_image_file`. |

All three forms start with a `DURABLE_PREFIXES` entry, which is what makes the backend's reference bridge accept them.

**Steps**

- [ ] Write the failing helper test. Create `frontend/features/canvas-core/library/addReferences.test.ts`:

```ts
// features/canvas-core/library/addReferences.test.ts
//
// Turning a library pick into `manual_refs`. The three stores mint their urls
// three different ways and all three have to come out durable, because the
// backend's reference bridge accepts exactly two url families.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const importResourceAsCanvasMedia = vi.fn();
const fetchAssetDetail = vi.fn();

vi.mock('../smart/mediaImport', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  importResourceAsCanvasMedia: (...a: unknown[]) => importResourceAsCanvasMedia(...a),
}));
vi.mock('../../../services/assetsService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  fetchAssetDetail: (...a: unknown[]) => fetchAssetDetail(...a),
}));

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { addReferences, resolveReferenceRefs } from './addReferences';
import type { LibraryItem } from './librarySearch';

const SCOPE = '727145299382534100';

const UPLOAD: LibraryItem = {
  store: 'uploads',
  id: '655000000000000001',
  title: 'harbour.png',
  thumbUrl: '',
  kind: 'image',
};
const GENERATED: LibraryItem = {
  store: 'generated',
  id: '800000000000000001',
  title: 'A wide shot',
  thumbUrl: '',
  kind: 'image',
};
const ASSET: LibraryItem = {
  store: 'assets',
  id: '727145299382534300',
  title: 'Cole Bannon',
  thumbUrl: '',
  kind: 'character',
  ready: true,
};

/** `GET /assets/{id}` — every id a string, `files` present. */
const ASSET_DETAIL = {
  id: ASSET.id,
  scope_id: SCOPE,
  asset_type: 'character' as const,
  subtype: null,
  name: 'Cole Bannon',
  role_tag: 'lead',
  description: '',
  attrs: {},
  prompt_positive: null,
  prompt_negative: null,
  prompt_positive_zh: null,
  prompt_negative_zh: null,
  platform_params: {},
  cover_file_id: '600000000000000001',
  source: 'manual',
  duplicated_from: null,
  is_system_preset: false,
  in_library: true,
  tags: {},
  sort_order: 0,
  created_by: null,
  created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z',
  readiness: { state: 'ready' as const, missing: [] },
  file_counts_by_slot: { portrait: 2 },
  project_ids: [],
  loadout_count: 0,
  files: [
    {
      asset_id: ASSET.id,
      resource_id: '600000000000000001',
      slot: 'portrait',
      loadout_id: null,
      sort_order: 0,
      note: null,
      attached_by: null,
      attached_at: '2026-09-01T00:00:00Z',
    },
    {
      asset_id: ASSET.id,
      resource_id: '600000000000000002',
      slot: 'portrait',
      loadout_id: null,
      sort_order: 1,
      note: null,
      attached_by: null,
      attached_at: '2026-09-01T00:00:00Z',
    },
  ],
  links: [],
  linked_by: [],
  loadouts: [],
};

function seed(manualRefs: Array<{ url: string; kind: string }> = []): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '900000000000000001',
    nodes: [
      {
        id: 'p1',
        type: 'prompt',
        position: { x: 0, y: 0 },
        data: {
          body: '',
          provider_slug: '',
          agent_id: null,
          run_status: 'idle',
          resource_refs: [],
          manual_refs: manualRefs,
          gen: { kind: 'image', model: 'doubao-seedream', ratio: '1:1', count: 1 },
        },
      },
    ] as never,
    connections: [],
    selection: [],
  });
}

function refs(): Array<{ url: string }> {
  return (
    (useCanvasCoreStore.getState().nodes.find((n) => (n as { id: string }).id === 'p1') as {
      data: { manual_refs?: Array<{ url: string }> };
    }).data.manual_refs ?? []
  );
}

beforeEach(() => {
  importResourceAsCanvasMedia.mockReset().mockResolvedValue({
    url: '/api/v1/generated-media/770000000000000001/file',
    kind: 'image',
    id: '770000000000000001',
  });
  fetchAssetDetail.mockReset().mockResolvedValue(ASSET_DETAIL);
});
afterEach(() => {
  useCanvasCoreStore.getState().reset();
});

describe('resolveReferenceRefs', () => {
  it('an upload is minted into a durable generated-media url', async () => {
    expect(await resolveReferenceRefs(UPLOAD, SCOPE)).toEqual([
      { url: '/api/v1/generated-media/770000000000000001/file', kind: 'image' },
    ]);
    expect(importResourceAsCanvasMedia).toHaveBeenCalledWith(UPLOAD.id);
  });

  it('a generation is ALREADY durable and costs no round trip', async () => {
    expect(await resolveReferenceRefs(GENERATED, SCOPE)).toEqual([
      { url: '/api/v1/generated-media/800000000000000001/file', kind: 'image' },
    ]);
    expect(importResourceAsCanvasMedia).not.toHaveBeenCalled();
  });

  it('an asset expands to its primary-slot files, in the library sort order', async () => {
    expect(await resolveReferenceRefs(ASSET, SCOPE)).toEqual([
      { url: '/api/v1/resources/600000000000000001/cover', kind: 'image' },
      { url: '/api/v1/resources/600000000000000002/cover', kind: 'image' },
    ]);
  });

  it('a video generation is refused rather than added as a picture', async () => {
    await expect(
      resolveReferenceRefs({ ...GENERATED, kind: 'video' }, SCOPE),
    ).rejects.toMatchObject({ reason: 'not_an_image' });
  });

  it('an asset with no primary-slot file is refused, not added empty', async () => {
    fetchAssetDetail.mockResolvedValue({ ...ASSET_DETAIL, files: [] });
    await expect(resolveReferenceRefs(ASSET, SCOPE)).rejects.toMatchObject({
      reason: 'no_image_file',
    });
  });
});

describe('addReferences', () => {
  it('appends every resolved ref to the node, in pick order', async () => {
    seed();
    const result = await addReferences('p1', [GENERATED, ASSET], SCOPE);
    expect(result).toEqual({ added: 3, skipped: 0, failed: [] });
    expect(refs().map((r) => r.url)).toEqual([
      '/api/v1/generated-media/800000000000000001/file',
      '/api/v1/resources/600000000000000001/cover',
      '/api/v1/resources/600000000000000002/cover',
    ]);
  });

  it('a url already on the node is skipped, not duplicated', async () => {
    seed([{ url: '/api/v1/generated-media/800000000000000001/file', kind: 'image' }]);
    const result = await addReferences('p1', [GENERATED], SCOPE);
    expect(result).toEqual({ added: 0, skipped: 1, failed: [] });
    expect(refs()).toHaveLength(1);
  });

  it('one failure is REPORTED and the rest still land', async () => {
    seed();
    fetchAssetDetail.mockRejectedValue(new Error('403'));
    const result = await addReferences('p1', [ASSET, GENERATED], SCOPE);
    expect(result.added).toBe(1);
    expect(result.failed).toEqual([{ item: ASSET, reason: 'mint_failed' }]);
    expect(refs()).toHaveLength(1);
  });

  it('reads the LIVE node after each await, so a concurrent edit is not clobbered', async () => {
    seed();
    importResourceAsCanvasMedia.mockImplementation(async () => {
      // Something else writes to the node while the mint is in flight.
      useCanvasCoreStore.getState().patchNode('p1', {
        data: { manual_refs: [{ url: '/api/v1/resources/1/cover', kind: 'image' }] },
      });
      return { url: '/api/v1/generated-media/770000000000000001/file', kind: 'image' };
    });
    await addReferences('p1', [UPLOAD], SCOPE);
    expect(refs().map((r) => r.url)).toEqual([
      '/api/v1/resources/1/cover',
      '/api/v1/generated-media/770000000000000001/file',
    ]);
  });
});
```

- [ ] Run it. Expected failure: `Failed to resolve import "./addReferences"`.

```bash
cd frontend && npx vitest run "features/canvas-core/library/addReferences.test.ts"
```

- [ ] Implement `frontend/features/canvas-core/library/addReferences.ts`:

```ts
// features/canvas-core/library/addReferences.ts
//
// One library pick → one or more `manual_refs` entries on a prompt node.
//
// Lifted out of `PromptNodeView.addManualRef`, which could only ever handle a
// single resource id. The store-read pattern is preserved verbatim and it is
// load-bearing: the node is re-read from the store AFTER every await, because
// minting is a round trip and a closure's snapshot would drop whatever else
// wrote to the node meanwhile.
//
// Every failure is TYPED and returned. A pick that quietly adds nothing is the
// silent no-op this repo keeps re-learning — the caller renders `failed`.

import { getResourceCoverUrl } from '../../../services/resourceService';
import { fetchAssetDetail } from '../../../services/assetsService';
import { primarySlotFileIds } from '../smart/assetFiles';
import { importResourceAsCanvasMedia } from '../smart/mediaImport';
import type { GeneratedImageRef, PromptNodeData } from '../smart/types';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { LibraryItem } from './librarySearch';

export type AddReferenceFailure = 'mint_failed' | 'no_image_file' | 'not_an_image';

export interface AddReferencesResult {
  added: number;
  skipped: number;
  failed: Array<{ item: LibraryItem; reason: AddReferenceFailure }>;
}

class ReferenceError_ extends Error {
  reason: AddReferenceFailure;
  constructor(reason: AddReferenceFailure) {
    super(reason);
    this.name = 'ReferenceError';
    this.reason = reason;
  }
}

/**
 * The durable refs one library item contributes.
 *
 * An asset contributes SEVERAL — its primary-slot files — which is why this
 * answers a list rather than a single ref. `/api/v1/resources/{id}/cover` and
 * `/api/v1/generated-media/{id}/file` are the two url families the backend's
 * reference bridge resolves (`DURABLE_PREFIXES`); anything else is dropped
 * server-side and reported as `dropped_refs` after the run, which is far too
 * late to be useful.
 */
export async function resolveReferenceRefs(
  item: LibraryItem,
  scopeId: string,
): Promise<GeneratedImageRef[]> {
  if (item.store === 'generated') {
    if (item.kind === 'video') throw new ReferenceError_('not_an_image');
    return [{ url: `/api/v1/generated-media/${item.id}/file`, kind: 'image' }];
  }
  if (item.store === 'uploads') {
    if (item.kind !== 'image') throw new ReferenceError_('not_an_image');
    const minted = await importResourceAsCanvasMedia(item.id);
    return [{ url: minted.url, kind: minted.kind }];
  }
  // assets — the card's own rule for "which files does this asset reference",
  // reused rather than re-derived (assetFiles.ts owns that question).
  const detail = await fetchAssetDetail(scopeId, item.id);
  const ids = primarySlotFileIds(detail, null);
  if (ids.length === 0) throw new ReferenceError_('no_image_file');
  return ids.map((rid) => ({
    // The RELATIVE form: `manual_refs` is persisted into `nodes_json` and an
    // absolute url would bake this deployment's API host into the document.
    url: `/api/v1/resources/${rid}/cover`,
    kind: 'image' as const,
  }));
}

/** Cover url for display only — never written into `manual_refs`. */
export function referenceThumb(resourceId: string): string {
  return getResourceCoverUrl(resourceId);
}

export async function addReferences(
  nodeId: string,
  items: readonly LibraryItem[],
  scopeId: string,
): Promise<AddReferencesResult> {
  const out: AddReferencesResult = { added: 0, skipped: 0, failed: [] };
  for (const item of items) {
    let resolved: GeneratedImageRef[];
    try {
      resolved = await resolveReferenceRefs(item, scopeId);
    } catch (err) {
      console.error('[addReferences] could not resolve', item, err);
      out.failed.push({
        item,
        reason: err instanceof ReferenceError_ ? err.reason : 'mint_failed',
      });
      continue;
    }
    // The LIVE node, re-read after the await — a stale closure would drop any
    // other write that landed while the round trip was in flight.
    const store = useCanvasCoreStore.getState();
    const current =
      ((store.nodes.find((n) => (n as { id?: unknown }).id === nodeId) as
        | { data?: PromptNodeData }
        | undefined)?.data?.manual_refs ?? []) as GeneratedImageRef[];
    const have = new Set(current.map((r) => r.url));
    const fresh = resolved.filter((r) => !have.has(r.url));
    out.skipped += resolved.length - fresh.length;
    if (fresh.length === 0) continue;
    store.patchNode(nodeId, { data: { manual_refs: [...current, ...fresh] } });
    out.added += fresh.length;
  }
  return out;
}
```

- [ ] Run it. Expected: `Tests 9 passed`.

- [ ] Write the failing popover test. Create `frontend/features/canvas-core/library/LibraryReferencePopover.test.tsx`:

```tsx
// features/canvas-core/library/LibraryReferencePopover.test.tsx
//
// The replacement for the Add-reference popover. What the old one could not
// do, and what these cases pin: SEARCH (its query was hard-coded to ''),
// MULTI-SELECT, and saying out loud how many references the model will take.

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown) =>
      typeof d === 'string'
        ? d
        : typeof d === 'object' && d !== null && 'defaultValue' in (d as object)
          ? String((d as { defaultValue: string }).defaultValue)
              .replace(/\{\{count\}\}/g, String((d as { count?: number }).count ?? ''))
              .replace(/\{\{model\}\}/g, String((d as { model?: string }).model ?? ''))
              .replace(/\{\{used\}\}/g, String((d as { used?: number }).used ?? ''))
              .replace(/\{\{max\}\}/g, String((d as { max?: number }).max ?? ''))
          : k,
  }),
}));

const searchResources = vi.fn();
const searchAssets = vi.fn();
const fetchGenerated = vi.fn();
const listGenerationCapabilities = vi.fn();

vi.mock('../../../services/resourceSearchService', () => ({
  searchResources: (...a: unknown[]) => searchResources(...a),
}));
vi.mock('../../../services/assetsService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  searchAssets: (...a: unknown[]) => searchAssets(...a),
}));
vi.mock('../../../services/generatedService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  fetchGenerated: (...a: unknown[]) => fetchGenerated(...a),
}));
vi.mock('../services/canvasGenerationService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  listGenerationCapabilities: () => listGenerationCapabilities(),
}));

import { _resetModelCapabilitiesCache } from '../smart/nodes/useModelCapabilities';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { LibraryReferencePopover } from './LibraryReferencePopover';

const SCOPE = '727145299382534100';
const UPLOAD_ROW = {
  id: '655000000000000001',
  name: 'harbour-dusk.png',
  kind: 'image' as const,
  mime: 'image/png',
  size: 1,
  scope: { type: 'team' as const, id: SCOPE },
  updated_at: '2026-09-01T10:11:12Z',
  thumbnail_url: '/api/v1/resources/655000000000000001/cover',
  transcript_status: null,
  summary_status: null,
};

function seed(manualRefs: Array<{ url: string; kind: string }> = []): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '900000000000000001',
    nodes: [
      {
        id: 'p1',
        type: 'prompt',
        position: { x: 0, y: 0 },
        data: {
          body: '',
          provider_slug: '',
          agent_id: null,
          run_status: 'idle',
          resource_refs: [],
          manual_refs: manualRefs,
          gen: { kind: 'image', model: 'doubao-seedream', ratio: '1:1', count: 1 },
        },
      },
    ] as never,
    connections: [],
    selection: [],
  });
}

function renderPopover() {
  return render(
    <MemoryRouter initialEntries={[`/team/${SCOPE}/canvas/900000000000000001`]}>
      <Routes>
        <Route
          path="/team/:teamId/canvas/:canvasId"
          element={
            <LibraryReferencePopover nodeId="p1" model="doubao-seedream" onClose={() => {}} />
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  _resetModelCapabilitiesCache();
  seed();
  searchResources.mockReset().mockResolvedValue({
    results: [UPLOAD_ROW],
    counts: { all: 1, video: 0, image: 1, doc: 0, audio: 0, pdf: 0 },
    next_cursor: null,
  });
  searchAssets.mockReset().mockResolvedValue([]);
  fetchGenerated.mockReset().mockResolvedValue({ items: [], next_cursor: null });
  listGenerationCapabilities.mockReset().mockResolvedValue({
    'doubao-seedream': {
      ratios: ['1:1'],
      quality: false,
      resolution: false,
      max_refs: 3,
      negative: true,
      video_modes: [],
    },
  });
});
afterEach(() => {
  cleanup();
  useCanvasCoreStore.getState().reset();
});

describe('LibraryReferencePopover', () => {
  it('HAS a search box, and typing narrows the request', async () => {
    renderPopover();
    await waitFor(() => expect(searchResources).toHaveBeenCalled());
    fireEvent.change(screen.getByTestId('library-search'), {
      target: { value: 'harbour' },
    });
    await waitFor(() =>
      expect(searchResources).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: 'harbour' }),
      ),
    );
  });

  it('the header says which model receives them and how many it takes', async () => {
    renderPopover();
    await waitFor(() =>
      expect(screen.getByTestId('library-consequence').textContent).toContain(
        'sent to doubao-seedream',
      ),
    );
    expect(screen.getByTestId('library-consequence').textContent).toContain('0 / 3');
  });

  it('a full quota disables the button and says why', async () => {
    seed([
      { url: '/api/v1/generated-media/1/file', kind: 'image' },
      { url: '/api/v1/generated-media/2/file', kind: 'image' },
      { url: '/api/v1/generated-media/3/file', kind: 'image' },
    ]);
    renderPopover();
    await waitFor(() => expect(screen.getAllByTestId('library-cell').length).toBe(1));
    fireEvent.click(screen.getAllByTestId('library-cell')[0]);
    expect(screen.getByTestId('library-primary')).toBeDisabled();
    expect(screen.getByTestId('library-note').textContent).toContain('3 / 3');
  });

  it('with unknown capabilities it falls back to the IC ceiling, never to zero', async () => {
    listGenerationCapabilities.mockRejectedValue(new Error('down'));
    renderPopover();
    await waitFor(() =>
      expect(screen.getByTestId('library-consequence').textContent).toContain('0 / 20'),
    );
    await waitFor(() => expect(screen.getAllByTestId('library-cell').length).toBe(1));
    fireEvent.click(screen.getAllByTestId('library-cell')[0]);
    expect(screen.getByTestId('library-primary')).not.toBeDisabled();
  });
});
```

- [ ] Run it. Expected failure: `Failed to resolve import "./LibraryReferencePopover"`.

- [ ] Implement `frontend/features/canvas-core/library/LibraryReferencePopover.tsx`:

```tsx
// features/canvas-core/library/LibraryReferencePopover.tsx
//
// The Add-reference affordance on a prompt node, rebuilt on LibraryGrid.
//
// What it replaces could not search (its `query` was hard-coded to '' — there
// was no input box to type into), could not multi-select, and never said what
// the model would do with the pick. All three are the same defect: the picker
// answered "which file in my library?" without ever answering "and then what?".
//
// It portals to the body because it opens from INSIDE a React Flow node, and
// it renders three stores as three segments so a generation from yesterday is
// reachable without a detour through Save To Uploads.

import React, { useCallback, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';

import { useCanvasScope } from '../smart/canvasScope';
import { useModelCapabilities } from '../smart/nodes/useModelCapabilities';
import { MAX_REFERENCE_IMAGES } from '../smart/refOrder';
import type { GeneratedImageRef, PromptNodeData } from '../smart/types';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { addReferences } from './addReferences';
import { LibraryGrid } from './LibraryGrid';
import { useLibrarySearch, type LibraryItem, type LibraryStore } from './librarySearch';
import type { LibraryItemKey } from './librarySelection';

const SEGMENTS: readonly LibraryStore[] = ['uploads', 'generated', 'assets'];

export interface LibraryReferencePopoverProps {
  nodeId: string;
  model: string | null;
  onClose: () => void;
}

export function LibraryReferencePopover({
  nodeId,
  model,
  onClose,
}: LibraryReferencePopoverProps): React.ReactElement {
  const { t } = useTranslation();
  const { scopeId } = useCanvasScope();
  const canvasId = useCanvasCoreStore((s) => s.canvasId);
  const nodes = useCanvasCoreStore((s) => s.nodes);
  const caps = useModelCapabilities(model);

  const [store, setStore] = useState<LibraryStore>('uploads');
  const [query, setQuery] = useState('');
  const [selection, setSelection] = useState<LibraryItemKey[]>([]);
  const [busy, setBusy] = useState(false);
  const [failedNote, setFailedNote] = useState<string | null>(null);

  const used = useMemo(() => {
    const data = (nodes.find((n) => (n as { id?: unknown }).id === nodeId) as
      | { data?: PromptNodeData }
      | undefined)?.data;
    return ((data?.manual_refs ?? []) as GeneratedImageRef[]).length;
  }, [nodes, nodeId]);

  // `null` capabilities means UNKNOWN — still loading, fetch failed, or a model
  // absent from the map — and every consumer of that hook renders FULL support.
  // Falling back to 0 would disable the button on the day the endpoint hiccups.
  const max = caps?.max_refs ?? MAX_REFERENCE_IMAGES;
  const atLimit = used >= max;

  const result = useLibrarySearch(query, {
    scopeId,
    stores: SEGMENTS,
    canvasId,
    generatedScope: 'this-canvas',
    uploadKinds: 'image',
  });
  const current = result[store];

  const commit = useCallback(
    (items: LibraryItem[]) => {
      if (busy) return;
      setBusy(true);
      setFailedNote(null);
      void addReferences(nodeId, items, scopeId)
        .then((r) => {
          if (r.failed.length > 0) {
            // A pick that adds nothing must SAY so. Silent no-op 不可接受.
            setFailedNote(
              t('canvas.library.someFailed', {
                count: r.failed.length,
                defaultValue: '{{count}} could not be added as references',
              }),
            );
            return;
          }
          setSelection([]);
          onClose();
        })
        .finally(() => setBusy(false));
    },
    [busy, nodeId, scopeId, t, onClose],
  );

  return createPortal(
    <div
      data-testid="reference-picker"
      className="mh-pop-in fixed left-1/2 top-24 z-[60] flex h-[26rem] w-[30rem] max-w-[92vw] -translate-x-1/2 flex-col rounded-xl border border-canvas-line bg-canvas-card shadow-xl"
      onMouseDown={(e) => e.preventDefault()}
    >
      <div className="flex gap-1 border-b border-canvas-line p-1.5">
        {SEGMENTS.map((s) => (
          <button
            key={s}
            type="button"
            data-testid={`library-segment-${s}`}
            aria-pressed={store === s}
            onClick={() => setStore(s)}
            className={`nodrag rounded-full border px-2 py-0.5 text-[11px] ${
              store === s
                ? 'border-[var(--accent-border)] text-[var(--accent-text)]'
                : 'border-canvas-line text-canvas-muted hover:text-canvas-text'
            }`}
          >
            {t(`canvas.library.store.${s}`, s === 'uploads' ? 'Uploads' : s === 'generated' ? 'Generated' : 'Assets')}
          </button>
        ))}
      </div>
      <LibraryGrid
        className="min-h-0 flex-1"
        items={current.items}
        loading={current.loading}
        error={current.error}
        onRetry={current.reload}
        query={query}
        onQueryChange={setQuery}
        searchPlaceholder={t('canvas.library.searchPlaceholder', 'Search library…')}
        selection={selection}
        onSelectionChange={setSelection}
        consequence={t('canvas.library.referenceConsequence', {
          model: model || t('canvas.library.noModel', 'no model yet'),
          used,
          max,
          defaultValue: 'Reference images · sent to {{model}} · {{used}} / {{max}} used',
        })}
        note={
          failedNote ??
          (atLimit
            ? t('canvas.library.quotaFull', {
                used,
                max,
                defaultValue: '{{used}} / {{max}} references used · remove one on the node',
              })
            : null)
        }
        primaryAction={{
          label: t('canvas.library.addReferences', {
            count: selection.length,
            defaultValue: 'Add {{count}} References',
          }),
          disabled: atLimit || busy,
          onClick: commit,
        }}
        onItemActivate={(item) => commit([item])}
        emptyLabel={t('canvas.library.empty', 'Nothing here yet')}
        targetRowHeight={96}
      />
    </div>,
    document.body,
  );
}

export default LibraryReferencePopover;
```

- [ ] Add the new i18n keys to both locales: `searchPlaceholder`, `referenceConsequence`, `noModel`, `quotaFull`, `someFailed`, `addReferences_one` / `addReferences_other`, `empty`, `store.uploads`, `store.generated`, `store.assets`. Chinese values, e.g. `"referenceConsequence": "参考图 · 发送给 {{model}} · 已用 {{used}} / {{max}}"`, `"quotaFull": "参考图已用满 {{used}} / {{max}} · 请先在节点上移除一张"`.

- [ ] Wire `PromptNodeView`. Delete `ActiveKind` (`:61`), the `activeKind` state (`:163`), the `useResourceSearch` call (`:367`) and its import (`:53`), the whole `addManualRef` callback (`:298-317`), and the `CanvasMentionPicker` import (`:36`). Replace the mount block at `:806-823` with:

```tsx
        {refPickerOpen && (
          <LibraryReferencePopover
            nodeId={id}
            model={gen?.model ?? null}
            onClose={() => setRefPickerOpen(false)}
          />
        )}
```

and add one import: `import { LibraryReferencePopover } from '../../library/LibraryReferencePopover';`. Keep `refPickerOpen` (`:295`), the `add-reference` button (`:791-805`) and `removeManualRef` (`:318-329`) exactly as they are.

- [ ] Delete `frontend/features/canvas-core/smart/nodes/CanvasMentionPicker.tsx` and confirm nothing imports it:

```bash
cd frontend && grep -rn "CanvasMentionPicker" --include='*.ts' --include='*.tsx' . | grep -v node_modules
```

Expected: no output.

- [ ] Update `PromptNodeView.inputimages.test.tsx`. Remove the `vi.mock('../../../../hooks/useResourceSearch', …)` block and its import (`:26-29`), and replace the last case with:

```tsx
  it('Add reference key opens a picker that can be SEARCHED', async () => {
    seed(false);
    renderPrompt();
    fireEvent.click(screen.getByTestId('add-reference'));
    expect(screen.getByTestId('reference-picker')).toBeInTheDocument();
    // The whole point of the replacement: the old popover had no input at all.
    expect(screen.getByTestId('library-search')).toBeInTheDocument();
  });
```

Add the same service mocks this task's popover test uses (`searchResources`, `searchAssets`, `fetchGenerated`, `listGenerationCapabilities`) to that file's header, and wrap `renderPrompt` in a `MemoryRouter` route carrying `/team/:teamId` so `useCanvasScope` resolves.

- [ ] Run the whole affected set. Expected: `Tests N passed` with zero failures on each file.

```bash
cd frontend && npx vitest run "features/canvas-core/library" "features/canvas-core/smart/nodes/PromptNodeView.inputimages.test.tsx" "features/canvas-core/smart/nodes/PromptNodeView.mention.test.tsx" "features/canvas-core/smart/nodes/PromptNodeView.chips.test.tsx"
```

- [ ] Mutation-verify, restoring after each:
  1. In `LibraryReferencePopover`, change `const max = caps?.max_refs ?? MAX_REFERENCE_IMAGES` to `caps?.max_refs ?? 0`. Expected: `with unknown capabilities it falls back to the IC ceiling` fails — the button is disabled.
  2. In `addReferences`, hoist the `useCanvasCoreStore.getState()` read above the `for` loop. Expected: `reads the LIVE node after each await` fails, the concurrent write is gone.
  3. Delete the `if (r.failed.length > 0)` branch. Expected: `one failure is REPORTED` still passes (it tests the helper), but re-run the popover test after also making `fetchAssetDetail` reject — the note disappears. Record this as a coverage gap rather than adding a case: the branch IS pinned by the helper test's `failed` assertion.

- [ ] Confirm the TypeScript baseline has not grown, and confirm `PromptNodeView.tsx` shrank:

```bash
cd frontend && npx tsc --noEmit -p . 2>&1 | grep -c "error TS"
wc -l features/canvas-core/smart/nodes/PromptNodeView.tsx   # must be < 1085
```

- [ ] Commit:

```bash
git add frontend/features/canvas-core/library/addReferences.ts frontend/features/canvas-core/library/addReferences.test.ts frontend/features/canvas-core/library/LibraryReferencePopover.tsx frontend/features/canvas-core/library/LibraryReferencePopover.test.tsx frontend/features/canvas-core/smart/nodes/PromptNodeView.tsx frontend/features/canvas-core/smart/nodes/PromptNodeView.inputimages.test.tsx frontend/public/locales/en.json frontend/public/locales/zh.json
git rm frontend/features/canvas-core/smart/nodes/CanvasMentionPicker.tsx
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(canvas): 加参考图弹层换成 LibraryGrid —— 能搜索、能多选、写明发给哪个模型还剩几个额度"
```

---

### Task 5: `PromptMentionPicker` 加 Uploads / Generated 两组 + 后果首行 + `⇥` 切组

Spec §3.4（现状修正：#2097 后本期是**扩展** `PromptMentionPicker`）+ §4 验收 6.

**Files**
- Modify `frontend/features/canvas-core/smart/nodes/PromptMentionPicker.tsx` — `Tab` type at `:93`, the mount-time tab choice at `:105-107`, the assets effect at `:150-176`, `count` at `:186`, `commitActive` at `:216-226`, and the tab strip / list markup below `:226`.
- Modify `frontend/features/canvas-core/smart/nodes/PromptNodeView.tsx` — the `PromptMentionPicker` mount gains two callbacks. ≤ 8 lines.
- Test `frontend/features/canvas-core/smart/nodes/PromptMentionPicker.library.test.tsx` (new file — the existing `PromptNodeView.mention.test.tsx` stays untouched)
- Modify `frontend/public/locales/en.json` / `zh.json`

**Interfaces**

Consumes: `useLibrarySearch` (Task 2), `addReferences` (Task 4), and verbatim from facts §4d / §4f:

```ts
export interface PromptMentionPickerHandle {
  move: (delta: number) => void;
  commitActive: () => boolean;
}
export interface MentionInputImage { url: string; label: string }
export interface PromptBodyEditorHandle {
  insertImage: (image: PromptImageRef) => void;
  insertAsset: (asset: MentionedAsset) => void;
  insertText: (text: string) => void;
  focus: () => void;
}
```

Produces — `Tab` widens and two props are added:

```ts
type Tab = 'input' | 'assets' | 'uploads' | 'generated';
export const MENTION_TABS: readonly Tab[] = ['input', 'assets', 'uploads', 'generated'];

interface Props {
  scopeId: string;
  inputImages: MentionInputImage[];
  onPickImage: (image: MentionInputImage, index: number) => void;
  onPickAsset: (asset: AssetSummary) => void;
  /** A library picture: it becomes a REFERENCE on the node, not a body chip. */
  onPickLibraryImage: (item: LibraryItem) => void;
  query: string;
  /** Canvas id for the `Generated · this canvas` group. '' disables that tab. */
  canvasId: string | null;
}
```

What each group does when picked, and therefore what the consequence line has to say:

| Group | Pick writes | Where the user sees it |
|---|---|---|
| Input images | an `@Image N` chip + `source_ref` | body text |
| Assets | an `@[asset:id]` chip; files bundled at run | body text |
| Uploads | a `manual_refs` entry (via `addReferences`) | reference strip |
| Generated · this canvas | a `manual_refs` entry | reference strip |

The first line is therefore fixed copy: *"Mentioning an asset sends its reference files at run. Mentioning an image adds it as a reference. Plain text stays text."*

**Steps**

- [ ] Write the failing test. Create `frontend/features/canvas-core/smart/nodes/PromptMentionPicker.library.test.tsx`:

```tsx
// features/canvas-core/smart/nodes/PromptMentionPicker.library.test.tsx
//
// The `@` palette's two NEW groups. #2097 gave it Input images + Assets; the
// library panel work adds Uploads and Generated · this canvas, so the whole
// library is reachable from the caret without leaving the sentence.
//
// The consequence line is pinned by text, not by snapshot: it is the one thing
// on screen that distinguishes "this becomes a reference" from "this becomes
// a chip", and a snapshot refresh would let it silently change.

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: unknown) => (typeof d === 'string' ? d : k) }),
}));

const searchAssets = vi.fn();
const searchResources = vi.fn();
const fetchGenerated = vi.fn();

vi.mock('../../../../services/assetsService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  searchAssets: (...a: unknown[]) => searchAssets(...a),
}));
vi.mock('../../../../services/resourceSearchService', () => ({
  searchResources: (...a: unknown[]) => searchResources(...a),
}));
vi.mock('../../../../services/generatedService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  fetchGenerated: (...a: unknown[]) => fetchGenerated(...a),
}));

import { PromptMentionPicker } from './PromptMentionPicker';

const SCOPE = '727145299382534100';
const CANVAS = '900000000000000001';

const UPLOAD_ROW = {
  id: '655000000000000001',
  name: 'harbour-dusk.png',
  kind: 'image' as const,
  mime: 'image/png',
  size: 1,
  scope: { type: 'team' as const, id: SCOPE },
  updated_at: '2026-09-01T10:11:12Z',
  thumbnail_url: '/api/v1/resources/655000000000000001/cover',
  transcript_status: null,
  summary_status: null,
};
const GENERATED_ROW = {
  id: '800000000000000001',
  scope_id: SCOPE,
  media_kind: 'image',
  mime: 'image/png',
  prompt: 'harbour',
  model: 'doubao-seedream',
  provider: 'volcengine',
  origin_kind: 'canvas_run',
  canvas_id: CANVAS,
  node_id: 'node-7',
  created_at: '2026-09-02T12:00:00Z',
  promoted_resource_id: null,
  review_state: 'unreviewed' as const,
  source_asset_id: null,
  source: {
    kind: 'canvas_run',
    label: 'Canvas',
    canvas_id: CANVAS,
    node_id: 'node-7',
    shot_id: null,
    conversation_id: null,
    deep_link: null,
  },
  title: 'A wide shot of the harbour',
};

const onPickLibraryImage = vi.fn();

function renderPicker(inputImages: Array<{ url: string; label: string }> = []) {
  return render(
    <PromptMentionPicker
      scopeId={SCOPE}
      canvasId={CANVAS}
      inputImages={inputImages}
      onPickImage={vi.fn()}
      onPickAsset={vi.fn()}
      onPickLibraryImage={onPickLibraryImage}
      query=""
    />,
  );
}

beforeEach(() => {
  onPickLibraryImage.mockReset();
  searchAssets.mockReset().mockResolvedValue([]);
  searchResources.mockReset().mockResolvedValue({
    results: [UPLOAD_ROW],
    counts: { all: 1, video: 0, image: 1, doc: 0, audio: 0, pdf: 0 },
    next_cursor: null,
  });
  fetchGenerated.mockReset().mockResolvedValue({ items: [GENERATED_ROW], next_cursor: null });
});
afterEach(cleanup);

describe('PromptMentionPicker library groups', () => {
  it('states the three different consequences before anything is picked', () => {
    renderPicker();
    const line = screen.getByTestId('mention-consequence').textContent ?? '';
    expect(line).toContain('sends its reference files at run');
    expect(line).toContain('adds it as a reference');
    expect(line).toContain('Plain text stays text');
  });

  it('offers four groups, in a fixed order', () => {
    renderPicker();
    expect(screen.getAllByTestId(/^mention-tab-/).map((b) => b.getAttribute('data-testid'))).toEqual([
      'mention-tab-input',
      'mention-tab-assets',
      'mention-tab-uploads',
      'mention-tab-generated',
    ]);
  });

  it('the Uploads group lists library images', async () => {
    renderPicker();
    fireEvent.click(screen.getByTestId('mention-tab-uploads'));
    await waitFor(() => expect(screen.getAllByTestId('library-cell')).toHaveLength(1));
    expect(searchResources).toHaveBeenCalledWith(
      expect.objectContaining({ teamId: SCOPE }),
    );
  });

  it('the Generated group asks for THIS canvas, not the whole scope', async () => {
    renderPicker();
    fireEvent.click(screen.getByTestId('mention-tab-generated'));
    await waitFor(() => expect(fetchGenerated).toHaveBeenCalled());
    expect(fetchGenerated).toHaveBeenCalledWith(
      SCOPE,
      expect.objectContaining({ canvasId: CANVAS }),
    );
  });

  it('picking from a library group reports the ITEM, so the caller can add a reference', async () => {
    renderPicker();
    fireEvent.click(screen.getByTestId('mention-tab-uploads'));
    await waitFor(() => expect(screen.getAllByTestId('library-cell')).toHaveLength(1));
    fireEvent.doubleClick(screen.getAllByTestId('library-cell')[0]);
    expect(onPickLibraryImage).toHaveBeenCalledWith(
      expect.objectContaining({ store: 'uploads', id: '655000000000000001' }),
    );
  });

  it('Tab walks the groups and wraps', () => {
    renderPicker([{ url: '/api/v1/generated-media/1/file', label: 'Image 1' }]);
    const root = screen.getByTestId('prompt-mention-picker');
    expect(screen.getByTestId('mention-tab-input')).toHaveAttribute('aria-pressed', 'true');
    fireEvent.keyDown(root, { key: 'Tab' });
    expect(screen.getByTestId('mention-tab-assets')).toHaveAttribute('aria-pressed', 'true');
    fireEvent.keyDown(root, { key: 'Tab' });
    fireEvent.keyDown(root, { key: 'Tab' });
    expect(screen.getByTestId('mention-tab-generated')).toHaveAttribute('aria-pressed', 'true');
    fireEvent.keyDown(root, { key: 'Tab' });
    expect(screen.getByTestId('mention-tab-input')).toHaveAttribute('aria-pressed', 'true');
  });

  it('with no canvas id the Generated group says so instead of listing the scope', async () => {
    render(
      <PromptMentionPicker
        scopeId={SCOPE}
        canvasId={null}
        inputImages={[]}
        onPickImage={vi.fn()}
        onPickAsset={vi.fn()}
        onPickLibraryImage={onPickLibraryImage}
        query=""
      />,
    );
    fireEvent.click(screen.getByTestId('mention-tab-generated'));
    await waitFor(() => expect(screen.getByTestId('library-empty')).toBeInTheDocument());
    expect(fetchGenerated).not.toHaveBeenCalled();
  });
});
```

- [ ] Run it. Expected failure: `TS2322` / a runtime error on the unknown `onPickLibraryImage` prop, and `Unable to find an element by: [data-testid="mention-consequence"]`.

```bash
cd frontend && npx vitest run "features/canvas-core/smart/nodes/PromptMentionPicker.library.test.tsx"
```

- [ ] Implement in `PromptMentionPicker.tsx`:

1. Widen the tab type and export the order:

```tsx
type Tab = 'input' | 'assets' | 'uploads' | 'generated';

/** Group order, exported so `⇥` and the tab strip cannot disagree. */
export const MENTION_TABS: readonly Tab[] = ['input', 'assets', 'uploads', 'generated'];
```

2. Add the two props to `Props` with the doc comments from the Interfaces section above.

3. Add the library query beside the existing assets effect (the existing `searchAssets` effect stays exactly as it is — the Assets tab keeps its own `library:'all'` / `inLibraryOnly` toggle, which `useLibrarySearch` does not model):

```tsx
    // Uploads and Generated come from the shared index. Assets deliberately do
    // NOT: this picker has an in-library toggle the panel has no equivalent
    // for, and routing it through the shared hook would either lose the toggle
    // or push a picker-only knob into the shared hook.
    const library = useLibrarySearch(debounced, {
      scopeId,
      stores: ['uploads', 'generated'],
      uploadKinds: 'image',
      canvasId,
      generatedScope: 'this-canvas',
    });
```

4. Render the consequence line above the tab strip:

```tsx
      <p
        data-testid="mention-consequence"
        className="border-b border-canvas-line px-2 py-1 text-[10px] leading-snug text-canvas-muted"
      >
        {t(
          'canvas.library.mentionConsequence',
          'Mentioning an asset sends its reference files at run. Mentioning an image adds it as a reference. Plain text stays text.',
        )}
      </p>
```

5. Render the tab strip from `MENTION_TABS` with `data-testid={`mention-tab-${tab}`}` and `aria-pressed={tab === active}`; add `data-testid="prompt-mention-picker"` to the root if it is not there already, plus:

```tsx
      onKeyDown={(e) => {
        if (e.key !== 'Tab') return;
        e.preventDefault();
        setTab((cur) => MENTION_TABS[(MENTION_TABS.indexOf(cur) + 1) % MENTION_TABS.length]);
      }}
```

6. For the two new tabs, render `LibraryGrid` instead of the existing thumbnail grid:

```tsx
        {(tab === 'uploads' || tab === 'generated') && (
          <LibraryGrid
            testId="mention-library-grid"
            className="min-h-0 flex-1"
            items={tab === 'generated' && !canvasId ? [] : library[tab].items}
            loading={library[tab].loading}
            error={library[tab].error}
            onRetry={library[tab].reload}
            query={search}
            onQueryChange={setSearch}
            searchPlaceholder={t('canvas.library.searchPlaceholder', 'Search library…')}
            selection={[]}
            onSelectionChange={() => {}}
            consequence={t('canvas.library.mentionAddsReference', 'Picking one adds it as a reference on this node')}
            onItemActivate={(item) => pickRef.current.onPickLibraryImage(item)}
            emptyLabel={
              tab === 'generated' && !canvasId
                ? t('canvas.library.noCanvas', 'Open this canvas from a workspace to see its generations')
                : t('canvas.library.empty', 'Nothing here yet')
            }
            targetRowHeight={84}
          />
        )}
```

7. Extend `count` and `commitActive` so the keyboard still works on the new tabs:

```tsx
    const libraryRows = tab === 'uploads' || tab === 'generated'
      ? (tab === 'generated' && !canvasId ? [] : library[tab].items)
      : [];
    const count =
      tab === 'input' ? images.length : tab === 'assets' ? assets.length : libraryRows.length;
```

```tsx
    const commitActive = useCallback((): boolean => {
      if (tab === 'input') {
        const image = images[active];
        if (!image) return false;
        pickRef.current.onPickImage(image, inputImages.indexOf(image));
        return true;
      }
      if (tab === 'assets') {
        const asset = assets[active];
        if (!asset) return false;
        pickRef.current.onPickAsset(asset);
        return true;
      }
      const row = libraryRows[active];
      if (!row) return false;
      pickRef.current.onPickLibraryImage(row);
      return true;
    }, [tab, active, images, assets, libraryRows, inputImages]);
```

Add `onPickLibraryImage` to the `pickRef.current = { … }` assignment.

- [ ] Wire `PromptNodeView`'s mount. Add to the existing `<PromptMentionPicker …>`:

```tsx
            canvasId={canvasId}
            onPickLibraryImage={(item) => {
              mention.closePicker();
              void addReferences(id, [item], scopeId);
            }}
```

`canvasId` comes from `useCanvasCoreStore((s) => s.canvasId)` — add that selector if the file does not already read it — and `scopeId` from the existing `useCanvasScope()` call at the top of the component.

- [ ] Add the i18n keys `mentionConsequence`, `mentionAddsReference`, `noCanvas` to both locales.

- [ ] Run the picker's whole test surface. Expected: `Tests N passed` on each.

```bash
cd frontend && npx vitest run "features/canvas-core/smart/nodes/PromptMentionPicker.library.test.tsx" "features/canvas-core/smart/nodes/PromptNodeView.mention.test.tsx" "features/canvas-core/smart/nodes/PromptNodeView.mentionRefs.test.tsx" "features/canvas-core/smart/nodes/promptMentionI18n.test.ts" "features/canvas-core/library"
```

- [ ] Mutation-verify, restoring after each:
  1. Drop `canvasId` from the `useLibrarySearch` options. Expected: `the Generated group asks for THIS canvas` fails on the `objectContaining({ canvasId })` assertion.
  2. Change the `⇥` handler to `MENTION_TABS.indexOf(cur) + 1` without the modulo. Expected: `Tab walks the groups and wraps` fails on the fourth press.
  3. Delete the `mention-consequence` paragraph. Expected: `states the three different consequences` fails to find the element.

- [ ] Confirm the TypeScript baseline has not grown.

- [ ] Commit:

```bash
git add frontend/features/canvas-core/smart/nodes/PromptMentionPicker.tsx frontend/features/canvas-core/smart/nodes/PromptMentionPicker.library.test.tsx frontend/features/canvas-core/smart/nodes/PromptNodeView.tsx frontend/public/locales/en.json frontend/public/locales/zh.json
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(canvas): @ 调色板加 Uploads / Generated 两组 + 后果首行 + ⇥ 切组"
```

---

### Task 6: 四处「Library」改名

Spec §3.5 入口收敛表 + §4 验收 1.

**Files**
- Modify `frontend/features/canvas-core/smart/CanvasComposer.tsx` — `:665-667` (the bottom-bar button reading `Library`).
- Modify `frontend/features/canvas-core/smart/WorkflowLibraryPicker.tsx` — `:131` and `:136` (`Workflow Library` label and heading).
- Modify `frontend/features/canvas-core/smart/nodes/PromptNodeView.tsx` — `:570` (`aria-label="Load from library"`).
- Modify `frontend/features/canvas-core/smart/nodes/AttachedComposerPanel.tsx` — `:235` (`aria-label="Load from library"`).
- Modify `frontend/features/canvas-core/smart/nodes/AssetPromptPicker.tsx` — `:120` (`canvas.assetPromptPicker.title`, currently `Prompt Library`).
- Test `frontend/features/canvas-core/library/libraryNaming.test.ts` (new)
- Modify `frontend/public/locales/en.json` / `zh.json`

**Interfaces**

Consumes: `useTranslation()` from `react-i18next`, and the `canvas.library` namespace Task 3 created.

Produces: no new exported symbol. Two new i18n keys, and the guarantee the naming test pins:

```ts
// canvas.library.workflows        — the workflow library, which imports a whole
//                                   graph and is therefore NOT a media library
// canvas.library.promptTemplates  — the prompt-asset picker, on all three of
//                                   its mounts
```

The rename table, applied exactly:

| Where | Was | Becomes | Key |
|---|---|---|---|
| Composer bottom bar | `Library` | `Workflows` | `canvas.library.workflows` |
| Workflow picker heading + aria-label | `Workflow Library` | `Workflows` | `canvas.library.workflows` |
| Prompt node bookshelf button | `Load from library` | `Prompt Templates` | `canvas.library.promptTemplates` |
| Attached composer bookshelf button | `Load from library` | `Prompt Templates` | `canvas.library.promptTemplates` |
| `AssetPromptPicker` heading | `Prompt Library` | `Prompt Templates` | `canvas.library.promptTemplates` |

After this task the word **Library** appears in canvas-core UI text in exactly two places, both added later by Task 7: the top-bar chip and the panel heading. `WorkflowLibraryPicker` / `workflowLibrary.ts` keep their FILE names — renaming files is a refactor and belongs in its own PR (CLAUDE.md: refactor and feature must not share a branch).

**Steps**

- [ ] Write the failing test. Create `frontend/features/canvas-core/library/libraryNaming.test.ts`:

```ts
// features/canvas-core/library/libraryNaming.test.ts
//
// Spec §4 acceptance 1: on the canvas, "Library" names ONE thing.
//
// Four different libraries used to answer to that word, so a user could not
// tell from the label which one a button would open. This scans the canvas
// source for the literal in user-visible positions and refuses any occurrence
// outside the allowlist below.
//
// It scans SOURCE rather than a rendered tree on purpose: the strings sit in
// five components with five different mounting conditions, and a render test
// would need all five staged to notice a regression in any one.

import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');

/** Files allowed to put the word Library in front of a user. */
const ALLOWED = new Set([
  path.join(ROOT, 'library', 'LibraryPanel.tsx'),
  path.join(ROOT, 'ui', 'TopNodeBar.tsx'),
]);

function tsFiles(dir: string): string[] {
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .flatMap((e) =>
      e.isDirectory()
        ? tsFiles(path.join(dir, e.name))
        : /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name)
          ? [path.join(dir, e.name)]
          : [],
    );
}

/** Quoted strings and JSX text — where a USER would read it. Import
 *  specifiers, identifiers and comments are none of this test's business. */
const USER_TEXT = /(?:aria-label=|title=|placeholder=|>)\s*[{"']?\s*([^"'<>{}\n]*Library[^"'<>{}\n]*)/g;

describe('the word Library on the canvas', () => {
  it('found files at all — an empty scan would pass every case below', () => {
    expect(tsFiles(ROOT).length).toBeGreaterThan(40);
  });

  it('names exactly one thing: the panel and its chip', () => {
    const offenders: string[] = [];
    for (const file of tsFiles(ROOT)) {
      if (ALLOWED.has(file)) continue;
      const src = fs.readFileSync(file, 'utf8');
      for (const m of src.matchAll(USER_TEXT)) {
        offenders.push(`${path.relative(ROOT, file)}: ${m[1].trim()}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
```

- [ ] Run it. Expected failure: a non-empty `offenders` array naming `smart/CanvasComposer.tsx`, `smart/WorkflowLibraryPicker.tsx`, `smart/nodes/PromptNodeView.tsx`, `smart/nodes/AttachedComposerPanel.tsx` and `smart/nodes/AssetPromptPicker.tsx`.

```bash
cd frontend && npx vitest run "features/canvas-core/library/libraryNaming.test.ts"
```

- [ ] Apply the five renames from the table, each going through `t()`:

```tsx
// CanvasComposer.tsx :665
          <ComposerButton ref={libraryButtonRef} onClick={() => setLibraryOpen((v) => !v)}>
            {t('canvas.library.workflows', 'Workflows')}
          </ComposerButton>
```

```tsx
// WorkflowLibraryPicker.tsx :131 / :136
      aria-label={t('canvas.library.workflows', 'Workflows')}
      …
        <span className="mh-node-title">{t('canvas.library.workflows', 'Workflows')}</span>
```

```tsx
// PromptNodeView.tsx :570 and AttachedComposerPanel.tsx :235
          aria-label={t('canvas.library.promptTemplates', 'Prompt Templates')}
```

```tsx
// AssetPromptPicker.tsx :120
            {t('canvas.library.promptTemplates', 'Prompt Templates')}
```

`CanvasComposer` and `WorkflowLibraryPicker` may not import `useTranslation` yet — add it where missing. `AssetPromptPicker` already has `t`; the old `canvas.assetPromptPicker.title` key becomes unused, so delete it from BOTH locale files (`canvasAssetEntryI18n.test.ts` does not cover that namespace, so nothing else will notice).

- [ ] Add `workflows` and `promptTemplates` to `canvas.library` in both locales (`"workflows": "工作流"`, `"promptTemplates": "提示词模板"`).

- [ ] Run the naming test plus everything that renders those five components. Expected: all pass.

```bash
cd frontend && npx vitest run "features/canvas-core/library" "features/canvas-core/smart/CanvasComposer.test.tsx" "features/canvas-core/smart/nodes/AssetPromptPicker.test.tsx" "features/canvas-core/smart/nodes/AttachedComposerPanel.test.tsx"
```

If a test asserts the old literal (for example an `AttachedComposerPanel` case querying `Load from library`), update that assertion to the new label in the same commit — it is the rename, not a regression.

- [ ] Mutation-verify: put `aria-label="Open Library"` back on the prompt node's bookshelf button. Expected: the naming test fails, listing `smart/nodes/PromptNodeView.tsx: Open Library`. Restore.

- [ ] Confirm the TypeScript baseline has not grown.

- [ ] Commit:

```bash
git add frontend/features/canvas-core/smart/CanvasComposer.tsx frontend/features/canvas-core/smart/WorkflowLibraryPicker.tsx frontend/features/canvas-core/smart/nodes/PromptNodeView.tsx frontend/features/canvas-core/smart/nodes/AttachedComposerPanel.tsx frontend/features/canvas-core/smart/nodes/AssetPromptPicker.tsx frontend/features/canvas-core/library/libraryNaming.test.ts frontend/public/locales/en.json frontend/public/locales/zh.json
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "refactor(canvas): 四处「Library」各归各名（Workflows / Prompt Templates）—— 一个词只指一个东西"
```

---

### Task 7: 岛式面板 —— `libraryStore.ts` + `LibraryPanel.tsx` + 顶栏 `Library` 胶囊 + `L`

Spec §3.1（面板）+ §3.3（Media 页）+ §3.5（入口收敛）+ §3.6（状态）+ §3.7（目标节点被删）.

**Ruling this task carries out**: the node's Add-reference button stops opening `LibraryReferencePopover` and opens the PANEL in target mode instead, and `LibraryReferencePopover.tsx` is deleted. That is the staging working as intended, not churn: Task 4 had to ship a usable Add-reference affordance before the panel existed, and P2's whole point is that there is one Media surface, not two. `addReferences.ts` — the part that actually does the work — survives untouched.

**Files**
- Create `frontend/features/canvas-core/library/libraryStore.ts`
- Create `frontend/features/canvas-core/library/placeLibraryItems.ts`
- Create `frontend/features/canvas-core/library/LibraryPanel.tsx`
- Test `frontend/features/canvas-core/library/libraryStore.test.ts`
- Test `frontend/features/canvas-core/library/placeLibraryItems.test.ts`
- Test `frontend/features/canvas-core/library/LibraryPanel.test.tsx`
- Test `frontend/features/canvas-core/ui/useCanvasShortcuts.library.test.tsx`
- Modify `frontend/features/canvas-core/ui/TopNodeBar.tsx` — delete the `asset` chip (`:100-102`), the `project-assets` chip (`:103-106`), `insertProjectAssets` (`:169-223`), the `pick`/`bulk` branches of `addAtCenter` (`:225-238`), the `AssetPickerDialog` mount (`:265-272`) and its import; add one `library` chip. Net NEGATIVE.
- Modify `frontend/features/canvas-core/ui/useCanvasShortcuts.ts` — the doc-comment inventory (`:1-22`), one option, one binding beside the bare-`z` branch (`:234-242`).
- Modify `frontend/features/canvas-core/ui/canvasShortcuts.ts` — one entry in the `Tools` group (`:53-60`).
- Modify `frontend/features/canvas-core/ui/CanvasPage.tsx` — ≤ 6 lines: mount `<LibraryPanel />` beside `TopNodeBar` (`:824-839`) and pass `onToggleLibrary` to `useCanvasShortcuts`.
- Modify `frontend/features/canvas-core/smart/nodes/PromptNodeView.tsx` — the Add-reference button opens the panel; delete the popover mount. ≤ 8 lines, net NEGATIVE.
- Delete `frontend/features/canvas-core/library/LibraryReferencePopover.tsx` and `LibraryReferencePopover.test.tsx`
- Modify (test) `frontend/features/canvas-core/ui/TopNodeBar.test.tsx`, `frontend/features/canvas-core/canvasAssetEntryI18n.test.ts`
- Modify `frontend/public/locales/en.json` / `zh.json`

**Interfaces**

Consumes (verbatim, facts §2a / §2d / §5a / §8a):

```ts
export function createAssetNode(asset: AssetNodeSeed, opts?: AssetFactoryOptions): SmartNode<AssetNodeData>
export function createMediaNode(data: Partial<MediaNodeData>, opts?: FactoryOptions): SmartNode<MediaNodeData>
export function layoutAssetLanes<T extends { asset_type: AssetType }>(items: readonly T[], origin: Position): LaneSlot<T>[]
export function buildProjectAssetNodes(assets: readonly AssetRow[], existing: readonly CanvasNode[]): ProjectAssetInsert
export function screenToWorld(screenPoint: Position, viewport: CanvasViewport): Position
export function useCanvasScope(): { scopeId: string; resPath: (p: string) => string }
// store: setNodes / setSelection / patchNode / nodes / projectId / canvasId / viewport / readOnly
```

Produces:

```ts
// libraryStore.ts
export type LibraryPage = 'media' | 'prompts';
export interface LibraryTarget {
  nodeId: string;
  kind: 'prompt' | 'media';
  /** Display name at open time. The id is the authority. */
  title: string;
}
export interface LibraryPanelState {
  open: boolean;
  page: LibraryPage;
  mediaStore: LibraryStore;
  query: string;
  kind: string | null;
  assetScope: AssetScope;
  generatedScope: GeneratedScope;
  selection: LibraryItemKey[];
  target: LibraryTarget | null;
  width: number;
  /** Bumped whenever something asks the search box to take focus. */
  focusNonce: number;
  openPanel(opts?: { page?: LibraryPage; mediaStore?: LibraryStore; target?: LibraryTarget | null; focusSearch?: boolean }): void;
  close(): void;
  toggle(): void;
  setPage(page: LibraryPage): void;
  setMediaStore(store: LibraryStore): void;
  setQuery(q: string): void;
  setKind(kind: string | null): void;
  setAssetScope(scope: AssetScope): void;
  setGeneratedScope(scope: GeneratedScope): void;
  setSelection(keys: LibraryItemKey[]): void;
  setWidth(px: number): void;
  clearTarget(): void;
}
export const LIBRARY_STORAGE_KEY = 'canvas.library.v1';
export const useLibraryStore: UseBoundStore<StoreApi<LibraryPanelState>>;

// placeLibraryItems.ts
export interface PlaceResult {
  nodeIds: string[];
  inserted: number;
  /** Asset already on this board — placing it twice would feed one asset's
   *  references into the graph from two cards. */
  skipped: number;
  failed: Array<{ item: LibraryItem; reason: AddReferenceFailure }>;
}
export async function placeLibraryItems(
  items: readonly LibraryItem[],
  scopeId: string,
  position: { x: number; y: number } | null,
): Promise<PlaceResult>;

// LibraryPanel.tsx
export function LibraryPanel(): React.ReactElement | null;
```

Panel geometry, from spec §3.1: `top / right / bottom = 14px`, width 340px (Prompts page 600px), class `canvas-island`, `nodrag nowheel nopan`, `role="dialog"`. Below a 1100px viewport it becomes a bottom drawer at `45vh`. `page`, `mediaStore` and `width` persist to `localStorage['canvas.library.v1']`; `query`, `selection` and `target` do not — a stale target pointing at a node from a different canvas is worse than no target.

**Steps**

- [ ] Write the failing store test. Create `frontend/features/canvas-core/library/libraryStore.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { LIBRARY_STORAGE_KEY, useLibraryStore } from './libraryStore';

beforeEach(() => {
  localStorage.clear();
  useLibraryStore.setState(useLibraryStore.getInitialState(), true);
});
afterEach(() => localStorage.clear());

describe('libraryStore', () => {
  it('opens closed, on the Media page, with no target', () => {
    const s = useLibraryStore.getState();
    expect(s.open).toBe(false);
    expect(s.page).toBe('media');
    expect(s.target).toBeNull();
  });

  it('openPanel carries a page, a store and a target in one call', () => {
    useLibraryStore.getState().openPanel({
      page: 'media',
      mediaStore: 'generated',
      target: { nodeId: 'p1', kind: 'prompt', title: 'Harbour' },
    });
    const s = useLibraryStore.getState();
    expect(s.open).toBe(true);
    expect(s.mediaStore).toBe('generated');
    expect(s.target?.nodeId).toBe('p1');
  });

  it('toggle closes an open panel and clears the target with it', () => {
    const s = () => useLibraryStore.getState();
    s().openPanel({ target: { nodeId: 'p1', kind: 'prompt', title: 'Harbour' } });
    s().toggle();
    expect(s().open).toBe(false);
    // A target that outlives its panel would silently re-arm the next open.
    expect(s().target).toBeNull();
  });

  it('focusSearch bumps a nonce rather than holding a ref', () => {
    const before = useLibraryStore.getState().focusNonce;
    useLibraryStore.getState().openPanel({ focusSearch: true });
    expect(useLibraryStore.getState().focusNonce).toBe(before + 1);
  });

  it('persists page / store / width, and nothing else', () => {
    const s = useLibraryStore.getState();
    s.openPanel({ mediaStore: 'assets' });
    s.setWidth(420);
    s.setQuery('harbour');
    s.setSelection(['uploads:1']);
    const saved = JSON.parse(localStorage.getItem(LIBRARY_STORAGE_KEY) ?? '{}');
    expect(saved).toEqual({ page: 'media', mediaStore: 'assets', width: 420 });
  });

  it('a corrupt stored value is ignored, not thrown on', () => {
    localStorage.setItem(LIBRARY_STORAGE_KEY, '{not json');
    expect(() => useLibraryStore.getState().setWidth(360)).not.toThrow();
  });
});
```

- [ ] Run it. Expected failure: `Failed to resolve import "./libraryStore"`.

- [ ] Implement `frontend/features/canvas-core/library/libraryStore.ts`:

```ts
// features/canvas-core/library/libraryStore.ts
//
// Panel state, in its OWN zustand store rather than in `canvasCoreStore`.
//
// Two reasons, and the second is the load-bearing one. `canvasCoreStore` is
// 1087 lines and every write there is entangled with dirty/revision/history
// bookkeeping — but more importantly, this state is not part of the DOCUMENT.
// Which library segment is showing must never mark a canvas dirty, never enter
// the undo stack, and never reach `nodes_json`.
//
// Selection holds `${store}:${id}` KEYS, never copies of the rows: the rows are
// re-queried constantly and a copy would go stale the moment a search narrows.

import { create } from 'zustand';

import type { AssetScope, GeneratedScope, LibraryStore } from './librarySearch';
import type { LibraryItemKey } from './librarySelection';

export const LIBRARY_STORAGE_KEY = 'canvas.library.v1';

export type LibraryPage = 'media' | 'prompts';

export interface LibraryTarget {
  nodeId: string;
  kind: 'prompt' | 'media';
  title: string;
}

/** Only the three durable knobs. `query`, `selection` and `target` are
 *  deliberately NOT persisted: a restored target points at a node id from
 *  whatever canvas was open last time, which is worse than no target. */
interface Persisted {
  page: LibraryPage;
  mediaStore: LibraryStore;
  width: number;
}

const DEFAULTS: Persisted = { page: 'media', mediaStore: 'assets', width: 340 };

function readPersisted(): Persisted {
  try {
    const raw = localStorage.getItem(LIBRARY_STORAGE_KEY);
    if (!raw) return DEFAULTS;
    const parsed = JSON.parse(raw) as Partial<Persisted>;
    return {
      page: parsed.page === 'prompts' ? 'prompts' : 'media',
      mediaStore:
        parsed.mediaStore === 'uploads' || parsed.mediaStore === 'generated'
          ? parsed.mediaStore
          : 'assets',
      width: typeof parsed.width === 'number' && parsed.width > 0 ? parsed.width : DEFAULTS.width,
    };
  } catch (err) {
    // A corrupt value must degrade to the default, not white-screen the canvas.
    console.error('[libraryStore] could not read stored panel state:', err);
    return DEFAULTS;
  }
}

function writePersisted(p: Persisted): void {
  try {
    localStorage.setItem(LIBRARY_STORAGE_KEY, JSON.stringify(p));
  } catch (err) {
    console.error('[libraryStore] could not persist panel state:', err);
  }
}

export interface LibraryPanelState extends Persisted {
  open: boolean;
  query: string;
  kind: string | null;
  assetScope: AssetScope;
  generatedScope: GeneratedScope;
  selection: LibraryItemKey[];
  target: LibraryTarget | null;
  focusNonce: number;
  openPanel(opts?: {
    page?: LibraryPage;
    mediaStore?: LibraryStore;
    target?: LibraryTarget | null;
    focusSearch?: boolean;
  }): void;
  close(): void;
  toggle(): void;
  setPage(page: LibraryPage): void;
  setMediaStore(store: LibraryStore): void;
  setQuery(q: string): void;
  setKind(kind: string | null): void;
  setAssetScope(scope: AssetScope): void;
  setGeneratedScope(scope: GeneratedScope): void;
  setSelection(keys: LibraryItemKey[]): void;
  setWidth(px: number): void;
  clearTarget(): void;
}

export const useLibraryStore = create<LibraryPanelState>((set, get) => {
  const persist = () => {
    const { page, mediaStore, width } = get();
    writePersisted({ page, mediaStore, width });
  };
  return {
    ...readPersisted(),
    open: false,
    query: '',
    kind: null,
    assetScope: 'all',
    generatedScope: 'this-canvas',
    selection: [],
    target: null,
    focusNonce: 0,

    openPanel(opts = {}) {
      set((s) => ({
        open: true,
        page: opts.page ?? s.page,
        mediaStore: opts.mediaStore ?? s.mediaStore,
        target: opts.target !== undefined ? opts.target : s.target,
        selection: [],
        focusNonce: opts.focusSearch ? s.focusNonce + 1 : s.focusNonce,
      }));
      persist();
    },
    // Closing releases the target. A target that outlives its panel silently
    // re-arms the next open, so the user's next `L` would start adding
    // references to a node they have forgotten about.
    close() {
      set({ open: false, target: null, selection: [] });
    },
    toggle() {
      if (get().open) get().close();
      else get().openPanel();
    },
    setPage(page) {
      set({ page });
      persist();
    },
    setMediaStore(mediaStore) {
      set({ mediaStore, selection: [], kind: null });
      persist();
    },
    setQuery(query) {
      set({ query });
    },
    setKind(kind) {
      set({ kind });
    },
    setAssetScope(assetScope) {
      set({ assetScope, selection: [] });
    },
    setGeneratedScope(generatedScope) {
      set({ generatedScope, selection: [] });
    },
    setSelection(selection) {
      set({ selection });
    },
    setWidth(width) {
      set({ width });
      persist();
    },
    clearTarget() {
      set({ target: null });
    },
  };
});
```

- [ ] Run it. Expected: `Tests 6 passed`.

- [ ] Write the failing placement test. Create `frontend/features/canvas-core/library/placeLibraryItems.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const fetchAssetDetail = vi.fn();
const importResourceAsCanvasMedia = vi.fn();

vi.mock('../../../services/assetsService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  fetchAssetDetail: (...a: unknown[]) => fetchAssetDetail(...a),
}));
vi.mock('../smart/mediaImport', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  importResourceAsCanvasMedia: (...a: unknown[]) => importResourceAsCanvasMedia(...a),
}));

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { placeLibraryItems } from './placeLibraryItems';
import type { LibraryItem } from './librarySearch';

const SCOPE = '727145299382534100';
const ASSET_ID = '727145299382534300';

const ASSET: LibraryItem = {
  store: 'assets', id: ASSET_ID, title: 'Cole Bannon', thumbUrl: '', kind: 'character', ready: true,
};
const GENERATED: LibraryItem = {
  store: 'generated', id: '800000000000000001', title: 'A wide shot', thumbUrl: '', kind: 'image',
};
const UPLOAD: LibraryItem = {
  store: 'uploads', id: '655000000000000001', title: 'harbour.png', thumbUrl: '', kind: 'image',
};

const DETAIL = {
  id: ASSET_ID, scope_id: SCOPE, asset_type: 'character' as const, subtype: null,
  name: 'Cole Bannon', role_tag: 'lead', description: '', attrs: {},
  prompt_positive: null, prompt_negative: null, prompt_positive_zh: null, prompt_negative_zh: null,
  platform_params: {}, cover_file_id: '600000000000000001', source: 'manual',
  duplicated_from: null, is_system_preset: false, in_library: true, tags: {}, sort_order: 0,
  created_by: null, created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
  readiness: { state: 'ready' as const, missing: [] },
  file_counts_by_slot: { portrait: 1 }, project_ids: [], loadout_count: 0,
  files: [{
    asset_id: ASSET_ID, resource_id: '600000000000000001', slot: 'portrait',
    loadout_id: null, sort_order: 0, note: null, attached_by: null,
    attached_at: '2026-09-01T00:00:00Z',
  }],
  links: [], linked_by: [], loadouts: [],
};

function seed(nodes: unknown[] = []): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart', canvasId: '900000000000000001',
    nodes: nodes as never, connections: [], selection: [],
  });
}
const nodes = () => useCanvasCoreStore.getState().nodes as Array<{ id: string; type: string; data: Record<string, unknown> }>;

beforeEach(() => {
  fetchAssetDetail.mockReset().mockResolvedValue(DETAIL);
  importResourceAsCanvasMedia.mockReset().mockResolvedValue({
    url: '/api/v1/generated-media/770000000000000001/file', kind: 'image', id: '770000000000000001',
  });
  seed();
});
afterEach(() => useCanvasCoreStore.getState().reset());

describe('placeLibraryItems', () => {
  it('an asset becomes an asset card SEEDED FROM ITS DETAIL, not from the list row', async () => {
    const r = await placeLibraryItems([ASSET], SCOPE, { x: 100, y: 40 });
    expect(r.inserted).toBe(1);
    const card = nodes().find((n) => n.type === 'asset')!;
    expect(card.data.asset_id).toBe(ASSET_ID);
    // The list endpoint answers `file_counts_by_slot`, a tally — a card seeded
    // from it would reference nothing while looking like it worked.
    expect(card.data.selected_file_ids).toEqual(['600000000000000001']);
    expect(fetchAssetDetail).toHaveBeenCalledWith(SCOPE, ASSET_ID);
  });

  it('an asset already on the board is skipped, not duplicated', async () => {
    seed([{ id: 'a1', type: 'asset', position: { x: 0, y: 0 }, data: { asset_id: ASSET_ID, loadout_id: null, selected_file_ids: [], name: 'Cole Bannon', asset_type: 'character', cover_file_id: null, readiness_state: 'ready' } }]);
    const r = await placeLibraryItems([ASSET], SCOPE, { x: 0, y: 0 });
    expect(r).toMatchObject({ inserted: 0, skipped: 1 });
    expect(nodes().filter((n) => n.type === 'asset')).toHaveLength(1);
  });

  it('uploads and generations land in ONE media card holding durable urls', async () => {
    const r = await placeLibraryItems([UPLOAD, GENERATED], SCOPE, { x: 10, y: 20 });
    expect(r.inserted).toBe(2);
    const media = nodes().filter((n) => n.type === 'media');
    expect(media).toHaveLength(1);
    expect((media[0].data.items as Array<{ url: string }>).map((i) => i.url)).toEqual([
      '/api/v1/generated-media/770000000000000001/file',
      '/api/v1/generated-media/800000000000000001/file',
    ]);
  });

  it('mixed stores produce both an asset lane and a media card, in one write', async () => {
    const before = useCanvasCoreStore.getState().revision;
    await placeLibraryItems([ASSET, GENERATED], SCOPE, { x: 0, y: 0 });
    expect(nodes().map((n) => n.type).sort()).toEqual(['asset', 'media']);
    // One setNodes, not two — a half-placed board must never be observable.
    expect(useCanvasCoreStore.getState().revision).toBe(before + 1);
  });

  it('a failed item is reported and the rest still land', async () => {
    importResourceAsCanvasMedia.mockRejectedValue(new Error('boom'));
    const r = await placeLibraryItems([UPLOAD, GENERATED], SCOPE, { x: 0, y: 0 });
    expect(r.inserted).toBe(1);
    expect(r.failed).toEqual([{ item: UPLOAD, reason: 'mint_failed' }]);
  });

  it('with no drop point the assets fall into project lanes instead', async () => {
    await placeLibraryItems([ASSET], SCOPE, null);
    expect(nodes().find((n) => n.type === 'asset')).toBeDefined();
  });

  it('the placed nodes end up selected', async () => {
    await placeLibraryItems([GENERATED], SCOPE, { x: 0, y: 0 });
    expect(useCanvasCoreStore.getState().selection).toEqual([nodes()[0].id]);
  });
});
```

- [ ] Run it. Expected failure: `Failed to resolve import "./placeLibraryItems"`.

- [ ] Implement `frontend/features/canvas-core/library/placeLibraryItems.ts`:

```ts
// features/canvas-core/library/placeLibraryItems.ts
//
// Library rows → canvas nodes. One function behind the panel's "Place on
// canvas" button AND the empty-pane drop (Task 8), because they are the same
// operation asked two ways.
//
// EVERY outcome says something. Placed nothing because it was all already
// here, placed nothing because the mint failed, and placed nothing because
// there was nothing selected are three different answers; a caller that gets
// one number cannot tell them apart, which is the silent no-op this repo keeps
// re-learning.

import { fetchAssetDetail, type AssetRow } from '../../../services/assetsService';
import { layoutAssetLanes, buildProjectAssetNodes } from '../smart/assetPlacement';
import { createAssetNode, createMediaNode } from '../smart/factories';
import type { AssetNodeSeed } from '../smart/assetFiles';
import { SMART_NODE_DEFAULT_WIDTH } from '../smart/types';
import type { CanvasNode } from '../types';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { resolveReferenceRefs, type AddReferenceFailure } from './addReferences';
import type { LibraryItem } from './librarySearch';

export interface PlaceResult {
  nodeIds: string[];
  inserted: number;
  skipped: number;
  failed: Array<{ item: LibraryItem; reason: AddReferenceFailure }>;
}

/** Asset ids already referenced by a card here. Placing one twice would feed
 *  the same asset's references into the graph from two places. */
function assetIdsOnBoard(nodes: readonly CanvasNode[]): Set<string> {
  const out = new Set<string>();
  for (const n of nodes) {
    const node = n as { type?: string; data?: { asset_id?: unknown } };
    if (node.type === 'asset' && typeof node.data?.asset_id === 'string') {
      out.add(node.data.asset_id);
    }
  }
  return out;
}

export async function placeLibraryItems(
  items: readonly LibraryItem[],
  scopeId: string,
  position: { x: number; y: number } | null,
): Promise<PlaceResult> {
  const out: PlaceResult = { nodeIds: [], inserted: 0, skipped: 0, failed: [] };
  if (items.length === 0) return out;

  const present = assetIdsOnBoard(useCanvasCoreStore.getState().nodes);
  const assetItems = items.filter((i) => i.store === 'assets');
  const mediaItems = items.filter((i) => i.store !== 'assets');

  // ── Assets: the DETAIL row, never the list row. `file_counts_by_slot` is a
  // tally, so a card seeded from a summary references nothing.
  const details: AssetRow[] = [];
  for (const item of assetItems) {
    if (present.has(item.id)) {
      out.skipped += 1;
      continue;
    }
    try {
      details.push(await fetchAssetDetail(scopeId, item.id));
    } catch (err) {
      console.error('[placeLibraryItems] asset detail failed:', err);
      out.failed.push({ item, reason: 'mint_failed' });
    }
  }

  // ── Uploads / generations: durable urls, minted the same way a reference is.
  const refs: Array<{ url: string; kind: 'image' | 'video' }> = [];
  for (const item of mediaItems) {
    try {
      const resolved = await resolveReferenceRefs(item, scopeId);
      for (const r of resolved) refs.push({ url: r.url, kind: r.kind === 'video' ? 'video' : 'image' });
    } catch (err) {
      out.failed.push({
        item,
        reason: (err as { reason?: AddReferenceFailure }).reason ?? 'mint_failed',
      });
    }
  }

  const store = useCanvasCoreStore.getState();
  // The LIVE list, re-read after the awaits above.
  const existing = store.nodes;
  const fresh: CanvasNode[] = [];

  if (details.length > 0) {
    const slots = position
      ? layoutAssetLanes(details, position)
      : null;
    if (slots) {
      for (const slot of slots) {
        fresh.push(createAssetNode(slot.item as AssetNodeSeed, { position: slot.position }) as CanvasNode);
      }
    } else {
      // No drop point: this is the Project Assets gesture, whose layout rule
      // (lanes starting at the next free position) already exists.
      fresh.push(...buildProjectAssetNodes(details, existing).nodes);
    }
    out.inserted += details.length;
  }

  if (refs.length > 0) {
    const width = SMART_NODE_DEFAULT_WIDTH.media;
    const origin = position ?? { x: 0, y: 0 };
    fresh.push(
      createMediaNode(
        {
          // The upload card's own naming rule: plural images are a Group.
          title: refs.length > 1 ? 'Group' : refs[0].kind === 'video' ? 'Video' : 'Image',
          items: refs,
        },
        {
          position: {
            x: Math.round(origin.x - width / 2),
            y: Math.round(origin.y - 60),
          },
        },
      ) as CanvasNode,
    );
    out.inserted += refs.length;
  }

  if (fresh.length === 0) return out;

  // ONE write. Two would make a half-placed board observable, and each would
  // push its own history entry for what the user did once.
  store.setNodes([...existing, ...fresh]);
  out.nodeIds = fresh.map((n) => String((n as { id?: unknown }).id));
  store.setSelection(out.nodeIds);
  return out;
}
```

- [ ] Run it. Expected: `Tests 7 passed`.

- [ ] Write the failing panel test. Create `frontend/features/canvas-core/library/LibraryPanel.test.tsx`. It needs one local helper, used by the excerpts below:

```tsx
function seedNodes(nodes: unknown[]): void {
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '900000000000000001',
    nodes: nodes as never,
    connections: [],
    selection: [],
  });
}
```

The cases cover: the panel is absent until opened; `role="dialog"` with the `canvas-island` class; the three Media segments; the target bar naming the node; ✕ closes and releases the target; Esc closes; a keydown of `l` inside the panel does NOT reach `window` while `Escape` does; the target node being deleted releases the target; Place on canvas calls `placeLibraryItems`; the Prompts page renders the P3 line. Mock `react-i18next`, the three services and `placeLibraryItems`; stub `getBoundingClientRect` exactly as `LibraryGrid.test.tsx` does; drive the store with `useLibraryStore.getState().openPanel(...)` inside `act`.

```tsx
  it('a keystroke inside the panel does not reach the canvas, but Escape does', () => {
    const onWindow = vi.fn();
    window.addEventListener('keydown', onWindow);
    act(() => { useLibraryStore.getState().openPanel(); });
    render(<LibraryPanel />);
    fireEvent.keyDown(screen.getByTestId('library-panel'), { key: 'l' });
    expect(onWindow).not.toHaveBeenCalled();
    fireEvent.keyDown(screen.getByTestId('library-panel'), { key: 'Escape' });
    expect(onWindow).toHaveBeenCalled();
    window.removeEventListener('keydown', onWindow);
  });

  it('the target releases itself when its node is deleted', async () => {
    act(() => {
      useLibraryStore.getState().openPanel({
        target: { nodeId: 'p1', kind: 'prompt', title: 'Harbour' },
      });
    });
    seedNodes([{ id: 'p1', type: 'prompt', position: { x: 0, y: 0 }, data: {} }]);
    render(<LibraryPanel />);
    expect(screen.getByTestId('library-target').textContent).toContain('Harbour');
    act(() => { seedNodes([]); });
    await waitFor(() => expect(screen.queryByTestId('library-target')).toBeNull());
  });
```

- [ ] Implement `frontend/features/canvas-core/library/LibraryPanel.tsx`. Shape:

```tsx
// features/canvas-core/library/LibraryPanel.tsx
//
// The island panel. It floats over the canvas rather than squeezing it: the
// board under it keeps its size and keeps panning, which is what makes
// "drag a picture from the panel onto a node" a single gesture.
//
// ⚠️ `useCanvasShortcuts` decides "am I typing?" from the EVENT TARGET
// (INPUT / TEXTAREA / SELECT / contentEditable). The search box is covered by
// that; the panel's own chrome is not — so `L` pressed with the panel focused
// but no field active would reach the canvas and toggle the panel shut under
// the user's hand. The root therefore stops keydown propagation, and lets
// Escape through on purpose: Escape means "get me out of here" at every level.

import React, { useCallback, useEffect, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';

import { useOptionalToast } from '../../../components/Toast';
import { useCanvasScope } from '../smart/canvasScope';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { addReferences } from './addReferences';
import { LibraryGrid } from './LibraryGrid';
import { useLibrarySearch, type LibraryStore } from './librarySearch';
import { selectedItems } from './librarySelection';
import { useLibraryStore } from './libraryStore';
import { placeLibraryItems } from './placeLibraryItems';

const SEGMENTS: readonly LibraryStore[] = ['assets', 'uploads', 'generated'];
const DRAWER_BREAKPOINT = 1100;
```

Body requirements, each already specified above:
1. Return `null` when `!open`.
2. Root: `data-testid="library-panel" role="dialog" aria-label={t('canvas.library.title','Library')}`, classes `canvas-island nodrag nowheel nopan pointer-events-auto absolute z-30 flex flex-col`, inline style `{ top: 14, right: 14, bottom: 14, width }` above the breakpoint and `{ left: 14, right: 14, bottom: 14, height: '45vh' }` below it (read the breakpoint with a `matchMedia` effect; treat a missing `matchMedia` as desktop).
3. `onKeyDown={(e) => { if (e.key !== 'Escape') e.stopPropagation(); }}` and a separate `if (e.key === 'Escape') close()`.
4. Header: page tabs `Media` / `Prompts`, then `✕` calling `close()`.
5. Target bar, rendered only when `target` is set: `data-testid="library-target"`, `bg-ok-soft text-ok`, text `t('canvas.library.targetPrompt', { title, defaultValue: 'Adding references to {{title}}' })`, and an `✕` calling `clearTarget()`. An effect watches `nodes`; when `target` no longer resolves, call `clearTarget()` and `toast?.addToast(t('canvas.library.targetGone','Target node was removed'),'info')`.
6. Media page: the three segment chips, then one `LibraryGrid` fed `result[mediaStore]`. Kind chips per store — assets: Character / Location / Prop / Costume / Audio (NOT Prompt, which belongs to the Prompts page); uploads: Image / Video / Audio / Doc; generated: none. Scope chips: assets get `This Project` / `All Library`, generated get `This Canvas` / `Today` / `All`. `consequence` is `t('canvas.library.targetConsequence', …)` when there is a target and `t('canvas.library.browseConsequence','Place on canvas, or drag onto a node')` when there is not.
7. Footer actions: `Place on Canvas` (always) calling `placeLibraryItems(chosen, scopeId, null)`, and `Add as References` (only with a prompt target) calling `addReferences(target.nodeId, chosen, scopeId)`. Both report `failed.length > 0` through a toast; both clear the selection on success.
8. Prompts page: one line, `data-testid="library-prompts-stub"`, `t('canvas.library.promptsLater','Prompt templates arrive with P3')`.
9. A search-focus effect keyed on `focusNonce` that focuses the grid's input via `document.querySelector` scoped to the panel ref.

- [ ] Mount it. In `CanvasPage.tsx`, beside the existing `TopNodeBar` block:

```tsx
      {isSmartFamily(kind) && !readOnly && <LibraryPanel />}
```

and pass the toggle to the shortcut hook (the hook call already exists in this file):

```tsx
    onToggleLibrary: () => useLibraryStore.getState().toggle(),
```

- [ ] Add the `L` binding. In `useCanvasShortcuts.ts` add to the doc-comment inventory:

```
 *   L                         → open / close the Library panel
```

add the option beside `onOpenHelp`:

```ts
  /** Called on `L` outside an editable element — opens or closes the Library
   *  panel. View-only, so it stays live in a read-only session. */
  onToggleLibrary?: () => void;
```

mirror it into the existing `onOpenHelpRef` pattern with an `onToggleLibraryRef`, and add the binding beside the bare-`z` branch:

```ts
      if ((key === 'l' || key === 'L') && !meta && !event.shiftKey) {
        // Bare key, like `z` and `x`. View-only — a read-only session can open
        // the library, it just cannot place anything from it.
        event.preventDefault();
        onToggleLibraryRef.current?.();
        return;
      }
```

- [ ] Add the help-table entry in `canvasShortcuts.ts`, in the `Tools` group, before `?`:

```ts
      { keys: ['L'], label: 'Library panel' },
```

- [ ] Write the failing shortcut test. Create `frontend/features/canvas-core/ui/useCanvasShortcuts.library.test.tsx`:

```tsx
// The `L` binding, and the fact that the help panel knows about it.
//
// These two live in different files with NO mechanism keeping them in step:
// `useCanvasShortcuts` is an if/else chain and `canvasShortcuts.ts` is a table
// the `?` panel reads. Nothing enforces they agree, so this file asserts both
// halves of the same fact — which is the whole guard there is.

import { fireEvent, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { CANVAS_SHORTCUT_GROUPS } from './canvasShortcuts';
import { useCanvasShortcuts } from './useCanvasShortcuts';

describe('L opens the library', () => {
  it('fires the callback on a bare l', () => {
    const onToggleLibrary = vi.fn();
    renderHook(() => useCanvasShortcuts({ onToggleLibrary }));
    fireEvent.keyDown(window, { key: 'l' });
    expect(onToggleLibrary).toHaveBeenCalledTimes(1);
  });

  it('does not fire while the user is typing', () => {
    const onToggleLibrary = vi.fn();
    renderHook(() => useCanvasShortcuts({ onToggleLibrary }));
    const input = document.createElement('input');
    document.body.appendChild(input);
    fireEvent.keyDown(input, { key: 'l' });
    expect(onToggleLibrary).not.toHaveBeenCalled();
    input.remove();
  });

  it('still fires in a read-only session — opening a library changes nothing', () => {
    const onToggleLibrary = vi.fn();
    renderHook(() => useCanvasShortcuts({ onToggleLibrary, readOnly: true }));
    fireEvent.keyDown(window, { key: 'L' });
    expect(onToggleLibrary).toHaveBeenCalled();
  });

  it('the help panel advertises it — the two files have no other link', () => {
    const entries = CANVAS_SHORTCUT_GROUPS.flatMap((g) => g.entries);
    expect(entries.some((e) => e.keys.length === 1 && e.keys[0] === 'L')).toBe(true);
  });
});
```

- [ ] Merge the top bar's two chips. In `TopNodeBar.tsx` replace the `asset` and `project-assets` entries with one:

```tsx
  // ONE Library chip where Asset and Project Assets used to be. Both did the
  // same thing at different granularities — pick one asset, or take the whole
  // project shelf — and the panel expresses that as a scope plus a selection,
  // which is a knob rather than a second button.
  { key: 'library', label: 'Library', icon: Library, panel: true },
```

Add `panel?: true` to the `Chip` interface, drop `pick` and `bulk`, delete `insertProjectAssets`, `pickerOpen`, `inserting`, the `AssetPickerDialog` mount and its import, plus the now-unused `listProjectAssets` / `buildProjectAssetNodes` / `createAssetNode` / `useOptionalToast` imports. `addAtCenter` becomes:

```tsx
      if (chip.panel) {
        useLibraryStore.getState().openPanel({ page: 'media', mediaStore: 'assets' });
        return;
      }
```

- [ ] Update `TopNodeBar.test.tsx`: delete the asset-picker cases and the two `assetsService` mocks they need, and add one asserting the chip opens the panel:

```tsx
  it('the Library chip opens the panel instead of a modal', () => {
    render(<TopNodeBar surfaceRef={surfaceRef} />);
    fireEvent.click(screen.getByTestId('top-node-chip-library'));
    expect(useLibraryStore.getState().open).toBe(true);
    expect(useLibraryStore.getState().mediaStore).toBe('assets');
  });
```

- [ ] Update `canvasAssetEntryI18n.test.ts`: `NODE_BAR_CHIP_KEYS` now ends `…, 'library'`, so delete `canvas.nodeBar.asset` and `canvas.nodeBar.project-assets` from BOTH locales and add `canvas.nodeBar.library` (`"素材库"` in zh). That test's `no locale carries a chip label the bar does not render` case demands exact equality, so this must happen in the same commit.

- [ ] Rewire the prompt node's Add-reference button and delete the popover. In `PromptNodeView.tsx` replace the `refPickerOpen` state and the popover mount with:

```tsx
              onClick={() =>
                useLibraryStore.getState().openPanel({
                  page: 'media',
                  mediaStore: 'uploads',
                  focusSearch: true,
                  // The node has no title field — its heading is the literal
                  // "Prompt". The first line of the body is what a user would
                  // call this card, so the target bar names that instead.
                  target: {
                    nodeId: id,
                    kind: 'prompt',
                    title: (body ?? '').split('\n')[0].slice(0, 40) || 'Prompt',
                  },
                })
              }
```

Delete `LibraryReferencePopover.tsx` and `LibraryReferencePopover.test.tsx`, and update the `PromptNodeView.inputimages.test.tsx` case from Task 4 to assert the store instead:

```tsx
  it('Add reference opens the Library panel targeting this node', () => {
    seed(false);
    renderPrompt();
    fireEvent.click(screen.getByTestId('add-reference'));
    expect(useLibraryStore.getState().open).toBe(true);
    expect(useLibraryStore.getState().target).toMatchObject({ nodeId: 'p1', kind: 'prompt' });
  });
```

- [ ] Add every new i18n key to both locales: `title`, `pageMedia`, `pagePrompts`, `targetPrompt`, `targetGone`, `targetConsequence`, `browseConsequence`, `place`, `addAsReferences`, `promptsLater`, `scopeThisProject`, `scopeAllLibrary`, `scopeThisCanvas`, `scopeToday`, `scopeAll`, `selectAll`, `placedSome`, plus `canvas.nodeBar.library`.

- [ ] Run everything this task touched. Expected: `Tests N passed` on every file.

```bash
cd frontend && npx vitest run "features/canvas-core/library" "features/canvas-core/ui/useCanvasShortcuts.library.test.tsx" "features/canvas-core/ui/TopNodeBar.test.tsx" "features/canvas-core/canvasAssetEntryI18n.test.ts" "features/canvas-core/smart/nodes/PromptNodeView.inputimages.test.tsx"
```

- [ ] Mutation-verify, restoring after each:
  1. Remove the `if (e.key !== 'Escape') e.stopPropagation()` from the panel root. Expected: `a keystroke inside the panel does not reach the canvas` fails — `onWindow` is called for `l`.
  2. Make `close()` keep the target (`set({ open: false })`). Expected: the store's `toggle closes an open panel and clears the target with it` fails.
  3. Delete the `{ keys: ['L'], label: 'Library panel' }` entry. Expected: `the help panel advertises it` fails.

- [ ] Confirm the TypeScript baseline has not grown, and confirm `TopNodeBar.tsx` shrank below 277 lines.

- [ ] Commit:

```bash
git add frontend/features/canvas-core/library frontend/features/canvas-core/ui/TopNodeBar.tsx frontend/features/canvas-core/ui/TopNodeBar.test.tsx frontend/features/canvas-core/ui/useCanvasShortcuts.ts frontend/features/canvas-core/ui/useCanvasShortcuts.library.test.tsx frontend/features/canvas-core/ui/canvasShortcuts.ts frontend/features/canvas-core/ui/CanvasPage.tsx frontend/features/canvas-core/smart/nodes/PromptNodeView.tsx frontend/features/canvas-core/smart/nodes/PromptNodeView.inputimages.test.tsx frontend/features/canvas-core/canvasAssetEntryI18n.test.ts frontend/public/locales/en.json frontend/public/locales/zh.json
git rm frontend/features/canvas-core/library/LibraryReferencePopover.tsx frontend/features/canvas-core/library/LibraryReferencePopover.test.tsx
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(canvas): Library 岛式面板 —— Media 页 + 目标模式 + 顶栏胶囊 + L；Asset/Project Assets 两个胶囊并成一个"
```

---

### Task 8: 拖放 —— `application/x-nous-library`

Spec §3.3 拖放三条落点 + §3.7 第三条（不接受的落点）+ §4 验收 3 / 4.

**Files**
- Create `frontend/features/canvas-core/library/dropLibraryItems.ts`
- Test `frontend/features/canvas-core/library/dropLibraryItems.test.ts`
- Test `frontend/features/canvas-core/ui/CanvasSurface.libraryDrop.test.tsx`
- Modify `frontend/features/canvas-core/library/LibraryCell.tsx` — the `onDragStart` already declared in Task 3 gains its payload writer, 6 lines.
- Modify `frontend/features/canvas-core/library/LibraryGrid.tsx` — pass `onItemDragStart` through (already in the props, Task 3), 1 line.
- Modify `frontend/features/canvas-core/library/LibraryPanel.tsx` — supply `onItemDragStart`, 8 lines.
- Modify `frontend/canvas-kit/CanvasEngine.tsx` — the two guards at `:796-839` and the binding at `:918-926`.
- Modify `frontend/features/canvas-core/ui/CanvasSurface.tsx` — ≤ 15 lines: one `onLibraryDrop` callback delegating to `dropLibraryItems`.
- Modify `frontend/features/canvas-core/smart/nodes/PromptNodeView.tsx` — node-level drop target, ≤ 12 lines.
- Modify `frontend/features/canvas-core/smart/nodes/MediaNodeView.tsx` — extend the existing handlers at `:165-179`, ≤ 10 lines.
- Modify `frontend/public/locales/en.json` / `zh.json`

**Interfaces**

Consumes (verbatim, facts §3):

```tsx
onFileDrop?: (files: File[], flowPosition: { x: number; y: number }) => void;
onUrlDrop?: (dataTransfer: DataTransfer, flowPosition: { x: number; y: number }) => void;
```

`CanvasEngine`'s two guards are why a new MIME cannot just be read: `onContainerDragOver` only calls `preventDefault()` for known types (so the browser refuses the drop outright), and `onDrop` is bound at all only when `onFileDrop` is truthy. Both must learn the new type.

Produces:

```ts
// dropLibraryItems.ts
export const LIBRARY_DND_MIME = 'application/x-nous-library';

/** The wire form. Deliberately a JSON string in one MIME slot: dataTransfer
 *  carries strings, and one slot keeps the reader from having to reassemble
 *  a multi-item drag out of several keys. */
export interface LibraryDragPayload {
  items: Array<{ store: LibraryStore; id: string; kind: string; title: string }>;
}

export function writeLibraryDrag(dt: DataTransfer, items: readonly LibraryItem[]): void;
export function readLibraryDrag(dt: DataTransfer): LibraryItem[] | null;
export function hasLibraryDrag(dt: { types: readonly string[] | DOMStringList }): boolean;

export type LibraryDropTarget =
  | { kind: 'canvas'; position: { x: number; y: number } }
  | { kind: 'prompt'; nodeId: string; mention: boolean }
  | { kind: 'media'; nodeId: string };

export interface LibraryDropOutcome {
  handled: boolean;
  placed: number;
  referenced: number;
  mentioned: number;
  failed: number;
}

export async function dropLibraryItems(
  items: readonly LibraryItem[],
  target: LibraryDropTarget,
  scopeId: string,
): Promise<LibraryDropOutcome>;

/** What the hovered node should SAY it will do — the label a highlight
 *  carries, so a drop never resolves into a surprise. */
export function dropConsequenceKey(target: LibraryDropTarget): string;
```

Drop routing, exactly as spec §3.3 states it:

| Landing on | Default | With `⌥` held |
|---|---|---|
| empty pane | `placeLibraryItems(items, scopeId, flowPosition)` | same |
| `prompt` node | `addReferences(nodeId, items, scopeId)` | insert a mention chip in the body |
| `media` node | append the resolved durable refs to `data.items` | same |
| anything else | not highlighted, drop refused, panel selection kept | same |

The `⌥` mention path needs the body editor, which only `PromptNodeView` holds — so `dropLibraryItems` returns `mentioned: 0` and the node's own handler performs the insert through `PromptBodyEditorHandle`. That split is stated rather than hidden: a module that reached into a node's imperative handle would be reaching across a boundary it cannot see.

**Steps**

- [ ] Write the failing payload/dispatch test. Create `frontend/features/canvas-core/library/dropLibraryItems.test.ts`:

```ts
// features/canvas-core/library/dropLibraryItems.test.ts
//
// The drag payload and where a drop lands. `DataTransfer` does not exist in
// jsdom, so the stub below is the REAL interface — `types` is a list of MIME
// strings and `getData` answers by key. Anything looser (an object with an
// `items` field, say) would test a shape the browser never produces.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const addReferences = vi.fn();
const placeLibraryItems = vi.fn();

vi.mock('./addReferences', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  addReferences: (...a: unknown[]) => addReferences(...a),
}));
vi.mock('./placeLibraryItems', () => ({
  placeLibraryItems: (...a: unknown[]) => placeLibraryItems(...a),
}));

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import {
  LIBRARY_DND_MIME,
  dropLibraryItems,
  hasLibraryDrag,
  readLibraryDrag,
  writeLibraryDrag,
} from './dropLibraryItems';
import type { LibraryItem } from './librarySearch';

function fakeDataTransfer(): DataTransfer {
  const store = new Map<string, string>();
  return {
    get types() {
      return [...store.keys()];
    },
    setData: (k: string, v: string) => void store.set(k, v),
    getData: (k: string) => store.get(k) ?? '',
    effectAllowed: 'all',
    dropEffect: 'none',
  } as unknown as DataTransfer;
}

const ITEMS: LibraryItem[] = [
  { store: 'generated', id: '800000000000000001', title: 'A wide shot', thumbUrl: 'x', kind: 'image' },
  { store: 'assets', id: '727145299382534300', title: 'Cole Bannon', thumbUrl: 'y', kind: 'character', ready: true },
];

function seed(): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '900000000000000001',
    nodes: [
      { id: 'm1', type: 'media', position: { x: 0, y: 0 }, data: { title: 'Media', items: [] } },
    ] as never,
    connections: [],
    selection: [],
  });
}

beforeEach(() => {
  seed();
  addReferences.mockReset().mockResolvedValue({ added: 2, skipped: 0, failed: [] });
  placeLibraryItems.mockReset().mockResolvedValue({
    nodeIds: ['n1'], inserted: 2, skipped: 0, failed: [],
  });
});
afterEach(() => useCanvasCoreStore.getState().reset());

describe('the drag payload', () => {
  it('round-trips through one MIME slot, keeping string ids as strings', () => {
    const dt = fakeDataTransfer();
    writeLibraryDrag(dt, ITEMS);
    expect(dt.types).toContain(LIBRARY_DND_MIME);
    const back = readLibraryDrag(dt)!;
    expect(back.map((i) => i.id)).toEqual([
      '800000000000000001',
      '727145299382534300',
    ]);
    expect(typeof back[0].id).toBe('string');
  });

  it('also writes text/plain, so a drag into a text field is not garbage', () => {
    const dt = fakeDataTransfer();
    writeLibraryDrag(dt, ITEMS);
    expect(dt.getData('text/plain')).toBe('A wide shot, Cole Bannon');
  });

  it('a foreign drag reads as null rather than as an empty selection', () => {
    const dt = fakeDataTransfer();
    dt.setData('text/plain', 'hello');
    expect(hasLibraryDrag(dt)).toBe(false);
    expect(readLibraryDrag(dt)).toBeNull();
  });

  it('a malformed payload reads as null, not as a throw during a drop', () => {
    const dt = fakeDataTransfer();
    dt.setData(LIBRARY_DND_MIME, '{not json');
    expect(readLibraryDrag(dt)).toBeNull();
  });
});

describe('dropLibraryItems', () => {
  it('an empty-pane drop places nodes at the drop point', async () => {
    const r = await dropLibraryItems(ITEMS, { kind: 'canvas', position: { x: 12, y: 34 } }, 's');
    expect(placeLibraryItems).toHaveBeenCalledWith(ITEMS, 's', { x: 12, y: 34 });
    expect(r).toMatchObject({ handled: true, placed: 2 });
  });

  it('a prompt drop adds references, and creates no node', async () => {
    const r = await dropLibraryItems(ITEMS, { kind: 'prompt', nodeId: 'p1', mention: false }, 's');
    expect(addReferences).toHaveBeenCalledWith('p1', ITEMS, 's');
    expect(placeLibraryItems).not.toHaveBeenCalled();
    expect(r).toMatchObject({ handled: true, referenced: 2, placed: 0 });
  });

  it('⌥ over a prompt does NOT write manual_refs — the node inserts the mention', async () => {
    const r = await dropLibraryItems(ITEMS, { kind: 'prompt', nodeId: 'p1', mention: true }, 's');
    expect(addReferences).not.toHaveBeenCalled();
    expect(r).toMatchObject({ handled: true, referenced: 0, mentioned: 0 });
  });

  it('a media drop appends to that card and creates no second one', async () => {
    await dropLibraryItems(
      [ITEMS[0]],
      { kind: 'media', nodeId: 'm1' },
      's',
    );
    const media = useCanvasCoreStore
      .getState()
      .nodes.find((n) => (n as { id: string }).id === 'm1') as { data: { items: Array<{ url: string }> } };
    expect(media.data.items.map((i) => i.url)).toEqual([
      '/api/v1/generated-media/800000000000000001/file',
    ]);
    expect(placeLibraryItems).not.toHaveBeenCalled();
  });

  it('an empty item list is not handled, so the caller does not claim it did something', async () => {
    expect(await dropLibraryItems([], { kind: 'canvas', position: { x: 0, y: 0 } }, 's')).toMatchObject({
      handled: false,
    });
  });
});
```

- [ ] Run it. Expected failure: `Failed to resolve import "./dropLibraryItems"`.

- [ ] Implement `frontend/features/canvas-core/library/dropLibraryItems.ts`:

```ts
// features/canvas-core/library/dropLibraryItems.ts
//
// One custom MIME carries a whole multi-item library drag, and one dispatcher
// decides what a landing means. Both live here rather than in CanvasSurface so
// the surface's job stays "convert a DOM event into a target" — the file is at
// its size budget and this is real logic, not wiring.
//
// The payload is a JSON string in ONE slot. `dataTransfer` only carries
// strings, and spreading a multi-item drag over several keys would make the
// reader reassemble it — with no way to tell a partial write from a foreign
// drag. `text/plain` is written alongside it purely so dropping into a text
// field yields the titles instead of nothing.

import { getResourceCoverUrl } from '../../../services/resourceService';
import type { GeneratedImageRef, MediaNodeData } from '../smart/types';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { resolveReferenceRefs, addReferences } from './addReferences';
import type { LibraryItem, LibraryStore } from './librarySearch';
import { placeLibraryItems } from './placeLibraryItems';

export const LIBRARY_DND_MIME = 'application/x-nous-library';

export interface LibraryDragPayload {
  items: Array<{ store: LibraryStore; id: string; kind: string; title: string }>;
}

export function writeLibraryDrag(dt: DataTransfer, items: readonly LibraryItem[]): void {
  const payload: LibraryDragPayload = {
    items: items.map((i) => ({ store: i.store, id: i.id, kind: i.kind, title: i.title })),
  };
  dt.setData(LIBRARY_DND_MIME, JSON.stringify(payload));
  dt.setData('text/plain', items.map((i) => i.title).join(', '));
}

export function hasLibraryDrag(dt: { types: readonly string[] | DOMStringList }): boolean {
  return Array.from(dt.types ?? []).includes(LIBRARY_DND_MIME);
}

export function readLibraryDrag(dt: DataTransfer): LibraryItem[] | null {
  if (!hasLibraryDrag(dt)) return null;
  try {
    const parsed = JSON.parse(dt.getData(LIBRARY_DND_MIME)) as LibraryDragPayload;
    if (!Array.isArray(parsed?.items)) return null;
    return parsed.items.map((i) => ({
      store: i.store,
      id: String(i.id),
      title: i.title,
      kind: i.kind,
      thumbUrl: i.store === 'assets' ? '' : getResourceCoverUrl(String(i.id)),
    }));
  } catch (err) {
    // A malformed payload must not throw INSIDE a drop handler: the browser
    // would leave the drag visually stuck with no error anyone can see.
    console.error('[dropLibraryItems] unreadable drag payload:', err);
    return null;
  }
}

export type LibraryDropTarget =
  | { kind: 'canvas'; position: { x: number; y: number } }
  | { kind: 'prompt'; nodeId: string; mention: boolean }
  | { kind: 'media'; nodeId: string };

export interface LibraryDropOutcome {
  handled: boolean;
  placed: number;
  referenced: number;
  mentioned: number;
  failed: number;
}

const NOTHING: LibraryDropOutcome = {
  handled: false, placed: 0, referenced: 0, mentioned: 0, failed: 0,
};

/** The label a hovered node shows, so a drop never resolves into a surprise. */
export function dropConsequenceKey(target: LibraryDropTarget): string {
  if (target.kind === 'prompt') {
    return target.mention
      ? 'canvas.library.dropInsertMention'
      : 'canvas.library.dropAddReference';
  }
  if (target.kind === 'media') return 'canvas.library.dropAppend';
  return 'canvas.library.dropPlace';
}

export async function dropLibraryItems(
  items: readonly LibraryItem[],
  target: LibraryDropTarget,
  scopeId: string,
): Promise<LibraryDropOutcome> {
  if (items.length === 0) return NOTHING;

  if (target.kind === 'canvas') {
    const r = await placeLibraryItems(items, scopeId, target.position);
    return { handled: true, placed: r.inserted, referenced: 0, mentioned: 0, failed: r.failed.length };
  }

  if (target.kind === 'prompt') {
    // ⌥ means "mention", and a mention is an edit to the prompt DOCUMENT —
    // only the node holds the editor handle that can make it. This function
    // reports the routing decision and lets the node do the insert.
    if (target.mention) {
      return { handled: true, placed: 0, referenced: 0, mentioned: 0, failed: 0 };
    }
    const r = await addReferences(target.nodeId, items, scopeId);
    return { handled: true, placed: 0, referenced: r.added, mentioned: 0, failed: r.failed.length };
  }

  // media — append to that card, exactly as a file drop on it would.
  const refs: GeneratedImageRef[] = [];
  let failed = 0;
  for (const item of items) {
    try {
      refs.push(...(await resolveReferenceRefs(item, scopeId)));
    } catch (err) {
      console.error('[dropLibraryItems] could not resolve for a media card:', err);
      failed += 1;
    }
  }
  if (refs.length === 0) return { ...NOTHING, handled: true, failed };
  const store = useCanvasCoreStore.getState();
  // The LIVE node, re-read after the awaits.
  const current = ((store.nodes.find((n) => (n as { id?: unknown }).id === target.nodeId) as
    | { data?: MediaNodeData }
    | undefined)?.data?.items ?? []) as GeneratedImageRef[];
  const have = new Set(current.map((r) => r.url));
  const fresh = refs.filter((r) => !have.has(r.url));
  if (fresh.length > 0) {
    store.patchNode(target.nodeId, { data: { items: [...current, ...fresh] } });
  }
  return { handled: true, placed: fresh.length, referenced: 0, mentioned: 0, failed };
}
```

- [ ] Give `LibraryCell` its payload writer. Replace its `onDragStart` pass-through with a wrapper the grid supplies (Task 3 already declared the prop); in `LibraryPanel`, pass:

```tsx
        onItemDragStart={(item, e) => {
          // Drag the SELECTION when the grabbed cell is part of it, else just
          // that cell. Dragging one item out of a five-item selection and
          // getting five is the behaviour every file manager has.
          const chosen = selectedItems(selection, current.items);
          const dragging = chosen.some((i) => libraryKey(i) === libraryKey(item))
            ? chosen
            : [item];
          writeLibraryDrag(e.dataTransfer, dragging);
          e.dataTransfer.effectAllowed = 'copy';
        }}
```

- [ ] Teach `CanvasEngine` the MIME. In `onContainerDragOver`:

```tsx
      const libraryDrag = onLibraryDrop && hasLibraryDrag(e.dataTransfer);
      if (!fileDrag && !urlDrag && !libraryDrag) return;
```

in `onContainerDrop`, before the `files.length === 0` branch:

```tsx
      if (onLibraryDrop && hasLibraryDrag(e.dataTransfer)) {
        e.preventDefault();
        const flowPos =
          instanceRef.current?.screenToFlowPosition({ x: e.clientX, y: e.clientY }) ??
          { x: e.clientX, y: e.clientY };
        onLibraryDrop(e.dataTransfer, flowPos, e.altKey);
        return;
      }
```

and change the bindings so a canvas with only a library handler still receives drops:

```tsx
      onDragOver={onFileDrop || onUrlDrop || onLibraryDrop ? onContainerDragOver : undefined}
      onDrop={onFileDrop || onUrlDrop || onLibraryDrop ? onContainerDrop : undefined}
```

Add `onLibraryDrop` to the prop interface beside `onUrlDrop`, and to `onContainerDrop`'s dependency array (which today omits `onUrlDrop` — add both while here; it is the same bug).

- [ ] Wire `CanvasSurface` (≤ 15 lines):

```tsx
  const onLibraryDrop = useCallback(
    (dt: DataTransfer, flowPosition: { x: number; y: number }) => {
      const items = readLibraryDrag(dt);
      if (!items) return;
      void dropLibraryItems(items, { kind: 'canvas', position: flowPosition }, scopeId);
    },
    [scopeId],
  );
```

and pass `onLibraryDrop={canTakeFiles ? onLibraryDrop : undefined}`. `scopeId` comes from the `useCanvasScope()` already available in this file (add the call if it is not).

- [ ] Add the node-level drop targets. In `PromptNodeView`, on the node's outer element:

```tsx
      onDragOver={(e) => {
        if (readOnly || !hasLibraryDrag(e.dataTransfer)) return;
        e.preventDefault();
        setDropHint(e.altKey ? 'mention' : 'reference');
      }}
      onDragLeave={() => setDropHint(null)}
      onDrop={(e) => {
        if (readOnly || !hasLibraryDrag(e.dataTransfer)) return;
        e.preventDefault();
        e.stopPropagation();
        const items = readLibraryDrag(e.dataTransfer) ?? [];
        setDropHint(null);
        if (e.altKey) {
          // The mention path lives here because only this component holds the
          // editor handle. `dropLibraryItems` routes it and does nothing else.
          for (const item of items) {
            bodyEditorRef.current?.insertText(`@${item.title} `);
          }
          return;
        }
        void dropLibraryItems(items, { kind: 'prompt', nodeId: id, mention: false }, scopeId);
      }}
```

plus a `dropHint` state and a highlight ring carrying `t(dropConsequenceKey(...))` as its label (`data-testid="prompt-drop-hint"`). In `MediaNodeView`, extend the existing `:165-179` handlers with the same `hasLibraryDrag` branch dispatching `{ kind: 'media', nodeId: id }`.

- [ ] Write `frontend/features/canvas-core/ui/CanvasSurface.libraryDrop.test.tsx` using the prop-capturing React Flow stub from `CanvasSurface.dragCreate.test.tsx:45-56`, plus a copy of the `fakeDataTransfer` helper defined earlier in this task (`dropLibraryItems.test.ts` — copy the eleven lines rather than exporting a helper out of a test file): render the surface, read `capturedProps.onLibraryDrop`, call it with a `fakeDataTransfer` carrying a real payload written by `writeLibraryDrag`, and assert `dropLibraryItems` was called with `{ kind: 'canvas', position }`. Add one case asserting `onLibraryDrop` is `undefined` on a read-only canvas.

- [ ] Add the i18n keys `dropPlace` ("Place on canvas"), `dropAddReference` ("Add as reference"), `dropInsertMention` ("Insert as mention"), `dropAppend` ("Add to this card") to both locales.

- [ ] Run everything. Expected: `Tests N passed` on each file.

```bash
cd frontend && npx vitest run "features/canvas-core/library" "features/canvas-core/ui/CanvasSurface.libraryDrop.test.tsx" "features/canvas-core/ui/CanvasSurface.dragCreate.test.tsx" "features/canvas-core/smart/nodes/MediaNodeView.test.tsx"
```

- [ ] Mutation-verify, restoring after each:
  1. Drop the `libraryDrag` term from `onContainerDragOver`'s guard. Expected: no unit test fails — record this as a KNOWN GAP in the ledger and verify it by hand in the real-stack walkthrough of Task 10 (a `dragover` that never calls `preventDefault` makes the browser refuse the drop, and jsdom does not model that rule).
  2. Make the `⌥` branch of `dropLibraryItems` call `addReferences` too. Expected: `⌥ over a prompt does NOT write manual_refs` fails.
  3. Delete the `have`/`fresh` dedupe in the media branch. Expected: `a media drop appends to that card` still passes — extend it with a second identical drop asserting one item, then the mutation fails.

- [ ] Confirm the TypeScript baseline has not grown, and confirm `CanvasSurface.tsx` grew by no more than 15 lines.

- [ ] Commit:

```bash
git add frontend/features/canvas-core/library frontend/canvas-kit/CanvasEngine.tsx frontend/features/canvas-core/ui/CanvasSurface.tsx frontend/features/canvas-core/ui/CanvasSurface.libraryDrop.test.tsx frontend/features/canvas-core/smart/nodes/PromptNodeView.tsx frontend/features/canvas-core/smart/nodes/MediaNodeView.tsx frontend/public/locales/en.json frontend/public/locales/zh.json
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(canvas): 面板拖放 —— 空白建节点 / prompt 加参考 / ⌥ 提及 / media 追加，落点先说后果再接收"
```

---

### Task 9: 悬停预览 `library/LibraryPreviewCard.tsx`

Spec §3.3 悬停预览 + §4 验收 7（max_refs=3、资产 4 文件 → 3 行 Sends、1 行 Cut）.

**Files**
- Create `frontend/features/canvas-core/library/referenceCut.ts`
- Create `frontend/features/canvas-core/library/LibraryPreviewCard.tsx`
- Test `frontend/features/canvas-core/library/referenceCut.test.ts`
- Test `frontend/features/canvas-core/library/LibraryPreviewCard.test.tsx`
- Modify `frontend/features/canvas-core/library/LibraryPanel.tsx` — hover plumbing, ≤ 20 lines.
- Modify `frontend/public/locales/en.json` / `zh.json`

**Interfaces**

Consumes (verbatim, facts §2a / §6):

```ts
export async function fetchAssetDetail(scopeId: string, id: string, opts?: AssetDetailOptions): Promise<AssetRowDetail>
export function orderedReferenceFiles(files: readonly AssetFileRow[], assetType: AssetType, loadoutId: string | null): AssetFileRow[]
export function useModelCapabilities(model: string | null | undefined): ModelCapabilities | null
export function slotLabelKey(slot: string): string      // components/resources/assets/assetTypeMeta
```

Produces:

```ts
// referenceCut.ts
export interface ReferenceRow {
  resourceId: string;
  slot: string;
  /** true = the provider will receive it; false = trimmed at the ceiling. */
  sends: boolean;
}
export function referenceCut(
  files: readonly AssetFileRow[],
  assetType: AssetType,
  loadoutId: string | null,
  maxRefs: number | null,
): ReferenceRow[];

// LibraryPreviewCard.tsx
export interface LibraryPreviewCardProps {
  item: LibraryItem;
  /** Anchor rect of the hovered cell, in viewport coordinates. */
  anchor: DOMRect;
  /** The target node's generation model, or null when browsing. */
  model: string | null;
  scopeId: string;
}
export const PREVIEW_DELAY_MS = 400;
export function LibraryPreviewCard(props: LibraryPreviewCardProps): React.ReactElement | null;
```

`referenceCut` is a NEW pure module, not a copy of `AssetNodeView`'s inline rule — facts §11 says that greying logic should be extracted rather than duplicated, and two copies of "which files does the provider actually get" is exactly the divergence `assetFiles.ts` was created to end. It reuses `orderedReferenceFiles` for the order, which is the mirror of the backend's `_slot_priority`, so list position IS delivery position and the trim really is the tail.

⚠️ The card's population differs from `AssetNodeView`'s on purpose and the difference is stated in the module doc: the node ranks among the CHECKED files (its `selected_file_ids`), because that is what the bundle endpoint is handed. The panel is previewing an asset that is not on the board yet, so there is no selection — it ranks among ALL files a freshly placed card would reference. Both answer "what will the provider get", for two different cards.

**Steps**

- [ ] Write the failing cut test. Create `frontend/features/canvas-core/library/referenceCut.test.ts`:

```ts
import { describe, expect, it } from 'vitest';

import { referenceCut } from './referenceCut';

const file = (rid: string, slot: string, sort: number) => ({
  asset_id: '727145299382534300',
  resource_id: rid,
  slot,
  loadout_id: null,
  sort_order: sort,
  note: null,
  attached_by: null,
  attached_at: '2026-09-01T00:00:00Z',
});

const FOUR = [
  file('600000000000000001', 'portrait', 0),
  file('600000000000000002', 'portrait', 1),
  file('600000000000000003', 'worn', 0),
  file('600000000000000004', 'stills', 0),
];

describe('referenceCut', () => {
  it('spec §4 item 7 — max_refs 3 over 4 files gives 3 Sends and 1 Cut', () => {
    const rows = referenceCut(FOUR, 'character', null, 3);
    expect(rows.map((r) => r.sends)).toEqual([true, true, true, false]);
  });

  it('an unknown ceiling sends everything — null means UNKNOWN, never zero', () => {
    expect(referenceCut(FOUR, 'character', null, null).every((r) => r.sends)).toBe(true);
  });

  it('a ceiling of 0 cuts everything, and says so rather than showing an empty table', () => {
    const rows = referenceCut(FOUR, 'character', null, 0);
    expect(rows).toHaveLength(4);
    expect(rows.every((r) => r.sends)).toBe(false);
  });

  it('rows are in DELIVERY order, so the cut is always the tail', () => {
    // `worn` is declared last for a character but delivered second — drawing
    // declaration order would dim a file that IS sent.
    expect(referenceCut(FOUR, 'character', null, 4).map((r) => r.slot)).toEqual([
      'portrait',
      'portrait',
      'worn',
      'stills',
    ]);
  });

  it('a loadout-pinned file is absent when no loadout is bound', () => {
    const withOutfit = [...FOUR, { ...file('600000000000000005', 'worn', 1), loadout_id: 'lo1' }];
    expect(referenceCut(withOutfit, 'character', null, 10)).toHaveLength(4);
  });
});
```

- [ ] Run it. Expected failure: `Failed to resolve import "./referenceCut"`.

- [ ] Implement `frontend/features/canvas-core/library/referenceCut.ts`:

```ts
// features/canvas-core/library/referenceCut.ts
//
// "Which of this asset's files will the provider actually receive?" — asked
// BEFORE the asset is on the board, which is the whole point of the hover
// preview (spec §1 row 7: today the answer only appears after a run).
//
// ⚠️ This is NOT a copy of `AssetNodeView`'s greying rule and must not become
// one. That one ranks among the card's CHECKED files, because the bundle
// endpoint is handed `selected_file_ids` and trims within them. Here there is
// no card and therefore no selection, so it ranks among every file a freshly
// placed card would reference — which is `primarySlotFileIds`' population seen
// through `orderedReferenceFiles`' order. Same question, two different cards.
//
// The ORDER is `orderedReferenceFiles`, the mirror of the backend's
// `_slot_priority`. That is what makes "the cut is the tail" true rather than
// a guess: get the order wrong and the table dims a file that was sent.

import type { AssetFileRow, AssetType } from '../../../services/assetsService';
import { orderedReferenceFiles } from '../smart/assetFiles';

export interface ReferenceRow {
  resourceId: string;
  slot: string;
  sends: boolean;
}

export function referenceCut(
  files: readonly AssetFileRow[],
  assetType: AssetType,
  loadoutId: string | null,
  maxRefs: number | null,
): ReferenceRow[] {
  const ordered = orderedReferenceFiles(files, assetType, loadoutId);
  return ordered.map((f, i) => ({
    resourceId: f.resource_id,
    slot: f.slot,
    // `null` is UNKNOWN — still loading, fetch failed, model absent from the
    // map — and every consumer of `useModelCapabilities` renders FULL support
    // on it. Reading it as 0 would tell the user their references are being
    // thrown away on the day the capabilities endpoint hiccups.
    sends: maxRefs === null ? true : i < maxRefs,
  }));
}
```

- [ ] Run it. Expected: `Tests 5 passed`.

- [ ] Write the failing card test. Create `frontend/features/canvas-core/library/LibraryPreviewCard.test.tsx` asserting: nothing renders before `PREVIEW_DELAY_MS` (fake timers); after the delay an asset's card shows one row per file with `data-sends="true"|"false"`, three true and one false at `max_refs: 3`; the header names the model and the ceiling; a detail-fetch failure renders `data-testid="library-preview-error"` rather than an empty table; a non-asset item renders the picture and no file table. Mock `fetchAssetDetail` and `listGenerationCapabilities`, reset the capabilities module cache with `_resetModelCapabilitiesCache()` in `beforeEach`, and use the same four-file `AssetRowDetail` fixture as `referenceCut.test.ts` wrapped in the full detail row shape from Task 4's fixture.

- [ ] Implement `LibraryPreviewCard.tsx`: a `createPortal`-to-body absolutely positioned card anchored beside `anchor` (flip left when `anchor.right + 320 > window.innerWidth`); an effect that waits `PREVIEW_DELAY_MS` before fetching; the big picture; for assets, `referenceCut(detail.files, item.asset_type, null, caps?.max_refs ?? null)` rendered as a table of slot label + `Sends` / `Cut · max {{max}}`; a one-line footer `t('canvas.library.modelNote', { model, max, defaultValue: 'On {{model}}, {{max}} references are sent.' })`. Use `ok` for Sends and `warn` for Cut. Wire it in `LibraryPanel` off `LibraryGrid`'s existing `onItemHover` prop, holding `{ item, rect }` in state and clearing it on `onItemHover(null)`.

- [ ] Add the i18n keys `previewSends`, `previewCut`, `modelNote`, `previewFailed`, `previewNoModel` to both locales.

- [ ] Run. Expected: `Tests N passed`.

```bash
cd frontend && npx vitest run "features/canvas-core/library"
```

- [ ] Mutation-verify, restoring after each:
  1. Change `sends: maxRefs === null ? true : i < maxRefs` to `i < (maxRefs ?? 0)`. Expected: `an unknown ceiling sends everything` fails.
  2. Swap `orderedReferenceFiles` for a plain `files.slice()`. Expected: `rows are in DELIVERY order` fails — `worn` and `stills` come back in declaration order.
  3. Drop the delay from the card's effect. Expected: the card test's `nothing renders before the delay` case fails.

- [ ] Confirm the TypeScript baseline has not grown.

- [ ] Commit:

```bash
git add frontend/features/canvas-core/library frontend/public/locales/en.json frontend/public/locales/zh.json
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(canvas): 悬停预览文件表 —— 选中之前就说清当前模型会送哪几张、砍掉哪几张"
```

---

### Task 10: 删除 `AssetPickerDialog`、`DragCreateMenu` 改指面板、全量回归

Spec §3.5 删除清单（本期部分）+ §4 验收 9 / 10.

**Files**
- Modify `frontend/features/canvas-core/ui/DragCreateMenu.tsx` — `create()` at `:230-243` and the picker mount at `:294-305`.
- Delete `frontend/features/canvas-core/smart/nodes/AssetPickerDialog.tsx`
- Delete `frontend/features/canvas-core/smart/nodes/AssetPickerDialog.test.tsx`
- Modify (test) `frontend/features/canvas-core/ui/CanvasSurface.dragCreate.test.tsx` — the asset-picker case and its two `assetsService` mocks (`:20-42`).
- Test `frontend/features/canvas-core/library/libraryRemovals.test.ts` (new)
- Modify `frontend/e2e-prod/walkthrough.spec.ts` — one step.

**Interfaces**

Consumes: `useLibraryStore.openPanel` (Task 7), `place` from `DragCreateMenu` (`:205-229`).

Produces: nothing new. The drag-create Asset card stops opening a modal and opens the panel with the search box focused, exactly as spec §3.5 says (*「drag-create 卡保留（打开面板并聚焦搜索）」*).

⚠️ The drag-create card LOSES its wire. `DragCreateMenu.place()` wires the new node back to the origin handle when the menu came from a wire drop, and the panel places nodes through `placeLibraryItems`, which knows nothing about that origin. Rather than thread a pending-connection through the panel, the Asset card now behaves like the other pane-created cards: it opens the panel, the user places from there, and no edge is drawn. This is a deliberate, named reduction — the alternative is panel state that remembers a half-finished wire across an arbitrary browsing session, which is worse than an edge the user draws themselves.

**Steps**

- [ ] Write the failing removal test. Create `frontend/features/canvas-core/library/libraryRemovals.test.ts`:

```ts
// features/canvas-core/library/libraryRemovals.test.ts
//
// Spec §4 acceptance 9, for the components THIS plan retires. A deleted file
// with a surviving import is a build error, so what needs pinning is the
// opposite case: a file that quietly comes back, or an import that outlives
// its usefulness in a comment-shaped reference nobody notices.
//
// `AssetPromptPicker` and `MentionImageGrid` are NOT here: they are P3 and are
// still mounted (see the plan's Plan-time ruling 5). Asserting their absence
// now would be asserting a thing this plan deliberately does not do.

import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');

const GONE = [
  'smart/nodes/AssetPickerDialog.tsx',
  'smart/nodes/CanvasMentionPicker.tsx',
  'library/LibraryReferencePopover.tsx',
];

function tsFiles(dir: string): string[] {
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .flatMap((e) =>
      e.isDirectory() ? tsFiles(path.join(dir, e.name)) : [path.join(dir, e.name)],
    )
    .filter((f) => /\.tsx?$/.test(f));
}

describe('components this plan retires', () => {
  it.each(GONE)('%s is gone', (rel) => {
    expect(fs.existsSync(path.join(ROOT, rel))).toBe(false);
  });

  it.each(GONE)('nothing still names %s', (rel) => {
    const stem = path.basename(rel).replace(/\.tsx?$/, '');
    const hits = tsFiles(ROOT).filter((f) => fs.readFileSync(f, 'utf8').includes(stem));
    expect(hits.map((f) => path.relative(ROOT, f))).toEqual([]);
  });

  it('found files at all — an empty scan would pass every case above', () => {
    expect(tsFiles(ROOT).length).toBeGreaterThan(40);
  });
});
```

- [ ] Run it. Expected failure: `expected true to be false` on `smart/nodes/AssetPickerDialog.tsx`.

```bash
cd frontend && npx vitest run "features/canvas-core/library/libraryRemovals.test.ts"
```

- [ ] Change `DragCreateMenu`. In `create()`, replace the `if (item.pick)` branch:

```tsx
      if (item.pick) {
        // The panel, not a modal — and with the search box focused, because a
        // user who reached for "Asset" already knows which one they want.
        //
        // ⚠️ This card no longer wires back to the origin handle: the panel
        // places through `placeLibraryItems`, which has no notion of a pending
        // connection. Threading one through would mean panel state that
        // remembers a half-drawn wire across a browsing session.
        useLibraryStore.getState().openPanel({
          page: 'media',
          mediaStore: 'assets',
          focusSearch: true,
        });
        onClose();
        return;
      }
```

Delete `pickerOpen`, the `AssetPickerDialog` mount at `:294-305` and its import; add the `useLibraryStore` import. `place` stays — the other cards still use it.

- [ ] Delete both `AssetPickerDialog` files and re-run the removal test. Expected: `Tests 7 passed`.

- [ ] Update `CanvasSurface.dragCreate.test.tsx`: delete the `searchAssets` / `fetchAssetDetail` mocks and the asset-pick case, and add:

```tsx
  it('the Asset card opens the Library panel instead of a modal', async () => {
    // …open the create menu exactly as the surrounding cases do…
    fireEvent.click(screen.getByTestId('drag-create-asset'));
    expect(useLibraryStore.getState().open).toBe(true);
    expect(useLibraryStore.getState().focusNonce).toBeGreaterThan(0);
  });
```

- [ ] Add the real-stack step. In `frontend/e2e-prod/walkthrough.spec.ts`, after the existing canvas-tab step, append (all assertions by visibility, never `toHaveCount` — the 2026-08-12 lesson is that duplicated nodes are present in the DOM but permanently hidden):

```ts
  await test.step('Library panel opens and a picture can be dragged onto a prompt', async () => {
    await page.getByTestId('top-node-chip-library').click();
    await expect(page.getByTestId('library-panel')).toBeVisible();
    await page.getByTestId('library-segment-uploads').click();
    await expect(page.locator('[data-testid="library-cell"]:visible').first()).toBeVisible();
    await page.getByTestId('library-search').fill('a');
    await expect(page.getByTestId('library-consequence')).toBeVisible();
  });
```

- [ ] Run the whole canvas suite plus the two i18n guards. Expected: a `Tests N passed` line with zero failures.

```bash
cd frontend
npx vitest run "features/canvas-core"
npx vitest run "features/canvas-core/library/libraryI18n.test.ts" "features/canvas-core/library/libraryNaming.test.ts" "features/canvas-core/canvasAssetEntryI18n.test.ts"
```

- [ ] Run the full frontend suite and the backend suite, and confirm the TypeScript baseline:

```bash
cd frontend && npm test
cd ../backend && uv run pytest -q
cd ../frontend && npx tsc --noEmit -p . 2>&1 | grep -c "error TS"
```

- [ ] Confirm every file-size budget held:

```bash
cd frontend && wc -l \
  features/canvas-core/smart/nodes/PromptNodeView.tsx \
  features/canvas-core/ui/CanvasPage.tsx \
  features/canvas-core/store/canvasCoreStore.ts \
  features/canvas-core/ui/CanvasSurface.tsx \
  features/canvas-core/smart/nodes/AssetNodeView.tsx \
  features/canvas-core/ui/TopNodeBar.tsx
```

Expected against the Global Constraints table: `PromptNodeView` and `TopNodeBar` strictly smaller than 1085 / 277; `canvasCoreStore` and `AssetNodeView` unchanged at 1087 / 540; `CanvasPage` ≤ 1039; `CanvasSurface` ≤ 749.

- [ ] Commit:

```bash
git add frontend/features/canvas-core/ui/DragCreateMenu.tsx frontend/features/canvas-core/ui/CanvasSurface.dragCreate.test.tsx frontend/features/canvas-core/library/libraryRemovals.test.ts frontend/e2e-prod/walkthrough.spec.ts
git rm frontend/features/canvas-core/smart/nodes/AssetPickerDialog.tsx frontend/features/canvas-core/smart/nodes/AssetPickerDialog.test.tsx
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(canvas): 删除 AssetPickerDialog，drag-create 素材卡改为打开面板并聚焦搜索"
```

**Real-stack acceptance — after the branch merges and Cloudflare Pages ships it.** Run against `https://app.nous.ink` with the debug account. Spec §4 items, minus the Prompts-page ones (8, and the `AssetPromptPicker` / `MentionImageGrid` halves of 9), which are P3:

```bash
cd frontend && npm run e2e:prod
```

Then by hand, on a real canvas with real data:

1. **§4.1 Naming** — the top bar reads `Library`, the composer bottom bar reads `Workflows`, both bookshelf buttons read `Prompt Templates`. No other canvas control says Library.
2. **§4.2 Search** — press `L`, type into the box, the grid narrows.
3. **§4.3 Multi-select drop** — select three pictures with `⌘`, drag onto a prompt node: three thumbnails appear in its reference strip. Drag three onto empty canvas: nodes appear at the drop point.
4. **§4.4 `⌥` mention** — the same drag with `⌥` held appends to the body and leaves the reference strip unchanged.
5. **§4.5 Generated reachable** — the Generated segment lists this canvas's own output with no detour through Save To Uploads.
6. **§4.6 Consequence visible** — the `@` palette's first line and the panel footer both state what a pick will do.
7. **§4.7 Preview cut** — hover an asset with four files while a `max_refs: 3` model is selected on the target node: three rows say Sends, one says Cut.
8. **§4.10** — the walkthrough above includes opening the panel and dragging onto a prompt node.

Anything that fails here is a real defect, not a flaky test: `e2e:prod` cold-load timeouts are the one known exception, and a retry that passes with a screenshot showing a spinner rather than an error page is that exception, not a regression.

---

## Spec coverage

Every §3.x design item and §4 acceptance item, mapped to the task that lands it.

| Spec item | Task |
|---|---|
| §3.1 island panel, position, `canvas-island`, does not squeeze the canvas | 7 |
| §3.1 open / close: chip, `L`, ✕, Esc | 7 |
| §3.1 `L` in BOTH `useCanvasShortcuts` and `canvasShortcuts.ts` | 7 |
| §3.1 panel swallows keydown except Esc | 7 |
| §3.1 width + last page in `localStorage['canvas.library.v1']` | 7 |
| §3.1 bottom drawer below 1100px | 7 |
| §3.1 target bar, node highlight, ✕ releases | 7 |
| §3.1 `lite` can open the panel | 7 (shipped — `isSmartFamily` already includes `lite`) |
| §3.1 `storyboard` / `classic` can open the panel | **P3** (spec §6 stages it there) |
| §3.2 Prompts page — sources, search, list + preview, four actions | **P3** (stub only, Task 7) |
| §3.3 Assets segment: type chips + This project / All library scope | 7 |
| §3.3 Uploads segment: Image / Video / Audio / Doc | 7 |
| §3.3 Generated segment: This canvas / Today / All, intermediates hidden | 1, 2, 7 |
| §3.3 justified grid on `computeJustifiedRows` + `useJustifiedVirtualizer` | 3 |
| §3.3 multi-select (`⌘` / `⇧` / click), double click = default action | 3 |
| §3.3 footer `N selected · M files` + Place / Add as references | 3, 7 |
| §3.3 hover preview: picture, readiness, file table, Sends / Cut, model note | 9 |
| §3.3 hover preview: loadout dropdown, "used in N canvases" | **P3** (`used_in` is an opt-in five-table aggregate; the cut table is the acceptance-bearing half) |
| §3.3 drop MIME `application/x-nous-library` | 8 |
| §3.3 drop on empty pane → asset / media nodes, lane layout | 7 (placement), 8 (dispatch) |
| §3.3 drop on prompt → `manual_refs`; `⌥` → mention; highlight + consequence label | 8 |
| §3.3 drop on media node → append | 8 |
| §3.4 extend `PromptMentionPicker` with Uploads / Generated + consequence first line + `⇥` | 5 |
| §3.4 replace the Add-reference popover with the panel grid | 4 (popover), 7 (panel) |
| §3.4 attached composer's `MentionImageGrid` | **P3** (spec §3.4 defers it explicitly) |
| §3.4 shared index `useLibrarySearch`, no backend aggregate | 2 |
| §3.5 rename the four Library labels, composer bottom bar → Workflows | 6 |
| §3.5 top bar: Asset + Project Assets merge into one Library chip | 7 |
| §3.5 drag-create Asset card opens the panel with search focused | 10 |
| §3.5 delete `AssetPickerDialog`, `CanvasMentionPicker` | 4 (`CanvasMentionPicker`), 10 (`AssetPickerDialog`) |
| §3.5 delete `AssetPromptPicker`, `MentionImageGrid` | **P3** |
| §3.6 `libraryStore.ts`, selection holds `{store,id}` keys only | 7 |
| §3.6 no new tables / columns; `Save current` writes `POST /assets` | **P3** (that action lives on the Prompts page) |
| §3.6 generation path unchanged (`manual_refs` + bundle) | 4, 8 |
| §3.7 one store failing does not fail the panel; per-store Retry | 2, 3 |
| §3.7 target node deleted → release + toast | 7 |
| §3.7 unacceptable drop target: no highlight, no action, selection kept | 8 |
| §3.7 quota full → primary disabled with the reason in words | 4, 7 |
| §4.1 naming acceptance (grep test) | 6 |
| §4.2 search acceptance | 4, 7 |
| §4.3 multi-select drop acceptance | 8 |
| §4.4 `⌥` mention acceptance | 8 |
| §4.5 Generated reachable acceptance | 1, 2, 7 |
| §4.6 consequence visible acceptance | 3, 4, 5 |
| §4.7 preview cut acceptance (3 Sends / 1 Cut) | 9 |
| §4.8 Prompts two actions acceptance | **P3** |
| §4.9 old components deleted acceptance | 4, 10 (partial — the P3 half stays) |
| §4.10 real-stack walkthrough step | 10 |

**Gaps, and why each is a gap rather than an omission**

1. Everything marked **P3** is staged there by spec §6 itself. The two that a reader might expect here anyway: the Prompts page (§3.2, §4.8) and the deletion of `AssetPromptPicker` / `MentionImageGrid` (§3.5, half of §4.9) — Plan-time ruling 5 explains why they move together.
2. `storyboard` / `classic` panel access (§3.1 last line) is P3 in spec §6. Task 7 mounts the panel behind `isSmartFamily(kind)` (`CanvasPage.tsx`), and that predicate already includes `lite` — so `lite` got the panel with Task 7 and only the two kinds outside the smart family are still waiting.
3. The hover preview's loadout dropdown and "used in N canvases" line need `fetchAssetDetail(..., { usedIn: true })`, which the service documents as a five-table server-side aggregate that is off by default. The acceptance-bearing half of §3.3's preview is the Sends / Cut table, and Task 9 ships that; the two decorative rows follow in P3 with the Prompts page's own detail fetches.
4. Task 8's mutation check 1 has **no unit-level guard**: jsdom does not model "a `dragover` that never calls `preventDefault()` makes the browser refuse the drop", so removing the `libraryDrag` term from `CanvasEngine`'s guard breaks the feature with every test still green. It is verified by hand in Task 10's real-stack item 3.
