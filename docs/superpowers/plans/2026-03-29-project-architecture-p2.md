# P2: Script Editor Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a ReactFlow-based Script Editor with chapter tree nodes, rich text editing, and AI outline generation — accessible from the Scripts tab within a project.

**Architecture:** Mirror the existing Storyboard Workbench pattern: Zustand store for canvas state, ReactFlow canvas with custom ChapterNode components, backend CRUD via FastAPI + Supabase. The Script Editor page loads from `/team/:teamId/projects/:projectId/scripts/:scriptId`. AI outline generation uses the existing LLM pipeline (unified_task_manager + Celery) to create chapter nodes from a story premise.

**Tech Stack:** React 19, TypeScript, ReactFlow (@xyflow/react), Zustand, FastAPI, Supabase (PostgreSQL), TailwindCSS, i18next

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `supabase/migrations/111_script_editor.sql` | DB schema: script_projects, script_chapters |
| Create | `backend/app/schemas/script.py` | Pydantic request/response schemas |
| Create | `backend/app/repositories/script_repository.py` | Data access for script_projects + script_chapters |
| Create | `backend/app/services/script_service.py` | Business logic: CRUD, AI outline dispatch |
| Create | `backend/app/api/script_projects_router.py` | REST endpoints for script projects |
| Create | `backend/app/api/script_canvas_router.py` | REST endpoints for chapter nodes (canvas sync) |
| Create | `frontend/services/scriptService.ts` | API client for script endpoints |
| Create | `frontend/stores/scriptCanvasStore.ts` | Zustand canvas store for script editor |
| Create | `frontend/features/script/domain/scriptNodes.ts` | Chapter node type definitions |
| Create | `frontend/features/script/nodes/ChapterNode.tsx` | ReactFlow chapter node component |
| Create | `frontend/features/script/nodes/index.ts` | Node type registry |
| Create | `frontend/features/script/ScriptCanvas.tsx` | ReactFlow canvas wrapper |
| Create | `frontend/features/script/ScriptToolbar.tsx` | Toolbar for chapter operations |
| Create | `frontend/features/script/CreateStoryDialog.tsx` | AI outline generation dialog |
| Create | `frontend/pages/ScriptEditor/index.tsx` | Script editor page entry |
| Create | `frontend/pages/ScriptEditor/ScriptEditorPage.tsx` | Main editor page |
| Modify | `frontend/types.ts` | Add ScriptProject, ScriptChapter types |
| Modify | `frontend/router.tsx` | Add script editor route |
| Modify | `frontend/components/project/ProjectScriptsTab.tsx` | Replace placeholder with real list |
| Modify | `backend/app/main.py` | Register new routers |

---

### Task 1: Database Migration — Script Tables

**Files:**
- Create: `supabase/migrations/111_script_editor.sql`

- [ ] **Step 1: Write the migration SQL**

```sql
-- 111_script_editor.sql
-- Script Editor Foundation — P2

-- 1. Script projects (1:N with projects)
CREATE TABLE IF NOT EXISTS script_projects (
  id bigint PRIMARY KEY DEFAULT generate_snowflake_id(),
  project_id bigint NOT NULL REFERENCES projects(id),
  team_id bigint NOT NULL REFERENCES teams(id),
  display_code text,
  name varchar(200) NOT NULL,
  description text,
  settings_json jsonb DEFAULT '{}',
  viewport_json jsonb,
  status text DEFAULT 'active',
  created_by uuid NOT NULL REFERENCES auth.users(id),
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_script_projects_project_id
  ON script_projects(project_id);
CREATE INDEX IF NOT EXISTS idx_script_projects_team_id
  ON script_projects(team_id);

-- 2. Script chapters (tree structure via parent_chapter_id)
CREATE TABLE IF NOT EXISTS script_chapters (
  id bigint PRIMARY KEY DEFAULT generate_snowflake_id(),
  script_id bigint NOT NULL REFERENCES script_projects(id) ON DELETE CASCADE,
  parent_chapter_id bigint REFERENCES script_chapters(id),
  chapter_number int,
  title text,
  summary text,
  content text,
  branch_label text,
  branch_type text,
  position_x float DEFAULT 0,
  position_y float DEFAULT 0,
  width float,
  height float,
  data_json jsonb DEFAULT '{}',
  sort_order int DEFAULT 0,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_script_chapters_script_id
  ON script_chapters(script_id);
CREATE INDEX IF NOT EXISTS idx_script_chapters_parent
  ON script_chapters(parent_chapter_id);

-- 3. Trigger: auto-update updated_at on script_projects
CREATE OR REPLACE FUNCTION update_script_projects_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_script_projects_updated_at
  BEFORE UPDATE ON script_projects
  FOR EACH ROW EXECUTE FUNCTION update_script_projects_updated_at();

-- 4. Trigger: auto-update updated_at on script_chapters
CREATE OR REPLACE FUNCTION update_script_chapters_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_script_chapters_updated_at
  BEFORE UPDATE ON script_chapters
  FOR EACH ROW EXECUTE FUNCTION update_script_chapters_updated_at();
```

- [ ] **Step 2: Apply migration to local Supabase**

Run: `PGPASSWORD=postgres psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -f supabase/migrations/111_script_editor.sql`
Expected: CREATE TABLE (x2), CREATE INDEX (x4), CREATE FUNCTION (x2), CREATE TRIGGER (x2).

- [ ] **Step 3: Verify schema**

Run: `PGPASSWORD=postgres psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -c "\d script_projects" | head -15`
Expected: Shows table with `id bigint` default `generate_snowflake_id()`, `project_id bigint`, etc.

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/111_script_editor.sql
git commit -m "feat: add script editor migration (script_projects, script_chapters)"
```

---

### Task 2: Backend Schemas — Script Pydantic Models

**Files:**
- Create: `backend/app/schemas/script.py`

- [ ] **Step 1: Create script schemas**

```python
"""Script Editor request/response Pydantic schemas."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Script Project schemas
# ---------------------------------------------------------------------------


class ScriptProjectCreate(BaseModel):
    """Request body for creating a new script project."""

    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    project_id: int


class ScriptProjectUpdate(BaseModel):
    """Request body for updating an existing script project."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    status: Optional[str] = Field(None, pattern="^(active|archived)$")
    settings_json: Optional[Dict[str, Any]] = None


class ScriptProjectResponse(BaseModel):
    """API response for a single script project."""

    id: str
    project_id: str
    team_id: str
    created_by: str
    name: str
    description: Optional[str] = None
    display_code: Optional[str] = None
    status: str
    settings_json: Optional[Dict[str, Any]] = None
    viewport_json: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Script Chapter schemas
# ---------------------------------------------------------------------------


class ScriptChapterCreate(BaseModel):
    """Request body for creating a chapter node."""

    title: Optional[str] = Field(None, max_length=200)
    summary: Optional[str] = Field(None, max_length=5000)
    content: Optional[str] = None
    chapter_number: Optional[int] = None
    parent_chapter_id: Optional[str] = None
    branch_label: Optional[str] = Field(None, max_length=100)
    branch_type: Optional[str] = Field(None, pattern="^(condition|choice)$")
    position_x: float = Field(default=0.0)
    position_y: float = Field(default=0.0)
    width: Optional[float] = None
    height: Optional[float] = None
    data_json: Dict[str, Any] = Field(default_factory=dict)
    sort_order: int = Field(default=0)


