# Task / List Pagination Rollout Plan (2026-06-09)

> Extends [`scale-100k-readiness-audit.md`](./scale-100k-readiness-audit.md). Removes the
> drain-all / 1000-cap pattern from user-facing lists. **Two patterns, matched to context —
> do not unify them:**
>
> | context | pattern | mechanism | examples |
> |---|---|---|---|
> | **table / log** | **page numbers (offset)** | `.range(offset, +limit)` + total count | **Settings → Tasks**, admin tables (`useNotionTable`) |
> | **feed / grid** | infinite scroll (keyset) | `KeysetCursor` + IntersectionObserver | Downloads, resource library (`fetchResourcesPaginated`) |

**Goal:** No user-facing list silently truncates at 1000; the Settings → Tasks log paginates
with page numbers; the secondary library feeds scroll to their true tail.

**Non-goals:** Admin rework; a universal pagination framework; touching the resource-library
grid or Downloads (already keyset-correct via `useLibrary`).

---

## TRACK A — Settings → Tasks: page-number (offset) pagination  ← current work

A task log in a modal is a **table**, so it gets admin-style page numbers (jump pages,
"Page 3 / 50"), NOT infinite scroll. The backend is already offset-based, so this is mostly
moving the client-side filter/sort to the server + a pagination control.

### Facts that shape it (verified 2026-06-09)
1. **`task_tracking` PK = `dbos_workflow_id` (text, mig 180); no `id` column.** `rowToTask`
   maps `UnifiedTask.id = dbos_workflow_id`.
2. **`get_tasks` already paginates with `.range(offset, offset+limit-1)` ordered
   `created_at desc`** (`unified_task_manager.py:681`). Only filters/sort/count are missing.
3. **Badge counts come from the full in-memory list.** `activeCounts`/`totalActive`
   (`TaskManagerContext.tsx:621-638`) filter `state.tasks` to active. **Paging the list breaks
   them** → counts MUST move to their own query (route-C discipline: counts via
   `COUNT(*) ... WHERE phase IN (...)`).

### A1 — Backend: server-side filter/sort/count (NO new RPC needed)
**Files:** `backend/app/api/task_manager_router.py::list_tasks`,
`backend/app/services/infra/unified_task_manager.py::get_tasks`.
- `get_tasks` gains: `statuses: list[str]` (→ `.in_("status", ...)`), `types: list[str]`
  (→ `.in_("task_type", ...)`), `search: str` (→ `.or_("title.ilike.%q%,subtitle.ilike.%q%,error_msg.ilike.%q%")`),
  `sort` (created_desc|created_asc|updated_desc|title_asc → `.order(...)`), and returns
  **rows + exact total** via `.select("*", count="exact")` in one round-trip.
- Endpoint: `GET /api/v1/task-manager/tasks` gains `statuses` (repeatable/CSV), `types`,
  `search`, `sort`; returns `{ data, total, page, page_size }`. `limit`/`offset` stay.
- **Search drops the id-label** (`DL-7B29`) — it's a frontend-computed prefix
  (`taskIdLabel`), not a column. Server search = title/subtitle/error_msg. (Confirmed accept.)
- **Active-counts endpoint:** add `GET /api/v1/task-manager/active-counts` →
  `{ total, by_type }` from `COUNT(*) WHERE phase IN ('queued','in_progress')`, so the badge
  never depends on the paged list.

### A2 — Frontend data layer
**Files:** `frontend/contexts/TaskManagerContext.tsx`, `frontend/utils/taskDisplay.ts`
(filter/sort move server-side), `frontend/components/TaskCenter/TaskCenter.tsx`.
- Replace `fetchAllTasks`/`paginateAll` (drain-all) with `fetchTasksPage(page, pageSize,
  { statuses, types, search, sort })` → `{ tasks, total }`.
- Context holds `{ tasks (current page), page, pageSize, total, loading }`. Filter/sort/search
  change → reset to page 1 + refetch (debounce search ~300ms).
- **Decouple counts:** badge subscribes to the new active-counts query; the list subscribes to
  the paged fetch. Two independent sources.

### A3 — Pagination control (UI)
**Files:** new `frontend/components/TaskCenter/TaskPagination.tsx`;
`TaskCenter.tsx` / `TaskToolbar.tsx`.
- Prev / Next + page numbers + "Page N / M" + total ("714 tasks"). Page size 50 (default).
  Match the dark TaskCenter styling; mobile = compact Prev/Next.

