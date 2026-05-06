# DBOS PoC #12 — Fallback Decision Tree

**Status**: Active reference (PR-D0 PoC deliverable)
**Date**: 2026-04-28
**Owner**: heygo
**Use when**: any one of PoC #1-11 fails its pass criteria, OR a later PR-D# discovers a hard block (latency regression, RLS incompatibility, OpenTelemetry gap).

This doc is the **predetermined escape route** so we don't redebate strategy under pressure. Pick the first option whose triggers match. Don't try options past the first one that fits — each step down costs more.

---

## Trigger Matrix — pick the first row that matches

| Trigger | Severity | Recommended fallback |
|---|---|---|
| DBOS schema cannot be isolated from supabase tables (PoC #2) | Hard | **F1: separate PG instance** |
| Required PG extension missing on NAS supabase (PoC #3) | Hard | **F1: separate PG instance** |
| `auth.uid()` returns NULL inside DBOS step / RLS un-enforceable (PoC #10/#11) | Hard | **F2: Hatchet** OR **F4: Approach A** |
| DBOS replay produces wrong output for one of 23 ported tasks (PR-D2/D3) | Medium | **F3: per-task carve-out** |
| OpenTelemetry / observability gap blocks operability | Medium | **F3 + manual instrumentation** OR **F2: Hatchet** |
| Latency regression > 110% on any of 23 user-facing task_types (PR-D3 shadow) | Medium | **F3: per-task carve-out** |
| DBOS upstream abandoned / acquired-and-killed within 6 months | Hard | **F2: Hatchet** (long-term) |
| Cross-AZ / multi-region deployment needed before mediahub goes that scale | Out of scope today | revisit at scale, **F2: Hatchet** likely |

---

## F1 — Separate PG instance for DBOS only

**What changes**: Spin a dedicated PG 17 container on NAS just for DBOS metadata + workflow state. mediahub's supabase stays untouched. DBOS uses `dbos://` connection string pointing at this dedicated PG; supabase-py operations from inside DBOS steps still go to NAS supabase via existing client.

**When**: NAS supabase rejects DBOS schema/role/extension changes (PoC #2/#3/#5 fail), OR ops decide "don't touch the supabase cluster from DBOS at all".

### Concrete plan

| Item | Effort | Note |
|---|---|---|
| Add `dbos-pg` service to `docker-compose.yml` (image `postgres:17-alpine`, volume mount, healthcheck) | 0.5d | Port 55434 to avoid clash with sb-dev (55433) and sb-prod (55434 — pick free) |
| `mediahub_dbos` role / pwd in `dbos-pg` (no supabase migration needed) | 0.5d | Single CREATE ROLE; no Supavisor pooler involved |
| Update `backend/app/main.py` lifespan: `DBOSConfig(database_url=os.environ['DBOS_DATABASE_URL'])` pointing at `dbos-pg` | 0.5d | `.env` adds `DBOS_DATABASE_URL=postgresql://...@dbos-pg:5432/postgres` |
| Backup/snapshot strategy: nightly `pg_dump` of `dbos-pg` to `/volume1/backup/dbos/` | 1d | Disaster recovery story |
| Failover plan: stale `dbos-pg` data == lost in-flight workflows; document RTO | 0.5d | Spell out user-visible impact |
| Removal from PR-D2/D3/etc — every place that creates `postgres_dbos_sys` etc must use the new env var | 1d | Search & replace, test |

**Total added effort**: 4 days (≈ 1 week with buffer).

**Cost**:
- 1 extra container in docker-compose (~256MB RAM)
- 1 extra port in router (if external monitoring needed) — likely no
- Operational: 1 extra DB to monitor / backup / patch

**Pros**:
- Total isolation: DBOS bugs / migrations cannot touch mediahub data
- DBOS PG can be upgraded/downgraded independently
- Simpler RLS story (no overlap with supabase auth roles)

**Cons**:
- Cross-DB transactions impossible: DBOS workflow can't atomically write `dbos.workflow_status` + `public.issues`. Workaround: workflow writes issues via supabase client, status update via DBOS — accepts brief inconsistency window (acceptable per design doc P11 already)
- 2x backup workflows
- Restore complexity if either PG corrupts: dbos-pg restore loses in-flight workflows; supabase restore loses issues created since backup

**Trigger to use**: PoC #2 fails (schema isolation broken), OR PoC #3 fails (extension missing on NAS supabase but available on plain PG 17), OR PoC #5 fails (DBOS needs superuser and we refuse to grant on supabase).

---

## F2 — Switch durable engine to Hatchet

**What changes**: Replace DBOS Transact (Python library) with Hatchet (Go engine + Python SDK + Postgres backend). Add `hatchet-engine` and `hatchet-dashboard` services to docker-compose. Workflow code rewritten with `@hatchet.workflow` decorators. Hatchet stores state in its own PG (or shared PG with separate schema).

**When**: DBOS-specific behavior cannot meet a hard requirement — replay determinism issue, lack of cross-step transactions we need, observability/dashboard gap, or DBOS project health concerns.

### Concrete plan

| Item | Effort | Note |
|---|---|---|
| Spike Hatchet on local docker (1 worker + 1 task) | 1d | Validate Python SDK, Postgres schema, dashboard |
| Re-write 3-5 already-ported workflows from DBOS → Hatchet | 1w | Test syntax/semantic differences |
| Add `hatchet-engine` (Go binary) + `hatchet-dashboard` (Next.js) + workers to docker-compose | 1w | Hatchet bundles dashboard; mediahub gets observability free |
| Adapt FastAPI lifespan + `start_workflow` calls (`hatchet.client.event.push(...)` instead of `DBOS.start_workflow(...)`) | 0.5w | Slightly different fan-out / await pattern |
| Re-validate PoC #7-11 against Hatchet | 1w | Same gate criteria |
| Re-port remaining tasks (continue from wherever DBOS port stopped) | 1.5-2w extra over DBOS pace | Hatchet syntax is more verbose |

**Total added effort relative to DBOS path**: +3-5 weeks (depending on how many tasks already ported in DBOS need rewriting).

**Cost**:
- 2 extra containers (engine + dashboard, ~512MB RAM total)
- License is MIT (no commercial constraint)
- Dashboard is a real product ([hatchet.run](https://hatchet.run)) — better than DBOS Conductor for self-host

**Pros**:
- Mature engine (Go core, used in production at companies before mediahub)
- Built-in dashboard with retry/replay UI (ops win)
- Larger community than DBOS

**Cons**:
- 2 extra services to operate
- Workflow code rewrite (lose any DBOS-specific patterns we built)
- Cross-language: Go engine + Python workers means harder to debug end-to-end
- Heavier than DBOS for our scale (1 dev, ≤100 workflows/day)

**Trigger to use**: DBOS upstream stalls (no commits 3+ months), OR PoC #11 reveals an unfixable auth-context limitation, OR ops demand a real dashboard before MVP.

---

## F3 — Per-task carve-out (DBOS for most, Celery kept for outliers)

**What changes**: Keep Celery+Redis running for the 1-3 task_types that fail DBOS port (e.g. task with hard ffmpeg subprocess + complex retry semantics), migrate the remaining 20-22 to DBOS. Extends the `dbos_workflow_routing` flag table indefinitely instead of tearing it down at PR-D3d.

**When**: A specific task_type doesn't fit DBOS semantics (long sync subprocess, custom heartbeat, etc.) but the rest do.

### Concrete plan

| Item | Effort | Note |
|---|---|---|
| Identify the offending task_type(s) precisely (PR-D2/D3 shadow comparison surfaces them) | 0d | Already covered by routing table observability |
| Keep `celery-worker` container alive but scaled to N=1 with only the carve-out task registered | 0.5d | Reduces blast radius |
| Skip PR-D3d (Celery removal) for the carve-out subset | — | Document as long-term debt |
| Add OpenTelemetry to Celery worker so observability story is consistent | 1w | Otherwise observability splits |

**Total added effort**: 1-2 weeks (mostly observability glue).

**Cost**: Operational complexity — 2 task systems instead of 1. Eats half the architectural simplification motivation.

**Pros**:
- Doesn't kill the project — most tasks benefit from DBOS
- Buys time to fix the carve-out tasks later (or rewrite them, or remove the feature)

**Cons**:
- Long-term debt: every new contributor must learn 2 systems
- Defeats one of the original 3 problems ("3 sets of retry/cancel/observability")

**Trigger to use**: Late-PR discovery that ≤3 specific tasks resist DBOS port. Don't use for >5 tasks — that means DBOS is wrong for our codebase, prefer F2 or F4.

---

## F4 — Retreat to Approach A (γ-min wrapper)

**What changes**: Abandon DBOS entirely. Resurrect Approach A from the design doc — a thin Python wrapper layer that gives users a unified Issues façade over the existing 3 backends (Celery / agent_tasks / project_tasks). No real durable execution improvements; just UX consolidation.

**When**: PoC #10/#11 reveals DBOS+supabase RLS is fundamentally incompatible AND F1 separate PG is rejected (e.g., ops won't host another PG), AND F2 Hatchet is over budget.

### Concrete plan

| Item | Effort | Note |
|---|---|---|
| `issues` table — Paperclip schema port (same as PR-D1) | 1w | Reused if we ever try DBOS again |
| Wrapper service `IssueOrchestrator` — translates issue.status + agent_tasks/unified_tasks events | 2w | Listens to existing tables, writes issue rows |
| Frontend Issues page (List + Kanban + 详情) | 2-3w | Same UI work as PR-D6 |
| project_tasks → issues backfill | 1w | Same data migration as PR-D6 |
| Documentation: explain why we have 3 execution backends + 1 façade | 0.5w | Future-proofing the decision |

**Total added effort relative to PR-D0 today**: ~6-8 weeks (matches Approach A original estimate).

**Cost**: Same operational cost as today (3 backends still running). Add 1 wrapper service.

**Pros**:
- Users still get Issues UX (the original product motivation) within 2 months
- Zero risk to existing 23 task_types
- Reversible: a future DBOS retry only needs to add DBOS execution, not redo Issues

**Cons**:
- Doesn't solve the 3 deep problems (3 retry/cancel/observability sets / no real durable execution / glue rot)
- Tech debt grows: 3 backends + façade is more surface area than 3 backends alone

**Trigger to use**: DBOS is provably incompatible AND F1/F2 both refused. Last resort.

---

## How to invoke a fallback (process)

1. **Document the failure** in `~/.gstack/projects/mediahub/poc-failures-2026-04-28.md` with: which PoC item, exact error, repro steps, screenshots/logs.
2. **Match against trigger matrix** above. Pick first matching row.
3. **14-day timebox**: if fallback chosen requires >14 days to validate, escalate (i.e., we're outside PoC scope).
4. **Update design doc** — set `Mode` to fallback path, link to this doc's chosen section.
5. **Notify channel** — Discord notification with: original failure / chosen fallback / new ETA.
6. **Close PR-D0** with status `FAILED → fallback={F1|F2|F3|F4}`. Open new PR scoped to the fallback path.

---

## Ranking summary (when in doubt)

```
  Cheapest (most reversible)
       ↓
   F3 per-task carve-out      — only if 1-3 tasks fail
   F1 separate PG             — only if PG-isolation problem
   F2 Hatchet                 — only if DBOS itself is the problem
   F4 Approach A              — only if everything DBOS-shaped fails
       ↓
  Most expensive (least reversible)
```

**Default presumption**: until a trigger fires, stay on Approach B (DBOS full migration). Fallbacks are pre-committed defenses, not ambient options.

---

## Validation

This doc satisfies PoC #12 of the eng-review 2026-04-27 PR-D0 gate. With it written and committed, PoC #12 is **PASS**.