class ScriptChapterUpdate(BaseModel):
    """Request body for updating a chapter node."""

    title: Optional[str] = Field(None, max_length=200)
    summary: Optional[str] = Field(None, max_length=5000)
    content: Optional[str] = None
    chapter_number: Optional[int] = None
    parent_chapter_id: Optional[str] = None
    branch_label: Optional[str] = Field(None, max_length=100)
    branch_type: Optional[str] = Field(None, pattern="^(condition|choice)$")
    position_x: Optional[float] = None
    position_y: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None
    data_json: Optional[Dict[str, Any]] = None
    sort_order: Optional[int] = None


# ---------------------------------------------------------------------------
# Canvas sync schema
# ---------------------------------------------------------------------------


class ScriptCanvasSyncRequest(BaseModel):
    """Request body for a full script canvas sync operation."""

    added_chapters: List[ScriptChapterCreate] = Field(default_factory=list)
    updated_chapters: List[Dict[str, Any]] = Field(default_factory=list)
    deleted_chapter_ids: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# AI outline generation
# ---------------------------------------------------------------------------


class GenerateOutlineRequest(BaseModel):
    """Request body for AI-generated story outline."""

    script_id: str
    premise: str = Field(..., min_length=10, max_length=10000)
    chapter_count: int = Field(default=5, ge=2, le=20)
    style_guide: Optional[str] = Field(None, max_length=2000)
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/schemas/script.py
git commit -m "feat: script editor Pydantic schemas"
```

---

### Task 3: Backend Repository — Script Data Access

**Files:**
- Create: `backend/app/repositories/script_repository.py`

- [ ] **Step 1: Create script repository**

```python
"""Script Repository Layer — data access for script_projects and script_chapters."""

