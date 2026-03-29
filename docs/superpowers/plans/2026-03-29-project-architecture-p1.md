# P1: Project Architecture Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge the standalone "Storyboard Workbench" sidebar entry into the unified "Projects" module, adding Scripts/Storyboard/Output tabs to the project detail page.

**Architecture:** The existing `ProjectsPage` gains new tabs (Scripts, Storyboard, Output) alongside the current Files/Tasks/Shares/Trash. Storyboard projects (`storyboard_projects`) get a `project_id` FK linking them to parent projects. The sidebar "Storyboard" entry is removed. A new database migration adds the FK, `display_code` fields, and `display_code_counters` table.

**Tech Stack:** React 19, TypeScript, ReactFlow, Zustand, FastAPI, Supabase (PostgreSQL), TailwindCSS

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `supabase/migrations/110_project_architecture.sql` | DB schema changes |
| Create | `frontend/components/project/ProjectStoryboardTab.tsx` | Storyboard list within project |
| Create | `frontend/components/project/ProjectScriptsTab.tsx` | Scripts placeholder tab |
| Create | `frontend/components/project/ProjectOutputTab.tsx` | Output placeholder tab |
| Create | `backend/app/services/display_code_service.py` | Display code generation |
| Modify | `frontend/pages/ProjectsPage.tsx` | Add new tabs, expand ProjectTab type |
| Modify | `frontend/components/Sidebar.tsx` | Remove storyboard entry |
| Modify | `frontend/router.tsx` | Restructure storyboard routes under projects |
| Modify | `frontend/types.ts` | Add display_code to Project, add new types |
| Modify | `frontend/services/storyboardService.ts` | Add project_id param to fetchProjects |
| Modify | `backend/app/api/sb_projects_router.py` | Support project_id filter |
| Modify | `backend/app/api/projects_router.py` | Add display_code to responses |
| Modify | `backend/app/schemas/storyboard.py` | Add project_id field |
| Modify | `backend/app/repositories/storyboard_repository.py` | Filter by project_id |

---

### Task 1: Database Migration

**Files:**
- Create: `supabase/migrations/110_project_architecture.sql`

- [ ] **Step 1: Write the migration SQL**

```sql
-- 110_project_architecture.sql
-- Project Architecture Redesign — P1

-- 1. Add display_code and modules to projects
ALTER TABLE projects
  ADD COLUMN IF NOT EXISTS display_code text,
  ADD COLUMN IF NOT EXISTS modules_enabled text[]
    DEFAULT '{files,scripts,storyboard,output}';

-- 2. Link storyboard_projects to projects
ALTER TABLE storyboard_projects
  ADD COLUMN IF NOT EXISTS project_id bigint REFERENCES projects(id),
  ADD COLUMN IF NOT EXISTS display_code text;

CREATE INDEX IF NOT EXISTS idx_storyboard_projects_project_id
  ON storyboard_projects(project_id);

-- 3. Display code sequence counters
CREATE TABLE IF NOT EXISTS display_code_counters (
  team_id bigint NOT NULL REFERENCES teams(id),
  year_month text NOT NULL,
  prefix text NOT NULL,
  current_seq int DEFAULT 0,
  PRIMARY KEY (team_id, year_month, prefix)
);

-- 4. Helper function: generate next display code
CREATE OR REPLACE FUNCTION next_display_code(
  p_team_id bigint,
  p_prefix text
) RETURNS text AS $$
DECLARE
  v_ym text;
  v_seq int;
BEGIN
  v_ym := to_char(now(), 'YYYYMM');
  INSERT INTO display_code_counters (team_id, year_month, prefix, current_seq)
  VALUES (p_team_id, v_ym, p_prefix, 1)
  ON CONFLICT (team_id, year_month, prefix)
  DO UPDATE SET current_seq = display_code_counters.current_seq + 1
  RETURNING current_seq INTO v_seq;
  RETURN p_prefix || '-' || v_ym || lpad(v_seq::text, 3, '0');
END;
$$ LANGUAGE plpgsql;
```

