# Eagle-Style Tag Picker Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace UnifiedTagPicker and ParserTagSelector with a unified EagleTagPicker featuring floating panel, category sidebar, starred tags, and display settings.

**Architecture:** New `EagleTagPicker/` component directory with 8 focused files. Backend adds `user_tag_preferences` table + 2 API endpoints. Portal-based floating panel positioned outside sidebar. Two modes: callback-driven (resource detail) and controlled (parser form).

**Tech Stack:** React 19, TypeScript, TailwindCSS, FastAPI, Supabase (PostgreSQL), Pydantic

**Spec:** `docs/superpowers/specs/2026-03-14-eagle-tag-picker-design.md`

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `supabase/migrations/105_user_tag_preferences.sql` | Database table + RLS |
| `backend/app/schemas/tag_preferences.py` | Pydantic request/response models |
| `backend/app/repositories/tag_preferences_repository.py` | Data access for preferences |
| `frontend/components/EagleTagPicker/types.ts` | Local TypeScript interfaces |
| `frontend/components/EagleTagPicker/useTagPreferences.ts` | Hook for preferences API |
| `frontend/components/EagleTagPicker/TagPill.tsx` | Assigned tag pill with ✕ button |
| `frontend/components/EagleTagPicker/TagRow.tsx` | Single tag row in panel |
| `frontend/components/EagleTagPicker/CategorySidebar.tsx` | Left sidebar with groups |
| `frontend/components/EagleTagPicker/SettingsPopover.tsx` | Settings gear popover |
| `frontend/components/EagleTagPicker/TagContent.tsx` | Right content area |
| `frontend/components/EagleTagPicker/FloatingPanel.tsx` | Portal-based resizable panel |
| `frontend/components/EagleTagPicker/index.tsx` | Main component + trigger |
| `frontend/services/tagPreferencesService.ts` | API service for preferences |

### Modified Files
| File | Change |
|------|--------|
| `backend/app/api/tags_router.py` | Add `/preferences` GET + PATCH routes (before `/{tag_id}`) |
| `frontend/components/MediaCard.tsx` | Replace UnifiedTagPicker import → EagleTagPicker |
| `frontend/components/DownloadsView.tsx` | Replace UnifiedTagPicker import → EagleTagPicker |
| `frontend/components/ResourceDetail.tsx` | Replace UnifiedTagPicker import → EagleTagPicker |
| `frontend/components/ResourceInfoPanel.tsx` | Replace UnifiedTagPicker import → EagleTagPicker |
| `frontend/pages/ParserPage.tsx` | Replace ParserTagSelector import → EagleTagPicker (Mode 2) |

### Deprecated (not deleted yet)
| File | Status |
|------|--------|
| `frontend/components/UnifiedTagPicker.tsx` | Keep until all consumers migrated, then delete |
| `frontend/components/ParserTagSelector.tsx` | Keep until all consumers migrated, then delete |

---

## Chunk 1: Backend (Database + API)

### Task 1: Database Migration

**Files:**
- Create: `supabase/migrations/105_user_tag_preferences.sql`

- [ ] **Step 1: Write migration SQL**

```sql
-- 105_user_tag_preferences.sql
-- User preferences for Eagle-style tag picker (starred tags, display settings, panel size)

CREATE TABLE IF NOT EXISTS user_tag_preferences (
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  starred_tag_ids TEXT[] DEFAULT '{}',
  picker_settings JSONB DEFAULT '{
    "layout": "list",
    "columnWidth": "medium",
    "showStarred": true,
    "showRecently": true,
    "showRecommended": false,
    "showCount": true
  }'::jsonb,
  panel_size JSONB DEFAULT '{"width": 480, "height": 400}'::jsonb,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (user_id)
);

-- RLS
ALTER TABLE user_tag_preferences ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can manage own tag preferences"
  ON user_tag_preferences FOR ALL
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

-- Allow service role full access
CREATE POLICY "Service role full access on user_tag_preferences"
  ON user_tag_preferences FOR ALL
  USING (auth.jwt()->>'role' = 'service_role');
```

**Note:** Verify no other migration has taken number 105 before creating. If a conflict exists, use the next available number.

- [ ] **Step 2: Apply migration via Supabase MCP**

Run: `mcp__supabase__apply_migration` with name `user_tag_preferences`

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/105_user_tag_preferences.sql
git commit -m "feat(db): add user_tag_preferences table for Eagle tag picker"
```

---

### Task 2: Pydantic Schemas

**Files:**
- Create: `backend/app/schemas/tag_preferences.py`

- [ ] **Step 1: Create schema file**

```python
"""Schemas for user tag picker preferences."""

from typing import Optional

from pydantic import BaseModel, Field


class PickerSettings(BaseModel):
    """Display settings for the tag picker panel."""
    layout: str = Field(default="list", pattern="^(list|grid)$")
    columnWidth: str = Field(default="medium", pattern="^(small|medium|large)$")
    showStarred: bool = True
    showRecently: bool = True
    showRecommended: bool = False
    showCount: bool = True


class PanelSize(BaseModel):
    """Persisted panel dimensions."""
    width: int = Field(default=480, ge=300, le=1200)
    height: int = Field(default=400, ge=250, le=800)


class TagPreferencesResponse(BaseModel):
    """Full preferences response."""
    starred_tag_ids: list[str] = Field(default_factory=list)
    picker_settings: PickerSettings = Field(default_factory=PickerSettings)
    panel_size: PanelSize = Field(default_factory=PanelSize)


class TagPreferencesUpdate(BaseModel):
    """Partial update — all fields optional."""
    starred_tag_ids: Optional[list[str]] = None
    picker_settings: Optional[dict] = None  # partial JSONB merge
    panel_size: Optional[PanelSize] = None
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/schemas/tag_preferences.py
git commit -m "feat(schemas): add tag preferences Pydantic models"
```

---

### Task 3: Repository

**Files:**
- Create: `backend/app/repositories/tag_preferences_repository.py`

- [ ] **Step 1: Create repository**

```python
"""Repository for user tag picker preferences."""