from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class ScriptProjectRepository:
    """CRUD + list operations for script_projects."""

    TABLE_NAME = "script_projects"

    async def _get_client(self):
        return await get_async_supabase_admin()

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_NAME).insert(data).execute()
            logger.info(f"Created script project: {data.get('name')}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create script project: {e}")
            raise

    async def update(self, script_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .update(data)
                .eq("id", script_id)
                .execute()
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update script project {script_id}: {e}")
            raise

    async def get_by_id(self, script_id: str) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .select("*")
                .eq("id", script_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get script project {script_id}: {e}")
            return None

    async def list_by_project(
        self,
        project_id: int,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            offset = (page - 1) * limit

            count_query = (
                client.table(self.TABLE_NAME)
                .select("id", count="exact")
                .eq("project_id", project_id)
                .neq("status", "deleted")
            )
            if search:
                count_query = count_query.ilike("name", f"%{search}%")
            count_result = await count_query.execute()
            total = count_result.count or 0

            data_query = (
                client.table(self.TABLE_NAME)
                .select("*")
                .eq("project_id", project_id)
                .neq("status", "deleted")
                .order("updated_at", desc=True)
                .range(offset, offset + limit - 1)
            )
            if search:
                data_query = data_query.ilike("name", f"%{search}%")
            data_result = await data_query.execute()

            return {
                "items": data_result.data or [],
                "total": total,
                "page": page,
                "limit": limit,
            }
        except Exception as e:
            logger.error(f"Failed to list script projects for project {project_id}: {e}")
            return {"items": [], "total": 0, "page": page, "limit": limit}

    async def soft_delete(self, script_id: str) -> None:
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_NAME)
                .update({"status": "deleted"})
                .eq("id", script_id)
                .execute()
            )
            logger.info(f"Soft-deleted script project {script_id}")
        except Exception as e:
            logger.error(f"Failed to soft-delete script project {script_id}: {e}")
            raise


class ScriptChapterRepository:
    """CRUD for script_chapters."""

    TABLE_NAME = "script_chapters"

    async def _get_client(self):
        return await get_async_supabase_admin()

    async def bulk_upsert(
        self, script_id: str, chapters: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        if not chapters:
            return []
        try:
            client = await self._get_client()
            rows = [{**ch, "script_id": script_id} for ch in chapters]
            result = (
                await client.table(self.TABLE_NAME)
                .upsert(rows, on_conflict="id")
                .execute()
            )
            logger.info(f"Bulk-upserted {len(rows)} chapters for script {script_id}")
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to bulk-upsert chapters for script {script_id}: {e}")
            raise

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_NAME).insert(data).execute()
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create chapter: {e}")
            raise

    async def update(self, chapter_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .update(data)
                .eq("id", chapter_id)
                .execute()
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update chapter {chapter_id}: {e}")
            raise

    async def delete(self, chapter_id: str) -> None:
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_NAME)
                .delete()
                .eq("id", chapter_id)
                .execute()
            )
            logger.info(f"Deleted chapter {chapter_id}")
        except Exception as e:
            logger.error(f"Failed to delete chapter {chapter_id}: {e}")
            raise

    async def get_by_script(self, script_id: str) -> List[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .select("*")
                .eq("script_id", script_id)
                .order("sort_order")
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get chapters for script {script_id}: {e}")
            return []
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/repositories/script_repository.py
git commit -m "feat: script repository layer (projects + chapters)"
```

---

### Task 4: Backend Service — Script Business Logic

**Files:**
- Create: `backend/app/services/script_service.py`

- [ ] **Step 1: Create script service**

```python
"""Script Service — business logic for script projects and chapters."""

from typing import Any, Dict, List, Optional

from loguru import logger

from app.repositories.script_repository import (
    ScriptChapterRepository,
    ScriptProjectRepository,
)
from app.services.display_code_service import generate_display_code


class ScriptService:
    """Orchestrates script project and chapter operations."""

    def __init__(self) -> None:
        self.project_repo = ScriptProjectRepository()
        self.chapter_repo = ScriptChapterRepository()

    async def create_project(
        self,
        team_id: str,
        user_id: str,
        project_id: int,
        name: str,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "team_id": team_id,
            "created_by": user_id,
            "project_id": project_id,
            "name": name,
        }
        if description is not None:
            data["description"] = description

        project = await self.project_repo.create(data)

        try:
            display_code = await generate_display_code(int(team_id), "S")
            project = await self.project_repo.update(
                project["id"], {"display_code": display_code}
            )
        except Exception as exc:
            logger.warning(f"Failed to generate display_code for script: {exc}")

        return project

    async def list_projects(
        self,
        project_id: int,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self.project_repo.list_by_project(
            project_id=project_id,
            page=page,
            limit=limit,
            search=search,
        )

    async def get_project_full(self, script_id: str) -> Optional[Dict[str, Any]]:
        project = await self.project_repo.get_by_id(script_id)
        if not project:
            return None
        chapters = await self.chapter_repo.get_by_script(script_id)
        return {"project": project, "chapters": chapters}

    async def update_project(
        self, script_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        return await self.project_repo.update(script_id, data)

    async def soft_delete_project(self, script_id: str) -> None:
        await self.project_repo.soft_delete(script_id)

    async def update_viewport(
        self, script_id: str, viewport_json: Dict[str, Any]
    ) -> None:
        await self.project_repo.update(script_id, {"viewport_json": viewport_json})

    # ─── Chapter operations ───────────────────────────────────────────

    async def create_chapter(
        self, script_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        data["script_id"] = script_id
        return await self.chapter_repo.create(data)

    async def update_chapter(
        self, chapter_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        return await self.chapter_repo.update(chapter_id, data)

    async def delete_chapter(self, chapter_id: str) -> None:
        await self.chapter_repo.delete(chapter_id)

    async def sync_canvas(
        self,
        script_id: str,
        added: List[Dict[str, Any]],
        updated: List[Dict[str, Any]],
        deleted_ids: List[str],
    ) -> Dict[str, Any]:
        if deleted_ids:
            for cid in deleted_ids:
                await self.chapter_repo.delete(cid)

        if added:
            await self.chapter_repo.bulk_upsert(script_id, added)

        if updated:
            for ch in updated:
                ch_id = ch.pop("id", None)
                if ch_id:
                    await self.chapter_repo.update(ch_id, ch)

        chapters = await self.chapter_repo.get_by_script(script_id)
        return {"chapters": chapters}
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/script_service.py
git commit -m "feat: script service with CRUD and canvas sync"
```

---

### Task 5: Backend Routers — Script API Endpoints

**Files:**
- Create: `backend/app/api/script_projects_router.py`
- Create: `backend/app/api/script_canvas_router.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Create script_projects_router.py**

```python
"""Script Projects Router — CRUD endpoints for script projects."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from app.core.deps import AuthDep
from app.db.supabase_client import get_async_supabase_admin
from app.schemas.script import ScriptProjectCreate, ScriptProjectUpdate
from app.services.script_service import ScriptService

router = APIRouter(prefix="/scripts/projects")


async def _get_team_id_for_user(user_id: str) -> Optional[str]:
    admin = await get_async_supabase_admin()
    result = (
        await admin.table("team_members")
        .select("team_id")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    return result.data[0]["team_id"] if result.data else None


async def _require_team_id(user_id: str) -> str:
    team_id = await _get_team_id_for_user(user_id)
    if not team_id:
        raise HTTPException(status_code=400, detail="User has no associated team")
    return team_id


@router.post("/")
async def create_script_project(
    auth: AuthDep, body: ScriptProjectCreate
) -> Dict[str, Any]:
    team_id = await _require_team_id(auth.user_id)
    try:
        svc = ScriptService()
        project = await svc.create_project(
            team_id=team_id,
            user_id=auth.user_id,
            project_id=body.project_id,
            name=body.name,
            description=body.description,
        )
        return {"success": True, "data": project}
    except Exception as exc:
        logger.error("[Scripts] create_project failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to create script: {exc}")


@router.get("/")
async def list_script_projects(
    auth: AuthDep,
    project_id: int = Query(...),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, max_length=200),
) -> Dict[str, Any]:
    try:
        svc = ScriptService()
        result = await svc.list_projects(
            project_id=project_id,
            page=page,
            limit=limit,
            search=search,
        )
        return {"success": True, "data": result}
    except Exception as exc:
        logger.error("[Scripts] list_projects failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to list scripts: {exc}")


@router.get("/{script_id}")
async def get_script_project(auth: AuthDep, script_id: str) -> Dict[str, Any]:
    try:
        svc = ScriptService()
        project = await svc.get_project_full(script_id)
        if not project:
            raise HTTPException(status_code=404, detail="Script project not found")
        return {"success": True, "data": project}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Scripts] get_project %s failed: %s", script_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to get script: {exc}")


@router.put("/{script_id}")
async def update_script_project(
    auth: AuthDep, script_id: str, body: ScriptProjectUpdate
) -> Dict[str, Any]:
    try:
        svc = ScriptService()
        updated = await svc.update_project(script_id, body.model_dump(exclude_none=True))
        return {"success": True, "data": updated}
    except Exception as exc:
        logger.error("[Scripts] update_project %s failed: %s", script_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to update script: {exc}")


@router.delete("/{script_id}")
async def delete_script_project(auth: AuthDep, script_id: str) -> Dict[str, Any]:
    try:
        svc = ScriptService()
        await svc.soft_delete_project(script_id)
        return {"success": True}
    except Exception as exc:
        logger.error("[Scripts] delete_project %s failed: %s", script_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to delete script: {exc}")


@router.patch("/{script_id}/viewport")
async def update_viewport(
    auth: AuthDep, script_id: str, body: Dict[str, Any]
) -> Dict[str, Any]:
    try:
        svc = ScriptService()
        await svc.update_viewport(script_id, body)
        return {"success": True}
    except Exception as exc:
        logger.error("[Scripts] update_viewport %s failed: %s", script_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to update viewport: {exc}")
```

- [ ] **Step 2: Create script_canvas_router.py**

```python
"""Script Canvas Router — chapter node CRUD and canvas sync endpoints."""

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.core.deps import AuthDep
from app.schemas.script import ScriptCanvasSyncRequest, ScriptChapterCreate, ScriptChapterUpdate
from app.services.script_service import ScriptService

router = APIRouter(prefix="/scripts/projects")


@router.post("/{script_id}/chapters")
async def create_chapter(
    auth: AuthDep, script_id: str, body: ScriptChapterCreate
) -> Dict[str, Any]:
    try:
        svc = ScriptService()
        chapter = await svc.create_chapter(script_id, body.model_dump(exclude_none=True))
        return {"success": True, "data": chapter}
    except Exception as exc:
        logger.error("[Scripts] create_chapter failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to create chapter: {exc}")


@router.put("/chapters/{chapter_id}")
async def update_chapter(
    auth: AuthDep, chapter_id: str, body: ScriptChapterUpdate
) -> Dict[str, Any]:
    try:
        svc = ScriptService()
        chapter = await svc.update_chapter(chapter_id, body.model_dump(exclude_none=True))
        return {"success": True, "data": chapter}
    except Exception as exc:
        logger.error("[Scripts] update_chapter %s failed: %s", chapter_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to update chapter: {exc}")


@router.delete("/chapters/{chapter_id}")
async def delete_chapter(auth: AuthDep, chapter_id: str) -> Dict[str, Any]:
    try:
        svc = ScriptService()
        await svc.delete_chapter(chapter_id)
        return {"success": True}
    except Exception as exc:
        logger.error("[Scripts] delete_chapter %s failed: %s", chapter_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to delete chapter: {exc}")


@router.post("/{script_id}/canvas/sync")
async def sync_canvas(
    auth: AuthDep, script_id: str, body: ScriptCanvasSyncRequest
) -> Dict[str, Any]:
    try:
        svc = ScriptService()
        added = [ch.model_dump(exclude_none=True) for ch in body.added_chapters]
        result = await svc.sync_canvas(
            script_id=script_id,
            added=added,
            updated=body.updated_chapters,
            deleted_ids=body.deleted_chapter_ids,
        )
        return {"success": True, "data": result}
    except Exception as exc:
        logger.error("[Scripts] sync_canvas %s failed: %s", script_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to sync canvas: {exc}")
```

- [ ] **Step 3: Register routers in main.py**

In `backend/app/main.py`, find where storyboard routers are included (look for `sb_projects_router`), and add below them:

```python
from app.api import script_projects_router, script_canvas_router
app.include_router(script_projects_router.router, prefix="/api/v1", tags=["scripts"])
app.include_router(script_canvas_router.router, prefix="/api/v1", tags=["scripts"])
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/script_projects_router.py backend/app/api/script_canvas_router.py backend/app/main.py
git commit -m "feat: script API routers (projects CRUD + chapter canvas sync)"
```

---

### Task 6: Frontend Types + Service

**Files:**
- Modify: `frontend/types.ts`
- Create: `frontend/services/scriptService.ts`

- [ ] **Step 1: Add TypeScript types**

Add to `frontend/types.ts` after the `StoryboardProject` interface:

```typescript
export interface ScriptProject {
  id: string;
  project_id: string;
  team_id: string;
  created_by: string;
  name: string;
  description?: string;
  display_code?: string;
  settings_json?: Record<string, unknown>;
  viewport_json?: { x: number; y: number; zoom: number };
  status: 'active' | 'archived' | 'deleted';
  created_at: string;
  updated_at: string;
}

export interface ScriptChapter {
  id: string;
  script_id: string;
  parent_chapter_id?: string;
  chapter_number?: number;
  title?: string;
  summary?: string;
  content?: string;
  branch_label?: string;
  branch_type?: 'condition' | 'choice';
  position_x: number;
  position_y: number;
  width?: number;
  height?: number;
  data_json: Record<string, unknown>;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface ScriptProjectSummary {
  id: string;
  name: string;
  display_code?: string;
  status: string;
  created_at: string;
  updated_at: string;
  chapter_count?: number;
}
```

- [ ] **Step 2: Create scriptService.ts**

```typescript
import { getAuthHeaders } from './parserService';
import { ScriptProject, ScriptChapter, ScriptProjectSummary } from '../types';

const getApiUrl = (): string => {
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    return import.meta.env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API error ${res.status}: ${text}`);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

async function unwrapResponse<T>(res: Response): Promise<T> {
  const body = await handleResponse<{ success: boolean; data: T }>(res);
  return body.data;
}

// ─── Script Project CRUD ─────────────────────────────────────────────────────

export async function fetchScriptProjects(
  projectId: string,
  page = 1,
  limit = 20,
): Promise<{ data: ScriptProjectSummary[]; total: number }> {
  const headers = await getAuthHeaders();
  const params = new URLSearchParams({
    project_id: projectId,
    page: String(page),
    limit: String(limit),
  });
  const res = await fetch(`${getApiUrl()}/api/v1/scripts/projects?${params}`, { headers });
  const result = await unwrapResponse<{ items: ScriptProjectSummary[]; total: number }>(res);
  return { data: result?.items ?? [], total: result?.total ?? 0 };
}

export interface ScriptProjectFull extends ScriptProject {
  chapters: ScriptChapter[];
}

export async function fetchScriptProject(scriptId: string): Promise<ScriptProjectFull> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/scripts/projects/${scriptId}`, { headers });
  const raw = await unwrapResponse<{ project: ScriptProject; chapters: ScriptChapter[] }>(res);
  return { ...raw.project, chapters: raw.chapters ?? [] };
}

export async function createScriptProject(data: {
  project_id: string;
  name: string;
  description?: string;
}): Promise<ScriptProject> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/scripts/projects`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<ScriptProject>(res);
}

export async function updateScriptProject(
  scriptId: string,
  data: Partial<Pick<ScriptProject, 'name' | 'description' | 'status' | 'settings_json'>>,
): Promise<ScriptProject> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/scripts/projects/${scriptId}`, {
    method: 'PUT',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<ScriptProject>(res);
}

export async function deleteScriptProject(scriptId: string): Promise<void> {
  const headers = await getAuthHeaders();
  await fetch(`${getApiUrl()}/api/v1/scripts/projects/${scriptId}`, {
    method: 'DELETE',
    headers,
  });
}

// ─── Chapter CRUD ────────────────────────────────────────────────────────────

export async function createChapter(
  scriptId: string,
  data: Partial<ScriptChapter>,
): Promise<ScriptChapter> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/scripts/projects/${scriptId}/chapters`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<ScriptChapter>(res);
}

export async function updateChapter(
  chapterId: string,
  data: Partial<ScriptChapter>,
): Promise<ScriptChapter> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/scripts/projects/chapters/${chapterId}`, {
    method: 'PUT',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<ScriptChapter>(res);
}

export async function deleteChapter(chapterId: string): Promise<void> {
  const headers = await getAuthHeaders();
  await fetch(`${getApiUrl()}/api/v1/scripts/projects/chapters/${chapterId}`, {
    method: 'DELETE',
    headers,
  });
}

// ─── Canvas Sync ─────────────────────────────────────────────────────────────

export async function syncScriptCanvas(
  scriptId: string,
  syncRequest: {
    added_chapters: Partial<ScriptChapter>[];
    updated_chapters: Partial<ScriptChapter>[];
    deleted_chapter_ids: string[];
  },
): Promise<{ chapters: ScriptChapter[] }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/scripts/projects/${scriptId}/canvas/sync`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(syncRequest),
  });
  return unwrapResponse<{ chapters: ScriptChapter[] }>(res);
}

