# Phase 6 — Canvas Realtime + Performance: SPEC + Phased Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship the five Phase 6 canvas items as six *independently shippable* sub-plans (6a–6f), reusing existing infra (Supabase Realtime, DBOS + task_tracking 路线 C, React Flow built-ins) rather than bespoke machinery.

**Architecture:** Realtime rides the existing Supabase Realtime pattern (`TaskManagerContext` → `postgres_changes`). Virtualization/minimap are React Flow built-ins (`MiniMap`, `onlyRenderVisibleElements`). Cmd+K extends `useCanvasShortcuts`. Full-graph orchestration mirrors `workflows/storyboard.py` + `download.py` under 路线 C. AI outpaint folds into the same nous-center workflow seam as orchestration.

**Tech Stack:** React 19 + Zustand + @xyflow/react ^12.10.1; FastAPI + DBOS + Supabase Realtime; nous-center workflow client.

---

## Source-of-truth caveat
`docs/plans/canvas-ai-upgrade-plan.md` (the "v1.2" plan referenced by migration 280) is gitignored and present only in the local `Feature-ai-agent` worktree. This plan is reconstructed from the five stated Phase 6 items + real code. Diff against the local doc's Phase 6 section before executing.

## Anchor files (ground truth)
| Concern | File |
|---|---|
| Store, debounced PUT, optimistic lock, 409 | `frontend/features/canvas-core/store/canvasCoreStore.ts` |
| React Flow surface (Background+Controls only) | `frontend/features/canvas-core/ui/CanvasSurface.tsx` |
| Existing Cmd shortcuts (window keydown) | `frontend/features/canvas-core/ui/useCanvasShortcuts.ts` |
| Page mount | `frontend/features/canvas-core/ui/CanvasPage.tsx` |
| Realtime reference pattern | `frontend/contexts/TaskManagerContext.tsx:567-638` |
| Run model (single sync LLM/nous) | `backend/app/services/canvas/canvas_run_service.py`, `canvases_router.py:165-185` |
| nous run-then-poll helper | `backend/app/services/canvas/nous_center_runner.py` |
| DAG ordering (frontend Kahn) | `frontend/features/canvas-core/smart/topology.ts` |
| Reference DBOS workflows | `backend/app/workflows/storyboard.py`, `download.py`, `services/infra/dbos_orchestrator.py` |
| Outpaint (deterministic blur, "AI path pending") | `backend/app/services/canvas/outpaint_derive_service.py:76-83` |
| Canvas table + RLS + lock token | `supabase/migrations/280_canvas_core_schema.sql` |
| Realtime publication pattern | `supabase/migrations/210_ensure_supabase_realtime_publication.sql` |
| 路线 C discipline (binding) | `CLAUDE.md:249-262` |

---

## RECOMMENDED PRIORITY: 6c → 6b → 6e → 6a → 6f → 6d

| Sub-plan | Value | Cost | Verdict |
|---|---|---|---|
| **6c Cmd+K palette** | High | Low (~1 day) | **First.** Pure frontend, high UX leverage. |
| **6b Minimap + virtualization** | Med-high | Very low (2 RF props/components) | **Early.** Mostly free. |
| **6e Perf audit** | Med | Low-med | **Before 6a/6d.** Defines acceptance numbers; likely exposes `setNodes`-on-drag full-array churn (`canvasCoreStore.ts:361`) as the real bottleneck. |
| **6a Realtime sync** | Med | Med | **Scope to cross-tab/last-writer-wins.** Multi-user co-edit = CRDT project, OUT. |
| **6f Outpaint AI path** | Low-med | Med | **Gate on 6d nous plumbing.** No standalone value until a nous outpaint workflow exists. |
| **6d DBOS full-graph orchestration** | Questionable @ scale | High (touches 路线 C) | **Last; challenge whether in scope.** No backend graph executor today; storyboard already owns DBOS gen. |

---