import json
from typing import Optional

from app.db.supabase_client import get_async_supabase_admin


class TagPreferencesRepository:
    """Data access for user_tag_preferences table."""

    DEFAULTS = {
        "starred_tag_ids": [],
        "picker_settings": {
            "layout": "list",
            "columnWidth": "medium",
            "showStarred": True,
            "showRecently": True,
            "showRecommended": False,
            "showCount": True,
        },
        "panel_size": {"width": 480, "height": 400},
    }

    async def get_preferences(self, user_id: str) -> dict:
        """Get preferences for a user. Returns defaults if not found."""
        client = await get_async_supabase_admin()
        result = (
            await client.table("user_tag_preferences")
            .select("starred_tag_ids, picker_settings, panel_size")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )

        if not result.data:
            return dict(self.DEFAULTS)

        row = result.data
        return {
            "starred_tag_ids": row.get("starred_tag_ids") or [],
            "picker_settings": {**self.DEFAULTS["picker_settings"], **(row.get("picker_settings") or {})},
            "panel_size": {**self.DEFAULTS["panel_size"], **(row.get("panel_size") or {})},
        }

    async def upsert_preferences(self, user_id: str, updates: dict) -> dict:
        """Upsert preferences. Merges picker_settings at field level."""
        client = await get_async_supabase_admin()

        # Get current to merge
        current = await self.get_preferences(user_id)

        # Build upsert data
        data = {"user_id": user_id}

        if "starred_tag_ids" in updates and updates["starred_tag_ids"] is not None:
            data["starred_tag_ids"] = updates["starred_tag_ids"]

        if "picker_settings" in updates and updates["picker_settings"] is not None:
            merged = {**current["picker_settings"], **updates["picker_settings"]}
            data["picker_settings"] = merged

        if "panel_size" in updates and updates["panel_size"] is not None:
            size = updates["panel_size"]
            data["panel_size"] = size if isinstance(size, dict) else size.model_dump()

        result = (
            await client.table("user_tag_preferences")
            .upsert(data, on_conflict="user_id")
            .execute()
        )

        return await self.get_preferences(user_id)
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/repositories/tag_preferences_repository.py
git commit -m "feat(repo): add TagPreferencesRepository for user preferences"
```

---

### Task 4: API Routes

**Files:**
- Modify: `backend/app/api/tags_router.py` — insert `/preferences` routes before `/{tag_id}` (before line 133)

- [ ] **Step 1: Add preference routes to tags_router.py**

Insert BEFORE the `@router.get("/{tag_id}")` route (line 133). Add imports at top:

```python
from app.repositories.tag_preferences_repository import TagPreferencesRepository
from app.schemas.tag_preferences import (
    TagPreferencesResponse,
    TagPreferencesUpdate,
)
```

**IMPORTANT:** Unlike other routes in this file that use optional auth (`AuthDep = None`), preferences endpoints require authentication because they read `auth.user_id`. Do NOT add `= None` default.

Add routes before `/{tag_id}`:

```python
@router.get("/preferences", response_model=TagPreferencesResponse)
async def get_tag_preferences(auth: AuthDep):
    """Get current user's tag picker preferences."""
    repo = TagPreferencesRepository()
    prefs = await repo.get_preferences(auth.user_id)
    return TagPreferencesResponse(**prefs)


@router.patch("/preferences", response_model=TagPreferencesResponse)
async def update_tag_preferences(
    auth: AuthDep,
    request: TagPreferencesUpdate,
):
    """Update tag picker preferences (partial merge)."""
    repo = TagPreferencesRepository()
    updated = await repo.upsert_preferences(
        auth.user_id,
        request.model_dump(exclude_none=True),
    )
    return TagPreferencesResponse(**updated)
```

- [ ] **Step 2: Verify route order**

Ensure the final route order in `tags_router.py` is:
1. `GET /groups`
2. `GET /` (list tags)
3. `GET /statistics`
4. `POST /` (create tag)
5. **`GET /preferences`** ← NEW
6. **`PATCH /preferences`** ← NEW
7. `GET /{tag_id}`
8. `PUT /{tag_id}`
9. `DELETE /{tag_id}`

- [ ] **Step 3: Test endpoints manually**

```bash
cd backend && uv run uvicorn app.main:app --reload --port 8080
# GET /api/v1/tags/preferences → should return defaults
# PATCH /api/v1/tags/preferences with {"starred_tag_ids": ["123"]} → should upsert
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/tags_router.py
git commit -m "feat(api): add GET/PATCH /tags/preferences endpoints"
```

---

## Chunk 2: Frontend Service + Hook

### Task 5: Preferences Service

**Files:**
- Create: `frontend/services/tagPreferencesService.ts`

- [ ] **Step 1: Create service**

```typescript
import { getAuthHeaders } from './parserService';

const API_BASE = import.meta.env.VITE_API_URL || '';

export interface PickerSettings {
  layout: 'list' | 'grid';
  columnWidth: 'small' | 'medium' | 'large';
  showStarred: boolean;
  showRecently: boolean;
  showRecommended: boolean;
  showCount: boolean;
}

export interface PanelSize {
  width: number;
  height: number;
}

export interface TagPreferences {
  starred_tag_ids: string[];
  picker_settings: PickerSettings;
  panel_size: PanelSize;
}

const DEFAULTS: TagPreferences = {
  starred_tag_ids: [],
  picker_settings: {
    layout: 'list',
    columnWidth: 'medium',
    showStarred: true,
    showRecently: true,
    showRecommended: false,
    showCount: true,
  },
  panel_size: { width: 480, height: 400 },
};

export async function fetchTagPreferences(): Promise<TagPreferences> {
  try {
    const res = await fetch(`${API_BASE}/api/v1/tags/preferences`, {
      headers: await getAuthHeaders(),
    });
    if (!res.ok) return { ...DEFAULTS };
    return await res.json();
  } catch (err) {
    console.error('Failed to fetch tag preferences:', err);
    return { ...DEFAULTS };
  }
}