// ─── Viewport ────────────────────────────────────────────────────────────────

export async function updateScriptViewport(
  scriptId: string,
  viewport: { x: number; y: number; zoom: number },
): Promise<void> {
  const headers = await getAuthHeaders();
  await fetch(`${getApiUrl()}/api/v1/scripts/projects/${scriptId}/viewport`, {
    method: 'PATCH',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(viewport),
  });
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/types.ts frontend/services/scriptService.ts
git commit -m "feat: frontend types and service for script editor"
```

---

### Task 7: Zustand Store — Script Canvas State

**Files:**
- Create: `frontend/stores/scriptCanvasStore.ts`

- [ ] **Step 1: Create the store**

Mirror the storyboard `canvasStore.ts` pattern but simplified for chapter nodes only. Key state: chapter nodes, edges (parent→child connections), selected node, history.

```typescript
import { create } from 'zustand';
import {
  type Node,
  type Edge,
  type NodeChange,
  type EdgeChange,
  type Connection,
  applyNodeChanges,
  applyEdgeChanges,
  addEdge as addReactFlowEdge,
} from '@xyflow/react';

export interface ChapterNodeData {
  title: string;
  summary: string;
  content: string;
  chapterNumber: number;
  branchLabel?: string;
  branchType?: 'condition' | 'choice';
  isExpanded?: boolean;
  [key: string]: unknown;
}

export type ScriptNode = Node<ChapterNodeData>;
export type ScriptEdge = Edge;

interface HistorySnapshot {
  nodes: ScriptNode[];
  edges: ScriptEdge[];
}

const MAX_HISTORY = 50;

interface ScriptCanvasState {
  nodes: ScriptNode[];
  edges: ScriptEdge[];
  selectedNodeId: string | null;
  history: { past: HistorySnapshot[]; future: HistorySnapshot[] };
  currentViewport: { x: number; y: number; zoom: number };

  // ReactFlow handlers
  onNodesChange: (changes: NodeChange<ScriptNode>[]) => void;
  onEdgesChange: (changes: EdgeChange<ScriptEdge>[]) => void;
  onConnect: (connection: Connection) => void;

  // Data
  setCanvasData: (nodes: ScriptNode[], edges: ScriptEdge[]) => void;
  clearCanvas: () => void;

  // Node operations
  addChapterNode: (position: { x: number; y: number }, data?: Partial<ChapterNodeData>) => string;
  updateNodeData: (nodeId: string, data: Partial<ChapterNodeData>) => void;
  deleteNode: (nodeId: string) => void;
  setSelectedNode: (nodeId: string | null) => void;

  // Viewport
  setViewportState: (viewport: { x: number; y: number; zoom: number }) => void;

  // History
  undo: () => boolean;
  redo: () => boolean;
}

function pushHistory(state: ScriptCanvasState): { past: HistorySnapshot[]; future: HistorySnapshot[] } {
  const snapshot: HistorySnapshot = {
    nodes: JSON.parse(JSON.stringify(state.nodes)),
    edges: JSON.parse(JSON.stringify(state.edges)),
  };
  const past = [...state.history.past, snapshot].slice(-MAX_HISTORY);
  return { past, future: [] };
}

export const useScriptCanvasStore = create<ScriptCanvasState>((set, get) => ({
  nodes: [],
  edges: [],
  selectedNodeId: null,
  history: { past: [], future: [] },
  currentViewport: { x: 0, y: 0, zoom: 1 },

  onNodesChange: (changes) => {
    set((state) => ({
      nodes: applyNodeChanges(changes, state.nodes),
    }));
  },

  onEdgesChange: (changes) => {
    set((state) => ({
      edges: applyEdgeChanges(changes, state.edges),
    }));
  },

  onConnect: (connection) => {
    set((state) => ({
      edges: addReactFlowEdge(connection, state.edges),
      history: pushHistory(state),
    }));
  },

  setCanvasData: (nodes, edges) => {
    set({ nodes, edges, history: { past: [], future: [] } });
  },

  clearCanvas: () => {
    set((state) => ({
      nodes: [],
      edges: [],
      selectedNodeId: null,
      history: pushHistory(state),
    }));
  },

  addChapterNode: (position, data) => {
    const id = crypto.randomUUID();
    const chapterCount = get().nodes.length;
    const newNode: ScriptNode = {
      id,
      type: 'chapterNode',
      position,
      data: {
        title: data?.title ?? `Chapter ${chapterCount + 1}`,
        summary: data?.summary ?? '',
        content: data?.content ?? '',
        chapterNumber: data?.chapterNumber ?? chapterCount + 1,
        branchLabel: data?.branchLabel,
        branchType: data?.branchType,
        ...data,
      },
    };
    set((state) => ({
      nodes: [...state.nodes, newNode],
      history: pushHistory(state),
    }));
    return id;
  },

  updateNodeData: (nodeId, data) => {
    set((state) => ({
      nodes: state.nodes.map((n) =>
        n.id === nodeId ? { ...n, data: { ...n.data, ...data } } : n,
      ),
      history: pushHistory(state),
    }));
  },

  deleteNode: (nodeId) => {
    set((state) => ({
      nodes: state.nodes.filter((n) => n.id !== nodeId),
      edges: state.edges.filter((e) => e.source !== nodeId && e.target !== nodeId),
      selectedNodeId: state.selectedNodeId === nodeId ? null : state.selectedNodeId,
      history: pushHistory(state),
    }));
  },

  setSelectedNode: (nodeId) => set({ selectedNodeId: nodeId }),

  setViewportState: (viewport) => set({ currentViewport: viewport }),

  undo: () => {
    const { history, nodes, edges } = get();
    if (history.past.length === 0) return false;
    const prev = history.past[history.past.length - 1];
    set({
      nodes: prev.nodes,
      edges: prev.edges,
      history: {
        past: history.past.slice(0, -1),
        future: [{ nodes: JSON.parse(JSON.stringify(nodes)), edges: JSON.parse(JSON.stringify(edges)) }, ...history.future],
      },
    });
    return true;
  },

  redo: () => {
    const { history, nodes, edges } = get();
    if (history.future.length === 0) return false;
    const next = history.future[0];
    set({
      nodes: next.nodes,
      edges: next.edges,
      history: {
        past: [...history.past, { nodes: JSON.parse(JSON.stringify(nodes)), edges: JSON.parse(JSON.stringify(edges)) }],
        future: history.future.slice(1),
      },
    });
    return true;
  },
}));
```

- [ ] **Step 2: Commit**

```bash
git add frontend/stores/scriptCanvasStore.ts
git commit -m "feat: Zustand store for script canvas state"
```

---

### Task 8: Script Canvas Components — ChapterNode + Canvas + Toolbar

**Files:**
- Create: `frontend/features/script/domain/scriptNodes.ts`
- Create: `frontend/features/script/nodes/ChapterNode.tsx`
- Create: `frontend/features/script/nodes/index.ts`
- Create: `frontend/features/script/ScriptCanvas.tsx`
- Create: `frontend/features/script/ScriptToolbar.tsx`

- [ ] **Step 1: Create scriptNodes.ts — node type definitions**

```typescript
export const SCRIPT_NODE_TYPES = {
  chapter: 'chapterNode',
} as const;

export type ScriptNodeType = (typeof SCRIPT_NODE_TYPES)[keyof typeof SCRIPT_NODE_TYPES];
```

- [ ] **Step 2: Create ChapterNode.tsx**

A chapter node displays: chapter number badge, title (editable), summary text, and action buttons (expand, branch). Branch edges have colored handles.

```typescript
import { memo, useState, useCallback } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { BookOpen, GitBranch, ChevronDown, ChevronUp, Trash2 } from 'lucide-react';
import { type ChapterNodeData, useScriptCanvasStore, type ScriptNode } from '../../../stores/scriptCanvasStore';

export const ChapterNode = memo(({ id, data, selected }: NodeProps<ScriptNode>) => {
  const updateNodeData = useScriptCanvasStore((s) => s.updateNodeData);
  const deleteNode = useScriptCanvasStore((s) => s.deleteNode);
  const [editing, setEditing] = useState(false);
  const [titleInput, setTitleInput] = useState(data.title);
  const [expanded, setExpanded] = useState(data.isExpanded ?? true);

  const handleTitleBlur = useCallback(() => {
    setEditing(false);
    if (titleInput.trim() !== data.title) {
      updateNodeData(id, { title: titleInput.trim() });
    }
  }, [id, titleInput, data.title, updateNodeData]);

  const handleSummaryChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      updateNodeData(id, { summary: e.target.value });
    },
    [id, updateNodeData],
  );

  return (
    <div
      className={`w-[320px] rounded-xl border bg-zinc-900 shadow-lg transition-colors ${
        selected ? 'border-indigo-500 ring-1 ring-indigo-500/30' : 'border-zinc-700'
      } ${data.branchType ? 'border-l-4 border-l-amber-500' : ''}`}
    >
      {/* Target handle */}
      <Handle type="target" position={Position.Top} id="target" className="!w-3 !h-3 !bg-zinc-500" />

      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-zinc-800">
        <span className="flex items-center justify-center w-6 h-6 rounded-md bg-indigo-600 text-[11px] font-bold text-white">
          {data.chapterNumber}
        </span>
        {editing ? (
          <input
            className="flex-1 bg-transparent text-sm font-semibold text-white outline-none border-b border-indigo-500"
            value={titleInput}
            onChange={(e) => setTitleInput(e.target.value)}
            onBlur={handleTitleBlur}
            onKeyDown={(e) => e.key === 'Enter' && handleTitleBlur()}
            autoFocus
          />
        ) : (
          <button
            className="flex-1 text-left text-sm font-semibold text-zinc-100 truncate hover:text-white"
            onDoubleClick={() => setEditing(true)}
          >
            {data.title || 'Untitled Chapter'}
          </button>
        )}
        {data.branchLabel && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-900/50 text-amber-400 font-medium">
            {data.branchLabel}
          </span>
        )}
        <button
          onClick={() => setExpanded(!expanded)}
          className="text-zinc-500 hover:text-zinc-300 p-0.5"
        >
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
      </div>

      {/* Body */}
      {expanded && (
        <div className="px-3 py-2 space-y-2">
          <textarea
            className="w-full bg-zinc-800 text-xs text-zinc-300 rounded-md px-2 py-1.5 resize-none outline-none focus:ring-1 focus:ring-indigo-500/50 min-h-[60px]"
            placeholder="Chapter summary..."
            value={data.summary}
            onChange={handleSummaryChange}
            rows={3}
          />

          {/* Action buttons */}
          <div className="flex items-center gap-1.5">
            <button
              className="flex items-center gap-1 text-[11px] text-zinc-500 hover:text-indigo-400 px-1.5 py-0.5 rounded hover:bg-zinc-800 transition-colors"
              title="Expand with AI (P3)"
              disabled
            >
              <BookOpen size={12} />
              Expand
            </button>
            <button
              className="flex items-center gap-1 text-[11px] text-zinc-500 hover:text-amber-400 px-1.5 py-0.5 rounded hover:bg-zinc-800 transition-colors"
              title="Create branch (P3)"
              disabled
            >
              <GitBranch size={12} />
              Branch
            </button>
            <div className="flex-1" />
            <button
              onClick={() => deleteNode(id)}
              className="text-zinc-600 hover:text-red-400 p-0.5 rounded hover:bg-zinc-800 transition-colors"
              title="Delete chapter"
            >
              <Trash2 size={12} />
            </button>
          </div>
        </div>
      )}

      {/* Source handle */}
      <Handle type="source" position={Position.Bottom} id="source" className="!w-3 !h-3 !bg-indigo-500" />
    </div>
  );
});