## 6a — Realtime node-state sync (cross-tab, last-writer-wins)
**Scope:** cross-tab + cross-user *invalidation/refresh*, NOT live co-editing. Reuse Supabase Realtime like `TaskManagerContext`. No bespoke WS server (canvas already persists to Postgres with optimistic-lock `base_updated_at`; `supabase_realtime` infra exists). Conflict policy = last-writer-wins at row level via the existing 409 conflict UI (`CanvasConflictDialog.tsx`); Realtime only *notifies* other tabs to refetch/rebase.
- **6a.1** migration `2XX_canvases_realtime_publication.sql` — `ALTER TABLE canvases REPLICA IDENTITY FULL` + add to `supabase_realtime` publication (mirror 210 idempotency). RLS already gates SELECT (280:118-126).
- **6a.2** store `applyRemoteUpdate(row)` (TDD): newer `base_updated_at` + no local unsaved edits → rebase; unsaved local edits → set `conflict` (reuse 409 path), never clobber; self-echo guard `row.base_updated_at <= s.baseUpdatedAt`.
- **6a.3** `useCanvasRealtime(canvasId)` hook — structural copy of `TaskManagerContext.tsx:590-638`, `postgres_changes` UPDATE filter `id=eq.<canvasId>` → `applyRemoteUpdate`; mount in `CanvasPage.tsx`. Treat payload as a *signal* (read id + base_updated_at, refetch lazily — don't render from broadcast).

**OQ:** conflict model → last-writer-wins via existing 409 UI; multi-user live editing → OUT; payload size → signal-only, switch to Supabase Broadcast `{id,base_updated_at}` if 6e shows cost; run-status sync → reuse TaskManagerContext channel (run state lives in task_tracking per 路线 C, not canvas blob).

## 6b — Minimap + viewport virtualization
React Flow built-ins cover ~95%. **6b.1** add `onlyRenderVisibleElements` prop (TDD: 500 off-viewport nodes, assert culled). **6b.2** add `<MiniMap pannable zoomable nodeColor={...selectionSet...}>` sibling to Background/Controls (`selectionSet` already at `CanvasSurface.tsx:77`). **OQ:** built-in MiniMap (theme via nodeColor/CSS vars), not hand-rolled; rely on RF measurement, add explicit width/height only if culling pops nodes.

## 6c — Cmd+K command palette
Pure frontend; extends window-scoped keydown. **6c.1** pure `commands.ts` registry — `Command{id,title,hint?,run(),enabled?()}` + fuzzy `filterCommands`; seed from store actions (undo/redo/select-all/clear/save). **6c.2** Cmd+K binding in `useCanvasShortcuts.ts` (before meta branches, `onOpenPalette` callback) + `CommandPalette.tsx` focus-trapped modal (input + filtered list + kbd nav; its own input focus makes `isInsideEditable` stop other shortcuts). Mount in `CanvasPage.tsx`. **OQ:** store actions + node-add only v1 (defer content search); canvas-only scope.

## 6e — Performance audit (do before 6a/6d)
**6e.1** baseline with `benchmark` skill + chrome-devtools `performance_start_trace`/`performance_analyze_insight` on 200/500/1000-node canvases; capture drag FPS, interaction-to-paint, heap, TTI, @xyflow bundle. Record in `docs/superpowers/perf/2026-06-14-canvas-baseline.md`. **6e.2** fix measured hot paths (TDD each): throttle `onMove` viewport writes (raf); avoid full nodes-array rebuild on drag (position-patch path vs `setNodes(next)`+history-debounce per tick); confirm 6b culling. **OQ acceptance:** 60fps drag @200 nodes, ≥30fps @1000, no main-thread block >50ms during pan (confirm vs local plan numbers).

## 6d — DBOS full-graph orchestration (the big one — see RISK NOTE)
No backend graph executor today; `topology.ts` sequences on frontend only. A "Run graph" enqueues ONE parent DBOS workflow per run; parent creates `task_tracking` row via `get_task_manager().create()`, topo-sorts, runs each node as a `@DBOS.step` calling EXISTING services (`canvas_run_service`, `nous_center_runner.run_nous_workflow`). Per-node status via task_tracking subtasks/metadata, NEVER PATCHing `phase`. Failures **raise** (路线 C rule #4).
- **6d-M1** contract+routing: add `canvas_graph_run` to `dbos_workflow_routing`; `CanvasGraphRunRequest{canvas_id,node_order,continue_on_failure}` (frontend supplies order from `topoSortPrompts`); `POST /canvases/{id}/graph-runs` enqueues + returns task_id immediately (NOT synchronous).
- **6d-M2** `backend/app/workflows/canvas_graph.py` (mirror `download.py:612-744`). TDD first: mid-chain failure **raises** (assert raises, not `{"status":"failed"}` dict). `run_async(get_task_manager().start(...))`; per-node `update_progress(metadata=...)` only; re-raise on failure when `not continue_on_failure`. Node step `@DBOS.step(retries_allowed=True, max_attempts=3)` dispatching to existing services — no new gen logic.
- **6d-M3** per-node status → UI: each node run = `task_tracking` subtask via `manager.create()` (free realtime via existing channel); canvas `run_status` becomes a projection of task_tracking, not an independent source.
- **6d-M4** frontend: swap smart "Cascade Run" from in-browser `runPrompts` (`runner.ts:125`) to enqueue endpoint; render per-node status from subtasks. Keep single-node sync "Run" as-is.

**OQ:** in scope at all? → DEFER/smallest-viable, reuse storyboard gen services; per-node status → task_tracking subtasks not phase PATCH; topo on frontend (reuse `topology.ts`) in M1; gen steps that bill credits → idempotent or `retries_allowed=False` (avoid double-charge); sequential v1 (parallel branches later).

## 6f — Outpaint AI generative path
`outpaint_derive_service.py:76-83` already collects `prompt` "for the AI path"; thin branch depending on a nous outpaint workflow existing (same plumbing as 6d). **6f.1** (TDD) when `prompt` + AI mode → call nous outpaint (mock), persist its bytes; when unavailable → fall back to `extend_canvas` deterministic fill, never raise. Add `mode:'deterministic'|'ai'` to schema; "AI fill" toggle in `OutpaintEditorModal.tsx`, deterministic stays default/free. **OQ:** async via 6d enqueue/poll (gen is 10–60s); **nous outpaint workflow slug = BLOCKING dependency** — don't build speculatively.

---

## RISK NOTE — 6d vs the 路线 C task_tracking discipline
6d introduces a new long-running multi-step backend executor. 路线 C (`CLAUDE.md:249-262`) exists because a prior double-source-of-truth between `task_tracking` and `dbos.workflow_status` produced real bugs. Landmines + baked-in guardrails:
1. **Returning a failed dict instead of raising** → DBOS marks SUCCESS, trigger mirrors `phase=completed` while node metadata never updates → "completed but stuck." 6d-M2's first test asserts the workflow *raises*.
2. **PATCHing phase/status/progress directly** (rules #1/#2) — forbidden; 6d-M3 makes node status a subtask + metadata, phase owned by the mirror trigger.
3. **Two run-truth sources** (canvas blob `run_status` + task_tracking) — resolved by making canvas blob run_status a projection.
4. **Duplicating `storyboard.py`** — node steps MUST call existing services, never new gen code.

**Recommendation:** treat 6d as deferrable. Ship 6c/6b/6e/6a first. Commit to 6d only when a concrete multi-node canvas-run flow genuinely needs it, and gate the M2 PR on the raises-not-returns test + a 路线 C compliance review. 6f rides 6d's nous plumbing.
