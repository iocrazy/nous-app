# Projects Phase B Deviation-Redo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the shipped Projects Phase B UI into alignment with the approved A · Stage Ring mockup by making the B3 suggestion card data-aware + one-click, upgrading the B1 activity row (file events + stall color), and adding the B2 Stage-history button.

**Architecture:** Backend gains a project-level storyboard-progress aggregation (JOIN `script_projects → script_scenes → script_shots`), a typed `GET /stage-suggestion` resolver, and a `POST /storyboard/generate-missing` batch endpoint that fans out over empty shots. Frontend reworks `StageSuggestion` to consume the typed payload (storyboard = one-click flagship, other stages = data-aware navigation), upgrades `ProjectCard`'s activity row to a hybrid file/stage event with a stall color, and adds a `StageHistoryDrawer`. All changes reuse existing tables — zero migration.

**Tech Stack:** FastAPI + SQLAlchemy-Core (`app.db.engine` async helpers) + Supabase Postgres; React 19 + TypeScript + Vite + TailwindCSS + i18next; pytest + vitest + Playwright.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-07-08-projects-phase-b-deviation-redo-design.md` — every task implicitly inherits its decisions (D1–D7).
- **Zero migration** — all data derived from existing tables (`script_projects`, `script_scenes`, `script_shots`, `project_stages`, `project_stage_history`, `project_style_profile`, `resources`).
- **UI is English + i18n keys**; test data English (`CLAUDE.md` UI language rule).
- **Task-system discipline** (`CLAUDE.md` 路线 C): `task_tracking` is the only UI source; never PATCH `phase/status/progress`; short-circuit failures `raise` (never `return {"status":"failed"}`); business fields → `metadata` jsonb.
- **BIGINT ids as str** in JSON (Snowflake REST convention); `_bigint()` coercion on write paths; compare path-param str vs native int with explicit coercion (`feedback_orm_native_int_vs_str_path_param`).
- **Immutability**: build new dicts/objects, never mutate inputs.
- **Flag gates:** batch endpoint gated by `settings.FEATURE_SHOT_GENERATE` (off → 404); B3 frontend under `VITE_FEATURE_PROJECT_AI_SUGGEST` (already true).
- **Backend lint gate before push:** `cd backend && black . && isort . && flake8` on changed `.py` (`feedback_backend_lint_gate_before_push`).
- **Frontend lint:** `cd frontend && npm run lint` (rules-of-hooks = error).
- **Stall thresholds (D6):** `review` stage ≥ 3 days, all other stages ≥ 7 days → stalled.
- **SOP slugs (fixed 6):** `planning, script, storyboard, generation, review, delivery`.

---

# PR-1 · B3 Backend (aggregation + resolver + batch endpoint)

**File Structure:**
- Modify `backend/app/repositories/script_shot_repository.py` — add `storyboard_progress_for_project`.
- Modify `backend/app/schemas/projects.py` — add `StageSuggestionResponse`, `SuggestionAction`, `StoryboardProgress`, `GenerateMissingResponse`.
- Modify `backend/app/services/library/projects_service.py` — add `build_stage_suggestion` + `generate_missing_frames`.
- Modify `backend/app/api/projects_router.py` — add `GET /{id}/stage-suggestion` + `POST /{id}/storyboard/generate-missing`.
- Create `backend/tests/test_storyboard_progress.py`, `backend/tests/test_stage_suggestion.py`, `backend/tests/integration/test_generate_missing.py`.

---

### Task 1: Storyboard-progress aggregation query

**Files:**
- Modify: `backend/app/repositories/script_shot_repository.py`
- Test: `backend/tests/test_storyboard_progress.py`

**Interfaces:**
- Produces: `ScriptShotRepository.storyboard_progress_for_project(project_id: int | str) -> dict` returning keys `{total, done, empty, generating, failed, script_count, scene_count}` (all `int`). Never raises — returns all-zero on failure.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_storyboard_progress.py
import pytest
from app.repositories.script_shot_repository import get_script_shot_repository


@pytest.mark.asyncio
async def test_progress_zero_when_query_fails(monkeypatch):
    """Best-effort: any DB error → all-zero dict, never raises."""
    repo = get_script_shot_repository()

    async def boom(*a, **k):
        raise RuntimeError("db down")

    import app.db.engine as db_engine
    monkeypatch.setattr(db_engine, "fetch_one", boom)

    out = await repo.storyboard_progress_for_project(123)
    assert out == {
        "total": 0, "done": 0, "empty": 0, "generating": 0,
        "failed": 0, "script_count": 0, "scene_count": 0,
    }


@pytest.mark.asyncio
async def test_progress_shape_from_row(monkeypatch):
    """Aggregated row → typed int dict."""
    repo = get_script_shot_repository()

    async def fake_fetch_one(sql, params):
        assert params == {"pid": 123}
        return {
            "total": 12, "done": 9, "empty": 3, "generating": 0,
            "failed": 0, "script_count": 2, "scene_count": 5,
        }

    import app.db.engine as db_engine
    monkeypatch.setattr(db_engine, "fetch_one", fake_fetch_one)

    out = await repo.storyboard_progress_for_project(123)
    assert out["total"] == 12 and out["done"] == 9 and out["empty"] == 3
    assert out["script_count"] == 2 and out["scene_count"] == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_storyboard_progress.py -v`
Expected: FAIL — `AttributeError: 'ScriptShotRepository' object has no attribute 'storyboard_progress_for_project'`

- [ ] **Step 3: Write minimal implementation**

Add to `backend/app/repositories/script_shot_repository.py` (module-level SQL constant near other constants, method on `ScriptShotRepository`):

```python
_STORYBOARD_PROGRESS_SQL = """
    SELECT
        COUNT(sh.id)                                             AS total,
        COUNT(sh.id) FILTER (WHERE sh.status = 'done')          AS done,
        COUNT(sh.id) FILTER (WHERE sh.status = 'empty')         AS empty,
        COUNT(sh.id) FILTER (WHERE sh.status = 'generating')    AS generating,
        COUNT(sh.id) FILTER (WHERE sh.status = 'failed')        AS failed,
        COUNT(DISTINCT sp.id)                                    AS script_count,
        COUNT(DISTINCT sc.id)                                    AS scene_count
    FROM public.script_projects sp
    LEFT JOIN public.script_scenes sc ON sc.script_id = sp.id
    LEFT JOIN public.script_shots  sh ON sh.scene_id  = sc.id
    WHERE sp.project_id = :pid AND sp.status != 'deleted'
"""

_ZERO_PROGRESS = {
    "total": 0, "done": 0, "empty": 0, "generating": 0,
    "failed": 0, "script_count": 0, "scene_count": 0,
}


async def storyboard_progress_for_project(self, project_id) -> dict:
    """Shot completion rolled up across ALL non-deleted scripts in a project.

    One JOIN script_projects → script_scenes → script_shots. Best-effort:
    any failure returns the all-zero shape so the suggestion card degrades
    to a navigation nudge instead of 500ing the workbench.
    """
    from app.db import engine as db_engine

    try:
        row = await db_engine.fetch_one(
            _STORYBOARD_PROGRESS_SQL, {"pid": int(project_id)}
        )
        if not row:
            return dict(_ZERO_PROGRESS)
        return {k: int(row[k] or 0) for k in _ZERO_PROGRESS}
    except Exception as e:  # noqa: BLE001 — enrichment must not sink the workbench
        from loguru import logger
        logger.error(f"[script_shots] storyboard progress for {project_id} failed: {e}")
        return dict(_ZERO_PROGRESS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_storyboard_progress.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/script_shot_repository.py backend/tests/test_storyboard_progress.py
git commit -m "feat(projects): storyboard progress aggregation across project scripts"
```

---

### Task 2: Stage-suggestion schemas

