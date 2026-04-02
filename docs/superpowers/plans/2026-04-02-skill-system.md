# Skill System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `style_templates` → `skills` table, add Skill CRUD API, integrate Skill injection into ChatPanel → LLM flow, and build Skills management UI.

**Architecture:** ALTER TABLE to extend existing `style_templates` into `skills` (rename + add columns). Backend follows existing Router → Repository → Schema pattern (no separate Service layer for v1). Frontend adds SkillSelector to ChatPanel and a full-page SkillEditor accessible via ProjectNavSidebar.

**Tech Stack:** FastAPI + Supabase (backend), React 19 + Vite 7 + TailwindCSS (frontend), `@uiw/react-md-editor` (Markdown editor)

**Spec:** `docs/superpowers/specs/2026-04-02-skill-system-design.md`

---

## File Structure

### New files
- `supabase/migrations/114_skills_migrate_from_style_templates.sql`
- `backend/app/schemas/skill.py`
- `backend/app/repositories/skill_repository.py`
- `backend/app/api/skills_router.py`
- `frontend/services/skillService.ts`
- `frontend/components/project/ProjectSkillsTab.tsx`
- `frontend/components/project/SkillEditor.tsx`
- `frontend/hooks/useSkillSelector.ts`

### Modified files
- `backend/app/api/__init__.py` — register skills_router, deprecate style_templates_router
- `backend/app/api/sb_ai_router.py:53-58` — add `skill_id` to ChatRequest
- `backend/app/api/sb_ai_router.py:334-359` — pass skill_id to service
- `backend/app/services/storyboard_ai_service.py:613-703` — accept skill_id, inject into prompt
- `frontend/types.ts:808,824-835` — add `skills` to ProjectTab, rename StyleTemplate → Skill
- `frontend/pages/ProjectsPage.tsx:18-30,205-220` — import and render ProjectSkillsTab
- `frontend/components/project/ProjectNavSidebar.tsx:42-53` — add Skills to NAV_SECTIONS
- `frontend/features/storyboard/ui/ChatPanel.tsx` — integrate SkillSelector
- `frontend/services/storyboardService.ts:258-270` — add skillId param to chatWithAI
- `frontend/public/locales/en.json` — add skill i18n keys
- `frontend/public/locales/zh.json` — add skill i18n keys

---

### Task 1: Database Migration

**Files:**
- Create: `supabase/migrations/114_skills_migrate_from_style_templates.sql`

- [ ] **Step 1: Write migration SQL**

```sql
-- 114_skills_migrate_from_style_templates.sql
-- Migrate style_templates → skills: add new columns, rename table

-- 1. Add new columns to style_templates
ALTER TABLE style_templates ADD COLUMN IF NOT EXISTS project_id bigint REFERENCES projects(id);
ALTER TABLE style_templates ADD COLUMN IF NOT EXISTS status varchar(20) DEFAULT 'active';
ALTER TABLE style_templates ADD COLUMN IF NOT EXISTS icon varchar(20) DEFAULT '✨';
ALTER TABLE style_templates ADD COLUMN IF NOT EXISTS output_format text;
ALTER TABLE style_templates ADD COLUMN IF NOT EXISTS trigger_keywords text[] DEFAULT '{}';

-- 2. Rename prompt_content → content_md
ALTER TABLE style_templates RENAME COLUMN prompt_content TO content_md;

-- 3. Rename table
ALTER TABLE style_templates RENAME TO skills;

-- 4. Rename existing indexes
ALTER INDEX IF EXISTS idx_style_templates_team_id RENAME TO idx_skills_team_id;
ALTER INDEX IF EXISTS idx_style_templates_category RENAME TO idx_skills_category;

-- 5. Add new indexes
CREATE INDEX IF NOT EXISTS idx_skills_project_id ON skills(project_id) WHERE project_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status);

-- 6. Set all existing rows to active
UPDATE skills SET status = 'active' WHERE status IS NULL;

-- 7. Insert system presets (idempotent)
INSERT INTO skills (team_id, name, description, content_md, category, is_public, icon, status)
VALUES
  (NULL, 'Short Video Script', '30-60s short-form video script structure',
   E'You are a short-form video scriptwriter.\n\nRules:\n- Target duration: 30-60 seconds\n- Hook in first 3 seconds\n- One clear message per video\n- End with call to action\n\nStructure:\n1. Hook (0-3s)\n2. Problem/Setup (3-15s)\n3. Solution/Content (15-45s)\n4. CTA (45-60s)',
   'script', true, '🎬', 'active'),
  (NULL, 'Film Storyboard', 'Professional storyboard format with shot types',
   E'You are a professional storyboard artist.\n\nFor each scene, specify:\n- Shot type (wide/medium/close-up/extreme close-up)\n- Camera angle (eye level/high/low/dutch/bird''s eye)\n- Camera movement (static/pan/tilt/dolly/crane/handheld)\n- Lighting (key light direction, mood)\n- Duration estimate\n- Transition to next scene\n\nUse cinematic language. Be specific about composition.',
   'storyboard', true, '🎞️', 'active'),
  (NULL, 'Product Copywriting', 'AIDA structure marketing copy',
   E'You are a marketing copywriter using the AIDA framework.\n\nStructure:\n1. **Attention** — Bold headline that stops the scroll\n2. **Interest** — Problem statement the reader relates to\n3. **Desire** — Benefits (not features) with social proof\n4. **Action** — Clear, urgent CTA\n\nTone: conversational, confident, specific. Use numbers and specifics over vague claims.',
   'copywriting', true, '✍️', 'active'),
  (NULL, 'Social Media Post', 'Platform-specific social content',
   E'You are a social media content creator.\n\nAdapt content for the specified platform:\n- **Instagram**: Visual-first, 2200 char max, 30 hashtags max, emoji-friendly\n- **Twitter/X**: 280 chars, punchy, thread-friendly\n- **LinkedIn**: Professional tone, storytelling, 3000 chars\n- **TikTok**: Script for spoken word, casual, trend-aware\n\nAlways include: hook, value, CTA.',
   'copywriting', true, '📱', 'active'),
  (NULL, 'Script to Storyboard', 'Convert written script to visual storyboard',
   E'You are a script-to-storyboard converter.\n\nFor each scene in the script:\n1. Identify the key visual moment\n2. Describe the frame composition\n3. Note character positions and expressions\n4. Specify shot type and camera angle\n5. Add timing/duration\n6. Note any VFX or special requirements\n\nOutput as a numbered scene list with consistent formatting.',
   'storyboard', true, '🔄', 'active')
ON CONFLICT DO NOTHING;
```

- [ ] **Step 2: Execute migration locally**

Run: `psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -f supabase/migrations/114_skills_migrate_from_style_templates.sql`
Expected: All ALTER/CREATE/INSERT statements succeed.

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/114_skills_migrate_from_style_templates.sql
git commit -m "feat(db): migrate style_templates → skills table with new columns and presets"
```

---

### Task 2: Backend Pydantic Schemas

**Files:**
- Create: `backend/app/schemas/skill.py`

- [ ] **Step 1: Create skill schemas**

```python
"""Skill request/response Pydantic schemas."""