ChapterNode.displayName = 'ChapterNode';
```

- [ ] **Step 3: Create nodes/index.ts**

```typescript
import type { NodeTypes } from '@xyflow/react';
import { ChapterNode } from './ChapterNode';

export const scriptNodeTypes: NodeTypes = {
  chapterNode: ChapterNode,
};
```

- [ ] **Step 4: Create ScriptCanvas.tsx**

```typescript
import { useCallback } from 'react';
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useScriptCanvasStore } from '../../stores/scriptCanvasStore';
import { scriptNodeTypes } from './nodes';

export function ScriptCanvas() {
  const nodes = useScriptCanvasStore((s) => s.nodes);
  const edges = useScriptCanvasStore((s) => s.edges);
  const onNodesChange = useScriptCanvasStore((s) => s.onNodesChange);
  const onEdgesChange = useScriptCanvasStore((s) => s.onEdgesChange);
  const onConnect = useScriptCanvasStore((s) => s.onConnect);
  const setSelectedNode = useScriptCanvasStore((s) => s.setSelectedNode);
  const setViewportState = useScriptCanvasStore((s) => s.setViewportState);

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: { id: string }) => {
      setSelectedNode(node.id);
    },
    [setSelectedNode],
  );

  const handlePaneClick = useCallback(() => {
    setSelectedNode(null);
  }, [setSelectedNode]);

  return (
    <div className="w-full h-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={scriptNodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={handleNodeClick}
        onPaneClick={handlePaneClick}
        onMoveEnd={(_, viewport) => setViewportState(viewport)}
        fitView
        deleteKeyCode={['Backspace', 'Delete']}
        className="bg-zinc-950"
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#27272a" />
        <Controls className="!bg-zinc-900 !border-zinc-700 [&>button]:!bg-zinc-800 [&>button]:!border-zinc-700 [&>button]:!text-zinc-400" />
        <MiniMap
          nodeColor="#4f46e5"
          maskColor="rgba(0, 0, 0, 0.7)"
          className="!bg-zinc-900 !border-zinc-700"
        />
      </ReactFlow>
    </div>
  );
}
```

- [ ] **Step 5: Create ScriptToolbar.tsx**

```typescript
import { Plus, Undo2, Redo2 } from 'lucide-react';
import { useScriptCanvasStore } from '../../stores/scriptCanvasStore';