export async function updateTagPreferences(
  updates: Partial<TagPreferences>,
): Promise<TagPreferences> {
  const res = await fetch(`${API_BASE}/api/v1/tags/preferences`, {
    method: 'PATCH',
    headers: {
      ...(await getAuthHeaders()),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error(`Failed to update preferences: ${res.status}`);
  return await res.json();
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/services/tagPreferencesService.ts
git commit -m "feat(service): add tagPreferencesService for picker preferences"
```

---

### Task 6: Local Types + useTagPreferences Hook

**Files:**
- Create: `frontend/components/EagleTagPicker/types.ts`
- Create: `frontend/components/EagleTagPicker/useTagPreferences.ts`

- [ ] **Step 1: Create types**

```typescript
import type { Tag } from '../../types';

export interface EagleTagPickerProps {
  // Mode 1: Resource detail
  assignedTags?: Tag[];
  onAdd?: (tagId: string) => void;
  onRemove?: (tagId: string) => void;

  // Mode 2: Form selection
  selectedTagIds?: string[];
  onTagsChange?: (tagIds: string[]) => void;

  // Shared
  allTags: Tag[];
  readOnly?: boolean;
  onCreate?: (name: string, color: string) => Promise<Tag | null>;
}
```

- [ ] **Step 2: Create useTagPreferences hook**

```typescript
import { useState, useEffect, useCallback, useRef } from 'react';
import {
  fetchTagPreferences,
  updateTagPreferences,
  type TagPreferences,
  type PickerSettings,
  type PanelSize,
} from '../../services/tagPreferencesService';

const DEFAULTS: TagPreferences = {
  starred_tag_ids: [],
  picker_settings: {
    layout: 'list',
    columnWidth: 'medium',
    showStarred: true,
    showRecently: true,
    showRecommended: false,
    showCount: true,
  },
  panel_size: { width: 480, height: 400 },
};

export function useTagPreferences() {
  const [prefs, setPrefs] = useState<TagPreferences>(DEFAULTS);
  const [loading, setLoading] = useState(true);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    fetchTagPreferences()
      .then(setPrefs)
      .finally(() => setLoading(false));
  }, []);

  const update = useCallback(
    (updates: Partial<TagPreferences>) => {
      // Optimistic update
      setPrefs((prev) => ({
        ...prev,
        ...updates,
        picker_settings: updates.picker_settings
          ? { ...prev.picker_settings, ...updates.picker_settings }
          : prev.picker_settings,
      }));

      // Debounced persist
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        updateTagPreferences(updates).catch((err) =>
          console.error('Failed to save preferences:', err),
        );
      }, 500);
    },
    [],
  );

  const toggleStar = useCallback(
    (tagId: string) => {
      const current = prefs.starred_tag_ids;
      const next = current.includes(tagId)
        ? current.filter((id) => id !== tagId)
        : [...current, tagId];
      update({ starred_tag_ids: next });
    },
    [prefs.starred_tag_ids, update],
  );

  const updateSettings = useCallback(
    (partial: Partial<PickerSettings>) => {
      update({ picker_settings: partial as any });
    },
    [update],
  );

  const updatePanelSize = useCallback(
    (size: PanelSize) => {
      update({ panel_size: size });
    },
    [update],
  );

  return {
    prefs,
    loading,
    toggleStar,
    updateSettings,
    updatePanelSize,
  };
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/components/EagleTagPicker/types.ts frontend/components/EagleTagPicker/useTagPreferences.ts
git commit -m "feat(hook): add EagleTagPicker types and useTagPreferences hook"
```

---

## Chunk 3: UI Components (Atoms)

### Task 7: TagPill (assigned tag with ✕)

**Files:**
- Create: `frontend/components/EagleTagPicker/TagPill.tsx`

- [ ] **Step 1: Create TagPill component**

```typescript
import React from 'react';
import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { Tag } from '../../types';

interface TagPillProps {
  tag: Tag;
  onRemove?: (tagId: string) => void;
  readOnly?: boolean;
}

export const TagPill: React.FC<TagPillProps> = ({ tag, onRemove, readOnly }) => {
  const { i18n } = useTranslation();
  const label = i18n.language === 'zh' && tag.name_zh ? tag.name_zh : tag.name;
  const color = tag.color || '#6366f1';

  return (
    <span
      className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-full border transition-colors"
      style={{
        backgroundColor: `${color}20`,
        color: color,
        borderColor: `${color}30`,
      }}
    >
      {label}
      {!readOnly && onRemove && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onRemove(String(tag.id));
          }}
          className="flex items-center justify-center w-4 h-4 rounded-full hover:bg-white/20 transition-colors"
          title="Remove"
        >
          <X size={12} />
        </button>
      )}
    </span>
  );
};
```

- [ ] **Step 2: Commit**

```bash
git add frontend/components/EagleTagPicker/TagPill.tsx
git commit -m "feat(ui): add TagPill component with always-visible remove button"
```

---

### Task 8: TagRow (tag in panel)

**Files:**
- Create: `frontend/components/EagleTagPicker/TagRow.tsx`

- [ ] **Step 1: Create TagRow component**

```typescript
import React from 'react';
import { Star } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { Tag } from '../../types';

interface TagRowProps {
  tag: Tag;
  isSelected: boolean;
  isStarred: boolean;
  showCount: boolean;
  onClick: () => void;
  onContextMenu: (e: React.MouseEvent) => void;
}