from typing import List, Optional

from pydantic import BaseModel, Field


class SkillCreate(BaseModel):
    """Request body for creating a skill."""

    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    content_md: str = Field(..., min_length=1, max_length=10000)
    category: Optional[str] = Field(None, max_length=100)
    icon: str = Field(default="✨", max_length=20)
    output_format: Optional[str] = Field(None, max_length=5000)
    trigger_keywords: List[str] = Field(default_factory=list)
    project_id: Optional[str] = None
    is_public: bool = False


class SkillUpdate(BaseModel):
    """Request body for updating a skill."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    content_md: Optional[str] = Field(None, min_length=1, max_length=10000)
    category: Optional[str] = Field(None, max_length=100)
    icon: Optional[str] = Field(None, max_length=20)
    output_format: Optional[str] = Field(None, max_length=5000)
    trigger_keywords: Optional[List[str]] = None
    project_id: Optional[str] = None
    is_public: Optional[bool] = None
    status: Optional[str] = Field(None, pattern=r"^(active|archived)$")
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/schemas/skill.py
git commit -m "feat(backend): add Skill Pydantic schemas"
```

---

### Task 3: Backend Repository

**Files:**
- Create: `backend/app/repositories/skill_repository.py`

- [ ] **Step 1: Create skill repository**

```python
"""Skill Repository — data access for skills table."""

from typing import Any, Dict, List, Optional

from loguru import logger

from app.repositories.base_repository import BaseRepository


# Columns returned in list queries (excludes content_md, output_format for performance)
_SUMMARY_COLUMNS = (
    "id, team_id, project_id, created_by, name, description, "
    "category, icon, trigger_keywords, is_public, status, created_at, updated_at"
)


class SkillRepository(BaseRepository):
    """CRUD + list operations for skills."""

    TABLE_NAME = "skills"

    async def archive(self, skill_id: str) -> None:
        """Soft-delete by setting status to 'archived'."""
        await self.update(skill_id, {"status": "archived"})
        logger.info("Archived skill %s", skill_id)

    async def list_skills(
        self,
        team_id: Optional[str] = None,
        project_id: Optional[str] = None,
        category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List active skills: team's own + system presets + public.

        Returns summary (no content_md) for performance.
        When project_id is given, includes project-specific skills.
        """
        try:
            client = await self._get_client()

            # Build OR filter: team's own OR public OR system presets (team_id is null)
            or_parts = ["team_id.is.null"]
            if team_id:
                or_parts.append(f"team_id.eq.{team_id}")
                or_parts.append("is_public.eq.true")

            query = (
                client.table(self.TABLE_NAME)
                .select(_SUMMARY_COLUMNS)
                .or_(",".join(or_parts))
                .eq("status", "active")
            )

            if project_id:
                # Include both project-specific and global (project_id is null)
                query = query.or_(f"project_id.eq.{project_id},project_id.is.null")
            else:
                query = query.is_("project_id", "null")

            if category:
                query = query.eq("category", category)

            query = query.order("created_at", desc=True)
            result = await query.execute()
            return result.data or []
        except Exception as e:
            logger.error("Failed to list skills: %s", e)
            return []
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/repositories/skill_repository.py
git commit -m "feat(backend): add SkillRepository with list_skills summary query"
```

---

### Task 4: Backend Router

**Files:**
- Create: `backend/app/api/skills_router.py`
- Modify: `backend/app/api/__init__.py`

- [ ] **Step 1: Create skills router**

```python
"""Skills Router — CRUD endpoints for AI skills."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from app.core.deps import AuthDep, get_team_id_for_user, require_team_id
from app.repositories.skill_repository import SkillRepository
from app.schemas.skill import SkillCreate, SkillUpdate

router = APIRouter(prefix="/skills")

VALID_CATEGORIES = ["script", "storyboard", "copywriting", "general"]


@router.post("")
async def create_skill(auth: AuthDep, body: SkillCreate) -> Dict[str, Any]:
    """Create a new skill (requires auth + team membership)."""
    try:
        repo = SkillRepository()
        team_id = await require_team_id(auth.user_id)
        data = {
            **body.model_dump(exclude_none=True),
            "team_id": team_id,
            "created_by": auth.user_id,
            "status": "active",
        }
        skill = await repo.create(data)
        return {"success": True, "data": skill}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Skills] create failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create skill")


@router.get("")
async def list_skills(
    auth: AuthDep,
    project_id: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
) -> Dict[str, Any]:
    """List skills: own team's + system presets + public. Returns summary (no content_md)."""
    try:
        repo = SkillRepository()
        team_id = await get_team_id_for_user(auth.user_id)
        skills = await repo.list_skills(
            team_id=team_id,
            project_id=project_id,
            category=category,
        )
        return {"success": True, "data": skills}
    except Exception as exc:
        logger.error("[Skills] list failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to list skills")


@router.get("/categories")
async def list_categories() -> Dict[str, Any]:
    """List available skill categories."""
    return {"success": True, "data": VALID_CATEGORIES}


@router.get("/{skill_id}")
async def get_skill(auth: AuthDep, skill_id: str) -> Dict[str, Any]:
    """Get full skill detail (includes content_md)."""
    try:
        repo = SkillRepository()
        skill = await repo.get_by_id(skill_id)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        return {"success": True, "data": skill}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Skills] get %s failed: %s", skill_id, exc)
        raise HTTPException(status_code=500, detail="Failed to get skill")


@router.patch("/{skill_id}")
async def update_skill(
    auth: AuthDep, skill_id: str, body: SkillUpdate
) -> Dict[str, Any]:
    """Update a skill (owner only)."""
    try:
        repo = SkillRepository()
        existing = await repo.get_by_id(skill_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Skill not found")
        if existing.get("team_id") is None:
            raise HTTPException(
                status_code=403, detail="Cannot modify system presets"
            )
        if existing.get("created_by") != auth.user_id:
            raise HTTPException(
                status_code=403, detail="Only the skill owner can update"
            )
        skill = await repo.update(skill_id, body.model_dump(exclude_none=True))
        return {"success": True, "data": skill}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Skills] update %s failed: %s", skill_id, exc)
        raise HTTPException(status_code=500, detail="Failed to update skill")


@router.delete("/{skill_id}")
async def delete_skill(auth: AuthDep, skill_id: str) -> Dict[str, Any]:
    """Archive a skill (owner only, system presets protected)."""
    try:
        repo = SkillRepository()
        existing = await repo.get_by_id(skill_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Skill not found")
        if existing.get("team_id") is None:
            raise HTTPException(
                status_code=403, detail="Cannot delete system presets"
            )
        if existing.get("created_by") != auth.user_id:
            raise HTTPException(
                status_code=403, detail="Only the skill owner can delete"
            )
        await repo.archive(skill_id)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Skills] delete %s failed: %s", skill_id, exc)
        raise HTTPException(status_code=500, detail="Failed to delete skill")