- [ ] **Step 2: Apply migration to local Supabase**

Run: `psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -f supabase/migrations/110_project_architecture.sql`
Expected: No errors, tables altered, function created.

- [ ] **Step 3: Verify schema**

Run: `psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -c "\d storyboard_projects" | grep project_id`
Expected: Shows `project_id | bigint` column.

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/110_project_architecture.sql
git commit -m "feat: add project architecture migration (display_code, project_id FK)"
```

---

### Task 2: Backend — Display Code Service + Schema Updates

**Files:**
- Create: `backend/app/services/display_code_service.py`
- Modify: `backend/app/schemas/storyboard.py`
- Modify: `backend/app/repositories/storyboard_repository.py`

- [ ] **Step 1: Create display_code_service.py**

```python
"""Display code generation service using database counters."""

from app.db.supabase_client import get_async_client


async def generate_display_code(team_id: int, prefix: str) -> str:
    """Generate next display code like P-202603001, S-202603001-001, etc."""
    client = await get_async_client()
    result = await client.rpc(
        "next_display_code",
        {"p_team_id": team_id, "p_prefix": prefix},
    ).execute()
    return result.data
```

- [ ] **Step 2: Add project_id to storyboard schemas**

In `backend/app/schemas/storyboard.py`, add `project_id: Optional[int] = None` to `StoryboardProjectCreate` and `StoryboardProjectResponse`.

- [ ] **Step 3: Update storyboard repository to filter by project_id**

In `backend/app/repositories/storyboard_repository.py`, modify the `list_projects` method to accept an optional `project_id` filter:

```python
async def list_projects(self, team_id: str, project_id: Optional[int] = None, ...):
    query = self.client.table("storyboard_projects").select("*").eq("team_id", team_id)
    if project_id is not None:
        query = query.eq("project_id", project_id)
    # ... rest of existing logic
```

- [ ] **Step 4: Update sb_projects_router to accept project_id query param**

In `backend/app/api/sb_projects_router.py`, add `project_id: Optional[int] = Query(None)` to the list endpoint and pass through.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/display_code_service.py backend/app/schemas/storyboard.py \
  backend/app/repositories/storyboard_repository.py backend/app/api/sb_projects_router.py
git commit -m "feat: backend support for project_id FK and display codes"
```

---

### Task 3: Frontend Types + Service Updates

**Files:**
- Modify: `frontend/types.ts`
- Modify: `frontend/services/storyboardService.ts`

- [ ] **Step 1: Extend Project type with display_code**

In `frontend/types.ts`, add to the `Project` interface:

```typescript
display_code?: string;
modules_enabled?: string[];
```

Add to `StoryboardProject`:

```typescript
project_id?: string;
display_code?: string;
```

Add new tab type:

```typescript
export type ProjectTab = 'files' | 'scripts' | 'storyboard' | 'output' | 'tasks' | 'shares' | 'trash';
```

- [ ] **Step 2: Update storyboardService to support project_id filter**

In `frontend/services/storyboardService.ts`, modify `fetchProjects`:

```typescript
export async function fetchProjects(teamId: string, projectId?: string) {
  let url = `${API_BASE}/api/v1/storyboard/projects?team_id=${teamId}`;
  if (projectId) {
    url += `&project_id=${projectId}`;
  }
  // ... rest unchanged
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/types.ts frontend/services/storyboardService.ts
git commit -m "feat: frontend types and service for project architecture"
```

---

### Task 4: ProjectStoryboardTab Component

**Files:**
- Create: `frontend/components/project/ProjectStoryboardTab.tsx`

- [ ] **Step 1: Create the component**

This component displays a list of storyboard sub-projects within a parent project. It reuses the card style from `ProjectCard` and adds "New Storyboard" button.

