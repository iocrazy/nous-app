# Agent Memory Phase C1 — Promotion Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a *private* agent_memory row become *shared* (team/project-readable) only after passing an authorization + classification + scrub gate and an explicit admin approval — never automatically, never across tenants.

**Architecture:** During `/dream` consolidation, every PRIVATE row written from a **team** or **project** context is evaluated: an **authorization gate** (DB: owner is a member of the target team; for project scope, the project's `team_id` is backfilled) then a **classification+scrub gate** (one LLM call returning shareable?/confidence/justification + a PII-scrubbed body). If both pass, a **pending `agent_memory_promotions`** proposal is created — the memory stays private. An admin reviews pending proposals and **approves** (a service-role transaction flips the memory row `visibility→'shared'`, sets `team_id`/`project_id`, and replaces `body_md` with the scrubbed text) or **rejects** (row untouched). A shared row is **revocable** via demote (flip back to private). Recall of shared rows is NOT activated here — `MemoryContext` still passes `team_ids=()`; wiring shared recall is Phase C2.

**Tech Stack:** FastAPI + SQLAlchemy Core (asyncpg) + DBOS workflows + Postgres RLS. Backend lint = black + isort + flake8. loguru `{}`/f-strings (never `%s`).

## Global Constraints

- **Cross-tenant isolation is the first-class invariant.** A personal-context (`scope='agent_user'`) memory must NEVER produce a promotion proposal and can never become shared. Only `scope ∈ {team, project}` rows are eligible.
- **No auto-promotion.** Passing the gates only creates a `pending` proposal. Only an explicit admin approve flips a row to `shared`. (Auto-promote is a deliberately deferred later toggle.)
- **Authorization is checked against real membership, never assumed.** `team` scope → owner must be in `team_members` for that `team_id`. `project` scope → the `projects` row must have a non-NULL `team_id` AND the owner must be a member of that team; the proposal's `target_team_id` = the project's `team_id` (backfill). If a project has NULL `team_id`, it is NOT promotable (no team to share with) → no proposal.
- **The `agent_memory_shared_requires_team_id` CHECK (mig 323) must always hold:** any row flipped to `shared` MUST have a non-NULL `team_id`. The approve transaction sets `team_id = target_team_id` (always non-NULL by construction) in the same UPDATE that sets `visibility='shared'`.
- **All agent_memory + agent_memory_promotions writes run as service-role** (Phase B+ pattern). There is no authenticated-user UPDATE policy; the admin endpoints execute via the service-role `write_scope()`.
- **Scrub before share.** The shared row's `body_md` is the LLM-scrubbed text stored on the proposal at proposal time (so the admin reviews exactly what will be shared), not the original private body.
- **No `@DBOS.step` dispatches a workflow.** Proposal creation happens inline inside the existing plain-async `_consolidate_context` (already non-DBOS), reusing the C0 pattern.
- **Exact identifiers:** table `public.agent_memory_promotions`; memory FK `memory_id BIGINT REFERENCES public.agent_memory(id) ON DELETE CASCADE` (agent_memory.id is BIGINT snowflake). Proposal status enum: `pending | approved | rejected`. Migration number: **325**.
- **Recall stays inert.** Do not change `MemoryContext`, `recall`, or `recall_rows` in C1. Shared rows become readable by RLS to team members, but the agent-turn recall path still passes `team_ids=()` until C2.

---

### Task 1: Migration 325 + promotion repository

**Files:**
- Create: `supabase/migrations/325_agent_memory_promotions.sql`
- Create: `backend/app/repositories/agent_memory_promotion_repository.py`
- Test: `backend/tests/memory/test_agent_memory_promotion_repository.py`

**Interfaces:**
- Produces (consumed by Tasks 3 & 4):
  - `async insert_proposal(*, memory_id: int, proposed_scope: str, target_team_id: int, target_project_id: Optional[int], classification_kind: str, confidence: float, justification: str, scrubbed_body_md: str) -> bool` — service-role INSERT; never raises (returns False on error); idempotent on the partial-unique `(memory_id) WHERE status='pending'` (ON CONFLICT DO NOTHING).
  - `async list_proposals(*, status: str = "pending", limit: int = 100) -> List[Dict[str, Any]]` — joins `agent_memory` for `title`, `owner_user_id`, original `body_md`, `scope`; ordered `created_at DESC`.
  - `async approve_proposal(*, proposal_id: int, reviewer_id: str) -> bool` — single service-role transaction: (a) `UPDATE agent_memory SET visibility='shared', team_id=p.target_team_id, project_id=p.target_project_id, body_md=p.scrubbed_body_md, updated_at=now() WHERE id=p.memory_id`; (b) `UPDATE agent_memory_promotions SET status='approved', reviewed_by=:reviewer_id, reviewed_at=now() WHERE id=:proposal_id AND status='pending'`. Returns False if the proposal is not pending or any step fails (rolled back). Never raises.
  - `async reject_proposal(*, proposal_id: int, reviewer_id: str) -> bool` — `UPDATE ... SET status='rejected', reviewed_by, reviewed_at WHERE id AND status='pending'`. Row untouched.
  - `async demote_memory(*, memory_id: int) -> bool` — `UPDATE agent_memory SET visibility='private', updated_at=now() WHERE id=:memory_id`. (Revoke; team_id left as-is — private rows may keep team_id, the CHECK only requires team_id when shared.)

**Migration SQL (`325_agent_memory_promotions.sql`):**

```sql
-- supabase/migrations/325_agent_memory_promotions.sql
-- Agent Memory Phase C1 — promotion review queue. A pending proposal to flip a
-- private team/project memory row to shared. Admin-reviewed; nothing here auto-shares.

CREATE TABLE IF NOT EXISTS public.agent_memory_promotions (
    id                  BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    memory_id           BIGINT NOT NULL REFERENCES public.agent_memory(id) ON DELETE CASCADE,
    proposed_scope      TEXT NOT NULL CHECK (proposed_scope IN ('team','project')),
    target_team_id      BIGINT NOT NULL,            -- always set (project's team backfilled)
    target_project_id   BIGINT,                     -- set for project scope only
    classification_kind TEXT NOT NULL DEFAULT 'fact',
    confidence          REAL NOT NULL DEFAULT 0,
    justification       TEXT NOT NULL DEFAULT '',
    scrubbed_body_md    TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','approved','rejected')),
    reviewed_by         UUID,
    reviewed_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- At most one OPEN proposal per memory row.
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_memory_promotions_pending
    ON public.agent_memory_promotions (memory_id) WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_agent_memory_promotions_status
    ON public.agent_memory_promotions (status, created_at DESC);

ALTER TABLE public.agent_memory_promotions ENABLE ROW LEVEL SECURITY;

-- Service-role only — admin endpoints run as service role, like agent_memory writes.
DROP POLICY IF EXISTS agent_memory_promotions_service_only ON public.agent_memory_promotions;
CREATE POLICY agent_memory_promotions_service_only ON public.agent_memory_promotions
    FOR ALL TO service_role USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
```

- [ ] **Step 1: Write the migration file** exactly as above.

- [ ] **Step 2: Write the repository.** Mirror `agent_memory_repository.py` style: module docstring, `from app.db.session import write_scope`, `text()` SQL constants, each function `async with write_scope() as session:` + try/except logging via loguru `{}`/f-strings, never raises. `approve_proposal` does both UPDATEs inside ONE `write_scope()` block (single transaction). Use a CTE or two `session.execute` calls in the same `async with` so they share the transaction; fetch the proposal row first (`SELECT ... FROM agent_memory_promotions WHERE id=:id AND status='pending' FOR UPDATE`), and if no row, return False without mutating.

- [ ] **Step 3: Write unit tests** (`tests/memory/test_agent_memory_promotion_repository.py`). Follow the Phase B repo test style: patch `write_scope` to yield a mock session whose `execute` returns canned results; assert the SQL params bound (e.g. `insert_proposal` binds `memory_id`, `target_team_id`, `proposed_scope`; `approve_proposal` issues the agent_memory UPDATE with `visibility='shared'` semantics and the promotions UPDATE with `status='approved'` and binds `reviewer_id`; `approve_proposal` returns False when the pending-select yields no row; `demote_memory` binds `visibility='private'`). At least: 2 tests for insert (success + error→False), 3 for approve (success flips both, not-pending→False, error→rolled-back False), 1 reject, 1 demote, 1 list. Run `cd backend && uv run pytest tests/memory/test_agent_memory_promotion_repository.py -v`.

- [ ] **Step 4: Lint** changed files (black/isort/flake8). **Commit:** `feat(memory): promotion review-queue table + repository (C1)`

---

### Task 2: Promotion gate (authorization + classification/scrub)

**Files:**
- Create: `backend/app/services/ai/memory/promotion_gate.py` (pure logic — prompt/parse/orchestrate, no DB/LLM import)
- Create: `backend/app/services/ai/memory/promotion_evaluator.py` (the governed LLM callable)
- Modify: `backend/app/repositories/agent_memory_promotion_repository.py` (add `resolve_promotion_target`)
- Modify: `backend/tests/test_run_recorder_coverage.py` (allowlist the new evaluator)
- Test: `backend/tests/memory/test_promotion_gate.py`

**Interfaces:**
- Consumes: `MemoryDraft` (from `agent_memory_consolidation.py`: `.title/.body_md/.when_to_use/.kind`).
- Produces (consumed by Task 3):
  - `PromotionVerdict` frozen dataclass: `shareable: bool`, `confidence: float`, `justification: str`, `scrubbed_body_md: str`.
  - `build_promotion_prompt(*, draft: MemoryDraft, scope: str) -> str` — instructs the model to decide if this entry is durable **project/team knowledge** (not personal/preference/PII), and to return JSON `{"shareable": bool, "confidence": 0..1, "justification": str, "scrubbed_body_md": str}` where `scrubbed_body_md` strips names/emails/tokens/verbatim-private text.
  - `parse_promotion_output(text: str) -> Optional[PromotionVerdict]` — defensive JSON parse; returns None on any malformed output (fail-closed → no proposal).
  - `async evaluate_promotion(*, draft, scope, evaluator) -> Optional[PromotionVerdict]` — builds prompt, awaits `evaluator(prompt)`, parses; returns the verdict only if `shareable is True AND confidence >= _MIN_CONFIDENCE AND draft.kind in {'fact','decision','procedure'}` (preference kind is never shareable); else None. Never raises (caught → None).
  - `_MIN_CONFIDENCE = 0.7` module constant.
  - In the repository: `async resolve_promotion_target(*, owner_user_id: str, scope: str, team_id: Optional[int], project_id: Optional[int]) -> Optional[Tuple[int, Optional[int]]]` — returns `(target_team_id, target_project_id)` if authorized, else None:
    - `scope == 'team'`: require `team_id` non-NULL AND `EXISTS (SELECT 1 FROM team_members WHERE team_id=:team_id AND user_id=:owner)`. Return `(team_id, None)`.
    - `scope == 'project'`: require `project_id` non-NULL; look up `SELECT team_id FROM projects WHERE id=:project_id`; if that team_id is NULL → None; require owner ∈ team_members for that team_id; return `(project_team_id, project_id)`.
    - any other scope → None. Never raises (→ None).

**Promotion evaluator (`promotion_evaluator.py`):** mirror `agent_memory_consolidator.py::default_consolidator` — a governed, cheap-model chat call returning the raw string. Same governance/model-resolution path. Add its path to `ALLOWED_BYPASS_PATHS` in `test_run_recorder_coverage.py` with justification: `"services/ai/memory/promotion_evaluator.py": "agent-memory promotion classification/scrub LLM, scheduled fire-and-forget"`.

- [ ] **Step 1: Write the failing tests** (`tests/memory/test_promotion_gate.py`):
  - `evaluate_promotion` returns a verdict when the fake evaluator emits `{"shareable":true,"confidence":0.9,...}` and kind='fact'.
  - returns None when `shareable=false`.
  - returns None when `confidence < 0.7`.
  - returns None when `draft.kind='preference'` even if shareable=true (kind gate).
  - returns None when the evaluator emits malformed JSON (fail-closed).
  - `parse_promotion_output` handles code fences + surrounding prose.
  - (resolve_promotion_target is DB-bound: cover it with a session-mock test asserting the team-scope EXISTS query is issued and project-scope reads `projects.team_id` then checks membership; unauthorized → None.)

- [ ] **Step 2: Run tests, verify they fail** (`pytest tests/memory/test_promotion_gate.py -v`).

- [ ] **Step 3: Implement** `promotion_gate.py`, `promotion_evaluator.py`, and `resolve_promotion_target`. Keep `promotion_gate.py` LLM/DB-free (caller supplies `evaluator`). Fail-closed everywhere.

- [ ] **Step 4: Run tests + the RunRecorder guard** (`pytest tests/memory/test_promotion_gate.py tests/test_run_recorder_coverage.py -v`).

- [ ] **Step 5: Lint. Commit:** `feat(memory): promotion authorization + classification/scrub gate (C1)`

---

### Task 3: Wire proposal creation into /dream consolidation

**Files:**
- Modify: `backend/app/workflows/consolidate_agent_memory.py` (`_consolidate_context` + the two return dicts + `_consolidate_pair` sum)
- Modify: `backend/app/api/admin/settings_router.py` + `backend/app/schemas/admin.py` (`ConsolidateResponse.proposed`)
- Test: `backend/tests/memory/test_consolidate_workflow.py` (extend)

**Interfaces:**
- Consumes: `resolve_promotion_target`, `evaluate_promotion`, `insert_proposal` (Tasks 1-2), `default_promotion_evaluator`.
- Produces: `_consolidate_context` return dict gains `"proposed": int`; `_consolidate_pair` sums it; `ConsolidateResponse.proposed: int = 0`.

- [ ] **Step 1: Write the failing test.** In `test_consolidate_workflow.py`, add a test that for a **team-scope** context (`team_id=10, project_id=None`) where `write_memory_row` succeeds, `resolve_promotion_target` returns `(10, None)`, and `evaluate_promotion` returns a shareable verdict, `_consolidate_context` calls `insert_proposal` once (with `memory_id` of the written row, `proposed_scope='team'`, `target_team_id=10`, `scrubbed_body_md` from the verdict) and returns `proposed=1`. Add a second test: a **personal-scope** context (`team_id=None, project_id=None`) NEVER calls `resolve_promotion_target`/`insert_proposal` and returns `proposed=0` (isolation invariant). Mock `resolve_promotion_target`/`evaluate_promotion`/`insert_proposal` as AsyncMock.

  > **Note for the implementer:** `write_memory_row` (Task-1 Phase B) returns `bool`, not the new row id. To get `memory_id` for the proposal you must obtain the id of the row just written. Add an `INSERT ... RETURNING id` path: introduce `write_memory_row_returning_id(...) -> Optional[int]` in `agent_memory_repository.py` (same INSERT + `RETURNING id`, returns the id or None) and use it in `_consolidate_context` ONLY for team/project scope (personal scope may keep using `write_memory_row`, or use the returning variant and ignore the id). Keep the existing `write_memory_row` for backward-compat (its tests must stay green). Cover the new function with a repository unit test (binds same params, returns the RETURNING id).

- [ ] **Step 2: Run test, verify it fails.**

- [ ] **Step 3: Implement.** In `_consolidate_context`, after a successful write for `scope in {'team','project'}`: call `resolve_promotion_target(owner_user_id=user_id, scope=scope, team_id=team_id, project_id=project_id)`; if None → skip (no proposal). Else `evaluate_promotion(draft=draft, scope=scope, evaluator=default_promotion_evaluator)`; if None → skip. Else `insert_proposal(memory_id=<written id>, proposed_scope=scope, target_team_id=<t>, target_project_id=<p>, classification_kind=draft.kind, confidence=verdict.confidence, justification=verdict.justification, scrubbed_body_md=verdict.scrubbed_body_md)`; increment `proposed`. Wrap each candidate's gate in its own try/except (a gate failure must not abort the write loop). `scope=='agent_user'` path must never enter this block. Thread `proposed` into the return dict, `_consolidate_pair` sum, and `ConsolidateResponse`.

- [ ] **Step 4: Run the full workflow test file + repository test.** Confirm personal-scope isolation test passes (no gate calls).

- [ ] **Step 5: Lint. Commit:** `feat(memory): create promotion proposals for team/project memories in /dream (C1)`

---

### Task 4: Admin review endpoints

**Files:**
- Modify: `backend/app/api/admin/settings_router.py` (4 routes)
- Modify: `backend/app/schemas/admin.py` (response/request models)
- Test: `backend/tests/test_admin_memory_promotions.py`

**Interfaces:**
- Consumes: `list_proposals`, `approve_proposal`, `reject_proposal`, `demote_memory` (Task 1), `AdminAuthDep`.
- Routes (all `AdminAuthDep`, prefix already `/api/v1/admin` via the router):
  - `GET /memory/promotions?status=pending` → `PromotionListResponse(items: List[PromotionItem])`. `PromotionItem`: `id: int`, `memory_id: int`, `proposed_scope: str`, `target_team_id: int`, `target_project_id: Optional[int]`, `title: str`, `owner_user_id: str`, `original_body_md: str`, `scrubbed_body_md: str`, `classification_kind: str`, `confidence: float`, `justification: str`, `status: str`, `created_at: str`.
  - `POST /memory/promotions/{proposal_id}/approve` → `{"approved": bool}` (calls `approve_proposal(proposal_id=..., reviewer_id=auth.user_id)`; 404 if returns False because not pending/not found — distinguish by a prior existence check or return `{"approved": false}`; keep simple: return `{"approved": result}`).
  - `POST /memory/promotions/{proposal_id}/reject` → `{"rejected": bool}`.
  - `POST /memory/{memory_id}/demote` → `{"demoted": bool}` (revoke a shared row).
  - Each handler logs via loguru `{}` with `auth.user_id`.

- [ ] **Step 1: Write failing tests** (`tests/test_admin_memory_promotions.py`) following `test_admin_ai_governance_settings.py`: patch the repository functions (e.g. `patch("app.api.admin.settings_router.list_proposals", AsyncMock(return_value=[...]))`), pass a `MagicMock` auth with `.user_id`, call each handler directly, assert the repo function is called with the right args and the response shape. Cover: list maps rows→items; approve forwards `reviewer_id=auth.user_id` and returns `{"approved": True}`; reject; demote.

- [ ] **Step 2: Run tests, verify they fail.**

- [ ] **Step 3: Implement** the 4 routes + schemas. Import the repo functions at module top (or lazy as the existing consolidate route does). Keep handlers thin.

- [ ] **Step 4: Run the test file.**

- [ ] **Step 5: Lint. Commit:** `feat(memory): admin promotion review endpoints — list/approve/reject/demote (C1)`

---

### Task 5: Regression + dev-DB validation + PR

- [ ] **Step 1:** `cd backend && uv run pytest tests/ -k "memory or consolidat or promotion or admin" -q` → all pass.
- [ ] **Step 2:** lint full changed set; `uv run python -c "import app.workflows._scheduled_bundle"` import smoke; `uv run python -c "import app.api.admin.settings_router"` import smoke.
- [ ] **Step 3 (controller — needs the DB):** apply `325_agent_memory_promotions.sql` to dev DB, then validate the real flow against dev: insert a private `scope='project'` agent_memory row whose project has a team; call `resolve_promotion_target` (asserts backfill returns the project's team_id); `insert_proposal`; `approve_proposal` and confirm the agent_memory row is now `visibility='shared'`, `team_id` set (non-NULL, satisfies the CHECK), `body_md` = scrubbed text; confirm a second `approve_proposal` returns False (already approved); `demote_memory` flips back to private. Clean up. (Then apply 325 to prod before merge, per the project's "validate apply before merge" discipline.)
- [ ] **Step 4:** PR:

```bash
git push -u origin feature/agent-memory-c1-promotion-gate
gh pr create --base master --head feature/agent-memory-c1-promotion-gate \
  --title "feat(memory): promotion gate — Phase C1 (review-required share)" \
  --body "C1 of Phase C. /dream now proposes (never auto-applies) promoting a team/project private memory to shared, behind an authorization gate (real team_members membership; project's team_id backfilled) + a classification/scrub LLM gate. Admin reviews pending proposals and approves (service-role txn flips visibility→shared, sets team_id, writes scrubbed body) / rejects / demotes. Personal-context memories can never be proposed or shared. Shared recall stays inert (wired in C2). Migration 325 adds the agent_memory_promotions review queue (service-role RLS)."
```

---

## Self-Review

**Spec coverage (§4.3 gates):** classification gate → Task 2 `evaluate_promotion` (kind∈{fact,decision,procedure} + LLM shareable + confidence≥0.7) ✓; authorization gate → Task 2 `resolve_promotion_target` (team_members membership + project→team backfill) ✓; scrub gate → Task 2 verdict `scrubbed_body_md`, written on approve (Task 1 `approve_proposal`) ✓; review-required → Task 1 pending status + Task 4 admin approve ✓; append-only + revocable → flip visibility (no delete) + Task 1 `demote_memory` ✓; isolation (personal never shares) → Task 3 scope gate + Global Constraints ✓.

**Placeholder scan:** none — SQL, signatures, gate order, and the approve transaction are concrete.

**Type consistency:** `memory_id`/`target_team_id`/`target_project_id` are BIGINT/int throughout (agent_memory.id is BIGINT snowflake; team/project ids BIGINT). `resolve_promotion_target` returns `(int, Optional[int])` consumed verbatim by `insert_proposal(target_team_id, target_project_id)`. `PromotionVerdict.scrubbed_body_md` (Task 2) → `insert_proposal(scrubbed_body_md=)` (Task 1) → `approve_proposal` writes it to `agent_memory.body_md` (Task 1). `ConsolidateResponse.proposed` (Task 3) matches the `_consolidate_context` return key. The `shared_requires_team_id` CHECK is satisfied because `approve_proposal` always sets a non-NULL `target_team_id`.
