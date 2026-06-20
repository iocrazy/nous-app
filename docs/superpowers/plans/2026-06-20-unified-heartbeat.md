# Plan: Evidence-based liveness — unified heartbeat as a recovery signal (not a kill signal)

Date: 2026-06-20
Status: DRAFT (planning only — no code yet)
Related: PR #801 (DBOS zombie reaper), `reference_debug_dbos_zombie_downloads`, `project_unified_heartbeat_backlog`

## 0. Reframing (the key decision, 2026-06-20)

The original draft treated "worker heartbeat stale" as a **kill** signal. That conflates two different things the current system already muddles:

| Reality | Correct verdict | What the system does today |
|---|---|---|
| Infra dropped it (worker crashed / host down — nobody is running it) | RECOVER, or mark **interrupted/retryable** — NOT the workflow's fault | marks `failed`/`lost` (looks like the workflow broke) |
| Workflow actually faulted (raised, real bug, input gone) | FAIL with the real error | ✅ #801 decodes the real DBOS ERROR |

**Principle: a death/cancel verdict must be evidence-based, not timer-based.** "Heartbeat stale" or "frozen 6h" only proves *nobody is running it* — it does NOT prove *it is broken*. So liveness staleness must NOT, by itself, fail or cancel a workflow. It may only (a) trigger recovery faster, or (b) mark a non-destructive "interrupted/retryable" UI state.

### Verdict taxonomy (evidence strength, strong → weak)

```
E1  DBOS recorded ERROR            → real fault   → FAIL with decoded reason   ✅ #801 (dbos_error_to_text)
E2  Provably never executable      → version-orphan (no executor of that ver) → CANCEL (evidence-backed)  ✅ #801
E3  Nobody is running it (liveness) → heartbeat/age stale → NOT a fault →
      → trigger DBOS recovery, or mark 'lost' (retryable). NEVER cancel, NEVER 'failed', on E3 alone.
```

Everything #801 already does is E1/E2 (evidence-backed). The heartbeat work lives entirely in E3, and E3's only licensed actions are non-destructive.

## 1. Goal / Non-goals

Goal: add a per-process heartbeat so "the worker is gone" is detected in ~minutes (not the crude 6h age backstop), and use it ONLY to (a) speed up recovery / interrupted-marking and (b) make #801's existing E2 age-backstop precise — WITHOUT widening what ever gets cancelled.

