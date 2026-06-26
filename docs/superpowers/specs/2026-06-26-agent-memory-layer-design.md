# Agent Memory Layer — Design Spec

**Status:** Draft for review (design only — no implementation until approved)
**Date:** 2026-06-26
**Inspiration:** Xiaomi MiMo-Code + Nous Hermes converge on *file-curated memory + full-text recall + periodic LLM distillation* (no vector/graph) for **agent** memory. This ports that pattern to mediahub's multi-tenant server, with strict scope isolation.

---

## 1. Problem & Scope

mediahub's durable memory today is **user memory**: Graphiti (L3 knowledge graph, `group_id=user-/project-`) + Honcho (L2 user model, `workspace=team-`). What's missing is **agent working memory** — the agent remembering *its own* curated knowledge (project facts, decisions, recurring workflows) across sessions, recalled by relevance, the way MiMo's `MEMORY.md` + FTS5 + `/dream` does.

**In scope:** a curated, scoped `agent_memory` store + ranked full-text recall + a periodic consolidation (`/dream`) workflow + a distill→skills (`/distill`) workflow, all multi-tenant-isolated.

**Explicitly NOT in scope:** replacing Honcho/Graphiti (different domain — user modeling). This is complementary. Also out: re-implementing `ai_session_memory` (the per-session checkpoint is live and reused as-is).