```typescript
import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Plus, Clapperboard, Layers } from 'lucide-react';
import { useTeamContext } from '../../contexts/TeamContext';
import {
  fetchProjects as fetchStoryboardProjects,
  createProject as createStoryboardProject,
  deleteProject as deleteStoryboardProject,
} from '../../services/storyboardService';
import { ProjectSummary } from '../../types';

interface Props {
  projectId: string;
}

export function ProjectStoryboardTab({ projectId }: Props) {
  const navigate = useNavigate();
  const { teamId } = useParams<{ teamId: string }>();
  const { selectedTeamId } = useTeamContext();
  const [items, setItems] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!selectedTeamId) return;
    setLoading(true);
    try {
      const result = await fetchStoryboardProjects(selectedTeamId, projectId);
      setItems(result.data ?? []);
    } catch (err) {
      console.error('Failed to load storyboards:', err);
    } finally {
      setLoading(false);
    }
  }, [selectedTeamId, projectId]);

  useEffect(() => { load(); }, [load]);

  const handleCreate = async () => {
    if (!selectedTeamId) return;
    const name = window.prompt('Storyboard name:');
    if (!name?.trim()) return;
    try {
      const sb = await createStoryboardProject({
        team_id: selectedTeamId,
        name: name.trim(),
        project_id: projectId,
      });
      navigate(`/team/${teamId}/projects/${projectId}/storyboard/${sb.id}`);
    } catch (err) {
      console.error('Failed to create storyboard:', err);
    }
  };

  const handleOpen = (sbId: string) => {
    navigate(`/team/${teamId}/projects/${projectId}/storyboard/${sbId}`);
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
          {items.length} storyboard{items.length !== 1 ? 's' : ''}
        </h3>
        <button
          onClick={handleCreate}
          className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg px-3 py-1.5 text-xs font-medium transition-colors"
        >
          <Plus size={14} />
          New Storyboard
        </button>
      </div>

      {items.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-48 gap-3 text-center">
          <Clapperboard size={40} className="text-zinc-700" />
          <p className="text-sm text-zinc-500">No storyboards yet</p>
          <button
            onClick={handleCreate}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white transition-colors"
          >
            <Plus size={14} />
            Create First Storyboard
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap gap-4">
          {items.map((sb) => (
            <button
              key={sb.id}
              onClick={() => handleOpen(sb.id)}
              className="w-[280px] text-left group relative flex flex-col rounded-xl border border-zinc-800 bg-zinc-900 p-4 transition-colors hover:border-zinc-600 cursor-pointer"
            >
              <div className="flex items-start gap-3">
                <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-blue-600/20">
                  <Layers size={18} className="text-blue-400" />
                </div>
                <div className="min-w-0 flex-1">
                  <h4 className="truncate text-sm font-semibold text-zinc-100">{sb.name}</h4>
                </div>
              </div>
              <div className="mt-2.5">
                <span className="rounded-full border border-blue-800/50 bg-blue-900/40 px-2 py-0.5 text-[11px] text-blue-400">
                  Storyboard
                </span>
              </div>
              <div className="mt-2 text-xs text-zinc-500">
                {sb.frame_count ?? 0} frames
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/components/project/ProjectStoryboardTab.tsx
git commit -m "feat: ProjectStoryboardTab component for storyboard list within project"
```

---

### Task 5: Placeholder Tabs (Scripts + Output)

**Files:**
- Create: `frontend/components/project/ProjectScriptsTab.tsx`
- Create: `frontend/components/project/ProjectOutputTab.tsx`

- [ ] **Step 1: Create ProjectScriptsTab placeholder**

```typescript
import React from 'react';
import { FileText } from 'lucide-react';

interface Props {
  projectId: string;
}

export function ProjectScriptsTab({ projectId }: Props) {
  return (
    <div className="flex flex-col items-center justify-center h-48 gap-3 text-center">
      <FileText size={40} className="text-zinc-700" />
      <p className="text-sm text-zinc-500">Script Editor coming in P2</p>
      <p className="text-xs text-zinc-600">AI-powered story creation with chapter branching</p>
    </div>
  );
}
```

