# Phase 5b — Project SOP Stages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give projects a lifecycle SOP stage (planning → script → storyboard → generation → review → delivery), with a recommended-tool grid per stage and an append-only transition history.

**Architecture:** A global, seeded `project_stages` catalog (BIGINT snowflake PK); `projects.current_stage_id` FK; an append-only `project_stage_history` audit table. `tools_recommended` (JSONB slug array) on the catalog drives a navigational tool grid in the project header. All changes are additive (`ADD COLUMN IF NOT EXISTS`, new tables/routes) — zero blast radius on existing functionality.

**Tech Stack:** Supabase migration (snowflake ids, RLS) + FastAPI Router→Repository (SQLAlchemy Core raw SQL via `db_engine`, mirroring `project_style_profile`) + React Query + i18n.

---

## OPEN QUESTIONS — resolve before Task 1

1. **[BLOCKER — type contradiction] What does `current_stage_id` reference?**
   Source plan says `current_stage_id BIGINT FK → workflow_nodes.id`, but `workflow_nodes.id` is **UUID** (`047_project_workflow.sql:21`), while `projects.id` is **BIGINT** snowflake. A BIGINT column cannot FK a UUID PK — the plan is internally inconsistent. Also `workflow_nodes` are per-team Kanban columns (`node_type IN ('status','milestone','gate')`), not lifecycle SOP stages.
   - **Recommended default: Option B — new global `project_stages` catalog table** (BIGINT snowflake PK), seeded with canonical SOP stages; `projects.current_stage_id BIGINT FK → project_stages.id`. Honors the stated BIGINT type, avoids overloading Kanban `workflow_nodes`, gives `tools_recommended` a natural home. (Option A = literal `workflow_nodes` FK but switch column to UUID — rejected: wrong semantics + per-workflow not lifecycle.)
2. **Are stages global, per-team, or per-project-template?** → **Default: global + seeded** (one canonical pipeline in v1). Per-team customization is a clean follow-up (`team_id NULL` = global).
3. **Where does `tools_recommended_per_stage` live?** → **Default: a `tools_recommended JSONB` column on `project_stages`.** Per-project overrides out of scope v1.
4. **What is the tool catalog source?** There is **no general user-facing tool registry today** (`capability_profile` governs AI-agent tool auth, not UI menus). → **Default: hand-listed slugs** stored as a JSONB array; frontend resolves slugs against a new `frontend/features/projects/stageTools.ts` `TOOL_CATALOG` constant mapping each slug to an existing `ProjectTab` (files/scripts/storyboard/output/tasks). Grid = navigational shortcut, not a new execution surface.
5. **Where does the stage UI live?** → **Default: a stage stepper in the project-detail header + a per-stage tool grid.** No new top-level nav in v1 (`NAV_SECTIONS` untouched).

Minor (defaulted silently): history append-only (no edit/delete); same-stage transition = 200 no-op, no history row; `current_stage_id` nullable, existing projects stay NULL until first transition.

---

## Context anchors (real files to copy)

| Concern | Anchor |
|---|---|
| New per-project sub-feature, end-to-end | `project_style_profile` (Phase 4 M8): mig `283_project_style_profile.sql`, repo `backend/app/repositories/project_style_profile_repository.py`, schema `backend/app/schemas/projects.py:256`, router `backend/app/api/projects_router.py:150-201`, tests `backend/tests/test_project_style_profile.py` |
| Sub-resource PUT shape | `PUT /projects/{id}/style-profile` (`projects_router.py:174`), guard `Depends(verify_project_write_access)` (`backend/app/core/scope_guards.py:54`) |
| Repo idiom | SQLAlchemy-**Core raw SQL** via `from app.db import engine as db_engine` → `db_engine.fetch_one`, `db_engine.execute_returning_one` |
| ID generation | `BIGINT PRIMARY KEY DEFAULT generate_snowflake_id()` (`280_canvas_core_schema.sql:46`) |
| `updated_at` trigger | `update_updated_at_column()` (used in `283:20`) |
| RLS member policy | owner-or-team-member subquery (`283:27-47`) |
| Frontend service idiom | `frontend/services/projectsService.ts` `apiClient.put<Envelope<T>>(...)` |
| Project detail surface | `frontend/pages/ProjectsPage.tsx`, `ProjectTab` type `frontend/types.ts:862` |
| i18n | `frontend/public/locales/{en,zh}.json` `projects` block (`en.json:1017`) |
| Latest migration | **289** (`289_resources_gen_prompt_zh.sql`) → **next is 290** |