export const TagRow: React.FC<TagRowProps> = ({
  tag,
  isSelected,
  isStarred,
  showCount,
  onClick,
  onContextMenu,
}) => {
  const { i18n } = useTranslation();
  const label = i18n.language === 'zh' && tag.name_zh ? tag.name_zh : tag.name;
  const count = tag.media_count ?? tag.video_count ?? 0;

  return (
    <button
      onClick={onClick}
      onContextMenu={onContextMenu}
      className={`flex items-center gap-1.5 px-2 py-1 text-xs rounded transition-colors w-full min-w-0 ${
        isSelected
          ? 'bg-indigo-500/20 text-indigo-300'
          : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200'
      }`}
    >
      <span
        className="w-2.5 h-2.5 rounded-full shrink-0"
        style={{ backgroundColor: tag.color || '#6366f1' }}
      />
      <span className="truncate flex-1 text-left">{label}</span>
      {isStarred && <Star size={10} className="text-yellow-500 shrink-0 fill-yellow-500" />}
      {showCount && count > 0 && (
        <span className="text-[10px] text-zinc-600 shrink-0">({count})</span>
      )}
    </button>
  );
};
```

- [ ] **Step 2: Commit**

```bash
git add frontend/components/EagleTagPicker/TagRow.tsx
git commit -m "feat(ui): add TagRow component for panel tag display"
```

---

### Task 9: CategorySidebar

**Files:**
- Create: `frontend/components/EagleTagPicker/CategorySidebar.tsx`

- [ ] **Step 1: Create CategorySidebar component**

```typescript
import React from 'react';
import { LayoutGrid, Circle, FolderOpen } from 'lucide-react';

interface GroupInfo {
  name: string;
  count: number;
}

interface CategorySidebarProps {
  totalCount: number;
  uncategorizedCount: number;
  groups: GroupInfo[];
  selectedGroup: string | null; // null = "All"
  onSelectGroup: (group: string | null) => void;
}

export const CategorySidebar: React.FC<CategorySidebarProps> = ({
  totalCount,
  uncategorizedCount,
  groups,
  selectedGroup,
  onSelectGroup,
}) => {
  const itemClass = (active: boolean) =>
    `flex items-center justify-between gap-2 px-2 py-1.5 rounded text-xs cursor-pointer transition-colors ${
      active
        ? 'bg-indigo-500/20 text-indigo-300'
        : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200'
    }`;

  return (
    <div className="w-[120px] shrink-0 border-r border-zinc-800 overflow-y-auto py-2 px-1.5">
      {/* All */}
      <button
        className={itemClass(selectedGroup === null)}
        onClick={() => onSelectGroup(null)}
      >
        <span className="flex items-center gap-1.5">
          <LayoutGrid size={12} />
          <span>All</span>
        </span>
        <span className="text-zinc-600">{totalCount}</span>
      </button>

      {/* Uncategorized */}
      <button
        className={itemClass(selectedGroup === '__uncategorized__')}
        onClick={() => onSelectGroup('__uncategorized__')}
      >
        <span className="flex items-center gap-1.5">
          <Circle size={12} />
          <span>Uncategorized</span>
        </span>
        <span className="text-zinc-600">{uncategorizedCount}</span>
      </button>

      {/* Groups header */}
      {groups.length > 0 && (
        <div className="mt-3 mb-1 px-2">
          <span className="text-[10px] font-semibold text-zinc-600 uppercase tracking-wider">
            Groups ({groups.length})
          </span>
        </div>
      )}

      {/* Group items */}
      {groups.map((group) => (
        <button
          key={group.name}
          className={itemClass(selectedGroup === group.name)}
          onClick={() => onSelectGroup(group.name)}
        >
          <span className="flex items-center gap-1.5 min-w-0">
            <FolderOpen size={12} className="shrink-0" />
            <span className="truncate">{group.name}</span>
          </span>
          <span className="text-zinc-600 shrink-0">{group.count}</span>
        </button>
      ))}
    </div>
  );
};
```

- [ ] **Step 2: Commit**

```bash
git add frontend/components/EagleTagPicker/CategorySidebar.tsx
git commit -m "feat(ui): add CategorySidebar for group filtering"
```

---

### Task 10: SettingsPopover

**Files:**
- Create: `frontend/components/EagleTagPicker/SettingsPopover.tsx`

- [ ] **Step 1: Create SettingsPopover component**

```typescript
import React, { useRef, useEffect } from 'react';
import { List, LayoutGrid } from 'lucide-react';
import type { PickerSettings } from '../../services/tagPreferencesService';

interface SettingsPopoverProps {
  settings: PickerSettings;
  onUpdate: (partial: Partial<PickerSettings>) => void;
  onClose: () => void;
}

export const SettingsPopover: React.FC<SettingsPopoverProps> = ({
  settings,
  onUpdate,
  onClose,
}) => {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [onClose]);

  const Toggle: React.FC<{ label: string; checked: boolean; disabled?: boolean; onChange: (v: boolean) => void }> = ({
    label, checked, disabled, onChange,
  }) => (
    <div className="flex items-center justify-between py-1">
      <span className={`text-xs ${disabled ? 'text-zinc-600' : 'text-zinc-300'}`}>{label}</span>
      <button
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`w-8 h-4 rounded-full transition-colors relative ${
          disabled ? 'bg-zinc-800 cursor-not-allowed' : checked ? 'bg-indigo-500' : 'bg-zinc-700'
        }`}
      >
        <span
          className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform ${
            checked ? 'translate-x-4' : 'translate-x-0.5'
          }`}
        />
      </button>
    </div>
  );

  return (
    <div
      ref={ref}
      className="absolute right-0 top-full mt-1 z-[70] bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl w-52 p-3 space-y-2"
    >
      {/* Layout */}
      <div className="flex items-center justify-between py-1">
        <span className="text-xs text-zinc-300">Layout</span>
        <div className="flex gap-1">
          <button
            onClick={() => onUpdate({ layout: 'list' })}
            className={`p-1 rounded ${settings.layout === 'list' ? 'bg-zinc-700 text-white' : 'text-zinc-500'}`}
          >
            <List size={14} />
          </button>
          <button
            onClick={() => onUpdate({ layout: 'grid' })}
            className={`p-1 rounded ${settings.layout === 'grid' ? 'bg-zinc-700 text-white' : 'text-zinc-500'}`}
          >
            <LayoutGrid size={14} />
          </button>
        </div>
      </div>

      {/* Column Width */}
      <div className="flex items-center justify-between py-1">
        <span className="text-xs text-zinc-300">Column Width</span>
        <select
          value={settings.columnWidth}
          onChange={(e) => onUpdate({ columnWidth: e.target.value as any })}
          className="bg-zinc-800 border border-zinc-700 rounded px-2 py-0.5 text-xs text-zinc-300"
        >
          <option value="small">Small</option>
          <option value="medium">Medium</option>
          <option value="large">Large</option>
        </select>
      </div>

      <div className="border-t border-zinc-800 my-1" />

      {/* Toggles */}
      <Toggle label="Starred" checked={settings.showStarred} onChange={(v) => onUpdate({ showStarred: v })} />
      <Toggle label="Frequently Used" checked={settings.showRecently} onChange={(v) => onUpdate({ showRecently: v })} />
      <Toggle label="Recommended" checked={settings.showRecommended} disabled onChange={() => {}} />
      <Toggle label="Count" checked={settings.showCount} onChange={(v) => onUpdate({ showCount: v })} />
    </div>
  );
};
```

