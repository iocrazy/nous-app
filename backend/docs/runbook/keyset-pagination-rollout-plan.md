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
/ `KeysetCursor`) — already extracted, so no "P0 extraction" is needed.

| function (`resourceService.ts`) | table | notes |
|---|---|---|
| `fetchFolderContents` | resource_items | folder view; mechanical mirror of `fetchResourcesPaginated` |
| `fetchTrashedResources` | resources (is_trashed) | uses `last_scope_*` snapshot, not resource_items |
| `fetchSmartFolderResources` | resource_items | dynamic rule → server WHERE translation (riskiest; do alone) |
| `fetchDownloadedResources` | resource_items | **RE-VERIFY IF LIVE FIRST** — main Downloads grid uses `useLibrary` (already keyset); this drain fn may be a dead/secondary caller. Don't "fix" a dead path. |

Each is a separate, individually-verified PR (own scope/RLS/Realtime). `fetchSmartFolderResources`
(client rule objects → server predicate) is the only non-mechanical one.

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