> **ORM note:** the most-recent project sub-feature (`project_style_profile`, same plan, June 2026) deliberately uses **Core raw SQL** ("new table, no legacy REST path, no USE_ORM_* dual-track"). This plan follows that precedent. Do not mix idioms within one repo.

Test runner: `cd backend && uv run pytest`. Migrations applied via the numeric-prefix Supabase tooling already used for 280–289.

---

## Task 1 — Migration `290_project_sop_stages.sql`

```sql
-- 290_project_sop_stages.sql
-- Phase 5b (Canvas+AI plan): project lifecycle SOP stages.
-- Global seeded catalog (project_stages); projects.current_stage_id points at the
-- active stage; project_stage_history is append-only transition audit.
-- NOTE: source plan said "FK -> workflow_nodes.id BIGINT" but workflow_nodes.id is
-- UUID + Kanban semantics; resolved to a dedicated BIGINT catalog (OPEN QUESTION #1, Option B).

CREATE TABLE IF NOT EXISTS project_stages (
    id                BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    slug              TEXT NOT NULL UNIQUE,
    name              TEXT NOT NULL,
    sort_order        INT  NOT NULL DEFAULT 0,
    tools_recommended JSONB NOT NULL DEFAULT '[]',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DROP TRIGGER IF EXISTS trg_project_stages_updated_at ON project_stages;
CREATE TRIGGER trg_project_stages_updated_at
    BEFORE UPDATE ON project_stages
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

INSERT INTO project_stages (slug, name, sort_order, tools_recommended) VALUES
    ('planning',   'Planning',   10, '["files","tasks"]'),
    ('script',     'Script',     20, '["scripts","files"]'),
    ('storyboard', 'Storyboard', 30, '["storyboard","scripts"]'),
    ('generation', 'Generation', 40, '["storyboard","output"]'),
    ('review',     'Review',     50, '["output","tasks"]'),
    ('delivery',   'Delivery',   60, '["output","files"]')
ON CONFLICT (slug) DO NOTHING;

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS current_stage_id BIGINT
    REFERENCES project_stages(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_projects_current_stage
    ON projects (current_stage_id) WHERE current_stage_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS project_stage_history (
    id              BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    project_id      BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    stage_id        BIGINT NOT NULL REFERENCES project_stages(id),
    entered_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    exited_at       TIMESTAMPTZ,
    transitioned_by UUID REFERENCES auth.users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_project_stage_history_project
    ON project_stage_history (project_id, entered_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_project_stage_history_open
    ON project_stage_history (project_id) WHERE exited_at IS NULL;

ALTER TABLE project_stages ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS project_stages_read ON project_stages;
CREATE POLICY project_stages_read ON project_stages
    FOR SELECT USING (auth.role() = 'authenticated' OR auth.role() = 'service_role');
DROP POLICY IF EXISTS project_stages_service_role ON project_stages;
CREATE POLICY project_stages_service_role ON project_stages
    FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

ALTER TABLE project_stage_history ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS project_stage_history_member_all ON project_stage_history;
CREATE POLICY project_stage_history_member_all ON project_stage_history
    FOR ALL
    USING (project_id IN (SELECT id FROM projects WHERE owner_id = auth.uid()
               OR team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())))
    WITH CHECK (project_id IN (SELECT id FROM projects WHERE owner_id = auth.uid()
               OR team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())));
DROP POLICY IF EXISTS project_stage_history_service_role ON project_stage_history;
CREATE POLICY project_stage_history_service_role ON project_stage_history
    FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
```

## Task 2 — Pydantic schema
In `backend/app/schemas/projects.py` (next to `StyleProfileUpdate:256`):
```python
class CurrentStageUpdate(BaseModel):
    """Request body for PUT /projects/{id}/current_stage."""
    stage_id: int = Field(..., description="Target project_stages.id")
```
Add to the router import list (`projects_router.py:18-35`).