Non-goals:
- NOT a new kill path. The set of workflows that can be cancelled stays exactly {E1 ERROR, E2 version-orphan} — heartbeat only sharpens the *timing/precision* of the E2 backstop, never the *blast radius*.
- NOT replacing DBOS startup recovery (it owns same-version recovery; we must not race it).
- NOT removing the agent per-task progress signal (`liveness_scanner` silent/stuck — that's E1-ish progress evidence for alive workers, a different axis).

## 2. Hard constraints (the breakage guards)

1. **Never fail/cancel on E3 alone.** Liveness staleness → recover or mark 'lost'(retryable), never 'failed', never cancel.
2. **Don't race DBOS recovery.** DBOS recovery is startup-triggered and filters by `(executor_id, current app_version)` (verified in `dbos/_recovery.py::recover_pending_workflows`). Same-version PENDING is DBOS's to recover at next worker boot — we only mark it interrupted for the UI, we don't cancel it.
3. **Heartbeat MUST run on a dedicated thread, not the asyncio event loop.** A media workflow's blocking step (yt-dlp/ffmpeg) can stall the event loop; an event-loop heartbeat would then go falsely stale and (in any cancel path) kill live, healthy work. This is the single highest false-positive risk — the heartbeat writer is a `threading.Thread`, independent of workflow execution.
4. **Keep boot grace.** A just-started process hasn't pinged yet.

## 3. Phased rollout (each ships + rolls back independently)

### Phase 1 — heartbeat write + observe (harmless)
- New `worker_heartbeats(executor_id text PK, last_seen timestamptz, app_version, pid, updated_at)`.
- Worker/combined role starts ONE `threading.Thread` that UPSERTs every `HEARTBEAT_PING_SECONDS` (default 15s). Gateway role skips (enqueue-only).
- Sweeper LOGS "would-mark-interrupted" candidates (executor heartbeat stale) — no action. Observe for days: does an alive-but-busy worker ever show stale heartbeat? (If yes → constraint #3 is being violated; fix before any enforcement.)

### Phase 2 — evidence-based verdict separation (non-destructive)
Behind `FEATURE_HEARTBEAT_LIVENESS` (default false):
- Stop conflating infra-drop with failure. Where a task's owning executor heartbeat is stale (worker gone) AND DBOS has no live claim:
  - if DBOS will recover it (same version) → mark UI `lost`(retryable) interim; recovery re-runs it and the lifecycle trigger corrects to the real outcome. **No cancel.**
  - Reconsider #801's reconcile: today it writes `status='failed'` for version-orphans, but a version-orphan is infra-drop, not a fault → switch to `status='lost'` now that the frontend handles `lost` (layer-3 shipped in #801). Keeps `failed` meaning "real bug only".

### Phase 3 — precise-ify #801's E2 age backstop (same blast radius)
- Replace `_is_permanent_dbos_orphan`'s crude `age_seconds >= 6h` backstop with "owning executor heartbeat stale > `HEARTBEAT_DEAD_SECONDS` (default ~30min) AND DBOS has no live/recoverable claim". This cancels the SAME class (provably-no-live-executor) faster and more precisely — it does NOT cancel anything new. Version-orphan (E2) stays the primary signal.

## 4. Touchpoints
- `supabase/migrations/3NN_worker_heartbeats.sql` — new table.
- `backend/app/startup/bootstrap.py::install_background_bootstrap` — start the `threading.Thread` ping (worker/combined only; skip gateway, mirroring the existing `executor_id='gateway'` discrimination there).
- `backend/app/startup/dbos_init.py` — `executor_id = role.value` (the ping key).
- `backend/app/workflows/workflow_health_sweeper.py` — `_executor_heartbeat_stale(executor_id)` helper; Phase 2 reconcile-as-lost; Phase 3 folds the heartbeat into `_is_permanent_dbos_orphan` replacing the 6h literal.
- `backend/app/workflows/sweep_guard.py::within_boot_grace` — KEEP.

## 5. Safety guardrails
- `FEATURE_HEARTBEAT_LIVENESS` gates all enforcement (Phase 2+). Phase 1 is action-free.
- Dedicated-thread heartbeat (constraint #3) — the one thing that, if gotten wrong, kills live work.
- Cancel blast radius is INVARIANT: {E1, E2} before and after. Heartbeat never adds a kill target.
- Generous `HEARTBEAT_DEAD_SECONDS` ≥ 100× ping interval; boot grace retained.

## 6. Test plan
- Unit: `_executor_heartbeat_stale` (fresh/stale/missing/boot-grace).
- Unit: verdict taxonomy — E3 (liveness only) NEVER returns fail/cancel; only E1/E2 do.
- Unit: Phase 3 gate still NEVER cancels a same-version DBOS-recoverable workflow.
- Integration (shadow, Phase 1): heartbeat row updates every 15s; stale-candidate log empty for a healthy worker across a real yt-dlp download (proves constraint #3 — event loop blocked but heartbeat thread alive).

## 7. Rollback
- Phase 1: drop thread + table.
- Phase 2/3: `FEATURE_HEARTBEAT_LIVENESS=false` → instant revert to #801-only.

## 8. Open questions for eng review
- Is Phase 3 worth it at all, or do Phase 1 (observe) + Phase 2 (correct verdict semantics) deliver the value, leaving #801's 6h backstop as the boring-but-fine cancel path?
- `executor_id=role` (one row per role) — fine for single-worker prod; revisit for multi-worker.
- Should infra-drop be its own UI state ("interrupted/recovering") distinct from `lost`, or is `lost`(retryable) enough?