```

- [ ] **Step 2: Register in `__init__.py`**

In `backend/app/api/__init__.py`, add import and registration:

```python
# Add import (after style_templates_router line 50)
from app.api.skills_router import router as skills_router

# Add registration (after style_templates_router line 133)
api_router.include_router(router=skills_router, tags=["Skills"])
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/skills_router.py backend/app/api/__init__.py
git commit -m "feat(backend): add Skills CRUD router with ownership checks"
```

---

### Task 5: Backend — Skill Injection into Chat

**Files:**
- Modify: `backend/app/api/sb_ai_router.py:53-58` — add skill_id to ChatRequest
- Modify: `backend/app/api/sb_ai_router.py:334-359` — pass skill_id to service
- Modify: `backend/app/services/storyboard_ai_service.py:613-618` — accept and inject skill

- [ ] **Step 1: Add `skill_id` to ChatRequest**

In `backend/app/api/sb_ai_router.py`, modify ChatRequest (line 53-58):

```python
class ChatRequest(BaseModel):
    """Request body for storyboard assistant chat."""

    project_id: str
    message: str = Field(..., min_length=1, max_length=4000)
    selected_frame_id: Optional[str] = None
    skill_id: Optional[str] = None
```

- [ ] **Step 2: Pass skill_id in chat endpoint**

In `backend/app/api/sb_ai_router.py`, modify the chat endpoint (line 349-353):

```python
        result = await svc.chat(
            project_id=body.project_id,
            message=body.message,
            selected_frame_id=body.selected_frame_id,
            skill_id=body.skill_id,
        )
```

- [ ] **Step 3: Accept skill_id in StoryboardAIService.chat()**

In `backend/app/services/storyboard_ai_service.py`, modify the chat method signature (line 613-618):

```python
    async def chat(
        self,
        project_id: str,
        message: str,
        selected_frame_id: Optional[str] = None,
        skill_id: Optional[str] = None,
    ) -> Dict[str, Any]:
```

- [ ] **Step 4: Add skill injection before system_prompt build**

In `backend/app/services/storyboard_ai_service.py`, add skill lookup before the system_prompt assignment (before line 692). Insert after `context_block` is built (after line 690):

```python
        # Skill injection
        skill_prefix = ""
        if skill_id:
            try:
                from app.repositories.skill_repository import SkillRepository
                skill_repo = SkillRepository()
                skill = await skill_repo.get_by_id(skill_id)
                if skill and skill.get("status") == "active":
                    skill_prefix = (
                        f"<skill>\n{skill['content_md']}\n</skill>\n\n"
                    )
                    if skill.get("output_format"):
                        skill_prefix += (
                            f"Output format:\n{skill['output_format']}\n\n"
                        )
                    logger.info(
                        "chat: injected skill %s for project %s",
                        skill_id, project_id,
                    )
                else:
                    logger.warning(
                        "chat: skill %s not found or archived, proceeding without",
                        skill_id,
                    )
            except Exception as exc:
                logger.warning("chat: skill lookup failed: %s", exc)
```

Then modify the system_prompt (line 692) to prepend skill_prefix:

```python
        system_prompt = skill_prefix + (
            "You are a helpful storyboard assistant. "
            # ... rest unchanged
        )
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/sb_ai_router.py backend/app/services/storyboard_ai_service.py
git commit -m "feat(backend): inject Skill content into ChatPanel LLM system prompt"
```

---

### Task 6: Frontend Types & Skill Service

**Files:**
- Modify: `frontend/types.ts:808,824-835`
- Create: `frontend/services/skillService.ts`

- [ ] **Step 1: Update types.ts — add Skill interface and ProjectTab**

In `frontend/types.ts`, replace `StyleTemplate` (line 824-835) with `Skill`:

```typescript
export interface Skill {
  id: string;
  team_id?: string;
  project_id?: string;
  name: string;
  description?: string;
  content_md?: string;
  category?: string;
  icon: string;
  output_format?: string;
  trigger_keywords: string[];
  is_public: boolean;
  status: string;
  created_by?: string;
  created_at: string;
  updated_at: string;
}
```

Update `ProjectTab` (line 808) to include `'skills'`:

```typescript
export type ProjectTab = 'files' | 'scripts' | 'storyboard' | 'output' | 'tasks' | 'skills' | 'shares' | 'trash';
```

Keep the old `StyleTemplate` as a deprecated alias (for any remaining references):

```typescript
/** @deprecated Use Skill instead */
export type StyleTemplate = Skill;
```

- [ ] **Step 2: Create skillService.ts**

```typescript
import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';
import { unwrapResponse } from '../utils/apiHelpers';
import { Skill } from '../types';

const CACHE_TTL_MS = 30_000;
let _cache: { data: Skill[]; ts: number; projectId?: string } | null = null;

function isCacheValid(projectId?: string): boolean {
  if (!_cache) return false;
  if (_cache.projectId !== projectId) return false;
  return Date.now() - _cache.ts < CACHE_TTL_MS;
}

export function invalidateSkillsCache(): void {
  _cache = null;
}

export async function fetchSkills(
  projectId?: string,
  category?: string,
): Promise<Skill[]> {
  if (!category && isCacheValid(projectId)) return _cache!.data;

  const headers = await getAuthHeaders();
  const params = new URLSearchParams();
  if (projectId) params.set('project_id', projectId);
  if (category) params.set('category', category);
  const res = await fetch(`${getApiUrl()}/api/v1/skills?${params}`, { headers });
  const data = await unwrapResponse<Skill[]>(res);

  if (!category) {
    _cache = { data, ts: Date.now(), projectId };
  }
  return data;
}

export async function fetchSkillDetail(id: string): Promise<Skill> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/skills/${id}`, { headers });
  return unwrapResponse<Skill>(res);
}

export async function createSkill(data: {
  name: string;
  description?: string;
  content_md: string;
  category?: string;
  icon?: string;
  output_format?: string;
  trigger_keywords?: string[];
  project_id?: string;
  is_public?: boolean;
}): Promise<Skill> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/skills`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  invalidateSkillsCache();
  return unwrapResponse<Skill>(res);
}

export async function updateSkill(
  id: string,
  data: Partial<Pick<Skill, 'name' | 'description' | 'content_md' | 'category' | 'icon' | 'output_format' | 'trigger_keywords' | 'project_id' | 'is_public' | 'status'>>,
): Promise<Skill> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/skills/${id}`, {
    method: 'PATCH',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  invalidateSkillsCache();
  return unwrapResponse<Skill>(res);
}

export async function deleteSkill(id: string): Promise<void> {
  const headers = await getAuthHeaders();
  await fetch(`${getApiUrl()}/api/v1/skills/${id}`, {
    method: 'DELETE',
    headers,
  });
  invalidateSkillsCache();
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/types.ts frontend/services/skillService.ts
git commit -m "feat(frontend): add Skill type, skillService with TTL cache"
```