### A4 — Realtime under offset paging
**Files:** `frontend/contexts/TaskManagerContext.tsx` (the `task_tracking` Realtime effect).
- On INSERT/UPDATE/DELETE affecting the **current page** (or when on page 1, where new tasks
  land) → **debounced refetch of the current page** (~500ms). Simpler than keyset prepend;
  acceptable because offset pages are short-lived views. Active-counts query also refreshes.
- Known offset caveat: a burst of new inserts while on a deep page can shift rows by one across
  a refetch. Acceptable for a log; documented.

### A5 — Batch select-all + grouping under paging
**Files:** `TaskCenter.tsx`, `BatchActionBar.tsx`, `frontend/utils/taskSelection.ts`.
- **"Select all"** = all rows on the **current page**. Add **"Select all N matching"** → a
  lightweight query of all matching ids for the current filter (cap ~5000; surface the cap,
  never silent). `partitionForRetry` already drops ids no longer present.
- **Grouping** runs client-side over the **current page only**. (Grouping + page numbers is
  niche; if it reads oddly, gate grouping off when paginated — decide during A3.)

### Track A gates
A1: filter/sort/count correct on dev DB (`mediahub-sb-dev`); cross-user isolation.
A2: counts stay correct independent of page. A3: page math correct at edges (last partial
page, 0 results). A4: live progress still streams on the current page. A5: select-all-matching
acts on the full filtered set. The shipped batch feature (#573) keeps working throughout.

---

## TRACK B — secondary library feeds: keyset infinite scroll (SEPARATE, LATER)

Independent of Track A. These are content feeds → keyset + IntersectionObserver, reusing the
existing primitive in `frontend/services/pagination.ts` (`applyKeysetCursor` / `sliceKeysetPage`
/ `KeysetCursor`) — already extracted.

### Investigation 2026-06-09 (before any code) — corrected scope

- **`fetchDownloadedResources` is NOT a Track B target.** The Downloads GRID renders via
  `<DownloadsView/>` (`ResourcesViewInner.tsx:530`), which uses `useLibrary` (keyset, scale-safe).
  `fetchDownloadedResources` → `downloadedResources` state only feeds the `allItems`
  client-aggregate (`ResourcesContext.tsx:647`), not the visible grid. Leave it (or fold into
  the aggregate's own fix). **Do not "fix" it as a grid path.**
- **Real targets = 3 views:** `fetchFolderContents` (folder), `fetchTrashedResources` (recycle
  bin), `fetchSmartFolderResources` (smart folders). All still drain → 1000 cap.
- **The blocker is NOT the service functions (those ARE mechanical mirrors of
  `fetchResourcesPaginated`, ordered `(created_at|trashed_at DESC, id DESC)` + `applyKeysetCursor`).
  It's the UI coupling:** `ResourceGrid.tsx:289` hard-wires its IntersectionObserver sentinel to
  the MAIN `resources` list's `loadMoreResources`/`hasMoreResources` from context. Folder/trash/
  smart-folder render through the same grid but with their own data + no loadMore. So Track B's
  REAL first step is **make `ResourceGrid` view-agnostic** — switch `{items, loadMore, hasMore}`
  by `sidebarView` (or take them as props) — then add per-view cursor/hasMore state in
  `ResourcesContext` (mirroring the `resources` keyset state) + a paginated service fn per view.
  This is per-view stateful work on the order of the Task Center conversion, NOT a copy-paste.

### ResourceGrid refactor is SMALLER than "rewrite" (verified 2026-06-09)

`sortedItems` (the displayed data) is ALREADY a **prop** — `ResourcesViewInner` computes the
per-view data and passes it in. Only the pagination is single-source: `ResourceGrid` reads
`loadMoreResources`/`hasMoreResources`/`isLoadingMoreResources` straight from **context** (the
main `resources` list). So step 1 is NOT a rewrite — it's **lift `loadMore`/`hasMore`/
`isLoadingMore` to props too** (mirroring `sortedItems`), and have `ResourcesViewInner` wire the
right pagination per view (main→`loadMoreResources`, recycle→`loadMoreTrashed`, folder→
`loadMoreFolder`). ResourceGrid's render logic barely changes.

### ⚠️ SEARCH MUST BE SERVER-SIDE (Downloads bug — do NOT repeat)

Downloads/library client-side search only ever covered **loaded** rows: `fetchVideoLibrary` loads
the first `LOCAL_CACHE_SIZE = 500` (`dataService.ts:239/707`) and `useLibrary.ts:633-650` filters
that cache in-browser + PAUSES infinite scroll while searching (`:321` `!isSearchActiveRef`) →
search silently misses everything past the first 500 / unscrolled. Correct precedent =
**server-side search**: Downloads now has the `search_downloads_library` RPC (mig 275, #556);
Task Center pushes `search` into `get_tasks`. **Every Track B view's search MUST push the term
into the keyset query / RPC WHERE — never client-filter the loaded subset.**

### Order
1. **ResourceGrid**: lift `loadMore`/`hasMore`/`isLoadingMore` to props (sortedItems already is).
2. `fetchTrashedResourcesPaginated` (recycle bin — flat list, simplest) + context keyset state +
   wire. **Search → server-side** (the `resources` is_trashed query's WHERE).
3. `fetchFolderContentsPaginated` (folder) — same shape; search server-side.
4. `fetchSmartFolderResources` — hardest: client rule objects → server WHERE predicate; search
   server-side too; do alone.

Each is a separate, individually-verified PR (own scope/RLS/Realtime). Acceptance bar per view:
**paginates past 1000 AND search finds matches that were never scrolled into view.**

---

## Decisions (confirmed 2026-06-09)
1. Settings → Tasks uses **page numbers (offset)**, not infinite scroll. ✅
2. Feeds (Downloads/library/secondary lists) stay **keyset infinite scroll**. ✅
3. Search = title/subtitle/error_msg ILIKE; id-label dropped. ✅
4. Counts decoupled from the paged list (required). ✅
5. Page size 50. ✅

## Sequencing
- **Now:** Track A (A1→A5), one feature branch (`feature/tasks-pagination`), phase-by-phase,
  each with its gate. A2 (counts decoupling) is the sharp edge.
- **Later:** Track B, separate PRs, after re-verifying `fetchDownloadedResources` liveness.
- Dev integration DB: `mediahub-sb-dev` (NAS, schema byte-identical to prod).

---

# TRACK B — Smart-folder keyset DESIGN (2026-06-09, design-first)

> Status of Track B so far: ✅ step 1 (ResourceGrid loadMore→props, #581) ✅ step 2 (recycle bin
> keyset, #582). **Scope correction:** main folder nav already uses `fetchResourcesPaginated({folderId})`
> (keyset — NOT a gap). The only remaining gaps: (a) recycle SUB-folder (`fetchFolderContents`,
> niche — a trashed folder with >1000 items inside) and (b) **smart folders** (this design).

## Why smart folders resist a simple keyset

`ResourcesRepository.execute_smart_rules` (resources_repository.py:1305) builds a PostgREST query
from the rule JSON, but two things break naive keyset:
1. **Tag conditions are post-filtered in Python** (`_filter_by_tags`) AFTER the query → can't keyset
   a result set that's then filtered (page of N shrinks to <N; cursor/hasMore math breaks).
2. **Exclude mode (`match:false`)** = "fetch ALL items, subtract matched" → drain-all by construction.

Partial keyset (base query only) is UNSAFE — with tags/exclude present it paginates the wrong set.
It's all-or-nothing → **one server-side RPC** that does rules→SQL + tags + exclude + keyset, mirroring
`search_scope_resources` (mig 269).

## Exact current semantics to replicate (verbatim from the repo)

Rule shape: `{ conditions: [{field, op, value}], operator: "AND"|"OR", match: bool }`.

**Resource-field ops** (`_apply_condition` / `_condition_to_postgrest`) — applied to `resources.{field}`:
| op | SQL |
|----|-----|
| `eq` | `r.{field} = v` |
| `contains` | `r.{field} ILIKE '%v%'` |
| `starts_with` | `r.{field} ILIKE 'v%'` |
| `gt` / `lt` / `gte` / `lte` | `r.{field} >/</>=/<= v` |
| `in` | `r.{field} IN (v split on ',')` |

**Tag ops** (`_filter_by_tags`) — case-insensitive on `tags.name`:
| op | SQL |
|----|-----|
| `contains` | `EXISTS (resource_tags rt JOIN tags t ON … WHERE rt.resource_id=r.id AND lower(t.name)=lower(v))` |
| `not_contains` | `NOT EXISTS (… same …)` |

**Combine:** `operator=AND` → all predicates AND'd (resource AND tag groups). `operator=OR` → all
OR'd (resource OR-group OR tag OR-group). **`match=false`** → wrap the whole thing in `NOT (...)`
(this REPLACES the fetch-all-subtract — same result, keyset-able).

**Relative dates** (`_resolve_value`): `relative:-7d|-30d|-1h|-Nm` → absolute ISO. **Resolve these in
Python BEFORE passing rules to the RPC** (keep `_resolve_value`), so the RPC only sees absolute values
— exact semantic parity, no `now()` drift inside SQL.

**Order:** `r.created_at DESC, ri.id DESC` (keyset tiebreak, same as search_scope_resources).

## RPC design — `search_smart_folder` (mirror mig 269)

```
search_smart_folder(
  p_scope_id    text,
  p_rules       jsonb,         -- relative dates pre-resolved by the caller
  p_search      text DEFAULT NULL,   -- smart folder HAS a filter bar → server-side search (Track B rule)
  p_cursor_ts   timestamptz DEFAULT NULL,
  p_cursor_id   bigint DEFAULT NULL,
  p_limit       int DEFAULT 40,
  p_with_count  bool DEFAULT false
) RETURNS jsonb   -- { rows: [...], total_count: bigint|null }
```

Build `WHERE ri.scope_id = p_scope_id::bigint AND r.is_trashed=false AND <rule-predicate>
[AND <search ILIKE on title/url/notes>] AND <keyset cursor>` ORDER BY `(r.created_at, ri.id) DESC`
LIMIT `p_limit`. SECURITY INVOKER + the scope guard already on the endpoint.

### ⚠️ The #1 risk: dynamic predicate from jsonb (correctness + injection)

Do NOT `format()`+`EXECUTE` raw `field`/`op`/`value` — injection + type errors. Instead:
- **Field allowlist** `field → (column, type)` baked into the RPC (e.g. `filename→text`,
  `created_at→timestamptz`, `duration_seconds→int`, `rating→int`, `source_platform→text`, …). Reject
  unknown fields (skip the condition, or error). This is the security AND correctness foundation —
  enumerate it from the smart-folder editor's field list.
- **Per-op + per-type** predicate construction (CASE on op, cast value to the field's type). `gt/lt`
  on a `timestamptz` field casts the value to timestamptz; on `int` casts to int. PostgREST got
  implicit coercion for free; SQL must be explicit.
- Build the predicate string with `quote_literal()` for values + the allowlisted column name (never
  the raw input) → injection-safe.

## Frontend wiring

`ResourcesContext.fetchResourcesPage` smart-folder branch (currently returns
`fetchSmartFolderResults` as a single `hasMore:false` page): replace with a paginated call that
threads `cursor` + `pageSize` + the current search term into `search_smart_folder`, returning a real
`KeysetListPage` (data/hasMore/nextCursor/totalCount). Then smart folders page like the main library
through the SAME `useKeysetPagination(fetchResourcesPage)` — no new hook, no new sentinel (already in
`isResourcesView`). **Resolve relative dates client-side or in the endpoint before the RPC.**

## Verification plan (MANDATORY — this is the silent-wrong-results piece)

Against `mediahub-sb-dev` with rule FIXTURES covering EVERY combination, asserting the RPC result ==
the legacy `execute_smart_rules` result (same set) for each:
- each resource op (eq/contains/starts_with/gt/lt/gte/lte/in) on text / int / timestamptz fields
- tag `contains` / `not_contains`
- `operator` AND vs OR
- `match` true vs false (exclude)
- relative-date resolution
- mixed resource + tag conditions
- keyset continuity (page1 ∪ page2 == full set, no dup/gap at the boundary on equal created_at)
- search term ANDed in
**Acceptance bar:** RPC set-equals legacy for every fixture, AND a smart folder matching >1000 pages
past 1000, AND search finds matches never scrolled into view.

## Effort / sequencing
Backend-heavy (migration + RPC + parity tests) ≈ the Task Center A1 round. One PR:
mig `search_smart_folder` + repo method `execute_smart_rules_paginated` (or route the endpoint to the
RPC) + endpoint gains cursor/limit/search/with_count + frontend adapter. Author + dev-verify in one
focused session; do NOT bundle with other work.