interface Props {
  onCreateStory: () => void;
}

export function ScriptToolbar({ onCreateStory }: Props) {
  const addChapterNode = useScriptCanvasStore((s) => s.addChapterNode);
  const undo = useScriptCanvasStore((s) => s.undo);
  const redo = useScriptCanvasStore((s) => s.redo);

  const handleAddChapter = () => {
    const offsetX = 100 + Math.random() * 200;
    const offsetY = 100 + Math.random() * 200;
    addChapterNode({ x: offsetX, y: offsetY });
  };

  return (
    <div className="flex items-center gap-2 px-3 py-2 bg-zinc-900 border-b border-zinc-800">
      <button
        onClick={handleAddChapter}
        className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg px-3 py-1.5 text-xs font-medium transition-colors"
      >
        <Plus size={14} />
        Add Chapter
      </button>

      <button
        onClick={onCreateStory}
        className="flex items-center gap-1.5 bg-violet-600 hover:bg-violet-500 text-white rounded-lg px-3 py-1.5 text-xs font-medium transition-colors"
      >
        ✨ Generate Outline
      </button>

      <div className="flex-1" />

      <button onClick={() => undo()} className="p-1.5 text-zinc-500 hover:text-zinc-300 rounded hover:bg-zinc-800" title="Undo">
        <Undo2 size={16} />
      </button>
      <button onClick={() => redo()} className="p-1.5 text-zinc-500 hover:text-zinc-300 rounded hover:bg-zinc-800" title="Redo">
        <Redo2 size={16} />
      </button>
    </div>
  );
}
```

- [ ] **Step 6: Commit**

```bash
git add frontend/features/script/
git commit -m "feat: script canvas components (ChapterNode, Canvas, Toolbar)"
```

---

### Task 9: CreateStoryDialog — AI Outline Generation

**Files:**
- Create: `frontend/features/script/CreateStoryDialog.tsx`

- [ ] **Step 1: Create the dialog**

This dialog lets the user enter a story premise and desired chapter count. On submit, it creates chapter nodes arranged vertically on the canvas. For P2, the generation is a local heuristic (splits premise into N chapters). The actual AI generation via backend Celery task will be added in P3.

```typescript
import { useState, useCallback } from 'react';
import { X, Sparkles } from 'lucide-react';
import { useScriptCanvasStore } from '../../stores/scriptCanvasStore';

interface Props {
  isOpen: boolean;
  onClose: () => void;
}