---

### Task 7: Frontend — ChatPanel Skill Selector

**Files:**
- Create: `frontend/hooks/useSkillSelector.ts`
- Modify: `frontend/services/storyboardService.ts:258-270`
- Modify: `frontend/features/storyboard/ui/ChatPanel.tsx`

- [ ] **Step 1: Create useSkillSelector hook**

```typescript
import { useState, useEffect, useCallback, useMemo } from 'react';
import { Skill } from '../types';
import { fetchSkills } from '../services/skillService';

interface SkillGroup {
  readonly label: string;
  readonly skills: readonly Skill[];
}

interface UseSkillSelectorResult {
  readonly groups: readonly SkillGroup[];
  readonly selectedSkill: Skill | null;
  readonly loading: boolean;
  readonly selectSkill: (skill: Skill | null) => void;
  readonly filterText: string;
  readonly setFilterText: (text: string) => void;
}

export function useSkillSelector(projectId: string): UseSkillSelectorResult {
  const [skills, setSkills] = useState<readonly Skill[]>([]);
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);
  const [loading, setLoading] = useState(true);
  const [filterText, setFilterText] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchSkills(projectId)
      .then((data) => {
        if (!cancelled) setSkills(data);
      })
      .catch((err) => console.error('[SkillSelector] fetch failed:', err))
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [projectId]);

  const groups = useMemo(() => {
    const lower = filterText.toLowerCase();
    const filtered = filterText
      ? skills.filter(
          (s) =>
            s.name.toLowerCase().includes(lower) ||
            (s.description || '').toLowerCase().includes(lower),
        )
      : skills;

    const project: Skill[] = [];
    const global: Skill[] = [];
    const system: Skill[] = [];

    for (const s of filtered) {
      if (s.team_id === null || s.team_id === undefined) {
        system.push(s);
      } else if (s.project_id) {
        project.push(s);
      } else {
        global.push(s);
      }
    }

    const result: SkillGroup[] = [];
    if (project.length > 0) result.push({ label: '📁 Project', skills: project });
    if (global.length > 0) result.push({ label: '🌐 Global', skills: global });
    if (system.length > 0) result.push({ label: '⚙️ System', skills: system });
    return result;
  }, [skills, filterText]);

  const selectSkill = useCallback((skill: Skill | null) => {
    setSelectedSkill(skill);
  }, []);

  return { groups, selectedSkill, loading, selectSkill, filterText, setFilterText };
}
```

- [ ] **Step 2: Add skillId param to chatWithAI**

In `frontend/services/storyboardService.ts`, modify the `chatWithAI` function (line 258-270):

```typescript
export async function chatWithAI(
  projectId: string,
  message: string,
  frameId?: string,
  skillId?: string,
): Promise<{ response: string; actions: unknown[] }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/storyboard/projects/${projectId}/chat`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      message,
      frame_id: frameId,
      skill_id: skillId,
    }),
  });
  return unwrapResponse<{ response: string; actions: unknown[] }>(res);
}
```

- [ ] **Step 3: Integrate SkillSelector into ChatPanel**

Replace the full content of `frontend/features/storyboard/ui/ChatPanel.tsx`:

```tsx
import { useCallback, useEffect, useRef, useState } from 'react';
import { Bot, ChevronDown, Loader2, MessageCircle, Send, User, Wand2, X } from 'lucide-react';
import { chatWithAI } from '../../../services/storyboardService';
import { useSkillSelector } from '../../../hooks/useSkillSelector';

// ─── Types ───────────────────────────────────────────────────────────────────

interface ChatPanelProps {
  projectId: string;
  onClose: () => void;
}

interface ChatMessage {
  readonly role: 'user' | 'assistant';
  readonly content: string;
}

// ─── Component ───────────────────────────────────────────────────────────────