- [ ] **Step 2: Commit**

```bash
git add frontend/components/EagleTagPicker/SettingsPopover.tsx
git commit -m "feat(ui): add SettingsPopover for picker display preferences"
```

---

## Chunk 4: UI Components (Composition)

### Task 11: TagContent (right content area)

**Files:**
- Create: `frontend/components/EagleTagPicker/TagContent.tsx`

- [ ] **Step 1: Create TagContent component**

```typescript
import React, { useMemo, useState, useCallback } from 'react';
import { Star, Flame, FolderOpen } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { TagRow } from './TagRow';
import type { Tag } from '../../types';
import type { PickerSettings } from '../../services/tagPreferencesService';

interface TagContentProps {
  allTags: Tag[];
  selectedIds: Set<string>;
  starredIds: string[];
  settings: PickerSettings;
  selectedGroup: string | null;
  search: string;
  onToggleTag: (tagId: string) => void;
  onToggleStar: (tagId: string) => void;
}

export const TagContent: React.FC<TagContentProps> = ({
  allTags,
  selectedIds,
  starredIds,
  settings,
  selectedGroup,
  search,
  onToggleTag,
  onToggleStar,
}) => {
  const { i18n } = useTranslation();
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; tagId: string } | null>(null);

  const starredSet = useMemo(() => new Set(starredIds), [starredIds]);

  // Filter by search
  const searchFiltered = useMemo(() => {
    if (!search) return allTags;
    const q = search.toLowerCase();
    return allTags.filter(
      (t) => t.name.toLowerCase().includes(q) || (t.name_zh && t.name_zh.toLowerCase().includes(q)),
    );
  }, [allTags, search]);

  // Filter by group
  const groupFiltered = useMemo(() => {
    if (selectedGroup === null) return searchFiltered;
    if (selectedGroup === '__uncategorized__') return searchFiltered.filter((t) => !t.group_name);
    return searchFiltered.filter((t) => t.group_name === selectedGroup);
  }, [searchFiltered, selectedGroup]);

  // Starred tags
  const starredTags = useMemo(
    () => (settings.showStarred ? groupFiltered.filter((t) => starredSet.has(String(t.id))) : []),
    [groupFiltered, starredSet, settings.showStarred],
  );

  // Frequently used (top 6 by media_count, only when not searching)
  const frequentTags = useMemo(() => {
    if (!settings.showRecently || search) return [];
    return [...groupFiltered]
      .filter((t) => (t.media_count ?? t.video_count ?? 0) > 0)
      .sort((a, b) => (b.media_count ?? b.video_count ?? 0) - (a.media_count ?? a.video_count ?? 0))
      .slice(0, 6);
  }, [groupFiltered, settings.showRecently, search]);

  // Grouped tags
  const grouped = useMemo(() => {
    const map = new Map<string, Tag[]>();
    const ungrouped: Tag[] = [];
    for (const tag of groupFiltered) {
      const group = tag.group_name;
      if (!group) ungrouped.push(tag);
      else {
        if (!map.has(group)) map.set(group, []);
        map.get(group)!.push(tag);
      }
    }
    const entries = Array.from(map.entries());
    if (ungrouped.length > 0) entries.push(['Uncategorized', ungrouped]);
    return entries;
  }, [groupFiltered]);

  const handleContextMenu = useCallback((e: React.MouseEvent, tagId: string) => {
    e.preventDefault();
    setContextMenu({ x: e.clientX, y: e.clientY, tagId });
  }, []);

  const closeContextMenu = useCallback(() => setContextMenu(null), []);

  const colWidthClass = settings.columnWidth === 'small' ? 'grid-cols-3' : settings.columnWidth === 'large' ? 'grid-cols-1' : 'grid-cols-2';
  const gridClass = settings.layout === 'grid' ? 'flex flex-wrap gap-1' : `grid ${colWidthClass} gap-x-1`;

  const renderTag = (tag: Tag) => (
    <TagRow
      key={tag.id}
      tag={tag}
      isSelected={selectedIds.has(String(tag.id))}
      isStarred={starredSet.has(String(tag.id))}
      showCount={settings.showCount}
      onClick={() => onToggleTag(String(tag.id))}
      onContextMenu={(e) => handleContextMenu(e, String(tag.id))}
    />
  );

  const SectionHeader: React.FC<{ icon: React.ReactNode; label: string; count: number }> = ({ icon, label, count }) => (
    <div className="flex items-center gap-1.5 px-1 py-1.5">
      {icon}
      <span className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider">
        {label} ({count})
      </span>
    </div>
  );

  return (
    <div className="flex-1 overflow-y-auto p-2 space-y-2" onClick={closeContextMenu}>
      {/* Starred */}
      {starredTags.length > 0 && (
        <div>
          <SectionHeader icon={<Star size={10} className="text-yellow-500 fill-yellow-500" />} label="Starred" count={starredTags.length} />
          <div className={gridClass}>{starredTags.map(renderTag)}</div>
        </div>
      )}

      {/* Frequently Used */}
      {frequentTags.length > 0 && (
        <div>
          <SectionHeader icon={<Flame size={10} className="text-orange-500" />} label="Frequently Used" count={frequentTags.length} />
          <div className={gridClass}>{frequentTags.map(renderTag)}</div>
        </div>
      )}

      {/* Grouped tags */}
      {grouped.map(([groupName, groupTags]) => (
        <div key={groupName}>
          <SectionHeader icon={<FolderOpen size={10} className="text-zinc-500" />} label={groupName} count={groupTags.length} />
          <div className={gridClass}>{groupTags.map(renderTag)}</div>
        </div>
      ))}

      {groupFiltered.length === 0 && (
        <p className="text-xs text-zinc-600 text-center py-6">No tags found</p>
      )}

      {/* Right-click context menu */}
      {contextMenu && (
        <div
          className="fixed z-[80] bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl py-1 w-32"
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          <button
            onClick={() => { onToggleStar(contextMenu.tagId); closeContextMenu(); }}
            className="w-full px-3 py-1.5 text-xs text-left text-zinc-300 hover:bg-zinc-800 flex items-center gap-2"
          >
            <Star size={10} className={starredSet.has(contextMenu.tagId) ? 'fill-yellow-500 text-yellow-500' : ''} />
            {starredSet.has(contextMenu.tagId) ? 'Unstar' : 'Star'}
          </button>
        </div>
      )}
    </div>
  );
};
```