- [ ] **Step 2: Create ProjectOutputTab placeholder**

```typescript
import React from 'react';
import { Download } from 'lucide-react';

interface Props {
  projectId: string;
}

export function ProjectOutputTab({ projectId }: Props) {
  return (
    <div className="flex flex-col items-center justify-center h-48 gap-3 text-center">
      <Download size={40} className="text-zinc-700" />
      <p className="text-sm text-zinc-500">Output & Export coming in P5</p>
      <p className="text-xs text-zinc-600">Export storyboards and scripts as PDF, ZIP, or video</p>
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/components/project/ProjectScriptsTab.tsx frontend/components/project/ProjectOutputTab.tsx
git commit -m "feat: placeholder tabs for Scripts and Output (P2/P5)"
```

---

### Task 6: Integrate New Tabs into ProjectsPage

**Files:**
- Modify: `frontend/pages/ProjectsPage.tsx`

- [ ] **Step 1: Add imports for new tab components**

At the top of `ProjectsPage.tsx`, add:

```typescript
import { Clapperboard, FileText, Download } from 'lucide-react';
import { ProjectStoryboardTab } from '../components/project/ProjectStoryboardTab';
import { ProjectScriptsTab } from '../components/project/ProjectScriptsTab';
import { ProjectOutputTab } from '../components/project/ProjectOutputTab';
```

- [ ] **Step 2: Expand ProjectTab type**

Change the type on line 18:

```typescript
type ProjectTab = 'files' | 'scripts' | 'storyboard' | 'output' | 'tasks' | 'shares' | 'trash';
```

- [ ] **Step 3: Add new tabs to the tab bar**

In the tab bar array (around line 99), insert the new tabs after 'files':

```typescript
{ tab: 'files' as ProjectTab, icon: <FolderOpen size={15} />, label: 'Files' },
{ tab: 'scripts' as ProjectTab, icon: <FileText size={15} />, label: 'Scripts' },
{ tab: 'storyboard' as ProjectTab, icon: <Clapperboard size={15} />, label: 'Storyboard' },
{ tab: 'output' as ProjectTab, icon: <Download size={15} />, label: 'Output' },
// separator — secondary tabs
{ tab: 'tasks' as ProjectTab, icon: <KanbanSquare size={15} />, label: 'Tasks' },
{ tab: 'shares' as ProjectTab, icon: <Share2 size={15} />, label: 'Shares', count: shareCount },
{ tab: 'trash' as ProjectTab, icon: <Trash2 size={15} />, label: 'Trash', count: trashCount },
```

- [ ] **Step 4: Add tab content rendering**

After the existing `{activeTab === 'files' && ...}` block, add:

```typescript
{activeTab === 'scripts' && (
  <ProjectScriptsTab projectId={selectedProject.id} />
)}
{activeTab === 'storyboard' && (
  <ProjectStoryboardTab projectId={selectedProject.id} />
)}
{activeTab === 'output' && (
  <ProjectOutputTab projectId={selectedProject.id} />
)}
```

- [ ] **Step 5: Commit**

```bash
git add frontend/pages/ProjectsPage.tsx
git commit -m "feat: integrate Scripts/Storyboard/Output tabs into ProjectsPage"
```

---

### Task 7: Sidebar — Remove Storyboard Entry

**Files:**
- Modify: `frontend/components/Sidebar.tsx`

- [ ] **Step 1: Remove the Clapperboard menu item**

Find the storyboard sidebar item (around line 427 in the personal mode menu items) and remove it. It looks like:

```typescript
{ icon: <Clapperboard size={iconSize} />, view: 'storyboard', label: t('sidebar.storyboard', 'Storyboard') },
```

Remove this line. Also remove `'storyboard'` from the `VIEW_PATH_MAP` object and the `viewFromPathname` function.

- [ ] **Step 2: Commit**