export default function ChatPanel({ projectId, onClose }: ChatPanelProps) {
  const [messages, setMessages] = useState<readonly ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showSkillDropdown, setShowSkillDropdown] = useState(false);

  const { groups, selectedSkill, selectSkill, filterText, setFilterText } =
    useSkillSelector(projectId);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Auto-dismiss error after 5 seconds
  useEffect(() => {
    if (!error) return;
    const timer = setTimeout(() => setError(null), 5000);
    return () => clearTimeout(timer);
  }, [error]);

  // Close dropdown on outside click
  useEffect(() => {
    if (!showSkillDropdown) return;
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowSkillDropdown(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [showSkillDropdown]);

  const handleSend = useCallback(async () => {
    const trimmed = input.trim();
    if (!trimmed || loading) return;

    const userMessage: ChatMessage = { role: 'user', content: trimmed };
    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    setError(null);
    setLoading(true);

    try {
      const result = await chatWithAI(
        projectId,
        trimmed,
        undefined,
        selectedSkill?.id,
      );
      const assistantMessage: ChatMessage = {
        role: 'assistant',
        content: result.response,
      };
      setMessages((prev) => [...prev, assistantMessage]);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : 'Failed to get AI response';
      setError(message);
    } finally {
      setLoading(false);
      textareaRef.current?.focus();
    }
  }, [input, loading, projectId, selectedSkill]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  return (
    <div className="flex h-full w-80 flex-col border-l border-zinc-700 bg-zinc-900">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-zinc-700 px-4 py-3">
        <div className="flex items-center gap-2">
          <MessageCircle className="h-4 w-4 text-indigo-400" />
          <span className="text-sm font-medium text-zinc-100">
            AI Assistant
          </span>
        </div>
        <div className="flex items-center gap-1">
          {/* Skill selector button */}
          <div className="relative" ref={dropdownRef}>
            <button
              onClick={() => setShowSkillDropdown(!showSkillDropdown)}
              className={`flex items-center gap-1 rounded px-2 py-1 text-xs transition-colors ${
                selectedSkill
                  ? 'bg-violet-900/40 text-violet-300 hover:bg-violet-900/60'
                  : 'text-zinc-400 hover:bg-zinc-700 hover:text-zinc-100'
              }`}
              aria-label="Select skill"
              aria-expanded={showSkillDropdown}
              role="combobox"
            >
              <Wand2 className="h-3 w-3" />
              <span className="max-w-[80px] truncate">
                {selectedSkill ? selectedSkill.name : 'Skill'}
              </span>
              <ChevronDown className="h-3 w-3" />
            </button>

            {/* Dropdown */}
            {showSkillDropdown && (
              <SkillDropdown
                groups={groups}
                filterText={filterText}
                onFilterChange={setFilterText}
                onSelect={(skill) => {
                  selectSkill(skill);
                  setShowSkillDropdown(false);
                  setFilterText('');
                }}
                onClear={() => {
                  selectSkill(null);
                  setShowSkillDropdown(false);
                  setFilterText('');
                }}
                selectedId={selectedSkill?.id}
              />
            )}
          </div>

          <button
            onClick={onClose}
            className="rounded p-1 text-zinc-400 transition-colors hover:bg-zinc-700 hover:text-zinc-100"
            aria-label="Close chat panel"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 space-y-3 overflow-y-auto px-3 py-4">
        {messages.length === 0 && !loading && <EmptyState />}
        {messages.map((msg, i) => (
          <MessageBubble key={i} message={msg} />
        ))}
        {loading && <TypingIndicator />}
        <div ref={messagesEndRef} />
      </div>

      {/* Error toast */}
      {error && (
        <div className="mx-3 mb-2 rounded-md bg-red-900/60 px-3 py-2 text-xs text-red-200">
          {error}
        </div>
      )}

      {/* Active skill badge */}
      {selectedSkill && (
        <div className="mx-3 mb-2 flex items-center gap-2 rounded-md bg-violet-900/30 px-3 py-1.5">
          <Wand2 className="h-3 w-3 text-violet-400" />
          <span className="flex-1 truncate text-xs text-violet-300">
            Using: {selectedSkill.name}
          </span>
          <button
            onClick={() => selectSkill(null)}
            className="text-violet-400 hover:text-violet-200"
            aria-label="Remove active skill"
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      )}

      {/* Input area */}
      <div className="border-t border-zinc-700 p-3">
        <div className="flex items-end gap-2">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about your storyboard..."
            rows={1}
            className="max-h-24 flex-1 resize-none rounded-md border border-zinc-600 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 outline-none focus:border-indigo-500"
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || loading}
            className="rounded-md bg-indigo-600 p-2 text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-40"
            aria-label="Send message"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
        <p className="mt-1.5 text-[10px] text-zinc-500">
          Enter to send, Shift+Enter for newline
        </p>
      </div>
    </div>
  );
}

// ─── Sub-components ──────────────────────────────────────────────────────────

interface SkillDropdownProps {
  groups: readonly { label: string; skills: readonly import('../../../types').Skill[] }[];
  filterText: string;
  onFilterChange: (text: string) => void;
  onSelect: (skill: import('../../../types').Skill) => void;
  onClear: () => void;
  selectedId?: string;
}

function SkillDropdown({ groups, filterText, onFilterChange, onSelect, onClear, selectedId }: SkillDropdownProps) {
  return (
    <div className="absolute right-0 top-full z-50 mt-1 w-64 rounded-lg border border-zinc-700 bg-zinc-800 shadow-xl">
      {/* Search */}
      <div className="border-b border-zinc-700 p-2">
        <input
          type="text"
          value={filterText}
          onChange={(e) => onFilterChange(e.target.value)}
          placeholder="Search skills..."
          className="w-full rounded bg-zinc-900 px-2 py-1.5 text-xs text-zinc-100 placeholder-zinc-500 outline-none"
          autoFocus
        />
      </div>

      {/* No Skill option */}
      <div className="max-h-64 overflow-y-auto p-1">
        <button
          onClick={onClear}
          className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs transition-colors ${
            !selectedId ? 'bg-zinc-700 text-zinc-100' : 'text-zinc-400 hover:bg-zinc-700/50'
          }`}
        >
          No Skill
        </button>

        {groups.map((group) => (
          <div key={group.label}>
            <div className="px-2 pb-1 pt-2 text-[10px] font-medium uppercase tracking-wider text-zinc-500">
              {group.label}
            </div>
            {group.skills.map((skill) => (
              <button
                key={skill.id}
                onClick={() => onSelect(skill)}
                className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs transition-colors ${
                  selectedId === skill.id
                    ? 'bg-violet-900/40 text-violet-200'
                    : 'text-zinc-300 hover:bg-zinc-700/50'
                }`}
              >
                <span>{skill.icon}</span>
                <div className="flex-1 truncate">
                  <div className="truncate">{skill.name}</div>
                  {skill.description && (
                    <div className="truncate text-[10px] text-zinc-500">
                      {skill.description}
                    </div>
                  )}
                </div>
              </button>
            ))}
          </div>
        ))}

        {groups.length === 0 && (
          <div className="px-2 py-4 text-center text-xs text-zinc-500">
            No skills found
          </div>
        )}
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center gap-2 py-12 text-center">
      <Bot className="h-8 w-8 text-zinc-600" />
      <p className="text-sm text-zinc-500">
        Ask me about your storyboard...
      </p>
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user';

  return (
    <div className={`flex gap-2 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      <div
        className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${
          isUser ? 'bg-indigo-600' : 'bg-zinc-700'
        }`}
      >
        {isUser ? (
          <User className="h-3.5 w-3.5 text-white" />
        ) : (
          <Bot className="h-3.5 w-3.5 text-zinc-300" />
        )}
      </div>
      <div
        className={`max-w-[75%] rounded-lg px-3 py-2 text-sm leading-relaxed ${
          isUser
            ? 'bg-indigo-600 text-white'
            : 'bg-zinc-800 text-zinc-200'
        }`}
      >
        {message.content}
      </div>
    </div>
  );
}

