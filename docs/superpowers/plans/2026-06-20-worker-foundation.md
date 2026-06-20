# Plan: Worker Foundation — evidence-based liveness, multi-worker safety, scheduled-growth hardening

Date: 2026-06-20
Status: DRAFT (planning only — no code yet)
Supersedes: `2026-06-20-unified-heartbeat.md` (that draft's dedicated-thread heartbeat + liveness-into-cancel are replaced here by activity-derived heartbeat + generation fencing)
Related: #801 (zombie reaper, shipped), `reference_debug_dbos_zombie_downloads`, `bug_sweeper_wrongly_marks_recoverable_lost` (#492), `bug_worker_down_after_deploy`, `bug_cover_hang_cascade_and_autotag_scope` (#608), `bug_extract_audio_misleading_lost_message` (#668)
Cross-pollination: patterns extracted from `openclaw` (lifecycleGeneration fencing, activity heartbeat, evidence-first orphan classifier) and `hermes-agent` (activity-evidence dual-threshold staleness, infra-vs-fault `completion_reason` enum, dynamic grace window, advance-before-dispatch).

## 1. Why now

Three signals converge: more users + a team (concurrent load), HA appetite (eliminate the worker single point of failure), and a growing pile of scheduled tasks (~25 `@DBOS.scheduled` jobs today, plus a data-driven `scheduled_master` reading `user_schedules`). Everything — downloads, parses, AI, AND every cron — runs on ONE worker process with weak liveness. As the surface grows, "the worker died and nobody noticed / it double-ran / a busy task got wrongly killed" stops being an edge case.

Today's weaknesses (all verified in code):
- **No heartbeat on media/DBOS workflows.** `task_tracking.heartbeat_at` stays NULL for download/parse/soda; LOST detection falls back to a crude hard-ceiling / 6h age. Pure timer → the #492 false-LOST and #608/#668 "legit-long task reaped early" bugs.
- **`executor_id = role.value` ('worker', shared).** Deliberately role-not-uuid (`dbos_orchestrator.py` init_dbos docstring) — designed for single-worker-per-role. At `--scale 2` (which compose advertises) DBOS startup recovery, keyed on `(executor_id, app_version)`, would let worker-B re-run worker-A's in-flight workflows → **double-execution** (double downloads, double AI spend). Acknowledged hazards already in `state_machine.py:38`, `workforce_dispatch.py:15`.
- **Verdict conflation.** Infra-drop (worker died) and real fault (workflow raised) both end up `failed`/`lost`, so the UI lies and the real error gets buried (the #668 scar).

## 2. Core principle — evidence-based verdict (both reference agents agree)

A death/cancel/fail verdict must rest on EVIDENCE, never a stopwatch. Timer staleness only proves *nobody is running it*, not *it is broken*. Borrowed verbatim in spirit from openclaw (`gateway-lock.ts` biases to "alive" when evidence is `unknown`; timer is the last resort) and hermes (`process_registry._is_host_pid_alive` asks the OS, not the clock).

```
VERDICT TAXONOMY (evidence strength: strong → weak)
┌────┬──────────────────────────────┬───────────────────────────────┬──────────────┐
│ E1 │ DBOS workflow_status = ERROR │ real fault                    │ FAIL + decoded│  ✅ #801
│    │                              │                               │ real reason   │
├────┼──────────────────────────────┼───────────────────────────────┼──────────────┤
│ E2 │ Provably never executable:   │ no live executor of that ver  │ CANCEL        │  ✅ #801 (version)
│    │  version-orphan OR boot-gen  │ / a dead boot generation      │ (engine clean)│  ⬅ P3 adds boot-gen
│    │  not in live set             │                               │               │
├────┼──────────────────────────────┼───────────────────────────────┼──────────────┤
│ E3 │ Nobody is running it         │ infra dropped it — NOT a fault│ RECOVER, or   │  ⬅ this plan
│    │  (heartbeat/age stale)       │                               │ mark INTERRUPTED│  NEVER cancel
│    │                              │                               │ (retryable)   │  NEVER 'failed'
└────┴──────────────────────────────┴───────────────────────────────┴──────────────┘
```

The whole plan lives in making E2/E3 precise and evidence-backed, and in NEVER letting E3 (liveness) leak into a fail or cancel.

## 3. The three pillars

```
                        WORKER FOUNDATION
   ┌──────────────────┬────────────────────┬────────────────────────┐
   │ A. Liveness       │ B. Multi-worker     │ C. Scheduled growth     │
   │   (evidence)      │    safety (fencing) │    hardening            │
   ├──────────────────┼────────────────────┼────────────────────────┤
   │ activity-derived  │ per-process         │ keep ALL cron on        │
   │ heartbeat (no     │ boot_generation +   │ @DBOS.scheduled         │
   │ dedicated thread) │ unique executor_id  │ (at-most-once free)     │
   │ dual-threshold    │ → generation-orphan │ dynamic grace window    │
   │ stale (in-step/   │ = E2 evidence       │ (catch-up vs skip)      │
   │ between-step)     │ → --scale N safe    │ recovery-storm guard    │
   │ infra-vs-fault    │ (no double-exec)    │ scales                  │
   │ reason enum       │                     │                         │
   └──────────────────┴────────────────────┴────────────────────────┘
```

### Pillar A — evidence-based liveness
- **Activity-derived heartbeat, NO dedicated thread.** Bump `task_tracking.heartbeat_at` as a side effect of the progress writes the workflow ALREADY makes (`update_progress`, step boundaries). A streaming download refreshes itself for free; only a genuinely silent task ages out. (openclaw `agent-events.ts:436` `lastActiveAt`; hermes activity counter.) This sidesteps the killer flaw of the prior draft: a dedicated heartbeat thread sharing the saturated Supavisor pool would false-stale exactly when the worker is busiest.
- **Dual-threshold staleness.** Generous budget while inside a long step (download/transcode/cover), tight budget between steps. (hermes `delegate_tool.py` `_STALE_CYCLES_IN_TOOL=40` vs `_IDLE=15`.) Kills the #608/#668 "legit-long task reaped early" class.
- **Detect ≠ act.** The sweeper FLAGS suspected-dead; a second evidence pass (DBOS status? boot-gen live? real file progressing?) decides. (hermes "heartbeat stops refreshing, lets a higher layer time out".)
- **Infra-vs-fault reason enum.** `lost`/`worker_lost` (→ recover/retry, surface "interrupted") vs `failed` + decoded real error (→ fault). Propagate the real error, never overwrite with a generic "lost" string. (hermes `completion_reason: lost|exited`, mediahub #668.)

### Pillar B — multi-worker safety via generation fencing
- **Per-process `executor_id`** = `worker-<replica-index>` (or `worker-<short-uuid>`), NOT bare `worker`. Restores DBOS's per-process recovery isolation: a worker only recovers its OWN orphans, so `--scale N` no longer double-executes. Gateway stays `gateway`.
- **`boot_generation` fencing token.** Each worker mints `boot_generation = uuid4()` at launch and registers it in a `worker_registry(executor_id PK, boot_generation, started_at, heartbeat_at, app_version)` row. It stamps `boot_generation` on every `task_tracking` row it claims. A sweeper treats any in-progress row whose `boot_generation` is NOT in the live-registered set as a **provable** E2 orphan → safe requeue, no timer. (openclaw `lifecycleGeneration`, `agent-events.ts:172,193`.) This is strictly better than executor_id games: orphan-ness becomes evidence, and it's correct at any worker count.
- This is what unblocks **HA dual-worker** — the actual reason to scale.

### Pillar C — scheduled-growth hardening
- **Codify the discipline: all scheduled work goes through `@DBOS.scheduled`** (deterministic `sched-<name>-<iso>` id → sys DB rejects dupes → exactly one worker fires per tick at any scale). NEVER ad-hoc `asyncio`/`threading` timers (that's hermes' cron weakness: in-memory `_running_job_ids`, breaks multi-worker). Add a lint/review check.
- **Dynamic grace window** for the data-driven `scheduled_master` (`user_schedules`): on a missed tick after downtime, "within half-period grace → catch up; beyond → fast-forward + skip" instead of either replaying a stale burst or dropping silently. (hermes `jobs.py:_compute_grace_seconds` 424-453.)
- **Recovery-storm guard scales.** `_pre_launch_sweep_stale_scheduled` (cancels `sched-*` PENDING >3min) already exists; verify it stays correct as cron count grows, and that boot-grace covers a larger sched backlog drain.

## 4. Phased rollout (risk-ordered: lowest-risk / highest-value first)

| Phase | What | Risk to running workflows | Ship gate |
|---|---|---|---|
| **P0** Semantic correctness | Infra-drop → `lost`(retryable) not `failed`; #801 version-orphan reconcile uses `lost`; seed the reason split. Propagate real DBOS error on E1. | **zero** (relabel only, no new infra) | tiny PR |
| **P1** Activity heartbeat (observe) | `heartbeat_at` bumped inside existing `update_progress`/step writes; `worker_registry` + per-process `executor_id` + `boot_generation` written; sweeper LOGS "would-flag" only. | **zero** (write + observe, no action) | shadow days |
| **P2** Evidence verdict + dual-threshold | Replace timer-LOST with E1/E2/E3 logic + dual-threshold staleness + detect/act split. Behind `FEATURE_EVIDENCE_LIVENESS`. | low (E3 never cancels/fails; only marks interrupted) | flag |
| **P3** Generation fencing → multi-worker | Sweeper treats dead-generation rows as E2 orphans (requeue). Validate `--scale 2` end-to-end (no double-exec). | medium (the scale test itself) | flag + staged scale test |
| **P4** Scheduled hardening | Dynamic grace for `scheduled_master`; "@DBOS.scheduled only" lint; recovery-storm guard review. | low | per-item |

Each phase is independently shippable and reverts via its flag (P2/P3) or a one-line relabel revert (P0). P1 is pure addition.

## 5. Touchpoints (grounded)
- `supabase/migrations/3NN_worker_registry.sql` — `worker_registry`; add `boot_generation`, `heartbeat_at` semantics to `task_tracking` consumers (column exists).
- `backend/app/startup/dbos_init.py` — `executor_id = f"{role.value}-{replica_index}"` (read replica index from env / hostname); mint `boot_generation`.
- `backend/app/startup/bootstrap.py::install_background_bootstrap` — register/refresh `worker_registry` row (lightweight, NOT a per-task pinger).
- `backend/app/services/infra/unified_task_manager.py` / `task_tracker.py::update_progress` — bump `heartbeat_at` + stamp `boot_generation` on claim (the activity-heartbeat seam).
- `backend/app/workflows/workflow_health_sweeper.py` — verdict taxonomy E1/E2/E3, dual-threshold staleness, generation-orphan detection (extends `_is_permanent_dbos_orphan` / `_classify_in_python`); detect/act split.
- `backend/app/workflows/scheduled_recovery.py::reap_stuck_pending_tasks_step` — align with the verdict taxonomy (stop writing bare `lost`/`failed` on timer alone).
- `backend/app/workflows/scheduled_master.py` — dynamic grace window.
- `backend/app/services/infra/dbos_orchestrator.py::_pre_launch_sweep_stale_scheduled` — recovery-storm guard review.

## 6. Test plan (per pillar)
- **A:** unit — verdict taxonomy: E3 (liveness only) NEVER returns fail/cancel; only E1/E2 do. Dual-threshold: in-step long task NOT flagged before its step budget; between-step idle flagged at tight budget. Bogus/NULL timestamp → not-stale (fail-safe, hermes guard).
- **B:** unit — generation-orphan: a row whose `boot_generation` ∉ live set → E2; a row with a live generation → never orphaned. Integration — `--scale 2` in a staged env: kill worker-A mid-download, assert worker-B does NOT re-execute A's in-flight step (no double download), and the orphan is requeued exactly once.
- **C:** unit — dynamic grace: missed tick within grace → fire; beyond → fast-forward. Lint/CI — fail the build if a new `asyncio`/`threading` timer schedules work outside `@DBOS.scheduled`.
- **Shadow (P1):** `worker_registry.heartbeat_at` updates during a real concurrent load (multiple yt-dlp + AI) and the "would-flag" log stays empty for a healthy worker — proving activity-heartbeat does NOT false-stale under pool pressure (the exact failure the prior draft's dedicated thread would hit).

## 7. Failure modes (new codepaths)
- Heartbeat write fails under load → with activity-derived heartbeat the NEXT progress write retries it; a flagged-but-still-progressing task is re-confirmed alive by the detect/act split. No false kill.
- Generation registry stale after crash → boot-grace + "fail-safe to alive on probe error" (openclaw `gateway-lock.ts:227`) means a transient registry read failure never reaps live work.
- `--scale 2` with a forgotten old `executor_id` deploy → mixed identities; mitigated by P3's staged scale test + the fact that mismatched generation = orphan = requeue (at-most-once via DBOS dequeue lock).

## 8. NOT in scope (deferred, with rationale)
- Cross-machine (multi-host) worker fleet — single NAS host for now; `worker_registry` is host-agnostic but the scale test targets 2 procs on one host.
- Replacing DBOS's scheduler — it already gives at-most-once; we harden around it, not replace it.
- GPU/transcode worker pool separation — separate capacity concern, not liveness.
- A bespoke UI "interrupted/recovering" state distinct from `lost` — start with `lost`(retryable); add a distinct state only if users find the relabel confusing.

## 9. What already exists (reuse, don't rebuild)
- #801 zombie reaper = E1 (decoded ERROR) + E2 (version-orphan). P3 EXTENDS its E2 with boot-generation; do not rebuild.
- `within_boot_grace()` (sweep_guard) — keep as the universal "don't reap during re-registration" gate.
- `_pre_launch_sweep_stale_scheduled` — the recovery-storm guard; Pillar C reviews, doesn't replace.
- `liveness_scanner` (agent_runs) — already does activity-evidence (last_useful_action_at) for agents; Pillar A generalizes the same instinct to media workflows.
- The flow trigger counts `phase IN ('failed','lost','timed_out')` (mig 203) — so writing `phase='lost'` already rolls into flow terminal state; no flow-layer change needed.

## 10. Open questions for eng review
- P3 replica-index source: env var per `--scale` replica, hostname, or DBOS `DBOS__VMID`? (DBOS default is a per-process uuid — could we just STOP overriding executor_id and let DBOS uuid it, then re-derive the gateway/worker distinction another way?)
- Is P0 (relabel) safe to ship standalone NOW, ahead of the rest? (It captures the highest-value insight with zero infra.)
- Dual-threshold budgets: derive from `workflow_timeout_policy` (already per-type) or new per-step config?