```bash
git add frontend/components/Sidebar.tsx
git commit -m "refactor: remove standalone Storyboard sidebar entry (merged into Projects)"
```

---

### Task 8: Router — Nest Storyboard Under Projects

**Files:**
- Modify: `frontend/router.tsx`

- [ ] **Step 1: Update routes**

Replace the standalone storyboard routes:

```typescript
// REMOVE these:
{ path: 'storyboard', element: <ModuleGuard moduleKey="storyboard"><StoryboardWorkbench /></ModuleGuard> },
{ path: 'storyboard/:projectId', element: <ModuleGuard moduleKey="storyboard"><StoryboardWorkbench /></ModuleGuard> },

// ADD nested route under projects:
{ path: 'projects/:projectId/storyboard/:storyboardId', element: <ModuleGuard moduleKey="storyboard"><StoryboardWorkbench /></ModuleGuard> },
```

The `StoryboardWorkbench` component already reads `projectId` from params — we just need to rename `projectId` to `storyboardId` in the `useParams` call within `StoryboardWorkbench/index.tsx`.

- [ ] **Step 2: Update StoryboardWorkbench/index.tsx**

```typescript
import { useParams } from 'react-router-dom';
import { CanvasEditorPage } from './CanvasEditorPage';

export function StoryboardWorkbench() {
  const { storyboardId } = useParams<{ storyboardId: string }>();
  // storyboardId is always present when this route is matched
  return <CanvasEditorPage />;
}
```

- [ ] **Step 3: Update CanvasEditorPage to use storyboardId param**

In `CanvasEditorPage.tsx`, change the `useParams` call from `projectId` to `storyboardId`:

```typescript
const { storyboardId } = useParams<{ storyboardId: string }>();
```

And use `storyboardId` wherever `projectId` was used for loading the storyboard project.

- [ ] **Step 4: Commit**

```bash
git add frontend/router.tsx frontend/pages/StoryboardWorkbench/index.tsx \
  frontend/pages/StoryboardWorkbench/CanvasEditorPage.tsx
git commit -m "refactor: nest storyboard routes under projects path"
```

---

### Task 9: Display Code in Project Cards

**Files:**
- Modify: `backend/app/api/projects_router.py`
- Modify: `frontend/components/ProjectsListView.tsx` (or equivalent project card)

- [ ] **Step 1: Backend — generate display_code on project creation**

In `projects_router.py`, after creating a project, call `next_display_code` to set the display code:

```python
from app.services.display_code_service import generate_display_code

# In create_project endpoint, after insert:
display_code = await generate_display_code(team_id, "P")
# Update the project with display_code
```

- [ ] **Step 2: Frontend — show display_code on project cards**

In the project list/card component, render `project.display_code` if present:

```typescript
{project.display_code && (
  <span className="text-[11px] font-mono text-zinc-500">{project.display_code}</span>
)}
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/projects_router.py frontend/components/ProjectsListView.tsx
git commit -m "feat: display project codes (P-YYYYMMNNN) on cards"
```

---

### Task 10: Final Integration Test

- [ ] **Step 1: Start dev servers**

```bash
cd frontend && npm install && npm run dev -- --port 5177
cd backend && uv sync && uv run uvicorn app.main:app --reload --port 8082
```

- [ ] **Step 2: Verify sidebar**

Navigate to app. Confirm:
- "Storyboard" sidebar entry is gone
- "Projects" entry still works

- [ ] **Step 3: Verify project tabs**

Open a project. Confirm tabs appear:
- Files (existing, works)
- Scripts (placeholder)
- Storyboard (shows list, create button)
- Output (placeholder)
- Tasks, Shares, Trash (existing, work)

- [ ] **Step 4: Verify storyboard within project**

Create a new storyboard from the Storyboard tab. Confirm:
- Navigates to `/team/:teamId/projects/:projectId/storyboard/:storyboardId`
- Canvas editor loads correctly

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat: P1 complete — project architecture integration"
```