export function CreateStoryDialog({ isOpen, onClose }: Props) {
  const addChapterNode = useScriptCanvasStore((s) => s.addChapterNode);
  const clearCanvas = useScriptCanvasStore((s) => s.clearCanvas);

  const [premise, setPremise] = useState('');
  const [chapterCount, setChapterCount] = useState(5);
  const [generating, setGenerating] = useState(false);

  const handleGenerate = useCallback(async () => {
    if (!premise.trim()) return;
    setGenerating(true);

    try {
      // P2: Local outline generation (placeholder for AI in P3)
      clearCanvas();

      const chapters = Array.from({ length: chapterCount }, (_, i) => ({
        title: `Chapter ${i + 1}`,
        summary: i === 0
          ? `Opening: ${premise.slice(0, 100)}...`
          : i === chapterCount - 1
          ? 'Conclusion and resolution.'
          : `Development of the story — part ${i + 1}.`,
        chapterNumber: i + 1,
      }));

      const VERTICAL_GAP = 200;
      const START_X = 400;
      const START_Y = 100;

      for (const ch of chapters) {
        addChapterNode(
          { x: START_X, y: START_Y + (ch.chapterNumber - 1) * VERTICAL_GAP },
          ch,
        );
      }

      onClose();
    } finally {
      setGenerating(false);
    }
  }, [premise, chapterCount, addChapterNode, clearCanvas, onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-[480px] bg-zinc-900 rounded-xl border border-zinc-700 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-zinc-800">
          <div className="flex items-center gap-2">
            <Sparkles size={16} className="text-violet-400" />
            <h3 className="text-sm font-semibold text-white">Generate Story Outline</h3>
          </div>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300">
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-4 space-y-4">
          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1.5">Story Premise</label>
            <textarea
              className="w-full bg-zinc-800 text-sm text-zinc-200 rounded-lg px-3 py-2 resize-none outline-none focus:ring-1 focus:ring-violet-500/50 min-h-[100px]"
              placeholder="Describe your story idea, setting, main characters, and conflict..."
              value={premise}
              onChange={(e) => setPremise(e.target.value)}
              maxLength={10000}
            />
            <p className="text-[11px] text-zinc-600 mt-1">{premise.length} / 10,000</p>
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1.5">
              Number of Chapters
            </label>
            <div className="flex items-center gap-3">
              <input
                type="range"
                min={2}
                max={20}
                value={chapterCount}
                onChange={(e) => setChapterCount(Number(e.target.value))}
                className="flex-1 accent-violet-500"
              />
              <span className="text-sm font-mono text-zinc-300 w-6 text-center">{chapterCount}</span>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-zinc-800">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-xs text-zinc-400 hover:text-zinc-200 rounded-lg hover:bg-zinc-800 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleGenerate}
            disabled={!premise.trim() || generating}
            className="flex items-center gap-1.5 px-4 py-1.5 text-xs font-medium text-white bg-violet-600 hover:bg-violet-500 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Sparkles size={12} />
            {generating ? 'Generating...' : 'Generate Outline'}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/features/script/CreateStoryDialog.tsx
git commit -m "feat: CreateStoryDialog for AI outline generation (P2 local, P3 AI)"
```

---

### Task 10: Script Editor Page

**Files:**
- Create: `frontend/pages/ScriptEditor/index.tsx`
- Create: `frontend/pages/ScriptEditor/ScriptEditorPage.tsx`

- [ ] **Step 1: Create index.tsx entry point**

```typescript
import { useParams } from 'react-router-dom';
import { ScriptEditorPage } from './ScriptEditorPage';

export function ScriptEditor() {
  const { scriptId } = useParams<{ scriptId: string }>();
  return <ScriptEditorPage />;
}
```

- [ ] **Step 2: Create ScriptEditorPage.tsx**

```typescript
import { useState, useEffect, useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Save } from 'lucide-react';
import { useScriptCanvasStore, type ScriptNode } from '../../stores/scriptCanvasStore';
import { ScriptCanvas } from '../../features/script/ScriptCanvas';
import { ScriptToolbar } from '../../features/script/ScriptToolbar';
import { CreateStoryDialog } from '../../features/script/CreateStoryDialog';
import {
  fetchScriptProject,
  updateScriptProject,
  syncScriptCanvas,
  updateScriptViewport,
} from '../../services/scriptService';
import type { ScriptChapter } from '../../types';

function mapChaptersToNodes(chapters: ScriptChapter[]): ScriptNode[] {
  return chapters.map((ch) => ({
    id: String(ch.id),
    type: 'chapterNode' as const,
    position: { x: ch.position_x, y: ch.position_y },
    data: {
      title: ch.title ?? '',
      summary: ch.summary ?? '',
      content: ch.content ?? '',
      chapterNumber: ch.chapter_number ?? 0,
      branchLabel: ch.branch_label,
      branchType: ch.branch_type,
    },
    ...(ch.width ? { width: ch.width } : {}),
    ...(ch.height ? { height: ch.height } : {}),
  }));
}

function mapChaptersToEdges(chapters: ScriptChapter[]): { id: string; source: string; target: string }[] {
  return chapters
    .filter((ch) => ch.parent_chapter_id)
    .map((ch) => ({
      id: `edge-${ch.parent_chapter_id}-${ch.id}`,
      source: String(ch.parent_chapter_id),
      target: String(ch.id),
    }));
}

export function ScriptEditorPage() {
  const navigate = useNavigate();
  const { teamId, projectId, scriptId } = useParams<{
    teamId: string;
    projectId: string;
    scriptId: string;
  }>();

  const setCanvasData = useScriptCanvasStore((s) => s.setCanvasData);
  const nodes = useScriptCanvasStore((s) => s.nodes);
  const edges = useScriptCanvasStore((s) => s.edges);
  const viewport = useScriptCanvasStore((s) => s.currentViewport);

  const [scriptName, setScriptName] = useState('Untitled Script');
  const [editingName, setEditingName] = useState(false);
  const [nameInput, setNameInput] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showCreateStory, setShowCreateStory] = useState(false);
  const [saving, setSaving] = useState(false);

  // Load script on mount
  useEffect(() => {
    if (!scriptId) {
      setLoading(false);
      return;
    }

    let cancelled = false;
    void (async () => {
      try {
        const project = await fetchScriptProject(scriptId);
        if (cancelled) return;
        setScriptName(project.name);
        const chapterNodes = mapChaptersToNodes(project.chapters);
        const chapterEdges = mapChaptersToEdges(project.chapters);
        setCanvasData(chapterNodes, chapterEdges);
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : String(err);
        setLoadError(message);
        console.error('[ScriptEditorPage] Failed to load:', message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => { cancelled = true; };
  }, [scriptId, setCanvasData]);

  // Save handler
  const handleSave = useCallback(async () => {
    if (!scriptId || saving) return;
    setSaving(true);
    try {
      const chaptersToSync = nodes.map((n) => ({
        id: n.id,
        title: n.data.title,
        summary: n.data.summary,
        content: n.data.content,
        chapter_number: n.data.chapterNumber,
        branch_label: n.data.branchLabel,
        branch_type: n.data.branchType,
        position_x: n.position.x,
        position_y: n.position.y,
        ...(n.width ? { width: n.width } : {}),
        ...(n.height ? { height: n.height } : {}),
      }));

      await syncScriptCanvas(scriptId, {
        added_chapters: [],
        updated_chapters: chaptersToSync,
        deleted_chapter_ids: [],
      });
      await updateScriptViewport(scriptId, viewport);
    } catch (err) {
      console.error('[ScriptEditorPage] Save failed:', err);
    } finally {
      setSaving(false);
    }
  }, [scriptId, nodes, viewport, saving]);

  // Name editing
  const handleNameBlur = useCallback(async () => {
    setEditingName(false);
    const trimmed = nameInput.trim();
    if (trimmed && trimmed !== scriptName && scriptId) {
      setScriptName(trimmed);
      await updateScriptProject(scriptId, { name: trimmed }).catch(console.error);
    }
  }, [nameInput, scriptName, scriptId]);

  const handleBack = () => {
    navigate(`/team/${teamId}/projects/${projectId}?tab=scripts`);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen bg-zinc-950">
        <div className="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="flex flex-col items-center justify-center h-screen bg-zinc-950 gap-3">
        <p className="text-sm text-red-400">Failed to load script: {loadError}</p>
        <button onClick={handleBack} className="text-sm text-indigo-400 hover:underline">
          Back to project
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen bg-zinc-950">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-2 border-b border-zinc-800 bg-zinc-900">
        <button onClick={handleBack} className="text-zinc-500 hover:text-zinc-300 p-1">
          <ArrowLeft size={18} />
        </button>
        {editingName ? (
          <input
            className="text-sm font-semibold text-white bg-transparent outline-none border-b border-indigo-500"
            value={nameInput}
            onChange={(e) => setNameInput(e.target.value)}
            onBlur={handleNameBlur}
            onKeyDown={(e) => e.key === 'Enter' && handleNameBlur()}
            autoFocus
          />
        ) : (
          <button
            className="text-sm font-semibold text-zinc-100 hover:text-white"
            onDoubleClick={() => {
              setNameInput(scriptName);
              setEditingName(true);
            }}
          >
            {scriptName}
          </button>
        )}
        <div className="flex-1" />
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-50"
        >
          <Save size={14} />
          {saving ? 'Saving...' : 'Save'}
        </button>
      </div>

      {/* Toolbar */}
      <ScriptToolbar onCreateStory={() => setShowCreateStory(true)} />

      {/* Canvas */}
      <div className="flex-1 relative">
        <ScriptCanvas />
      </div>

      {/* Create Story Dialog */}
      <CreateStoryDialog
        isOpen={showCreateStory}
        onClose={() => setShowCreateStory(false)}
      />
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/pages/ScriptEditor/
git commit -m "feat: ScriptEditorPage with canvas loading, saving, and navigation"
```

---

### Task 11: Router + ProjectScriptsTab — Wire Everything Together

**Files:**
- Modify: `frontend/router.tsx`
- Modify: `frontend/components/project/ProjectScriptsTab.tsx`

- [ ] **Step 1: Add script editor route**

In `frontend/router.tsx`, add the import and route (near the storyboard route):

```typescript
import { ScriptEditor } from './pages/ScriptEditor';
```

Add route in the team routes array (next to the storyboard route):

```typescript
{ path: 'projects/:projectId/scripts/:scriptId', element: <ScriptEditor /> },
```

- [ ] **Step 2: Replace ProjectScriptsTab placeholder**

Replace the placeholder in `frontend/components/project/ProjectScriptsTab.tsx` with a real script list that mirrors `ProjectStoryboardTab`:

```typescript
import { useState, useEffect, useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Plus, FileText, ScrollText } from 'lucide-react';
import { useTeamContext } from '../../contexts/TeamContext';
import {
  fetchScriptProjects,
  createScriptProject,
} from '../../services/scriptService';
import { ScriptProjectSummary } from '../../types';

interface Props {
  projectId: string;
}

export function ProjectScriptsTab({ projectId }: Props) {
  const navigate = useNavigate();
  const { teamId } = useParams<{ teamId: string }>();
  const { selectedTeamId } = useTeamContext();
  const [items, setItems] = useState<ScriptProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await fetchScriptProjects(projectId);
      setItems(result.data ?? []);
    } catch (err) {
      console.error('Failed to load scripts:', err);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { load(); }, [load]);

  const handleCreate = async () => {
    const name = window.prompt('Script name:');
    if (!name?.trim()) return;
    try {
      const script = await createScriptProject({
        project_id: projectId,
        name: name.trim(),
      });
      navigate(`/team/${teamId}/projects/${projectId}/scripts/${script.id}`);
    } catch (err) {
      console.error('Failed to create script:', err);
    }
  };

  const handleOpen = (scriptId: string) => {
    navigate(`/team/${teamId}/projects/${projectId}/scripts/${scriptId}`);
  };

  if (loading) {
    return (
      <div className="flex flex-wrap gap-4">
        {Array.from({ length: 2 }).map((_, i) => (
          <div key={i} className="w-[280px] h-[140px] rounded-xl bg-zinc-900 animate-pulse" />
        ))}
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-medium text-zinc-400">
          {items.length} script{items.length !== 1 ? 's' : ''}
        </h3>
        <button
          onClick={handleCreate}
          className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg px-3 py-1.5 text-xs font-medium transition-colors"
        >
          <Plus size={14} />
          New Script
        </button>
      </div>

      {items.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-48 gap-3 text-center">
          <FileText size={40} className="text-zinc-700" />
          <p className="text-sm text-zinc-500">No scripts yet</p>
          <button
            onClick={handleCreate}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white transition-colors"
          >
            <Plus size={14} />
            Create First Script
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap gap-4">
          {items.map((script) => (
            <button
              key={script.id}
              onClick={() => handleOpen(script.id)}
              className="w-[280px] text-left group relative flex flex-col rounded-xl border border-zinc-800 bg-zinc-900 p-4 transition-colors hover:border-zinc-600 cursor-pointer"
            >
              <div className="flex items-start gap-3">
                <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-violet-600/20">
                  <ScrollText size={18} className="text-violet-400" />
                </div>
                <div className="min-w-0 flex-1">
                  <h4 className="truncate text-sm font-semibold text-zinc-100">{script.name}</h4>
                  {script.display_code && (
                    <p className="text-[11px] font-mono text-zinc-500 mt-0.5">{script.display_code}</p>
                  )}
                </div>
              </div>
              <div className="mt-2.5">
                <span className="rounded-full border border-violet-800/50 bg-violet-900/40 px-2 py-0.5 text-[11px] text-violet-400">
                  Script
                </span>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/router.tsx frontend/components/project/ProjectScriptsTab.tsx
git commit -m "feat: wire script editor route and replace Scripts tab placeholder"
```

---

### Task 12: Build Verification

- [ ] **Step 1: Install dependencies and build**

```bash
cd frontend && npm install && npx vite build 2>&1 | tail -5
```

Expected: Build completes (PWA size warning is pre-existing, not a blocker).

- [ ] **Step 2: Check TypeScript for our new files**

```bash
npx tsc --noEmit 2>&1 | grep -E "script|Script|chapter|Chapter" || echo "No errors in script files"
```

Expected: No errors in our new files.

- [ ] **Step 3: Verify backend starts**

```bash
cd backend && uv run python -c "from app.api.script_projects_router import router; print('script_projects_router OK')"
cd backend && uv run python -c "from app.api.script_canvas_router import router; print('script_canvas_router OK')"
```

- [ ] **Step 4: Commit (if any fixes needed)**

```bash
git add -A
git commit -m "fix: build verification fixes for P2 script editor"
```