- [ ] **Step 2: Commit**

```bash
git add frontend/components/EagleTagPicker/TagContent.tsx
git commit -m "feat(ui): add TagContent with starred, frequent, and grouped sections"
```

---

### Task 12: FloatingPanel (portal + resizable)

**Files:**
- Create: `frontend/components/EagleTagPicker/FloatingPanel.tsx`

- [ ] **Step 1: Create FloatingPanel component**

```typescript
import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { Search, Settings, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { CategorySidebar } from './CategorySidebar';
import { TagContent } from './TagContent';
import { SettingsPopover } from './SettingsPopover';
import type { Tag } from '../../types';
import type { PickerSettings, PanelSize } from '../../services/tagPreferencesService';

interface FloatingPanelProps {
  triggerRef: React.RefObject<HTMLElement | null>;
  allTags: Tag[];
  selectedIds: Set<string>;
  starredIds: string[];
  settings: PickerSettings;
  panelSize: PanelSize;
  onToggleTag: (tagId: string) => void;
  onToggleStar: (tagId: string) => void;
  onUpdateSettings: (partial: Partial<PickerSettings>) => void;
  onPanelResize: (size: PanelSize) => void;
  onClose: () => void;
}

export const FloatingPanel: React.FC<FloatingPanelProps> = ({
  triggerRef,
  allTags,
  selectedIds,
  starredIds,
  settings,
  panelSize,
  onToggleTag,
  onToggleStar,
  onUpdateSettings,
  onPanelResize,
  onClose,
}) => {
  const { t } = useTranslation();
  const panelRef = useRef<HTMLDivElement>(null);
  const [search, setSearch] = useState('');
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [size, setSize] = useState(panelSize);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const resizeRef = useRef<{ startX: number; startY: number; startW: number; startH: number } | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  // Position panel to the left of trigger
  useEffect(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const panelW = size.width;
    const panelH = size.height;

    let left = rect.left - panelW - 8;
    let top = rect.top;

    // Fallback: if not enough space on left, try right
    if (left < 8) {
      left = rect.right + 8;
    }
    // Fallback: if not enough space on right either, center
    if (left + panelW > window.innerWidth - 8) {
      left = Math.max(8, (window.innerWidth - panelW) / 2);
    }
    // Vertical bounds
    if (top + panelH > window.innerHeight - 8) {
      top = Math.max(8, window.innerHeight - panelH - 8);
    }

    setPosition({ top, left });
  }, [triggerRef, size.width, size.height]);

  // Close on ESC
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  // Close on click outside
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    // Use setTimeout to avoid closing immediately from the same click that opened
    const timer = setTimeout(() => document.addEventListener('mousedown', handler), 0);
    return () => { clearTimeout(timer); document.removeEventListener('mousedown', handler); };
  }, [onClose]);

  // Resize handlers
  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    resizeRef.current = { startX: e.clientX, startY: e.clientY, startW: size.width, startH: size.height };
    const handleMove = (me: MouseEvent) => {
      if (!resizeRef.current) return;
      const newW = Math.min(1200, Math.max(300, resizeRef.current.startW + me.clientX - resizeRef.current.startX));
      const newH = Math.min(800, Math.max(250, resizeRef.current.startH + me.clientY - resizeRef.current.startY));
      setSize({ width: newW, height: newH });
    };
    const handleUp = () => {
      document.removeEventListener('mousemove', handleMove);
      document.removeEventListener('mouseup', handleUp);
      resizeRef.current = null;
      // Debounced persist
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        setSize((s) => { onPanelResize(s); return s; });
      }, 500);
    };
    document.addEventListener('mousemove', handleMove);
    document.addEventListener('mouseup', handleUp);
  }, [size, onPanelResize]);

  // Compute group info
  const { groups, totalCount, uncategorizedCount } = useMemo(() => {
    const groupMap = new Map<string, number>();
    let uncat = 0;
    for (const tag of allTags) {
      if (tag.group_name) {
        groupMap.set(tag.group_name, (groupMap.get(tag.group_name) || 0) + 1);
      } else {
        uncat++;
      }
    }
    return {
      groups: Array.from(groupMap.entries()).map(([name, count]) => ({ name, count })),
      totalCount: allTags.length,
      uncategorizedCount: uncat,
    };
  }, [allTags]);

  return createPortal(
    <div
      ref={panelRef}
      className="fixed z-[60] bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl flex flex-col overflow-hidden"
      style={{ top: position.top, left: position.left, width: size.width, height: size.height }}
    >
      {/* Top bar: search + settings */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-zinc-800">
        <div className="flex-1 relative">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-zinc-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('resources.searchTags', 'Search tags...')}
            className="w-full bg-zinc-800 border border-zinc-700/50 rounded pl-7 pr-2 py-1.5 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500/50"
            autoFocus
          />
        </div>
        <div className="relative">
          <button
            onClick={() => setShowSettings(!showSettings)}
            className="p-1.5 rounded text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
          >
            <Settings size={14} />
          </button>
          {showSettings && (
            <SettingsPopover settings={settings} onUpdate={onUpdateSettings} onClose={() => setShowSettings(false)} />
          )}
        </div>
        <button
          onClick={onClose}
          className="p-1.5 rounded text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
        >
          <X size={14} />
        </button>
      </div>

      {/* Main content: sidebar + tags */}
      <div className="flex flex-1 min-h-0">
        <CategorySidebar
          totalCount={totalCount}
          uncategorizedCount={uncategorizedCount}
          groups={groups}
          selectedGroup={selectedGroup}
          onSelectGroup={setSelectedGroup}
        />
        <TagContent
          allTags={allTags}
          selectedIds={selectedIds}
          starredIds={starredIds}
          settings={settings}
          selectedGroup={selectedGroup}
          search={search}
          onToggleTag={onToggleTag}
          onToggleStar={onToggleStar}
        />
      </div>

      {/* Resize handle */}
      <div
        onMouseDown={handleResizeStart}
        className="absolute bottom-0 right-0 w-4 h-4 cursor-se-resize"
        style={{ background: 'linear-gradient(135deg, transparent 50%, rgba(113,113,122,0.4) 50%)' }}
      />
    </div>,
    document.body,
  );
};
```