## Task 3 — Repository (TDD)
Write `backend/tests/test_project_sop_stages.py` first (copy `engine_calls` monkeypatch from `test_project_style_profile.py:58`). Assert: `list_catalog()` ordered SELECT; `set_current_stage` closes open history row + inserts new open row + updates `projects.current_stage_id` as one transaction; same-stage = no-op; `_serialize` ISO dates + str ids.
Then `backend/app/repositories/project_stages_repository.py` mirroring `project_style_profile_repository.py`. Methods: `list_catalog`, `get_current` (JOIN projects→project_stages), `history`, `set_current_stage(project_id, stage_id, user_id)`. `set_current_stage` in one transaction: `SELECT current_stage_id ... FOR UPDATE` (equal → no-op), `UPDATE ... SET exited_at=NOW() WHERE exited_at IS NULL`, `INSERT` new history row, `UPDATE projects SET current_stage_id`. FK violation → `ValueError`.

## Task 4 — Endpoints (TDD)
Add to `projects_router.py` after style-profile block (`:201`), same guard:
```python
@router.get("/stages/catalog")
async def list_stage_catalog(auth: AuthDep):
    from app.repositories.project_stages_repository import get_project_stages_repository
    return {"success": True, "data": await get_project_stages_repository().list_catalog()}

@router.get("/{project_id}/current_stage")
async def get_current_stage(project_id: str, auth: AuthDep,
        _project_guard: None = Depends(verify_project_write_access)):
    from app.repositories.project_stages_repository import get_project_stages_repository
    try:
        return {"success": True, "data": await get_project_stages_repository().get_current(int(project_id))}
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid project id")

@router.put("/{project_id}/current_stage")
async def put_current_stage(project_id: str, data: CurrentStageUpdate, auth: AuthDep,
        _project_guard: None = Depends(verify_project_write_access)):
    from app.repositories.project_stages_repository import get_project_stages_repository
    try:
        stage = await get_project_stages_repository().set_current_stage(int(project_id), data.stage_id, auth.user_id)
        return {"success": True, "data": stage}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to set stage for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to set current stage")
```
Endpoint test (FastAPI TestClient): 200 + envelope, 422 bad stage_id, second PUT closes first history row.

## Task 5 — Frontend service + types
`frontend/services/projectsService.ts` (copy `apiClient.put<Envelope<T>>` from `updateProject:52`): `fetchStageCatalog`, `fetchCurrentStage(projectId)`, `setCurrentStage(projectId, stageId)`. Add `ProjectStage` interface to `frontend/types.ts` (`id/slug/name/sort_order/tools_recommended:string[]`). Add `projectsService.test.ts` cases asserting URLs.

## Task 6 — Tool catalog constant
New `frontend/features/projects/stageTools.ts` — `TOOL_CATALOG: Record<string, ToolDef>` (`slug/labelKey/tab/icon`), icons matching lucide imports in `ProjectNavSidebar.tsx:43-49`. Map slug → existing `ProjectTab`.

## Task 7 — UI components
`frontend/components/project/StageSelector.tsx` (horizontal stepper; click → `setCurrentStage` + invalidate query; gated on write access) and `StageToolGrid.tsx` (read `currentStage.tools_recommended`, map slug → `TOOL_CATALOG`, button → `setActiveTab(tab)`; skip unknown slugs). Wire into `ProjectsPage.tsx` header above `activeTab` switch (`:208`), gated on `selectedProject`. React Query: catalog once + current stage per project.

## Task 8 — i18n
Add `projects.stages.*` (title/selectStage/currentStage/recommendedTools + per-slug names) and `projects.tools.*` to `en.json` (`:1017`) and parallel `zh.json`. `StageSelector` prefers `projects.stages.<slug>` key over catalog `name` so Chinese UI isn't stuck on English seed names.

## Task 9 — Verification
```bash
cd backend && uv run pytest tests/test_project_sop_stages.py -q
cd frontend && npm run test -- projectsService
cd frontend && npx tsc --noEmit
```
Manual: create project → PUT each stage in sequence → history shows one open + closed prior rows; grid switches tabs.

## Sequencing
1→2→3→4 (backend, strictly ordered, each TDD). 5/6/7/8 (frontend) depend only on 4's contract, parallel once endpoints stubbed. 9 last.

## Risk note
Dominant risk = OQ#1 `current_stage_id` reference contradiction (BIGINT FK to a UUID Kanban table). Building literally is impossible; building correctly needs the global-catalog vs reuse-workflow_nodes decision the 4-line spec never made. Secondary: `set_current_stage` transactional integrity — the partial unique index `uq_project_stage_history_open` is the real guard against duplicate open rows under concurrent PUTs, must ship in the same migration. The hand-maintained tool catalog (Task 6) will drift from real capabilities unless slugs stay in sync with `ProjectTab`. All changes additive → minimal blast radius on the ORM-2 work.