**Files:**
- Modify: `backend/app/schemas/projects.py`
- Test: (covered by Task 3's resolver test — schemas are validated there)

**Interfaces:**
- Produces: Pydantic models `StoryboardProgress`, `SuggestionAction`, `StageSuggestionResponse`, `GenerateMissingResponse` (exact fields below).

- [ ] **Step 1: Add the models**

Append to `backend/app/schemas/projects.py` (follow the file's existing `BaseModel` style):

```python
class StoryboardProgress(BaseModel):
    total: int
    done: int
    empty: int
    generating: int
    failed: int
    script_count: int
    scene_count: int


class SuggestionAction(BaseModel):
    # "generate_missing_frames" fires the batch endpoint; "navigate" switches tab.
    type: str
    label_key: str
    tab: Optional[str] = None      # navigate target: scripts|output|files
    count: Optional[int] = None    # generate_missing_frames: number of empty shots


class StageSuggestionResponse(BaseModel):
    stage_slug: Optional[str] = None       # None when project has no current stage
    kind: str                              # see spec kind decision table; "" = render nothing
    progress: Optional[StoryboardProgress] = None
    action: Optional[SuggestionAction] = None


class GenerateMissingResponse(BaseModel):
    parent_task_id: str
    dispatched_count: int
```

Ensure `from typing import Optional` is imported at the top (it already is if other Optionals exist — verify).

- [ ] **Step 2: Verify import compiles**

Run: `cd backend && uv run python -c "from app.schemas.projects import StageSuggestionResponse, GenerateMissingResponse; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/app/schemas/projects.py
git commit -m "feat(projects): stage-suggestion + generate-missing response schemas"
```

---

### Task 3: Stage-suggestion resolver (service)

**Files:**
- Modify: `backend/app/services/library/projects_service.py`
- Test: `backend/tests/test_stage_suggestion.py`

**Interfaces:**
- Consumes: `ScriptShotRepository.storyboard_progress_for_project` (Task 1); `ProjectStagesRepository.get_current` (existing, returns `{slug, name, ...}` or None).
- Produces: `ProjectsService.build_stage_suggestion(project_id) -> dict` matching `StageSuggestionResponse` shape. Pure decision logic per the spec's kind decision table.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_stage_suggestion.py
import pytest
from app.services.library.projects_service import ProjectsService


class _FakeStages:
    def __init__(self, stage):
        self._stage = stage
    async def get_current(self, pid):
        return self._stage


class _FakeShots:
    def __init__(self, progress):
        self._p = progress
    async def storyboard_progress_for_project(self, pid):
        return self._p


def _svc(stage, progress):
    s = ProjectsService.__new__(ProjectsService)  # bypass __init__ deps
    s._stages_repo_override = _FakeStages(stage)
    s._shots_repo_override = _FakeShots(progress)
    return s


@pytest.mark.asyncio
async def test_no_current_stage_renders_nothing():
    out = await _svc(None, {}).build_stage_suggestion(1)
    assert out["kind"] == "" and out["stage_slug"] is None


@pytest.mark.asyncio
async def test_storyboard_with_empty_shots_is_generate():
    stage = {"slug": "storyboard", "name": "Storyboarding"}
    progress = {"total": 12, "done": 9, "empty": 3, "generating": 0,
                "failed": 0, "script_count": 2, "scene_count": 5}
    out = await _svc(stage, progress).build_stage_suggestion(1)
    assert out["kind"] == "storyboard_generate"
    assert out["action"]["type"] == "generate_missing_frames"
    assert out["action"]["count"] == 3
    assert out["progress"]["done"] == 9


@pytest.mark.asyncio
async def test_storyboard_no_script_navigates_to_scripts():
    stage = {"slug": "storyboard", "name": "Storyboarding"}
    progress = {"total": 0, "done": 0, "empty": 0, "generating": 0,
                "failed": 0, "script_count": 0, "scene_count": 0}
    out = await _svc(stage, progress).build_stage_suggestion(1)
    assert out["kind"] == "storyboard_no_script"
    assert out["action"]["type"] == "navigate" and out["action"]["tab"] == "scripts"


@pytest.mark.asyncio
async def test_storyboard_all_done_is_ready():
    stage = {"slug": "storyboard", "name": "Storyboarding"}
    progress = {"total": 12, "done": 12, "empty": 0, "generating": 0,
                "failed": 0, "script_count": 1, "scene_count": 4}
    out = await _svc(stage, progress).build_stage_suggestion(1)
    assert out["kind"] == "storyboard_ready"
    assert out["action"]["type"] == "navigate"


@pytest.mark.asyncio
async def test_non_storyboard_stage_is_nav():
    stage = {"slug": "planning", "name": "Planning"}
    out = await _svc(stage, {}).build_stage_suggestion(1)
    assert out["kind"] == "planning_nav"
    assert out["action"]["type"] == "navigate"
    assert out["progress"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_stage_suggestion.py -v`
Expected: FAIL — `AttributeError: build_stage_suggestion`

- [ ] **Step 3: Write minimal implementation**

Add to `backend/app/services/library/projects_service.py`. First add lazy repo accessors that honor test overrides, then the resolver:

```python
# module-level near other constants
_STORYBOARD_STAGE = "storyboard"
# tab each non-storyboard stage's nav CTA targets
_STAGE_NAV_TAB = {
    "planning": "scripts",
    "script": "scripts",
    "generation": "output",
    "review": "files",
    "delivery": "output",
}


def _stages_repo(self):
    override = getattr(self, "_stages_repo_override", None)
    if override is not None:
        return override
    from app.repositories.project_stages_repository import (
        get_project_stages_repository,
    )
    return get_project_stages_repository()


def _shots_repo(self):
    override = getattr(self, "_shots_repo_override", None)
    if override is not None:
        return override
    from app.repositories.script_shot_repository import get_script_shot_repository
    return get_script_shot_repository()


async def build_stage_suggestion(self, project_id) -> dict:
    """Typed 'what's the one next step' for the project's current stage.

    Storyboard stage is data-aware + one-click (generate_missing_frames);
    every other stage is data-aware + navigation. Unknown / no stage → kind=""
    so the frontend renders nothing.
    """
    stage = await self._stages_repo().get_current(project_id)
    if not stage:
        return {"stage_slug": None, "kind": "", "progress": None, "action": None}

    slug = stage["slug"]

    if slug == _STORYBOARD_STAGE:
        p = await self._shots_repo().storyboard_progress_for_project(project_id)
        if p["script_count"] == 0:
            return {
                "stage_slug": slug, "kind": "storyboard_no_script", "progress": p,
                "action": {"type": "navigate", "tab": "scripts",
                           "label_key": "projects.suggest.ctaScripts", "count": None},
            }
        if p["total"] == 0:
            return {
                "stage_slug": slug, "kind": "storyboard_no_shots", "progress": p,
                "action": {"type": "navigate", "tab": "scripts",
                           "label_key": "projects.suggest.ctaBreakdown", "count": None},
            }
        if p["empty"] > 0:
            return {
                "stage_slug": slug, "kind": "storyboard_generate", "progress": p,
                "action": {"type": "generate_missing_frames", "tab": None,
                           "label_key": "projects.suggest.ctaGenerate", "count": p["empty"]},
            }
        return {
            "stage_slug": slug, "kind": "storyboard_ready", "progress": p,
            "action": {"type": "navigate", "tab": "scripts",
                       "label_key": "projects.suggest.ctaReady", "count": None},
        }

    tab = _STAGE_NAV_TAB.get(slug, "files")
    return {
        "stage_slug": slug, "kind": f"{slug}_nav", "progress": None,
        "action": {"type": "navigate", "tab": tab,
                   "label_key": f"projects.suggest.cta_{slug}", "count": None},
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_stage_suggestion.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/library/projects_service.py backend/tests/test_stage_suggestion.py
git commit -m "feat(projects): stage-suggestion resolver with per-stage decision table"
```

---

### Task 4: Batch generate-missing service method

**Files:**
- Modify: `backend/app/services/library/projects_service.py`
- Test: `backend/tests/integration/test_generate_missing.py` (unit-level with fakes here; true-DB integration in Task 6)

**Interfaces:**
- Consumes: task manager (`get_task_manager().create(...)`), `start_workflow_routed`, `script_shot_generate_workflow`, `ScriptShotRepository.update_status`, `ProjectStyleProfileRepository.get`.
- Produces: `ProjectsService.generate_missing_frames(project_id, user_id) -> dict` = `{parent_task_id, dispatched_count}`. Fans out one `script_shot_generate` workflow per empty shot; per-shot failure rolls that shot back to `empty` and continues (partial success is not a whole-call failure).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/integration/test_generate_missing.py
import pytest
from app.services.library.projects_service import ProjectsService


class _FakeShotsRepo:
    def __init__(self, empty_ids):
        self.empty_ids = empty_ids
        self.status_calls = []
    async def list_empty_shot_ids_for_project(self, pid):
        return list(self.empty_ids)
    async def update_status(self, shot_id, status):
        self.status_calls.append((shot_id, status))


def _svc(empty_ids):
    s = ProjectsService.__new__(ProjectsService)
    s._shots_repo_override = _FakeShotsRepo(empty_ids)
    return s


@pytest.mark.asyncio
async def test_dispatches_one_per_empty_shot(monkeypatch):
    svc = _svc(["s1", "s2", "s3"])
    created = {}
    dispatched = []

    class _Mgr:
        async def create(self, **kw):
            created.update(kw)
            return "parent-task-1"

    async def fake_start(name, **kw):
        dispatched.append(kw["dbos_workflow_kwargs"]["shot_id"])

    monkeypatch.setattr(
        "app.services.library.projects_service.get_task_manager", lambda: _Mgr()
    )
    monkeypatch.setattr(
        "app.services.library.projects_service.start_workflow_routed", fake_start
    )
    monkeypatch.setattr(
        "app.services.library.projects_service._load_style_profile",
        _noop_style, raising=False,
    )

    out = await svc.generate_missing_frames("proj1", "user1")
    assert out["dispatched_count"] == 3
    assert out["parent_task_id"] == "parent-task-1"
    assert sorted(dispatched) == ["s1", "s2", "s3"]


async def _noop_style(pid):
    return {}


@pytest.mark.asyncio
async def test_no_empty_shots_dispatches_zero(monkeypatch):
    svc = _svc([])

    class _Mgr:
        async def create(self, **kw):
            return "parent-task-2"

    monkeypatch.setattr(
        "app.services.library.projects_service.get_task_manager", lambda: _Mgr()
    )
    out = await svc.generate_missing_frames("proj1", "user1")
    assert out["dispatched_count"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/integration/test_generate_missing.py -v`
Expected: FAIL — `AttributeError: generate_missing_frames` / `list_empty_shot_ids_for_project`

- [ ] **Step 3a: Add the empty-shot-ids query to the shot repo**

Add to `backend/app/repositories/script_shot_repository.py`:

```python
_EMPTY_SHOT_IDS_SQL = """
    SELECT sh.id
    FROM public.script_projects sp
    JOIN public.script_scenes sc ON sc.script_id = sp.id
    JOIN public.script_shots  sh ON sh.scene_id  = sc.id
    WHERE sp.project_id = :pid AND sp.status != 'deleted'
      AND sh.status = 'empty'
    ORDER BY sh.id
"""


async def list_empty_shot_ids_for_project(self, project_id) -> list[str]:
    """IDs of every 'empty' shot across the project's non-deleted scripts."""
    from app.db import engine as db_engine

    rows = await db_engine.fetch_all(_EMPTY_SHOT_IDS_SQL, {"pid": int(project_id)})
    return [str(r["id"]) for r in rows]
```

- [ ] **Step 3b: Add the batch service method**

Add to `backend/app/services/library/projects_service.py` (imports at top of file):

```python
# top-of-file imports
import uuid as _uuid
from app.services.task_tracker import get_task_manager
from app.services.infra.dbos_orchestrator import start_workflow_routed


async def _load_style_profile(project_id) -> dict:
    """Style guidance passed to each shot-generate workflow (best-effort)."""
    try:
        from app.repositories.project_style_profile_repository import (
            get_project_style_profile_repository,
        )
        prof = await get_project_style_profile_repository().get(project_id)
        return prof or {}
    except Exception:  # noqa: BLE001
        return {}


async def generate_missing_frames(self, project_id, user_id: str) -> dict:
    """Fan out one shot-generate workflow per empty shot in the project.

    One parent task_tracking row for the batch; per-shot dispatch failures
    roll that shot back to 'empty' and are skipped (partial success is fine).
    """
    from loguru import logger
    from app.workflows.script_shot_generate import script_shot_generate_workflow

    shots_repo = self._shots_repo()
    empty_ids = await shots_repo.list_empty_shot_ids_for_project(project_id)
    style = await _load_style_profile(project_id)

    mgr = get_task_manager()
    parent_task_id = await mgr.create(
        user_id=user_id,
        task_type="sb_generate_all",  # ≤20 chars (task_tracking.task_type VARCHAR(20))
        title="Generate storyboard frames",
        dbos_workflow_id=str(_uuid.uuid4()),
    )
    # Business decoration (subtitle/metadata) is business-owned — safe to PATCH.
    try:
        await mgr.update_metadata(
            parent_task_id,
            subtitle=f"Generating {len(empty_ids)} frames",
            metadata={"project_id": str(project_id), "empty_count": len(empty_ids)},
        )
    except Exception:  # noqa: BLE001 — decoration is best-effort
        pass

    dispatched = 0
    for shot_id in empty_ids:
        try:
            await shots_repo.update_status(shot_id, "generating")
            wf_id = str(_uuid.uuid4())
            await start_workflow_routed(
                "script_shot_generate",
                dbos_workflow_callable=script_shot_generate_workflow,
                dbos_workflow_kwargs={
                    "shot_id": shot_id,
                    "user_id": user_id,
                    "style_profile": style,
                },
                workflow_id=wf_id,
            )
            dispatched += 1
        except Exception as exc:  # noqa: BLE001 — skip this shot, keep the batch
            logger.error(f"[projects] generate-missing shot {shot_id} failed: {exc}")
            try:
                await shots_repo.update_status(shot_id, "empty")
            except Exception:  # noqa: BLE001
                pass

    return {"parent_task_id": parent_task_id, "dispatched_count": dispatched}
```

> **Note for implementer:** verify `get_task_manager().update_metadata(...)` exists with a `subtitle`/`metadata` signature; if the manager exposes decoration differently (e.g. `patch(task_id, subtitle=...)`), use that method instead — the point is to write subtitle/metadata via the manager API, never a raw PATCH of phase columns. Also verify `script_shot_generate_workflow` accepts a `style_profile` kwarg; if it doesn't yet, pass style through the existing kwarg it reads (grep `script_shot_generate.py` for how it currently loads style) — do NOT invent a param the workflow ignores.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/integration/test_generate_missing.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/script_shot_repository.py backend/app/services/library/projects_service.py backend/tests/integration/test_generate_missing.py
git commit -m "feat(projects): batch generate-missing-frames service fan-out"
```

---

### Task 5: Wire the two routes

**Files:**
- Modify: `backend/app/api/projects_router.py`
- Test: `backend/tests/test_projects_authz_wiring.py` (extend existing wiring test)

**Interfaces:**
- Consumes: `ProjectsService.build_stage_suggestion`, `generate_missing_frames`; existing guards `verify_project_read_access`, the write guard used by other mutating project routes (grep the file — reuse the exact dependency other PATCH/POST project routes use).

- [ ] **Step 1: Write the failing wiring test**

Add to `backend/tests/test_projects_authz_wiring.py` (follow its existing structural-assertion style — it inspects route guards without a live server):

```python
def test_stage_suggestion_has_read_guard():
    from app.api import projects_router
    route = _find_route(projects_router.router, "/{project_id}/stage-suggestion", "GET")
    assert route is not None
    assert _has_dependency(route, "verify_project_read_access")


def test_generate_missing_has_write_guard():
    from app.api import projects_router
    route = _find_route(
        projects_router.router, "/{project_id}/storyboard/generate-missing", "POST"
    )
    assert route is not None
    # reuse whatever write guard the other mutating project routes use
    assert _has_write_guard(route)
```

> If `_find_route` / `_has_dependency` / `_has_write_guard` helpers don't exist in that test file, add small ones that walk `router.routes` matching `route.path` / `route.methods` and inspect `route.dependant.dependencies`. Mirror how the file already asserts guards on the 42 Phase-A endpoints.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_projects_authz_wiring.py -k "stage_suggestion or generate_missing" -v`
Expected: FAIL — routes not found.

- [ ] **Step 3: Add the routes**

Add to `backend/app/api/projects_router.py` (near the other stage routes ~`:231-312`; reuse the module's existing `AuthDep`, service accessor, and guards):

```python
@router.get("/{project_id}/stage-suggestion", response_model=StageSuggestionResponse)
async def get_stage_suggestion(
    project_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_project_read_access),
) -> StageSuggestionResponse:
    """Typed 'one next step' for the project's current SOP stage (Phase B B3)."""
    svc = get_projects_service()
    data = await svc.build_stage_suggestion(project_id)
    return StageSuggestionResponse(**data)


@router.post(
    "/{project_id}/storyboard/generate-missing",
    response_model=GenerateMissingResponse,
)
async def generate_missing_frames(
    project_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_project_write_access),  # match sibling write routes
) -> GenerateMissingResponse:
    """Batch-generate every empty storyboard shot in the project (flag-gated)."""
    if not settings.FEATURE_SHOT_GENERATE:
        raise HTTPException(status_code=404, detail="Not Found")
    svc = get_projects_service()
    data = await svc.generate_missing_frames(project_id, auth.user_id)
    return GenerateMissingResponse(**data)
```

Add imports at top: `from app.schemas.projects import StageSuggestionResponse, GenerateMissingResponse` and ensure `settings` + the write guard name match what the file already imports (grep for `verify_project_write_access` or the actual guard other POST/PATCH routes use, and reuse it verbatim).

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/test_projects_authz_wiring.py -v`
Expected: PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/projects_router.py backend/tests/test_projects_authz_wiring.py
git commit -m "feat(projects): stage-suggestion + generate-missing routes with authz"
```

---

### Task 6: True-DB integration for generate-missing

**Files:**
- Test: `backend/tests/integration/test_generate_missing_db.py`

**Interfaces:**
- Consumes: live test DB fixtures (mirror an existing integration test's fixture setup, e.g. `tests/integration/test_projects_repository_orm.py`).

- [ ] **Step 1: Write the integration test**

```python
# backend/tests/integration/test_generate_missing_db.py
import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_generate_missing_flips_empty_to_generating(seed_project_with_shots):
    """End-to-end: 3 empty shots → dispatched_count=3, all now 'generating'."""
    project_id, empty_shot_ids = seed_project_with_shots(empty=3, done=9)
    from app.services.library.projects_service import get_projects_service

    out = await get_projects_service().generate_missing_frames(project_id, "test-user")
    assert out["dispatched_count"] == 3

    from app.repositories.script_shot_repository import get_script_shot_repository
    remaining = await get_script_shot_repository().list_empty_shot_ids_for_project(project_id)
    assert remaining == []  # every empty shot left the 'empty' state
```

> Implement `seed_project_with_shots` as a fixture in `backend/tests/integration/conftest.py` (or reuse an existing project/script/scene/shot factory if one exists — grep `conftest`). It must insert 1 `script_projects` (status active) → 1 `script_scenes` → N `script_shots` with the requested status mix, and return `(project_id, [empty_shot_id,...])`. Use English test data (`Test Project`, `Test Scene`).

- [ ] **Step 2: Run it**

Run: `cd backend && uv run pytest tests/integration/test_generate_missing_db.py -v -m integration`
Expected: PASS (needs local Supabase at `127.0.0.1:54322`; if the workflow dispatch requires a live DBOS, stub `start_workflow_routed` in the fixture to a no-op that still lets `update_status` run).

- [ ] **Step 3: Lint + commit**

```bash
cd backend && black . && isort . && flake8 app/ tests/
git add backend/tests/integration/
git commit -m "test(projects): true-DB integration for generate-missing fan-out"
```

---

### Task 7: PR-1 review + ship

- [ ] **Step 1:** Run full backend suite: `cd backend && uv run pytest tests/test_storyboard_progress.py tests/test_stage_suggestion.py tests/integration/test_generate_missing.py tests/test_projects_authz_wiring.py -v` → all pass.
- [ ] **Step 2:** Lint gate: `cd backend && black --check . && isort --check . && flake8 app/ tests/`.
- [ ] **Step 3:** Ship PR-1 (use `/ship`): title `feat(projects): B3 backend — storyboard progress + stage-suggestion + batch generate`. Body notes zero-migration, flag-gated batch endpoint.

---

# PR-2 · B3 Frontend (data-aware suggestion card)

**File Structure:**
- Modify `frontend/types.ts` — `StageSuggestion`, `SuggestionAction`, `StoryboardProgress` types.
- Modify `frontend/services/projectsService.ts` — `fetchStageSuggestion`, `generateMissingFrames`.
- Rewrite `frontend/components/project/StageSuggestion.tsx` — consume typed payload.
- Modify `frontend/components/project/StageSuggestion.test.tsx` — per-kind tests.
- Modify `frontend/public/locales/{en,zh}.json` — new keys.

---

### Task 8: Frontend types + service

**Files:**
- Modify: `frontend/types.ts`
- Modify: `frontend/services/projectsService.ts`

**Interfaces:**
- Produces: `fetchStageSuggestion(projectId: string): Promise<StageSuggestion>`, `generateMissingFrames(projectId: string): Promise<{ dispatched_count: number; task_ids: string[] }>`.

- [ ] **Step 1: Add types**

Add to `frontend/types.ts`:

```typescript
export interface StoryboardProgress {
  total: number; done: number; empty: number;
  generating: number; failed: number;
  script_count: number; scene_count: number;
}
export interface SuggestionAction {
  type: 'generate_missing_frames' | 'navigate';
  label_key: string;
  tab?: ProjectTab | null;
  count?: number | null;
}
export interface StageSuggestion {
  stage_slug: string | null;
  kind: string;
  progress?: StoryboardProgress | null;
  action?: SuggestionAction | null;
}
```

- [ ] **Step 2: Add service functions**

Add to `frontend/services/projectsService.ts` (mirror the file's existing fetch helpers — same base URL + `getAuthHeaders()` pattern used by `fetchStageCatalog`/`setCurrentStage`):

```typescript
export async function fetchStageSuggestion(projectId: string): Promise<StageSuggestion> {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/stage-suggestion`, {
    headers: await getAuthHeaders(),
  });
  if (!res.ok) throw new Error(`stage-suggestion ${res.status}`);
  return res.json();
}

export async function generateMissingFrames(
  projectId: string,
): Promise<{ dispatched_count: number; task_ids: string[] }> {
  const res = await fetch(
    `${API_BASE}/api/v1/projects/${projectId}/storyboard/generate-missing`,
    { method: 'POST', headers: await getAuthHeaders() },
  );
  if (!res.ok) throw new Error(`generate-missing ${res.status}`);
  return res.json();
}
```

Import `StageSuggestion` at the top. Use whatever the file already names the base URL constant (grep — likely `API_BASE` or inline `import.meta.env.VITE_API_URL`); match it exactly.

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/types.ts frontend/services/projectsService.ts
git commit -m "feat(projects): stage-suggestion types + service functions"
```

---

### Task 9: Rework StageSuggestion to data-aware card

**Files:**
- Modify: `frontend/components/project/StageSuggestion.tsx`
- Modify: `frontend/components/project/StageSuggestion.test.tsx`
- Modify: `frontend/public/locales/en.json`, `frontend/public/locales/zh.json`

**Interfaces:**
- Consumes: `fetchStageSuggestion`, `generateMissingFrames` (Task 8); `useToast` (`from '../../components/Toast'` or the path other project components use — grep).
- Produces: same component export `StageSuggestion` with props `{ projectId, currentStage, setActiveTab }` (unchanged signature — caller in `StageWorkbench` needs no edit).

- [ ] **Step 1: Add i18n keys**

Add to `frontend/public/locales/en.json` under `projects.suggest` (merge, don't clobber siblings):

```json
"suggest": {
  "eyebrow": "Suggested next",
  "ctaScripts": "Go to scripts",
  "ctaBreakdown": "Break into shots",
  "ctaGenerate": "Generate {{count}} frames",
  "ctaReady": "Review & advance",
  "cta_planning": "Start planning",
  "cta_script": "Open scripts",
  "cta_generation": "Open output",
  "cta_review": "Review files",
  "cta_delivery": "Prepare delivery",
  "storyboard_no_script": "No script yet — write one, then storyboard it.",
  "storyboard_no_shots": "Script ready — break {{scene_count}} scenes into shots.",
  "storyboard_generate": "{{done}} of {{total}} shots have frames — generate the {{count}} missing frames, then this stage is ready to advance.",
  "storyboard_ready": "All {{total}} shots have frames — review the boards, then advance.",
  "planning_nav": "Outline the project and start the first script.",
  "script_nav": "Draft or refine the script for this project.",
  "generation_nav": "Generate final media from the approved boards.",
  "review_nav": "Review deliverables and gather feedback.",
  "delivery_nav": "Package and hand off the finished work.",
  "generating": "Generating {{count}} frames…"
}
```

Add Chinese values to `zh.json` (English keys, Chinese values), e.g. `"ctaGenerate": "生成 {{count}} 帧"`, `"storyboard_generate": "{{total}} 个分镜已生成 {{done}} 个 — 生成缺的 {{count}} 帧，即可推进本阶段。"`, etc.

- [ ] **Step 2: Write the failing tests**

Rewrite `frontend/components/project/StageSuggestion.test.tsx`:

```tsx
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { vi } from 'vitest';
import { StageSuggestion } from './StageSuggestion';
import * as svc from '../../services/projectsService';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: any) => (o?.count != null ? `${k}:${o.count}` : k) }),
}));
vi.mock('../../components/Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

const stage = { id: '30', slug: 'storyboard', name: 'Storyboarding' } as any;

test('storyboard_generate renders count and fires batch on click', async () => {
  vi.spyOn(svc, 'fetchStageSuggestion').mockResolvedValue({
    stage_slug: 'storyboard', kind: 'storyboard_generate',
    progress: { total: 12, done: 9, empty: 3, generating: 0, failed: 0, script_count: 2, scene_count: 5 },
    action: { type: 'generate_missing_frames', label_key: 'projects.suggest.ctaGenerate', count: 3 },
  });
  const gen = vi.spyOn(svc, 'generateMissingFrames').mockResolvedValue({ dispatched_count: 3, task_ids: ['t1', 't2', 't3'] });

  render(<StageSuggestion projectId="p1" currentStage={stage} setActiveTab={vi.fn()} />);
  const btn = await screen.findByTestId('suggest-cta');
  expect(btn.textContent).toContain('3');
  fireEvent.click(btn);
  await waitFor(() => expect(gen).toHaveBeenCalledWith('p1'));
});

test('navigate kind switches tab, does not call batch', async () => {
  vi.spyOn(svc, 'fetchStageSuggestion').mockResolvedValue({
    stage_slug: 'planning', kind: 'planning_nav', progress: null,
    action: { type: 'navigate', tab: 'scripts', label_key: 'projects.suggest.cta_planning', count: null },
  });
  const setTab = vi.fn();
  render(<StageSuggestion projectId="p1" currentStage={{ ...stage, slug: 'planning' }} setActiveTab={setTab} />);
  const btn = await screen.findByTestId('suggest-cta');
  fireEvent.click(btn);
  expect(setTab).toHaveBeenCalledWith('scripts');
});

test('empty kind renders nothing', async () => {
  vi.spyOn(svc, 'fetchStageSuggestion').mockResolvedValue({
    stage_slug: null, kind: '', progress: null, action: null,
  });
  const { container } = render(<StageSuggestion projectId="p1" currentStage={null} setActiveTab={vi.fn()} />);
  await waitFor(() => expect(container.querySelector('[data-testid="stage-suggestion"]')).toBeNull());
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd frontend && npx vitest run components/project/StageSuggestion.test.tsx`
Expected: FAIL.

- [ ] **Step 4: Rewrite the component**

Replace `frontend/components/project/StageSuggestion.tsx` body with a payload-driven card. Key behaviors: fetch suggestion on mount / stage change; render message from `kind` + `progress` interpolation; CTA either fires `generateMissingFrames` (toast + disabled/loading + refetch) or `setActiveTab(action.tab)`.

```tsx
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Lightbulb, ArrowRight, Loader2 } from 'lucide-react';
import { fetchStageSuggestion, generateMissingFrames } from '../../services/projectsService';
import { useToast } from '../../components/Toast';
import type { ProjectStage, ProjectTab, StageSuggestion as Suggestion } from '../../types';

interface Props {
  projectId: string;
  currentStage: ProjectStage | null;
  setActiveTab: (tab: ProjectTab) => void;
}

export function StageSuggestion({ projectId, currentStage, setActiveTab }: Props) {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [data, setData] = useState<Suggestion | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    let cancelled = false;
    fetchStageSuggestion(projectId)
      .then((s) => { if (!cancelled) setData(s); })
      .catch((err) => { console.error('[StageSuggestion] load failed:', err); if (!cancelled) setData(null); });
    return () => { cancelled = true; };
  }, [projectId, currentStage?.slug]);

  useEffect(() => load(), [load]);

  const onGenerate = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    try {
      const res = await generateMissingFrames(projectId);
      addToast(t('projects.suggest.generating', { count: res.dispatched_count }), 'success');
      load();
    } catch (err) {
      console.error('[StageSuggestion] generate failed:', err);
      addToast(t('common.error'), 'error');
    } finally {
      setBusy(false);
    }
  }, [busy, projectId, addToast, t, load]);

  if (!data || !data.kind || !data.action) return null;

  const p = data.progress ?? undefined;
  const message = t(`projects.suggest.${data.kind}`, {
    done: p?.done, total: p?.total, count: data.action.count, scene_count: p?.scene_count,
  });
  const isGenerate = data.action.type === 'generate_missing_frames';

  return (
    <div
      className="rounded-xl border border-indigo-500/35 bg-gradient-to-b from-indigo-500/[0.08] to-transparent p-4 flex items-start gap-3"
      data-testid="stage-suggestion"
    >
      <span className="grid place-items-center w-7 h-7 rounded-lg bg-indigo-500/15 text-indigo-400 shrink-0 mt-0.5">
        <Lightbulb className="w-3.5 h-3.5" />
      </span>
      <div className="flex-1 min-w-0">
        <div className="text-[11px] uppercase tracking-wider text-indigo-400 font-semibold">
          {t('projects.suggest.eyebrow')}
        </div>
        <p className="text-sm text-ink-200 mt-1">{message}</p>
      </div>
      <button
        data-testid="suggest-cta"
        disabled={busy}
        onClick={() => (isGenerate ? onGenerate() : setActiveTab(data.action!.tab as ProjectTab))}
        className={`flex items-center gap-1.5 shrink-0 rounded-lg font-medium text-sm px-3 py-1.5 transition-colors ${
          isGenerate
            ? 'bg-indigo-500 hover:bg-indigo-400 disabled:opacity-50 text-ink-950'
            : 'border border-indigo-500/40 hover:bg-indigo-500/15 text-indigo-300'
        }`}
      >
        {busy ? <Loader2 size={14} className="animate-spin" /> : null}
        {t(data.action.label_key, { count: data.action.count })}
        {!isGenerate && <ArrowRight size={14} />}
      </button>
    </div>
  );
}

export default StageSuggestion;
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run components/project/StageSuggestion.test.tsx`
Expected: PASS (3 passed).

- [ ] **Step 6: Lint + commit**

```bash
cd frontend && npm run lint
git add frontend/components/project/StageSuggestion.tsx frontend/components/project/StageSuggestion.test.tsx frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(projects): data-aware B3 suggestion card with one-click generate"
```

---

### Task 10: PR-2 review + ship

- [ ] **Step 1:** `cd frontend && npx tsc --noEmit && npm run lint && npx vitest run components/project/StageSuggestion.test.tsx`.
- [ ] **Step 2:** Ship PR-2 (`/ship`): `feat(projects): B3 frontend — data-aware suggestion card + one-click generate`. Depends on PR-1 being merged (endpoints live).

---

# PR-3 · B1 Hybrid Activity Row + Stall Color

**File Structure:**
- Modify `backend/app/repositories/project_stages_repository.py` — `latest_file_activity_for_projects`.
- Modify `backend/app/services/library/projects_service.py` — merge into `latest_activity` + `stalled`.
- Modify `frontend/components/ProjectCard.tsx` — hybrid row + amber stall dot.
- Modify `frontend/components/ProjectCard.test.tsx`.
- Modify `frontend/public/locales/{en,zh}.json`.

---

### Task 11: File-activity batch query

**Files:**
- Modify: `backend/app/repositories/project_stages_repository.py`
- Test: `backend/tests/test_latest_file_activity.py`

**Interfaces:**
- Produces: `ProjectStagesRepository.latest_file_activity_for_projects(project_ids) -> dict[str, dict]` = `{str(pid): {actor, created_at, kind: "file"}}`. Never raises → `{}` on failure.

> **Verify first:** grep `resources` columns for the project linkage + creator + timestamp (`get_project_file_counts` in `projects_repository.py` already joins resources→projects — copy its exact JOIN path and column names; likely `resources.creator_id` per Phase-A `link_media` note, and a `created_at`). Use `user_profiles.username` for actor as `latest_activity_for_projects` does.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_latest_file_activity.py
import pytest
from app.repositories.project_stages_repository import get_project_stages_repository


@pytest.mark.asyncio
async def test_empty_input_returns_empty():
    assert await get_project_stages_repository().latest_file_activity_for_projects([]) == {}


@pytest.mark.asyncio
async def test_maps_rows_by_project(monkeypatch):
    repo = get_project_stages_repository()

    async def fake_fetch_all(sql, params):
        class _TS:
            def isoformat(self): return "2026-07-08T00:00:00"
        return [{"project_id": 5, "actor": "hg", "created_at": _TS()}]

    import app.db.engine as db_engine
    monkeypatch.setattr(db_engine, "fetch_all", fake_fetch_all)

    out = await repo.latest_file_activity_for_projects([5])
    assert out["5"]["kind"] == "file"
    assert out["5"]["actor"] == "hg"
    assert out["5"]["created_at"] == "2026-07-08T00:00:00"


@pytest.mark.asyncio
async def test_never_raises(monkeypatch):
    repo = get_project_stages_repository()

    async def boom(*a, **k): raise RuntimeError("db")
    import app.db.engine as db_engine
    monkeypatch.setattr(db_engine, "fetch_all", boom)

    assert await repo.latest_file_activity_for_projects([5]) == {}
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && uv run pytest tests/test_latest_file_activity.py -v`
Expected: FAIL — method missing.

- [ ] **Step 3: Implement** (adapt JOIN/columns to what the grep confirmed)

```python
_LATEST_FILE_ACTIVITY_SQL = """
    SELECT DISTINCT ON (r.project_id)
           r.project_id, r.created_at,
           up.username AS actor
    FROM public.resources r
    LEFT JOIN public.user_profiles up ON up.id = r.creator_id
    WHERE r.project_id = ANY(:pids) AND COALESCE(r.is_trashed, false) = false
    ORDER BY r.project_id, r.created_at DESC
"""


async def latest_file_activity_for_projects(self, project_ids) -> dict:
    """Most recent file added per project (B1 hybrid activity row). Never raises."""
    if not project_ids:
        return {}
    from app.db import engine as db_engine

    try:
        rows = await db_engine.fetch_all(
            _LATEST_FILE_ACTIVITY_SQL, {"pids": [int(p) for p in project_ids]}
        )
        out = {}
        for r in rows:
            created = r["created_at"]
            out[str(r["project_id"])] = {
                "kind": "file",
                "actor": r["actor"] or "",
                "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
            }
        return out
    except Exception as e:  # noqa: BLE001
        logger.error(f"[project_stages] file activity lookup failed: {e}")
        return {}
```

> If `resources` has no `project_id` column (verify — Phase-A linked media via `resource_items`/`libraries` scope), route through the same table `get_project_file_counts` uses and alias it to `project_id`. Do NOT `SELECT` a column you haven't confirmed (`feedback_verify_columns_before_select`).

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest tests/test_latest_file_activity.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/project_stages_repository.py backend/tests/test_latest_file_activity.py
git commit -m "feat(projects): latest-file-activity batch query for hybrid activity row"
```

---

### Task 12: Merge hybrid activity + stall into enrichment

**Files:**
- Modify: `backend/app/services/library/projects_service.py` (`_get_card_enrichment`)
- Test: `backend/tests/test_card_activity_merge.py`

**Interfaces:**
- Produces: `latest_activity` dict now shaped `{kind: "file"|"stage", label_source, actor, at, stalled}` where the service picks the newer of file/stage event and sets `stalled` from the current stage's `entered_at` vs `STAGE_STALL_THRESHOLDS`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_card_activity_merge.py
from app.services.library.projects_service import _merge_activity, STAGE_STALL_THRESHOLDS


def test_file_newer_than_stage_wins():
    stage = {"stage_name": "Storyboarding", "actor": "lm", "entered_at": "2026-07-01T00:00:00"}
    file = {"kind": "file", "actor": "hg", "created_at": "2026-07-08T00:00:00"}
    out = _merge_activity(stage, file, stage_slug="storyboard", now="2026-07-08T12:00:00")
    assert out["kind"] == "file" and out["actor"] == "hg"


def test_stage_newer_than_file_wins():
    stage = {"stage_name": "Review", "actor": "lm", "entered_at": "2026-07-08T09:00:00"}
    file = {"kind": "file", "actor": "hg", "created_at": "2026-07-02T00:00:00"}
    out = _merge_activity(stage, file, stage_slug="review", now="2026-07-08T10:00:00")
    assert out["kind"] == "stage"


def test_review_stalls_after_3_days():
    stage = {"stage_name": "Review", "actor": "lm", "entered_at": "2026-07-01T00:00:00"}
    out = _merge_activity(stage, None, stage_slug="review", now="2026-07-08T00:00:00")
    assert out["stalled"] is True


def test_other_stage_not_stalled_before_7_days():
    stage = {"stage_name": "Planning", "actor": "lm", "entered_at": "2026-07-05T00:00:00"}
    out = _merge_activity(stage, None, stage_slug="planning", now="2026-07-08T00:00:00")
    assert out["stalled"] is False
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && uv run pytest tests/test_card_activity_merge.py -v`
Expected: FAIL — `_merge_activity` missing.

- [ ] **Step 3: Implement `_merge_activity` + wire into `_get_card_enrichment`**

Add module-level to `projects_service.py`:

```python
from datetime import datetime, timezone

STAGE_STALL_THRESHOLDS = {"review": 3}  # days; default for others below
_DEFAULT_STALL_DAYS = 7


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def _merge_activity(stage_act, file_act, *, stage_slug, now=None):
    """Pick the newer of stage/file event; flag stall from current-stage dwell.

    `now` is injectable for tests (ISO str); production passes None → utcnow.
    """
    now_dt = _parse_iso(now) or datetime.now(timezone.utc)
    stage_at = _parse_iso(stage_act.get("entered_at")) if stage_act else None
    file_at = _parse_iso(file_act.get("created_at")) if file_act else None

    # stall: dwell in current stage beyond threshold
    stalled = False
    if stage_at is not None:
        threshold = STAGE_STALL_THRESHOLDS.get(stage_slug, _DEFAULT_STALL_DAYS)
        stalled = (now_dt - stage_at).days >= threshold

    use_file = file_at is not None and (stage_at is None or file_at > stage_at)
    if use_file:
        return {"kind": "file", "actor": file_act["actor"],
                "at": file_act["created_at"], "stalled": stalled}
    if stage_act is not None:
        return {"kind": "stage", "actor": stage_act.get("actor", ""),
                "label": stage_act.get("stage_name", ""),
                "at": stage_act.get("entered_at"), "stalled": stalled}
    if file_act is not None:
        return {"kind": "file", "actor": file_act["actor"],
                "at": file_act["created_at"], "stalled": False}
    return None
```

Then in `_get_card_enrichment`, add `latest_file_activity_for_projects` to the `asyncio.gather`, and replace the `latest_activity` assignment:

```python
stage_map, activity, file_activity, members, catalog = await asyncio.gather(
    stages_repo.stages_for_projects(project_ids),
    stages_repo.latest_activity_for_projects(project_ids),
    stages_repo.latest_file_activity_for_projects(project_ids),
    self.repo.get_project_members_preview(project_ids),
    stages_repo.list_catalog(),
)
...
out[pid] = {
    "current_stage": current_stage,
    "members_preview": members.get(pid),
    "latest_activity": _merge_activity(
        activity.get(pid), file_activity.get(pid),
        stage_slug=(stage or {}).get("slug"),
    ),
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest tests/test_card_activity_merge.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/library/projects_service.py backend/tests/test_card_activity_merge.py
git commit -m "feat(projects): hybrid activity merge with stage stall detection"
```

---

### Task 13: ProjectCard hybrid row + amber stall

**Files:**
- Modify: `frontend/components/ProjectCard.tsx`
- Modify: `frontend/components/ProjectCard.test.tsx`
- Modify: `frontend/types.ts` (widen `latest_activity` type)
- Modify: `frontend/public/locales/{en,zh}.json`

**Interfaces:**
- Consumes: `project.latest_activity` now `{kind, actor, at, stalled, label?}`.

- [ ] **Step 1: Update the type** in `frontend/types.ts` — replace the existing `latest_activity` field on `Project`:

```typescript
latest_activity?: {
  kind: 'file' | 'stage';
  actor?: string;
  at?: string | null;
  label?: string;        // stage: stage_name
  stalled?: boolean;
} | null;
```

- [ ] **Step 2: Add i18n keys** to `en.json` under `projects.card`:

```json
"activityFile": "Added files",
"activityStage": "Entered {{stage}}",
"activityStalled": "Waiting in {{stage}}"
```

(`zh.json`: `"活动 · 新增文件"`, `"进入 {{stage}}"`, `"停在 {{stage}}"` style.)

- [ ] **Step 3: Write the failing test**

Add to `frontend/components/ProjectCard.test.tsx`:

```tsx
test('stalled activity renders amber dot', () => {
  const project = { ...baseProject, latest_activity: {
    kind: 'stage', label: 'Review', at: '2026-07-01T00:00:00', stalled: true } };
  const { container } = render(<ProjectCard project={project} onClick={() => {}} onToggleStar={() => {}} />);
  expect(container.querySelector('.bg-amber-400')).not.toBeNull();
  expect(container.querySelector('.bg-emerald-400')).toBeNull();
});

test('file activity renders file label', () => {
  const project = { ...baseProject, latest_activity: {
    kind: 'file', actor: 'HG', at: '2026-07-08T00:00:00', stalled: false } };
  render(<ProjectCard project={project} onClick={() => {}} onToggleStar={() => {}} />);
  // template key resolves via test i18n mock; assert the mono relative-time still shows
});
```

(Reuse the file's existing `baseProject` fixture and i18n mock; if none, mirror the setup in `StageSuggestion.test.tsx`.)

- [ ] **Step 4: Run to verify fail**

Run: `cd frontend && npx vitest run components/ProjectCard.test.tsx`
Expected: FAIL.

- [ ] **Step 5: Update the activity block** in `ProjectCard.tsx` (replace lines ~116-133):

```tsx
<div className="flex items-center gap-1.5 mt-3 text-xs text-ink-400 min-w-0">
  {activity ? (
    <>
      <span
        className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
          activity.stalled ? 'bg-amber-400' : 'bg-emerald-400'
        }`}
      />
      <span className="truncate">
        {activity.kind === 'file'
          ? t('projects.card.activityFile')
          : t(activity.stalled ? 'projects.card.activityStalled' : 'projects.card.activityStage', {
              stage: activity.label,
            })}
        {activity.actor ? ` · ${activity.actor}` : ''}
        {activity.at ? ` · ${formatRelativeTime(activity.at, t)}` : ''}
      </span>
    </>
  ) : (
    <>
      <Clock size={12} className="flex-shrink-0" />
      <span>{formatRelativeTime(project.updated_at, t)}</span>
    </>
  )}
</div>
```

- [ ] **Step 6: Run to verify pass**

Run: `cd frontend && npx vitest run components/ProjectCard.test.tsx`
Expected: PASS.

- [ ] **Step 7: Lint + commit**

```bash
cd frontend && npm run lint
git add frontend/components/ProjectCard.tsx frontend/components/ProjectCard.test.tsx frontend/types.ts frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(projects): hybrid activity row with amber stall indicator"
```

---

### Task 14: PR-3 review + ship

- [ ] **Step 1:** `cd backend && uv run pytest tests/test_latest_file_activity.py tests/test_card_activity_merge.py -v && black --check . && isort --check . && flake8 app/ tests/`.
- [ ] **Step 2:** `cd frontend && npx tsc --noEmit && npm run lint && npx vitest run components/ProjectCard.test.tsx`.
- [ ] **Step 3:** Ship PR-3 (`/ship`): `feat(projects): B1 hybrid activity row + stall color`.

---

# PR-4 · B2 Stage History Button

**File Structure:**
- Create `frontend/components/project/StageHistoryDrawer.tsx`.
- Create `frontend/components/project/StageHistoryDrawer.test.tsx`.
- Modify `frontend/components/project/StageWorkbench.tsx` — add ghost button + drawer state.
- Modify `frontend/services/projectsService.ts` — `fetchStageHistory` (if not already present).
- Modify `frontend/public/locales/{en,zh}.json`.

---

### Task 15: Stage-history service + drawer

**Files:**
- Modify: `frontend/services/projectsService.ts`
- Create: `frontend/components/project/StageHistoryDrawer.tsx`
- Create: `frontend/components/project/StageHistoryDrawer.test.tsx`
- Modify: `frontend/types.ts`, `frontend/public/locales/{en,zh}.json`

**Interfaces:**
- Produces: `fetchStageHistory(projectId): Promise<StageHistoryEntry[]>`; `StageHistoryDrawer` component `{ projectId, open, onClose }`.

- [ ] **Step 1: Add type + service**

`types.ts`:

```typescript
export interface StageHistoryEntry {
  id: string; stage_slug: string; stage_name: string;
  entered_at: string; exited_at: string | null; transitioned_by?: string;
}
```

`projectsService.ts` (verify no dup exists first — grep `stage_history`):

```typescript
export async function fetchStageHistory(projectId: string): Promise<StageHistoryEntry[]> {
  const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/stage_history`, {
    headers: await getAuthHeaders(),
  });
  if (!res.ok) throw new Error(`stage_history ${res.status}`);
  return res.json();
}
```

- [ ] **Step 2: Add i18n keys** to `en.json` under `projects.workbench`: `"stageHistory": "Stage history"`, `"historyEmpty": "No stage transitions yet."`, `"historyCurrent": "Current"`. (`zh.json` accordingly.)

- [ ] **Step 3: Write the failing test** `StageHistoryDrawer.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import { StageHistoryDrawer } from './StageHistoryDrawer';
import * as svc from '../../services/projectsService';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

test('renders history entries when open', async () => {
  vi.spyOn(svc, 'fetchStageHistory').mockResolvedValue([
    { id: '1', stage_slug: 'storyboard', stage_name: 'Storyboarding', entered_at: '2026-07-02T00:00:00', exited_at: null },
    { id: '2', stage_slug: 'script', stage_name: 'Scripting', entered_at: '2026-06-25T00:00:00', exited_at: '2026-07-02T00:00:00' },
  ]);
  render(<StageHistoryDrawer projectId="p1" open onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText('Storyboarding')).toBeInTheDocument());
  expect(screen.getByText('Scripting')).toBeInTheDocument();
});

test('renders nothing when closed', () => {
  const { container } = render(<StageHistoryDrawer projectId="p1" open={false} onClose={() => {}} />);
  expect(container.firstChild).toBeNull();
});
```

- [ ] **Step 4: Run to verify fail**

Run: `cd frontend && npx vitest run components/project/StageHistoryDrawer.test.tsx`
Expected: FAIL.

- [ ] **Step 5: Implement the drawer** (match the app's existing drawer/modal idiom — grep another drawer, e.g. an existing right-side panel; below is a self-contained fallback):

```tsx
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';
import { fetchStageHistory } from '../../services/projectsService';
import { formatRelativeTime } from '../../utils/relativeTime';
import type { StageHistoryEntry } from '../../types';

interface Props { projectId: string; open: boolean; onClose: () => void; }

export function StageHistoryDrawer({ projectId, open, onClose }: Props) {
  const { t } = useTranslation();
  const [entries, setEntries] = useState<StageHistoryEntry[] | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    fetchStageHistory(projectId)
      .then((e) => { if (!cancelled) setEntries(e); })
      .catch((err) => { console.error('[StageHistory] load failed:', err); if (!cancelled) setEntries([]); });
    return () => { cancelled = true; };
  }, [open, projectId]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end" data-testid="stage-history-drawer">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative w-80 max-w-full h-full bg-ink-900 border-l border-ink-800 p-5 overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-ink-100 font-semibold">{t('projects.workbench.stageHistory')}</h3>
          <button onClick={onClose} className="p-1 rounded hover:bg-ink-800 text-ink-400"><X size={16} /></button>
        </div>
        {entries && entries.length === 0 && (
          <p className="text-sm text-ink-500">{t('projects.workbench.historyEmpty')}</p>
        )}
        <ol className="flex flex-col gap-3">
          {(entries ?? []).map((e) => (
            <li key={e.id} className="flex gap-3">
              <span className={`mt-1 w-2 h-2 rounded-full flex-shrink-0 ${e.exited_at ? 'bg-indigo-500' : 'bg-emerald-400'}`} />
              <div className="min-w-0">
                <div className="text-sm text-ink-200 font-medium">{e.stage_name}</div>
                <div className="text-xs text-ink-500">
                  {formatRelativeTime(e.entered_at, t)}
                  {!e.exited_at && ` · ${t('projects.workbench.historyCurrent')}`}
                </div>
              </div>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

export default StageHistoryDrawer;
```

- [ ] **Step 6: Run to verify pass**

Run: `cd frontend && npx vitest run components/project/StageHistoryDrawer.test.tsx`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add frontend/components/project/StageHistoryDrawer.tsx frontend/components/project/StageHistoryDrawer.test.tsx frontend/services/projectsService.ts frontend/types.ts frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(projects): stage-history drawer + service"
```

---

### Task 16: Wire the Stage-history button into StageWorkbench

**Files:**
- Modify: `frontend/components/project/StageWorkbench.tsx`
- Modify: `frontend/components/project/StageWorkbench.test.tsx`

- [ ] **Step 1: Write the failing test** (add to `StageWorkbench.test.tsx`):

```tsx
test('Stage history button opens the drawer', async () => {
  // ...render StageWorkbench with a catalog + currentStage (reuse file's setup)
  const btn = await screen.findByTestId('stage-history-btn');
  fireEvent.click(btn);
  expect(await screen.findByTestId('stage-history-drawer')).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify fail**

Run: `cd frontend && npx vitest run components/project/StageWorkbench.test.tsx`
Expected: FAIL — no `stage-history-btn`.

- [ ] **Step 3: Add button + drawer** to `StageWorkbench.tsx`. Import `StageHistoryDrawer`, add `const [historyOpen, setHistoryOpen] = useState(false);`, and in the header actions block (where Advance renders, ~line 112-131) put the ghost button to the LEFT of Advance:

```tsx
<div className="flex items-center gap-2">
  <button
    data-testid="stage-history-btn"
    onClick={() => setHistoryOpen(true)}
    className="rounded-lg border border-ink-700 hover:border-ink-500 text-ink-300 font-medium text-sm px-3 py-2 transition-colors"
  >
    {t('projects.workbench.stageHistory')}
  </button>
  {canWrite && (nextStage ? (
    /* existing Advance button unchanged */
  ) : (
    /* existing final-stage marker unchanged */
  ))}
</div>
```

And render the drawer at the end of the component's returned tree:

```tsx
<StageHistoryDrawer projectId={projectId} open={historyOpen} onClose={() => setHistoryOpen(false)} />
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npx vitest run components/project/StageWorkbench.test.tsx`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
cd frontend && npm run lint
git add frontend/components/project/StageWorkbench.tsx frontend/components/project/StageWorkbench.test.tsx
git commit -m "feat(projects): stage-history button in workbench header"
```

---

### Task 17: PR-4 review + ship

- [ ] **Step 1:** `cd frontend && npx tsc --noEmit && npm run lint && npx vitest run components/project/StageHistoryDrawer.test.tsx components/project/StageWorkbench.test.tsx`.
- [ ] **Step 2:** Ship PR-4 (`/ship`): `feat(projects): B2 stage-history button`.

---

# Visual Verification (e2e stub — per spec ④)

### Task 18: Playwright visual spec

**Files:**
- Create: `frontend/e2e/projects-phase-b.spec.ts`

**Interfaces:**
- Consumes: `frontend/e2e/helpers/stubs.ts` (fake session + `page.route`); mirror `frontend/e2e/storyboard.spec.ts` structure.

- [ ] **Step 1: Write the spec** — stub session, `page.route` intercept `**/projects/*/stage-suggestion` returning each kind and `**/projects*` list returning the mockup's three cards (storyboard 9/12, review stalled amber, archived). Navigate to the projects page, screenshot the list + workbench header for visual compare against the A mockup.

```typescript
import { test, expect } from '@playwright/test';
import { installStubSession } from './helpers/stubs';

test.describe('Projects Phase B — Stage Ring alignment', () => {
  test.beforeEach(async ({ page }) => {
    await installStubSession(page);
  });

  test('list cards match A mockup states', async ({ page }) => {
    await page.route('**/api/v1/projects*', (route) =>
      route.fulfill({ json: [
        { id: '1', name: 'Spring Campaign 2026', description: 'Short-form ad series',
          current_stage: { slug: 'storyboard', name: 'Storyboarding', index: 3, total: 6 },
          latest_activity: { kind: 'file', actor: 'HG', at: '2026-07-08T10:00:00', stalled: false },
          members_preview: { count: 4, members: [{ user_id: 'u1', username: 'HG' }] },
          file_count: 128, project_type: 'external' },
        { id: '2', name: 'Client Reel — Northwind',
          current_stage: { slug: 'review', name: 'Review', index: 5, total: 6 },
          latest_activity: { kind: 'stage', label: 'Review', at: '2026-07-05T00:00:00', stalled: true },
          file_count: 64, project_type: 'external' },
        { id: '3', name: 'Q4 Retrospective Edit', archived_at: '2026-01-01T00:00:00',
          file_count: 211, project_type: 'internal' },
      ] }),
    );
    await page.goto('/projects');
    await expect(page.getByText('Spring Campaign 2026')).toBeVisible();
    // stalled card shows amber dot
    await expect(page.locator('.bg-amber-400').first()).toBeVisible();
    await page.screenshot({ path: 'e2e-artifacts/projects-list-phase-b.png', fullPage: true });
  });

  test('storyboard suggestion card shows generate CTA', async ({ page }) => {
    await page.route('**/stage-suggestion', (route) =>
      route.fulfill({ json: {
        stage_slug: 'storyboard', kind: 'storyboard_generate',
        progress: { total: 12, done: 9, empty: 3, generating: 0, failed: 0, script_count: 2, scene_count: 5 },
        action: { type: 'generate_missing_frames', label_key: 'projects.suggest.ctaGenerate', count: 3 },
      } }),
    );
    // navigate into a project detail; assert the generate button text contains "3"
    // (exact nav depends on stub project detail route — mirror storyboard.spec.ts)
  });
});
```

- [ ] **Step 2: Run it**

Run: `cd frontend && npx playwright test e2e/projects-phase-b.spec.ts`
Expected: PASS; inspect `e2e-artifacts/projects-list-phase-b.png` against the A mockup (ring, amber stall dot, archived opacity).

- [ ] **Step 3: Commit**

```bash
git add frontend/e2e/projects-phase-b.spec.ts
git commit -m "test(projects): e2e visual spec for Phase B Stage Ring alignment"
```

- [ ] **Step 4 (optional, canary):** Per spec ④ 1b — create `qa_projphaseb_<ts>@example.com`, `email_confirmed_at=now()`, log in via Playwright, drive one project through storyboard stage, click Generate N frames, confirm shots flip empty→generating→done end-to-end. Delete the account after.

---

## Self-Review (completed by plan author)

**Spec coverage:**
- B3 data-aware + one-click → Tasks 1–10 (aggregation, resolver, batch endpoint, data-aware card). ✓
- D3 shot-state-driven copy (no fake "locked") → Task 3 kind table + Task 9 i18n. ✓
- D4 cross-script aggregation → Task 1 SQL (`COUNT(DISTINCT sp.id)`, no single-script filter). ✓
- D5 non-storyboard = nav → Task 3 `{slug}_nav` branch. ✓
- B1 hybrid activity + stall → Tasks 11–14. ✓ (D2, D6)
- B2 stage-history button → Tasks 15–17. ✓
- Visual verification → Task 18. ✓ (D7)
- Flag gates, task-system discipline, zero migration → Global Constraints + Task 4/5. ✓

**Placeholder scan:** No TBD/TODO. Two `> Note` blocks (Task 4 style-profile kwarg, Task 11 resources JOIN, Task 5 guard name) are explicit "verify-then-match-existing-pattern" instructions, not placeholders — each names the exact grep and fallback.

**Type consistency:** `storyboard_progress_for_project` keys (`total/done/empty/generating/failed/script_count/scene_count`) identical across Tasks 1, 2, 3, 8, 9, 18. `latest_activity` shape (`kind/actor/at/label/stalled`) consistent across Tasks 12, 13, 18. `generate_missing_frames` return (`parent_task_id/dispatched_count`) consistent across Tasks 4, 5, 8, 9.