- [ ] **Step 2: Commit**

```bash
git add frontend/components/EagleTagPicker/FloatingPanel.tsx
git commit -m "feat(ui): add FloatingPanel with portal, positioning, and resize"
```

---

### Task 13: Main EagleTagPicker Component

**Files:**
- Create: `frontend/components/EagleTagPicker/index.tsx`

- [ ] **Step 1: Create main component**

```typescript
import React, { useState, useRef, useMemo, useCallback } from 'react';
import { Plus, Palette } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { TagPill } from './TagPill';
import { FloatingPanel } from './FloatingPanel';
import { useTagPreferences } from './useTagPreferences';
import type { EagleTagPickerProps } from './types';
import type { Tag } from '../../types';

const TAG_COLORS = [
  '#ef4444', '#f97316', '#eab308', '#22c55e',
  '#14b8a6', '#3b82f6', '#8b5cf6', '#ec4899',
];

export const EagleTagPicker: React.FC<EagleTagPickerProps> = ({
  assignedTags,
  onAdd,
  onRemove,
  selectedTagIds,
  onTagsChange,
  allTags,
  readOnly = false,
  onCreate,
}) => {
  const { t } = useTranslation();
  const [panelOpen, setPanelOpen] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newColor, setNewColor] = useState(TAG_COLORS[5]);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const { prefs, toggleStar, updateSettings, updatePanelSize } = useTagPreferences();

  // Determine mode and selected IDs
  const isMode1 = assignedTags !== undefined;
  const selectedIds = useMemo(() => {
    if (isMode1) return new Set(assignedTags!.map((t) => String(t.id)));
    return new Set(selectedTagIds || []);
  }, [isMode1, assignedTags, selectedTagIds]);

  // Tags to display as pills (assigned in Mode 1, selected from allTags in Mode 2)
  const displayTags = useMemo(() => {
    if (isMode1) return assignedTags!;
    return allTags.filter((t) => selectedIds.has(String(t.id)));
  }, [isMode1, assignedTags, allTags, selectedIds]);

  const handleToggleTag = useCallback(
    (tagId: string) => {
      if (isMode1) {
        if (selectedIds.has(tagId)) {
          onRemove?.(tagId);
        } else {
          onAdd?.(tagId);
        }
      } else {
        const current = selectedTagIds || [];
        const next = current.includes(tagId)
          ? current.filter((id) => id !== tagId)
          : [...current, tagId];
        onTagsChange?.(next);
      }
    },
    [isMode1, selectedIds, onAdd, onRemove, selectedTagIds, onTagsChange],
  );

  const handleRemove = useCallback(
    (tagId: string) => {
      if (isMode1) {
        onRemove?.(tagId);
      } else {
        onTagsChange?.((selectedTagIds || []).filter((id) => id !== tagId));
      }
    },
    [isMode1, onRemove, onTagsChange, selectedTagIds],
  );

  const handleCreate = useCallback(async () => {
    const name = newName.trim();
    if (!name || !onCreate) return;
    const created = await onCreate(name, newColor);
    if (created) {
      handleToggleTag(String(created.id));
      setNewName('');
      setShowCreate(false);
    }
  }, [newName, newColor, onCreate, handleToggleTag]);

  return (
    <div className="space-y-2">
      {/* Assigned / selected tag pills */}
      <div className="flex flex-wrap gap-1.5">
        {displayTags.map((tag) => (
          <TagPill key={tag.id} tag={tag} onRemove={handleRemove} readOnly={readOnly} />
        ))}
        {!readOnly && (
          <>
            <button
              ref={triggerRef}
              onClick={() => setPanelOpen(!panelOpen)}
              className="inline-flex items-center gap-1 px-2.5 py-1 text-xs rounded-full bg-zinc-800 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700 transition-colors"
            >
              <Plus size={10} />
              {t('resources.addTag', 'Add Tag')}
            </button>

            {/* Inline create */}
            {onCreate && !showCreate && (
              <button
                onClick={() => setShowCreate(true)}
                className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-full text-zinc-500 hover:text-zinc-300 transition-colors"
              >
                <Palette size={10} />
                {t('resources.createTag', 'Create')}
              </button>
            )}
          </>
        )}
      </div>

      {/* Inline create form */}
      {showCreate && onCreate && (
        <div className="flex items-center gap-2 p-2 bg-zinc-800/50 rounded-lg">
          <input
            type="text"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Tag name..."
            className="flex-1 bg-zinc-800 border border-zinc-700/50 rounded px-2 py-1 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500/50"
            autoFocus
            onKeyDown={(e) => { if (e.key === 'Enter') handleCreate(); if (e.key === 'Escape') setShowCreate(false); }}
          />
          <div className="flex gap-1">
            {TAG_COLORS.map((c) => (
              <button
                key={c}
                onClick={() => setNewColor(c)}
                className={`w-4 h-4 rounded-full border-2 transition-all ${
                  newColor === c ? 'border-white scale-110' : 'border-transparent'
                }`}
                style={{ backgroundColor: c }}
              />
            ))}
          </div>
          <button
            onClick={handleCreate}
            disabled={!newName.trim()}
            className="px-2 py-1 text-xs bg-indigo-600 hover:bg-indigo-500 text-white rounded disabled:opacity-50 transition-colors"
          >
            Create
          </button>
        </div>
      )}

      {/* Floating panel */}
      {panelOpen && (
        <FloatingPanel
          triggerRef={triggerRef}
          allTags={allTags}
          selectedIds={selectedIds}
          starredIds={prefs.starred_tag_ids}
          settings={prefs.picker_settings}
          panelSize={prefs.panel_size}
          onToggleTag={handleToggleTag}
          onToggleStar={toggleStar}
          onUpdateSettings={updateSettings}
          onPanelResize={updatePanelSize}
          onClose={() => setPanelOpen(false)}
        />
      )}
    </div>
  );
};
```