function TypingIndicator() {
  return (
    <div className="flex items-center gap-2">
      <div className="flex h-6 w-6 items-center justify-center rounded-full bg-zinc-700">
        <Bot className="h-3.5 w-3.5 text-zinc-300" />
      </div>
      <div className="flex items-center gap-1.5 rounded-lg bg-zinc-800 px-3 py-2">
        <Loader2 className="h-3.5 w-3.5 animate-spin text-zinc-400" />
        <span className="text-xs text-zinc-400">Thinking...</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/hooks/useSkillSelector.ts frontend/services/storyboardService.ts frontend/features/storyboard/ui/ChatPanel.tsx
git commit -m "feat(frontend): integrate SkillSelector into ChatPanel with grouped dropdown"
```

---

### Task 8: Frontend — Skills Management Page + NavSidebar

**Files:**
- Create: `frontend/components/project/ProjectSkillsTab.tsx`
- Modify: `frontend/components/project/ProjectNavSidebar.tsx:42-53`
- Modify: `frontend/pages/ProjectsPage.tsx`

- [ ] **Step 1: Create ProjectSkillsTab**

```tsx
import { useState, useEffect, useCallback } from 'react';
import { Wand2, Plus, Archive } from 'lucide-react';
import { Skill } from '../../types';
import { fetchSkills, deleteSkill, invalidateSkillsCache } from '../../services/skillService';
import { useToast } from '../../contexts/ToastContext';

const CATEGORY_TABS = ['all', 'script', 'storyboard', 'copywriting', 'general'] as const;

interface ProjectSkillsTabProps {
  projectId: string;
  onEditSkill?: (skillId: string | null) => void;
}

export function ProjectSkillsTab({ projectId, onEditSkill }: ProjectSkillsTabProps) {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeCategory, setActiveCategory] = useState<string>('all');
  const { addToast } = useToast();

  const loadSkills = useCallback(async () => {
    setLoading(true);
    try {
      invalidateSkillsCache();
      const category = activeCategory === 'all' ? undefined : activeCategory;
      const data = await fetchSkills(projectId, category);
      setSkills(data);
    } catch (err) {
      console.error('[ProjectSkillsTab] load failed:', err);
      addToast('Failed to load skills', 'error');
    } finally {
      setLoading(false);
    }
  }, [projectId, activeCategory, addToast]);

  useEffect(() => { loadSkills(); }, [loadSkills]);

  const handleArchive = useCallback(async (skillId: string) => {
    try {
      await deleteSkill(skillId);
      addToast('Skill archived', 'success');
      loadSkills();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to archive skill';
      addToast(msg, 'error');
    }
  }, [addToast, loadSkills]);

  // Separate into groups
  const projectSkills = skills.filter((s) => s.project_id);
  const globalSkills = skills.filter((s) => s.team_id && !s.project_id);
  const systemSkills = skills.filter((s) => !s.team_id);

  if (loading) {
    return (
      <div className="flex flex-wrap gap-5 p-6">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-[140px] w-[280px] animate-pulse rounded-xl bg-zinc-900" />
        ))}
      </div>
    );
  }

  if (skills.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-4 py-24 text-center">
        <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-violet-500/15">
          <Wand2 className="h-8 w-8 text-violet-400" />
        </div>
        <div>
          <h3 className="text-lg font-medium text-zinc-100">No Skills Yet</h3>
          <p className="mt-1 text-sm text-zinc-500">
            Create your first skill to standardize your AI outputs
          </p>
        </div>
        <button
          onClick={() => onEditSkill?.(null)}
          className="mt-2 flex items-center gap-2 rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-violet-500"
        >
          <Plus className="h-4 w-4" />
          New Skill
        </button>
      </div>
    );
  }

  return (
    <div className="p-6">
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-zinc-100">Skills</h2>
          <p className="text-sm text-zinc-500">{skills.length} skills available</p>
        </div>
        <button
          onClick={() => onEditSkill?.(null)}
          className="flex items-center gap-2 rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-violet-500"
        >
          <Plus className="h-4 w-4" />
          New Skill
        </button>
      </div>

      {/* Category tabs */}
      <div className="mb-6 flex gap-1 rounded-lg bg-zinc-800/50 p-1">
        {CATEGORY_TABS.map((cat) => (
          <button
            key={cat}
            onClick={() => setActiveCategory(cat)}
            className={`rounded-md px-3 py-1.5 text-xs font-medium capitalize transition-colors ${
              activeCategory === cat
                ? 'bg-zinc-700 text-zinc-100'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            {cat}
          </button>
        ))}
      </div>

      {/* Skill groups */}
      {projectSkills.length > 0 && (
        <SkillGroup
          label="📁 Project Skills"
          skills={projectSkills}
          onEdit={onEditSkill}
          onArchive={handleArchive}
          canEdit
        />
      )}
      {globalSkills.length > 0 && (
        <SkillGroup
          label="🌐 Global Skills"
          skills={globalSkills}
          onEdit={onEditSkill}
          onArchive={handleArchive}
          canEdit
        />
      )}
      {systemSkills.length > 0 && (
        <SkillGroup
          label="⚙️ System Presets"
          skills={systemSkills}
          onEdit={onEditSkill}
          canEdit={false}
        />
      )}
    </div>
  );
}

// ─── Sub-components ──────────────────────────────────────────────────────────

function SkillGroup({
  label,
  skills,
  onEdit,
  onArchive,
  canEdit,
}: {
  label: string;
  skills: Skill[];
  onEdit?: (id: string | null) => void;
  onArchive?: (id: string) => void;
  canEdit: boolean;
}) {
  return (
    <div className="mb-8">
      <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">
        {label}
      </h3>
      <div className="flex flex-wrap gap-5">
        {skills.map((skill) => (
          <SkillCard
            key={skill.id}
            skill={skill}
            onClick={() => onEdit?.(skill.id)}
            onArchive={canEdit ? () => onArchive?.(skill.id) : undefined}
          />
        ))}
      </div>
    </div>
  );
}

function SkillCard({
  skill,
  onClick,
  onArchive,
}: {
  skill: Skill;
  onClick: () => void;
  onArchive?: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="group flex w-[280px] flex-col rounded-xl border border-zinc-800/60 bg-zinc-900/50 p-5 text-left transition-all duration-200 hover:-translate-y-0.5 hover:border-zinc-600 hover:bg-zinc-800/40 hover:shadow-lg"
    >
      <div className="mb-3 flex items-start justify-between">
        <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-violet-500/15">
          <span className="text-lg">{skill.icon}</span>
        </div>
        {onArchive && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onArchive();
            }}
            className="rounded p-1 text-zinc-600 opacity-0 transition-all hover:bg-zinc-700 hover:text-zinc-300 group-hover:opacity-100"
            aria-label="Archive skill"
          >
            <Archive className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      <h4 className="mb-1 truncate text-sm font-medium text-zinc-100">
        {skill.name}
      </h4>
      {skill.category && (
        <span className="mb-2 inline-block self-start rounded-full border border-violet-800/50 bg-violet-900/30 px-2 py-0.5 text-[10px] font-medium text-violet-400">
          {skill.category}
        </span>
      )}
      {skill.description && (
        <p className="line-clamp-2 text-xs leading-relaxed text-zinc-500">
          {skill.description}
        </p>
      )}
    </button>
  );
}
```

- [ ] **Step 2: Add Skills to NAV_SECTIONS**

In `frontend/components/project/ProjectNavSidebar.tsx`, add the Skills entry to NAV_SECTIONS (after `storyboard` line 45, before `output`):

```typescript
const NAV_SECTIONS = [
  { key: 'files', label: 'Files', icon: FolderOpen },
  { key: 'scripts', label: 'Scripts', icon: FileText },
  { key: 'storyboard', label: 'Storyboard', icon: Clapperboard },
  { key: 'skills', label: 'Skills', icon: Wand2 },
  { key: 'output', label: 'Output', icon: Download },
  { key: 'tasks', label: 'Tasks', icon: KanbanSquare },
  { key: 'divider-1', label: '', icon: null },
  { key: 'shares', label: 'Shares', icon: Share2 },
  { key: 'trash', label: 'Trash', icon: Trash2 },
  { key: 'divider-2', label: '', icon: null },
  { key: 'settings', label: 'Settings', icon: Settings },
] as const;
```

Add `Wand2` to the lucide-react import at the top of the file.

- [ ] **Step 3: Add ProjectSkillsTab to ProjectsPage**

In `frontend/pages/ProjectsPage.tsx`:

Add import:
```typescript
import { ProjectSkillsTab } from '../components/project/ProjectSkillsTab';
```

Add to TAB_TO_SECTION map:
```typescript
const TAB_TO_SECTION: Record<string, string> = {
  files: 'files',
  scripts: 'scripts',
  storyboard: 'storyboard',
  skills: 'skills',
  output: 'output',
  tasks: 'tasks',
  shares: 'shares',
  trash: 'trash',
};
```

Add the tab rendering (after the storyboard line, around line 216):
```tsx
{activeTab === 'skills' && <ProjectSkillsTab projectId={selectedProject.id} />}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/components/project/ProjectSkillsTab.tsx frontend/components/project/ProjectNavSidebar.tsx frontend/pages/ProjectsPage.tsx
git commit -m "feat(frontend): add Skills management page with grouped cards and nav item"
```

---

### Task 9: Frontend — Skill Editor (Full Page)

**Files:**
- Create: `frontend/components/project/SkillEditor.tsx`
- Modify: `frontend/pages/ProjectsPage.tsx` — handle editor state

- [ ] **Step 1: Install Markdown editor dependency**

Run: `cd frontend && npm install @uiw/react-md-editor`

- [ ] **Step 2: Create SkillEditor component**

```tsx
import { useState, useEffect, useCallback } from 'react';
import { ArrowLeft, Save, X } from 'lucide-react';
import MDEditor from '@uiw/react-md-editor';
import { Skill } from '../../types';
import { fetchSkillDetail, createSkill, updateSkill } from '../../services/skillService';
import { useToast } from '../../contexts/ToastContext';

const CATEGORIES = ['script', 'storyboard', 'copywriting', 'general'];
const ICONS = ['✨', '🎬', '🎞️', '✍️', '📱', '🔄', '📝', '🎯', '💡', '🎨'];
const MAX_CONTENT_LENGTH = 10000;

interface SkillEditorProps {
  skillId: string | null;
  projectId: string;
  onClose: () => void;
}

export function SkillEditor({ skillId, projectId, onClose }: SkillEditorProps) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [contentMd, setContentMd] = useState('');
  const [category, setCategory] = useState('general');
  const [icon, setIcon] = useState('✨');
  const [outputFormat, setOutputFormat] = useState('');
  const [isPublic, setIsPublic] = useState(false);
  const [isProjectScope, setIsProjectScope] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [dirty, setDirty] = useState(false);
  const { addToast } = useToast();

  const isNew = !skillId;

  // Load existing skill
  useEffect(() => {
    if (!skillId) return;
    let cancelled = false;
    setLoadingDetail(true);
    fetchSkillDetail(skillId)
      .then((skill) => {
        if (cancelled) return;
        setName(skill.name);
        setDescription(skill.description || '');
        setContentMd(skill.content_md || '');
        setCategory(skill.category || 'general');
        setIcon(skill.icon || '✨');
        setOutputFormat(skill.output_format || '');
        setIsPublic(skill.is_public);
        setIsProjectScope(!!skill.project_id);
      })
      .catch((err) => {
        if (!cancelled) addToast('Failed to load skill', 'error');
        console.error('[SkillEditor] load failed:', err);
      })
      .finally(() => {
        if (!cancelled) setLoadingDetail(false);
      });
    return () => { cancelled = true; };
  }, [skillId, addToast]);

  // Track dirty state
  useEffect(() => {
    if (!loadingDetail && (name || contentMd)) setDirty(true);
  }, [name, description, contentMd, category, icon, outputFormat, isPublic, isProjectScope, loadingDetail]);

  // Warn on navigate-away
  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  const handleSave = useCallback(async () => {
    if (!name.trim()) {
      addToast('Skill name is required', 'error');
      return;
    }
    if (!contentMd.trim()) {
      addToast('Skill content is required', 'error');
      return;
    }
    if (contentMd.length > MAX_CONTENT_LENGTH) {
      addToast(`Content too long (max ${MAX_CONTENT_LENGTH} chars)`, 'error');
      return;
    }

    setSaving(true);
    try {
      const data = {
        name: name.trim(),
        description: description.trim() || undefined,
        content_md: contentMd,
        category,
        icon,
        output_format: outputFormat.trim() || undefined,
        is_public: isPublic,
        project_id: isProjectScope ? projectId : undefined,
      };

      if (isNew) {
        await createSkill(data);
        addToast('Skill created', 'success');
      } else {
        await updateSkill(skillId, data);
        addToast('Skill updated', 'success');
      }
      setDirty(false);
      onClose();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to save skill';
      addToast(msg, 'error');
    } finally {
      setSaving(false);
    }
  }, [name, description, contentMd, category, icon, outputFormat, isPublic, isProjectScope, projectId, isNew, skillId, onClose, addToast]);

  const handleClose = useCallback(() => {
    if (dirty && !window.confirm('You have unsaved changes. Discard?')) return;
    onClose();
  }, [dirty, onClose]);

  if (loadingDetail) {
    return (
      <div className="flex h-64 items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-zinc-600 border-t-violet-500" />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Top bar */}
      <div className="flex items-center justify-between border-b border-zinc-800 px-6 py-3">
        <button
          onClick={handleClose}
          className="flex items-center gap-2 text-sm text-zinc-400 transition-colors hover:text-zinc-100"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Skills
        </button>
        <div className="flex items-center gap-2">
          <button
            onClick={handleClose}
            className="rounded-md px-3 py-1.5 text-sm text-zinc-400 transition-colors hover:text-zinc-100"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !name.trim() || !contentMd.trim()}
            className="flex items-center gap-2 rounded-md bg-violet-600 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-violet-500 disabled:opacity-40"
          >
            <Save className="h-3.5 w-3.5" />
            {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>

      {/* Editor body — two columns */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left column: metadata */}
        <div className="w-72 shrink-0 space-y-5 overflow-y-auto border-r border-zinc-800 p-6">
          {/* Name */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-zinc-400">Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My Skill"
              maxLength={200}
              className="w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-violet-500"
            />
          </div>

          {/* Category */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-zinc-400">Category</label>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-violet-500"
            >
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </div>

          {/* Description */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-zinc-400">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What does this skill do?"
              rows={3}
              maxLength={2000}
              className="w-full resize-none rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-violet-500"
            />
          </div>

          {/* Icon */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-zinc-400">Icon</label>
            <div className="flex flex-wrap gap-1.5">
              {ICONS.map((i) => (
                <button
                  key={i}
                  onClick={() => setIcon(i)}
                  className={`flex h-8 w-8 items-center justify-center rounded-md text-lg transition-colors ${
                    icon === i ? 'bg-violet-600 ring-2 ring-violet-400' : 'bg-zinc-800 hover:bg-zinc-700'
                  }`}
                >
                  {i}
                </button>
              ))}
            </div>
          </div>

          {/* Scope */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-zinc-400">Scope</label>
            <div className="space-y-2">
              <label className="flex items-center gap-2 text-sm text-zinc-300">
                <input
                  type="radio"
                  checked={isProjectScope}
                  onChange={() => setIsProjectScope(true)}
                  className="accent-violet-500"
                />
                This Project Only
              </label>
              <label className="flex items-center gap-2 text-sm text-zinc-300">
                <input
                  type="radio"
                  checked={!isProjectScope}
                  onChange={() => setIsProjectScope(false)}
                  className="accent-violet-500"
                />
                Global (all projects)
              </label>
            </div>
          </div>

          {/* Visibility */}
          <div>
            <label className="flex items-center gap-2 text-sm text-zinc-300">
              <input
                type="checkbox"
                checked={isPublic}
                onChange={(e) => setIsPublic(e.target.checked)}
                className="accent-violet-500"
              />
              Public (visible to team)
            </label>
          </div>
        </div>

        {/* Right column: content editors */}
        <div className="flex flex-1 flex-col overflow-y-auto p-6">
          {/* Content MD */}
          <div className="mb-6 flex-1">
            <div className="mb-1.5 flex items-center justify-between">
              <label className="text-xs font-medium text-zinc-400">Content (Markdown)</label>
              <span className={`text-[10px] ${contentMd.length > MAX_CONTENT_LENGTH ? 'text-red-400' : 'text-zinc-500'}`}>
                {contentMd.length}/{MAX_CONTENT_LENGTH}
              </span>
            </div>
            <div data-color-mode="dark">
              <MDEditor
                value={contentMd}
                onChange={(val) => setContentMd(val || '')}
                height={400}
                preview="edit"
              />
            </div>
          </div>

          {/* Output Format */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-zinc-400">
              Output Format (optional)
            </label>
            <textarea
              value={outputFormat}
              onChange={(e) => setOutputFormat(e.target.value)}
              placeholder="Describe the expected output format..."
              rows={4}
              maxLength={5000}
              className="w-full resize-none rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-violet-500"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Wire editor into ProjectsPage**

In `frontend/pages/ProjectsPage.tsx`, add state and rendering for the editor:

Add import:
```typescript
import { SkillEditor } from '../components/project/SkillEditor';
```

Add state (after existing state declarations):
```typescript
const [editingSkillId, setEditingSkillId] = useState<string | null | undefined>(undefined);
```

Modify the skills tab rendering:
```tsx
{activeTab === 'skills' && editingSkillId !== undefined && (
  <SkillEditor
    skillId={editingSkillId}
    projectId={selectedProject.id}
    onClose={() => setEditingSkillId(undefined)}
  />
)}
{activeTab === 'skills' && editingSkillId === undefined && (
  <ProjectSkillsTab
    projectId={selectedProject.id}
    onEditSkill={(id) => setEditingSkillId(id)}
  />
)}
```

- [ ] **Step 4: Commit**

```bash
cd frontend && npm install @uiw/react-md-editor
git add frontend/components/project/SkillEditor.tsx frontend/pages/ProjectsPage.tsx frontend/package.json frontend/package-lock.json
git commit -m "feat(frontend): add full-page SkillEditor with two-column layout and MD editor"
```

---

### Task 10: i18n Keys

**Files:**
- Modify: `frontend/public/locales/en.json`
- Modify: `frontend/public/locales/zh.json`

- [ ] **Step 1: Add English i18n keys**

Add to `frontend/public/locales/en.json` under a new `"skills"` section:

```json
"skills": {
  "title": "Skills",
  "noSkills": "No Skills Yet",
  "noSkillsDesc": "Create your first skill to standardize your AI outputs",
  "newSkill": "New Skill",
  "editSkill": "Edit Skill",
  "backToSkills": "Back to Skills",
  "save": "Save",
  "cancel": "Cancel",
  "name": "Name",
  "description": "Description",
  "category": "Category",
  "icon": "Icon",
  "content": "Content (Markdown)",
  "outputFormat": "Output Format",
  "scope": "Scope",
  "scopeProject": "This Project Only",
  "scopeGlobal": "Global (all projects)",
  "visibility": "Visibility",
  "public": "Public (visible to team)",
  "archived": "Skill archived",
  "created": "Skill created",
  "updated": "Skill updated",
  "unsavedChanges": "You have unsaved changes. Discard?",
  "nameRequired": "Skill name is required",
  "contentRequired": "Skill content is required",
  "contentTooLong": "Content too long (max 10,000 chars)",
  "projectSkills": "Project Skills",
  "globalSkills": "Global Skills",
  "systemPresets": "System Presets",
  "selectSkill": "Select Skill",
  "noSkill": "No Skill",
  "using": "Using",
  "searchSkills": "Search skills..."
}
```

- [ ] **Step 2: Add Chinese i18n keys**

Add to `frontend/public/locales/zh.json`:

```json
"skills": {
  "title": "技能",
  "noSkills": "暂无技能",
  "noSkillsDesc": "创建你的第一个技能来规范 AI 输出",
  "newSkill": "新建技能",
  "editSkill": "编辑技能",
  "backToSkills": "返回技能列表",
  "save": "保存",
  "cancel": "取消",
  "name": "名称",
  "description": "描述",
  "category": "分类",
  "icon": "图标",
  "content": "内容 (Markdown)",
  "outputFormat": "输出格式",
  "scope": "作用范围",
  "scopeProject": "仅限本项目",
  "scopeGlobal": "全局（所有项目）",
  "visibility": "可见性",
  "public": "公开（团队可见）",
  "archived": "技能已归档",
  "created": "技能已创建",
  "updated": "技能已更新",
  "unsavedChanges": "有未保存的修改，确认丢弃？",
  "nameRequired": "技能名称不能为空",
  "contentRequired": "技能内容不能为空",
  "contentTooLong": "内容过长（最多 10,000 字符）",
  "projectSkills": "项目技能",
  "globalSkills": "全局技能",
  "systemPresets": "系统预设",
  "selectSkill": "选择技能",
  "noSkill": "不使用技能",
  "using": "使用中",
  "searchSkills": "搜索技能..."
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(i18n): add Skill system translation keys (en + zh)"
```

---

### Task 11: Cleanup — Deprecate style_templates

**Files:**
- Modify: `backend/app/api/style_templates_router.py`
- Modify: `frontend/services/styleTemplateService.ts`

- [ ] **Step 1: Add deprecation redirect to style_templates_router**

Replace `backend/app/api/style_templates_router.py` content with a redirect shim:

```python
"""Style Templates Router — DEPRECATED, redirects to Skills."""

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter(prefix="/style-templates")


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def redirect_to_skills(path: str = ""):
    """All style-templates endpoints redirect to /skills."""
    return RedirectResponse(url=f"/api/v1/skills/{path}", status_code=301)


@router.api_route("", methods=["GET", "POST"])
async def redirect_to_skills_root():
    return RedirectResponse(url="/api/v1/skills", status_code=301)
```

- [ ] **Step 2: Update styleTemplateService.ts to re-export from skillService**

Replace `frontend/services/styleTemplateService.ts`:

```typescript
/**
 * @deprecated Use skillService instead. This file is a compatibility shim.
 */
export {
  fetchSkills as fetchStyleTemplates,
  createSkill as createStyleTemplate,
  updateSkill as updateStyleTemplate,
  deleteSkill as deleteStyleTemplate,
} from './skillService';
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/style_templates_router.py frontend/services/styleTemplateService.ts
git commit -m "refactor: deprecate style_templates with redirect shim and re-exports"
```