**The defining requirement (the reviewer's stated concern):** *one person's conversation must never leak into a project's/team's shared memory.* Isolation is the spec's first-class constraint, addressed in §4.

---

## 2. What Already Exists (build on, don't duplicate)

| Asset | State | Role in this design |
|-------|-------|---------------------|
| `ai_session_memory` (per-session 6-section MD, dual-threshold updater, consumed by compactor) | **live** | The **session checkpoint** layer. Reused unchanged — it's mediahub's `checkpoint.md`. |
| `agent_memories` table | **dropped (mig 301)** | Gone. The new `agent_memory` table is a fresh, cleaner design (not a revival). |
| `ai_agents.memory_injection_top_n` (0-50) | **orphan column** | Wired as this layer's per-agent recall `top_n` cap. |
| `prompt_composer` (`graph_facts` + `user_context`, post-cache-boundary, dynamic fingerprint) | **live** | A new `agent_memory` recall block slots in beside graph_facts/user_context, same budgeting + fingerprint discipline. |
| `ai_sessions` (`user_id`, `team_id`, `project_id`, `agent_id`) | **live** | Supplies every scope dimension — no new session plumbing. |
| DBOS scheduled workflows (e.g. the 6h model-health poll) | **live** | `/dream` + `/distill` are scheduled DBOS workflows. |
| Skills system (`skills` table, SkillTool, editor) | **live** | `/distill` writes proposed skills here. |
| RLS dual-policy pattern (`own_*_readable` + `*_write_service_only`) | **established** | Reused verbatim for `agent_memory`. |
| FTS substrate | **only pg_trgm GIN + ILIKE today; no tsvector** | This layer introduces the first `tsvector` + GIN-tsvector recall (ranked). See §6 decision. |

---

## 3. Architecture

Three layers, mirroring MiMo's *raw-trajectory / curated-cache / FTS-index* split, adapted to multi-tenant Postgres:

```
RAW TRAJECTORY  (authoritative, already exists)
  ai_messages · agent_runs · task_tracking          ← full history, never the recall source

CURATED CACHE   (new + existing)
  agent_memory  (NEW)   — compact, scoped, typed memory entries (MiMo "MEMORY.md" rows)
  ai_session_memory     — per-session checkpoint (EXISTING, reused)

RECALL INDEX
  agent_memory.search_tsv (tsvector, GIN) — ranked FTS recall, MANDATORY scope-filtered + RLS

SELF-IMPROVEMENT (DBOS scheduled)
  /dream   — consolidate recent sessions → curated agent_memory (private), dedup/compact, PROMOTION-GATED
  /distill — mine repeated workflows → propose skills
```

Recall flows into `prompt_composer` as a budgeted, top_n-capped block beside the existing Graphiti/Honcho blocks.

---

## 4. Scope & Isolation Model — THE CORE

### 4.1 Scope columns (every row)

`agent_memory` carries the full multi-tenant scope, not MiMo's single `scope_id` (MiMo is single-user):

| column | meaning |
|--------|---------|
| `scope` ENUM(`session`,`user`,`agent_user`,`project`,`team`) | the namespace tier |
| `owner_user_id` UUID **NOT NULL** | who authored it — always set, the isolation anchor |
| `team_id` BIGINT NULL | set when scope ∈ {team, project-in-team} |
| `project_id` BIGINT NULL | set when scope = project |
| `agent_id` UUID NULL | set when scope ∈ {agent_user} |
| `session_id` BIGINT NULL | set when scope = session |
| `visibility` ENUM(`private`,`shared`) | private = owner-only; shared = team/project-readable |
| `kind` ENUM(`fact`,`decision`,`preference`,`procedure`) | for ranking + promotion policy |

**Invariant:** `private` rows are visible to `owner_user_id` only. `shared` rows are visible to members of their `team_id`/`project_id`. There is no other read path.

### 4.2 Read isolation (recall) — mandatory scoped API + RLS

Two independent guards (defense-in-depth):

1. **Typed recall API, never raw MATCH.** Recall takes a `MemoryContext(user_id, team_id, project_id, agent_id, session_id)` derived from the live session — callers cannot issue an unscoped query. The API builds the scope predicate:
   ```
   (owner_user_id = :user_id)                              -- my own (any visibility)
   OR (visibility = 'shared' AND team_id = :team_id)        -- my team's shared
   OR (visibility = 'shared' AND project_id = :project_id)  -- my project's shared
   ```
   combined with the FTS `search_tsv @@ websearch_to_tsquery(:q)` and ranked by `ts_rank * recency * kind_weight`.
2. **RLS** on `agent_memory` (`own_agent_memory_readable`): `owner_user_id = (SELECT auth.uid())` OR (`visibility='shared'` AND team membership via `team_members` EXISTS) — mirrors the dropped `agent_memories` M1.B policy. Even a forgotten WHERE clause cannot cross tenants. Writes: `TO service_role` only.

→ *"别人对话污染 project memory" cannot happen on read*: a private session/user row is never in any other user's predicate, and RLS double-blocks it.

### 4.3 Write/promotion isolation — the real leak vector, gated

The danger is **`/dream` promoting a personal conversation into shared project/team memory**. Policy:

- **Default: everything `/dream` writes is `private`** (scope ∈ {session, user, agent_user}, visibility=`private`). A personal chat never auto-becomes project/team memory.
- **Promotion to `shared` (project/team) requires ALL of:**
  1. **Classification gate** — the entry is `kind ∈ {fact, decision, procedure}` *about the project/codebase*, not `preference`/personal/conversational (the consolidation LLM must classify + justify; low-confidence stays private).
  2. **Authorization gate** — `owner_user_id` has write permission on the target `project_id`/`team_id` (checked against team_members/project membership, not assumed).
  3. **Scrub gate** — PII / personal-identifiers / verbatim private message text stripped before the shared row is written.
- Promotion is **append-only + attributed** (`owner_user_id` retained) and **revocable** (admin/owner can demote a shared row back to private or delete it).

MiMo's `dream.txt` already *advises* "promote to global only when a rule clearly applies across projects" — here it's an **enforced policy**, not prompt etiquette.

---

## 5. Components & Interfaces

1. **Migration** — `agent_memory` table (§4.1 columns + `title`, `body_md`, `when_to_use`, `fingerprint`, `created_at`, `last_recalled_at`, `reinforcement_count`, `status`(active/archived/superseded), `search_tsv` GENERATED tsvector) + GIN index on `search_tsv` + scope b-tree indexes + RLS policies.
2. **Repo + recall service** — `AgentMemoryRepository` (ORM) + `recall(ctx: MemoryContext, query, *, limit) -> list[MemoryHit]` (the only read path; builds the §4.2 predicate). `top_n` defaults from `ai_agents.memory_injection_top_n`. On recall, bump `reinforcement_count`/`last_recalled_at` (MiMo/MemU salience).
3. **prompt_composer wiring** — add `agent_memory_facts: list[str]` to `ComposerInput`, render a post-cache-boundary `<agent_memory>` block, include in `_dynamic_fingerprint` (cache-safety). Recall runs in the existing concurrent recall budget (`asyncio.gather` + `MEMORY_RECALL_BUDGET_S`) beside Graphiti/Honcho.
4. **`/dream` consolidation workflow** — DBOS `@DBOS.scheduled` (default weekly, configurable; mirror MiMo's 7d). Per (user, agent[, project]) with recent activity: pull recent `ai_messages`/`ai_session_memory` → LLM consolidates durable knowledge → upsert curated `agent_memory` (private) with fingerprint dedup → dedup/compact existing → apply §4.3 promotion gate for any `shared` candidate. Idempotent; fingerprint prevents re-writing unchanged knowledge.
5. **`/distill` workflow** — DBOS scheduled (default monthly). Mine recurring multi-step workflows from recent sessions → propose `skills` rows (status `draft`) for admin/user review. Never auto-activates a skill.
6. **Admin surface (optional, later)** — a memory browser/editor + per-agent `memory_injection_top_n` control + promotion review (approve/demote shared rows). Reuses the Settings admin patterns.

---

## 6. Key Decisions

- **tsvector FTS (new) over the established pg_trgm+ILIKE.** Recall needs *ranking* (relevance, MiMo's BM25). `websearch_to_tsquery` + `ts_rank` over a GENERATED `search_tsv` gives ranked recall with stemming; pg_trgm+ILIKE gives fuzzy substring with no ranking. This introduces the codebase's first tsvector column (its own GIN index + migration). Borrow MiMo's *relative score floor* (keep ≥ ratio of top hit) to drop common-word noise. **Alternative considered:** reuse pg_trgm — rejected (no ranking → poor recall quality for the budgeted top_n injection).
- **DB rows, not files.** MiMo uses per-project local `.md` files; a multi-tenant server uses rows (RLS-isolatable, query-scopable). The `body_md` column keeps the human-curatable markdown nature.
- **Reuse `ai_session_memory` as the session checkpoint** — no parallel session structure.
- **Promotion is enforced policy, not prompt advice** (§4.3) — the security crux.
- **Phased** (§7) so each phase ships working, testable software.

---

## 7. Phasing (each phase = its own plan → PR)

- **Phase A — store + recall (read path).** Migration (`agent_memory` + RLS + tsvector GIN) + repo + scoped `recall()` + prompt_composer wiring (behind a flag). No writers yet → recall returns empty; behavior-neutral. Proves the isolation model + injection.
- **Phase B — `/dream` consolidation (write path, private-only).** The scheduled consolidation workflow writing `private` curated memory + dedup. **No promotion yet** (everything private) → zero cross-tenant risk while the write path is proven.
- **Phase C — promotion gate (shared memory).** The §4.3 classification/authorization/scrub gates + the admin promotion-review surface. This is the only phase that can write shared rows; gated + reviewable.
- **Phase D — `/distill` → skills.** Workflow mining → draft skills.

Phase A is independently valuable (scoped recall infra). Promotion (the risk) is isolated to Phase C, behind explicit gates + review.

---

## 8. Open Questions (for the reviewer)

1. **Recall scope default:** should an agent turn recall `private` (owner) memory only, or `private` + the session's `project_id`/`team_id` `shared` memory? (Proposed: both, since the agent acts within a project context — but `shared` is opt-in per deployment.)
2. **`/dream` cadence + cost:** weekly (MiMo) vs on-demand? Consolidation is an LLM job per active (user,agent) — cost scales with active agents. (Proposed: weekly scheduled + a manual admin trigger.)
3. **Promotion authority:** auto-promote on the gates passing, or always require human review (admin approves shared rows)? (Proposed: Phase C ships review-required; auto-promote is a later toggle.)
4. **Does mediahub want agent memory at all yet**, given the agent runtime's current usage level — or is this premature until agent traffic grows? (Honest YAGNI check.)