- [ ] **Step 2: Commit**

```bash
git add frontend/components/EagleTagPicker/index.tsx
git commit -m "feat(ui): add EagleTagPicker main component with dual mode support"
```

---

## Chunk 5: Integration + Cleanup

### Task 14: Replace UnifiedTagPicker in All Consumers

**Files:**
- Modify: `frontend/components/ResourceInfoPanel.tsx:6` — change import
- Modify: `frontend/components/ResourceDetail.tsx:55` — change import
- Modify: `frontend/components/MediaCard.tsx:25` — change import
- Modify: `frontend/components/DownloadsView.tsx:49` — change import

- [ ] **Step 1: Update imports and usage in all 4 files**

In each file, change:
```typescript
// Before
import { UnifiedTagPicker } from './UnifiedTagPicker';

// After
import { EagleTagPicker } from './EagleTagPicker';
```

And update JSX usage — props should be compatible:
```tsx
// Before
<UnifiedTagPicker assignedTags={...} allTags={...} onAdd={...} onRemove={...} onCreate={...} />

// After
<EagleTagPicker assignedTags={...} allTags={...} onAdd={...} onRemove={...} onCreate={...} />
```

- [ ] **Step 2: Commit**

```bash
git add frontend/components/ResourceInfoPanel.tsx frontend/components/ResourceDetail.tsx frontend/components/MediaCard.tsx frontend/components/DownloadsView.tsx
git commit -m "refactor: replace UnifiedTagPicker with EagleTagPicker in all consumers"
```

---

### Task 15: Replace ParserTagSelector

**Files:**
- Modify: `frontend/pages/ParserPage.tsx:12,148` — change import and usage

- [ ] **Step 1: Update ParserPage**

```typescript
// Before
import { ParserTagSelector } from '../components/ParserTagSelector';
// <ParserTagSelector selectedTagIds={selectedTagIds} onTagsChange={setSelectedTagIds} />

// After
import { EagleTagPicker } from '../components/EagleTagPicker';
// Need to also pass allTags — fetch them in ParserPage or pass from context
```

Note: ParserTagSelector currently fetches its own tags internally. EagleTagPicker expects `allTags` as a prop. The ParserPage will need to fetch tags and pass them in, or we add a `fetchOwnTags` mode. Simplest: fetch tags in ParserPage and pass as prop.

```typescript
// Add to ParserPage:
const [allTags, setAllTags] = useState<Tag[]>([]);
useEffect(() => {
  fetchTags().then(setAllTags).catch(console.error);
}, []);

// JSX:
<EagleTagPicker
  selectedTagIds={selectedTagIds}
  onTagsChange={setSelectedTagIds}
  allTags={allTags}
/>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/pages/ParserPage.tsx
git commit -m "refactor: replace ParserTagSelector with EagleTagPicker in ParserPage"
```

---

### Task 16: Delete Deprecated Components

**Files:**
- Delete: `frontend/components/UnifiedTagPicker.tsx`
- Delete: `frontend/components/ParserTagSelector.tsx`

- [ ] **Step 1: Verify no remaining imports**

Search for any remaining imports of `UnifiedTagPicker` or `ParserTagSelector` in the codebase. If none found, proceed to delete.

```bash
grep -r "UnifiedTagPicker\|ParserTagSelector" frontend/ --include="*.tsx" --include="*.ts"
```

- [ ] **Step 2: Delete files**

```bash
git rm frontend/components/UnifiedTagPicker.tsx frontend/components/ParserTagSelector.tsx
```

- [ ] **Step 3: Commit**

```bash
git commit -m "refactor: remove deprecated UnifiedTagPicker and ParserTagSelector"
```

---

### Task 17: Build Verification + Push

- [ ] **Step 1: Build frontend**

```bash
cd frontend && npm run build
```

Expected: Build succeeds with no TypeScript errors.

- [ ] **Step 2: Visual smoke test**

Open the app, navigate to a resource detail view, verify:
- Tag pills display with ✕ buttons (always visible)
- Click ✕ removes the tag
- Click "+ Add Tag" opens floating panel to the left
- Panel has category sidebar + tag content area
- Search works
- Settings gear opens popover
- Star/unstar via right-click works
- Panel closes on click outside / ESC
- Panel is resizable

- [ ] **Step 3: Push to deploy**

```bash
git push origin master
```

- [ ] **Step 4: Commit any fixes from smoke test**

If any issues found during smoke test, fix and commit before pushing.
