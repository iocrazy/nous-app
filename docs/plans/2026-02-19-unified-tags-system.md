# Unified Tags System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Unify the tags system so both Downloads (parsed_media) and Resources share a single global tag pool with a single UI component, while keeping their separate junction tables.

**Architecture:** The `tags` table is already global and shared. We keep `video_tags` and `resource_tags` as separate junction tables (different metadata needs: video_tags has confidence/source, resource_tags has tagged_by). We create a single `UnifiedTagPicker` component that replaces 4 existing tag UIs, and a unified service layer that abstracts the junction table differences.

**Tech Stack:** React 19 + TypeScript, Supabase (PostgreSQL), FastAPI backend

---

## Current State

| Component | Location | Lines | Purpose |
|-----------|----------|-------|---------|
| `TagSelector` | `frontend/components/TagSelector.tsx` | 472 | Video tag editing (lazy-load, dropdown) |
| `ParserTagSelector` | `frontend/components/ParserTagSelector.tsx` | 293 | Pre-parse tag selection (ID-based) |
| `TagsSection` | Inside `ResourceInfoPanel.tsx` | ~100 | Resource tags in info panel (Eagle style) |
| Inline tags | Inside `ResourceDetail.tsx:918-986` | ~70 | Resource tags in detail page |
| `tagsService.ts` | `frontend/services/tagsService.ts` | 271 | Video tag CRUD via backend API |
| Resource tag fns | `frontend/services/resourceService.ts:510-539` | 30 | Resource tag CRUD via backend API |

**Problem:** 4 different tag UIs + 2 service files = duplicated logic, inconsistent UX, maintenance burden.

**Target:** 1 `UnifiedTagPicker` + 1 `unifiedTagService` → consistent UX everywhere.

---

### Task 1: Create Unified Tag Service

Consolidate tag operations into one service that abstracts over both junction tables.

**Files:**
- Create: `frontend/services/unifiedTagService.ts`
- Modify: `frontend/services/tagsService.ts` (re-export for backward compat)

**Step 1: Create the unified service file**

Create `frontend/services/unifiedTagService.ts`:

```typescript
import { Tag } from '../types';
import { getSupabaseAccessToken } from '../supabaseClient';

const getApiUrl = () =>
  import.meta.env.VITE_API_URL || 'http://localhost:8080';

async function getAuthHeaders(): Promise<Record<string, string>> {
  const token = await getSupabaseAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// ─── Global tag CRUD (shared by all entity types) ──────────

export async function fetchAllTags(): Promise<Tag[]> {
  const apiUrl = getApiUrl();
  const res = await fetch(`${apiUrl}/api/v1/tags`, {
    headers: await getAuthHeaders(),
  });
  if (!res.ok) throw new Error('Failed to fetch tags');
  const json = await res.json();
  return json.tags ?? json.data ?? [];
}

export async function createTag(data: {
  name: string;
  color?: string;
  type?: 'system' | 'user';
}): Promise<Tag> {
  const apiUrl = getApiUrl();
  const res = await fetch(`${apiUrl}/api/v1/tags`, {
    method: 'POST',
    headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...data, type: data.type || 'user' }),
  });
  if (!res.ok) throw new Error('Failed to create tag');
  const json = await res.json();
  return json.tag ?? json.data;
}

export async function deleteTag(tagId: string): Promise<void> {
  const apiUrl = getApiUrl();
  const res = await fetch(`${apiUrl}/api/v1/tags/${tagId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!res.ok) throw new Error('Failed to delete tag');
}

// ─── Entity-specific tag associations ──────────────────────

export type TaggableType = 'resource' | 'media';

export interface TagAssociation {
  tag: Tag;
  confidence?: number;
  source?: string;
  tagged_by?: string;
  created_at?: string;
}

/**
 * Fetch tags for any entity type.
 */
export async function fetchEntityTags(
  entityType: TaggableType,
  entityId: string,
): Promise<TagAssociation[]> {
  const apiUrl = getApiUrl();
  const url =
    entityType === 'resource'
      ? `${apiUrl}/api/v1/resources/${entityId}/tags`
      : `${apiUrl}/api/v1/tags/videos/${entityId}/tags`;

  const res = await fetch(url, { headers: await getAuthHeaders() });
  if (!res.ok) throw new Error(`Failed to fetch ${entityType} tags`);
  const json = await res.json();

  // Normalize response: resource returns { data: [{tag}] }, video returns { tags: [{tag, confidence, source}] }
  if (entityType === 'resource') {
    return (json.data ?? []).map((item: any) => ({ tag: item.tag }));
  }
  return (json.tags ?? []).map((item: any) => ({
    tag: item.tag,
    confidence: item.confidence,
    source: item.source,
  }));
}

/**
 * Add a tag to any entity.
 */
export async function addEntityTag(
  entityType: TaggableType,
  entityId: string,
  tagId: string,
): Promise<void> {
  const apiUrl = getApiUrl();
  if (entityType === 'resource') {
    const res = await fetch(`${apiUrl}/api/v1/resources/${entityId}/tags`, {
      method: 'POST',
      headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
      body: JSON.stringify({ tag_id: tagId }),
    });
    if (!res.ok) throw new Error('Failed to add resource tag');
  } else {
    const res = await fetch(`${apiUrl}/api/v1/tags/videos/${entityId}/tags`, {
      method: 'POST',
      headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
      body: JSON.stringify({ tag_ids: [tagId] }),
    });
    if (!res.ok) throw new Error('Failed to add media tag');
  }
}

/**
 * Remove a tag from any entity.
 */
export async function removeEntityTag(
  entityType: TaggableType,
  entityId: string,
  tagId: string,
): Promise<void> {
  const apiUrl = getApiUrl();
  const url =
    entityType === 'resource'
      ? `${apiUrl}/api/v1/resources/${entityId}/tags/${tagId}`
      : `${apiUrl}/api/v1/tags/videos/${entityId}/tags/${tagId}`;

  const res = await fetch(url, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to remove ${entityType} tag`);
}
```

**Step 2: Verify build**

Run: `cd frontend && npm run build`
Expected: Build succeeds (new file, no imports yet)

**Step 3: Commit**

```bash
git add frontend/services/unifiedTagService.ts
git commit -m "feat: add unified tag service abstracting resource and media tag APIs"
```

---

### Task 2: Create UnifiedTagPicker Component

A single, reusable tag picker that replaces all 4 existing tag UIs.

**Files:**
- Create: `frontend/components/UnifiedTagPicker.tsx`

**Step 1: Create the component**

Create `frontend/components/UnifiedTagPicker.tsx`:

```typescript
import React, { useState, useRef, useEffect, useCallback } from 'react';
import { X, Plus, Palette } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Tag } from '../types';

const TAG_COLORS = [
  '#ef4444', '#f97316', '#eab308', '#22c55e',
  '#14b8a6', '#3b82f6', '#8b5cf6', '#ec4899',
];

interface UnifiedTagPickerProps {
  /** Currently assigned tags */
  assignedTags: Tag[];
  /** All available tags (fetched by parent) */
  allTags: Tag[];
  /** Read-only mode (no add/remove) */
  readOnly?: boolean;
  /** Called when user adds a tag */
  onAdd: (tagId: string) => void;
  /** Called when user removes a tag */
  onRemove: (tagId: string) => void;
  /** Called when user creates a new tag; should return the created tag */
  onCreate?: (name: string, color: string) => Promise<Tag | null>;
}

export const UnifiedTagPicker: React.FC<UnifiedTagPickerProps> = ({
  assignedTags,
  allTags,
  readOnly = false,
  onAdd,
  onRemove,
  onCreate,
}) => {
  const { t } = useTranslation();
  const [showDropdown, setShowDropdown] = useState(false);
  const [search, setSearch] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [newColor, setNewColor] = useState(TAG_COLORS[5]); // default blue
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
        setSearch('');
        setShowCreate(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const assignedIds = new Set(assignedTags.map((t) => t.id));
  const available = allTags.filter((t) => !assignedIds.has(t.id));
  const filtered = search
    ? available.filter((t) => t.name.toLowerCase().includes(search.toLowerCase()))
    : available;

  const handleCreate = useCallback(async () => {
    const name = search.trim();
    if (!name || !onCreate) return;
    const created = await onCreate(name, newColor);
    if (created) {
      onAdd(created.id);
      setSearch('');
      setShowCreate(false);
      setShowDropdown(false);
    }
  }, [search, newColor, onCreate, onAdd]);

  const noExactMatch = search.trim() && !allTags.some(
    (t) => t.name.toLowerCase() === search.trim().toLowerCase()
  );

  return (
    <div className="px-4 mt-4 border-t border-zinc-800/60 pt-3">
      <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
        {t('resources.tags')}
      </h4>
      <div className="flex flex-wrap gap-1.5">
        {/* Assigned tags */}
        {assignedTags.map((tag) => (
          <span
            key={tag.id}
            className="group inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full transition-opacity"
            style={{
              backgroundColor: (tag.color || '#6366f1') + '20',
              color: tag.color || '#6366f1',
            }}
          >
            {tag.name}
            {!readOnly && (
              <button
                onClick={() => onRemove(tag.id)}
                className="opacity-0 group-hover:opacity-100 transition-opacity hover:text-white"
                title="Remove"
              >
                <X size={10} />
              </button>
            )}
          </span>
        ))}

        {/* Add tag button + dropdown */}
        {!readOnly && (
          <div className="relative" ref={dropdownRef}>
            <button
              onClick={() => { setShowDropdown(!showDropdown); setSearch(''); setShowCreate(false); }}
              className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-zinc-800 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700 transition-colors"
            >
              <Plus size={10} />
              {t('resources.addTag')}
            </button>

            {showDropdown && (
              <div className="absolute left-0 top-full mt-1 z-30 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl w-52 py-1">
                {/* Search */}
                <div className="px-2 pb-1">
                  <input
                    type="text"
                    value={search}
                    onChange={(e) => { setSearch(e.target.value); setShowCreate(false); }}
                    placeholder={t('resources.searchTags', 'Search tags...')}
                    className="w-full bg-zinc-800 border border-zinc-700/50 rounded px-2 py-1 text-xs text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500/50"
                    autoFocus
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && noExactMatch && onCreate) {
                        e.preventDefault();
                        handleCreate();
                      }
                    }}
                  />
                </div>

                {/* Available tags list */}
                <div className="max-h-36 overflow-y-auto">
                  {filtered.map((tag) => (
                    <button
                      key={tag.id}
                      onClick={() => { onAdd(tag.id); setShowDropdown(false); setSearch(''); }}
                      className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition-colors"
                    >
                      <span
                        className="w-2.5 h-2.5 rounded-full shrink-0"
                        style={{ backgroundColor: tag.color || '#6366f1' }}
                      />
                      <span className="truncate">{tag.name}</span>
                      {tag.type === 'system' && (
                        <span className="ml-auto text-[10px] text-zinc-600">system</span>
                      )}
                    </button>
                  ))}
                  {filtered.length === 0 && !noExactMatch && (
                    <p className="text-xs text-zinc-600 text-center py-2">
                      {t('resources.noTagsAvailable')}
                    </p>
                  )}
                </div>

                {/* Create new tag */}
                {noExactMatch && onCreate && (
                  <div className="border-t border-zinc-800 mt-1 pt-1 px-2">
                    {!showCreate ? (
                      <button
                        onClick={() => setShowCreate(true)}
                        className="w-full flex items-center gap-2 px-1 py-1.5 text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
                      >
                        <Plus size={10} />
                        Create &quot;{search.trim()}&quot;
                      </button>
                    ) : (
                      <div className="py-1 space-y-1.5">
                        <div className="flex items-center gap-1.5">
                          <Palette size={10} className="text-zinc-500 shrink-0" />
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
                        </div>
                        <button
                          onClick={handleCreate}
                          className="w-full py-1 text-xs bg-indigo-600 hover:bg-indigo-500 text-white rounded transition-colors"
                        >
                          Create &quot;{search.trim()}&quot;
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
```

**Step 2: Verify build**

Run: `cd frontend && npm run build`
Expected: Build succeeds

**Step 3: Commit**

```bash
git add frontend/components/UnifiedTagPicker.tsx
git commit -m "feat: add UnifiedTagPicker component for shared tag management UI"
```

---

### Task 3: Integrate UnifiedTagPicker into ResourceInfoPanel

Replace the inline `TagsSection` in ResourceInfoPanel with UnifiedTagPicker.

**Files:**
- Modify: `frontend/components/ResourceInfoPanel.tsx`
- Modify: `frontend/components/ResourcesView.tsx` (adjust props)

**Step 1: Replace TagsSection in ResourceInfoPanel**

In `ResourceInfoPanel.tsx`:

1. Remove the entire `TagsSection` sub-component (lines ~97-198)
2. Import `UnifiedTagPicker`
3. Replace `TagsSection` usage with `UnifiedTagPicker`

Change props interface — remove `allTags`, `assignedTags`, `onAddTag`, `onRemoveTag` and replace:

```typescript
interface ResourceInfoPanelProps {
  resource: Resource;
  tags: Tag[];                    // assigned tags (simplified)
  allTags: Tag[];                 // all available tags
  folderName?: string | null;
  readOnly?: boolean;
  onClose: () => void;
  onAddTag: (tagId: string) => void;
  onRemoveTag: (tagId: string) => void;
  onCreate?: (name: string, color: string) => Promise<Tag | null>;
  onUpdate: (data: Partial<Resource>) => void;
}
```

Replace `<TagsSection ... />` with:

```tsx
<UnifiedTagPicker
  assignedTags={tags}
  allTags={allTags}
  readOnly={readOnly}
  onAdd={onAddTag}
  onRemove={onRemoveTag}
  onCreate={onCreate}
/>
```

**Step 2: Update ResourcesView to pass simplified props**

In `ResourcesView.tsx`, where `ResourceInfoPanel` is rendered (~line 2560):

- Map `selectedResourceTags` to extract just the `Tag` objects:

```tsx
const infoTags = selectedResourceTags
  .map((item) => item.tag)
  .filter((t): t is Tag => !!t);
```

- Add `handleCreateTag` callback:

```typescript
const handleCreateTag = useCallback(async (name: string, color: string): Promise<Tag | null> => {
  try {
    const tag = await createTag({ name, color, type: 'user' });
    setAllTags(prev => [...prev, tag]);
    return tag;
  } catch {
    return null;
  }
}, []);
```

- Pass to InfoPanel:

```tsx
<ResourceInfoPanel
  resource={selectedResource.resource}
  tags={infoTags}
  allTags={allTags}
  folderName={...}
  readOnly={isRecycleView}
  onClose={...}
  onAddTag={handleAddTag}
  onRemoveTag={handleRemoveTag}
  onCreate={handleCreateTag}
  onUpdate={handleResourceUpdate}
/>
```

**Step 3: Verify build**

Run: `cd frontend && npm run build`
Expected: Build succeeds

**Step 4: Commit**

```bash
git add frontend/components/ResourceInfoPanel.tsx frontend/components/ResourcesView.tsx
git commit -m "refactor: replace inline TagsSection with UnifiedTagPicker in ResourceInfoPanel"
```

---

### Task 4: Integrate UnifiedTagPicker into ResourceDetail

Replace the inline tag section (lines 918-986) in ResourceDetail.

**Files:**
- Modify: `frontend/components/ResourceDetail.tsx`

**Step 1: Replace inline tag section**

In `ResourceDetail.tsx`:

1. Import `UnifiedTagPicker` and `createTag` from `unifiedTagService`
2. Find the inline tag section (~lines 918-986, "Section 3 — Tags (Eagle style)")
3. Replace with:

```tsx
<UnifiedTagPicker
  assignedTags={assignedTags.map(item => item.tag).filter((t): t is Tag => !!t)}
  allTags={allTags}
  onAdd={handleAddTag}
  onRemove={handleRemoveTag}
  onCreate={async (name, color) => {
    try {
      const tag = await createTag({ name, color, type: 'user' });
      setAllTags(prev => [...prev, tag]);
      return tag;
    } catch { return null; }
  }}
/>
```

4. Remove the inline dropdown state (`showTagDropdown`, `tagSearch`, `tagDropdownRef`) that was only used by the old inline section.

**Step 2: Verify build**

Run: `cd frontend && npm run build`
Expected: Build succeeds

**Step 3: Commit**

```bash
git add frontend/components/ResourceDetail.tsx
git commit -m "refactor: replace inline tag editor with UnifiedTagPicker in ResourceDetail"
```

---

### Task 5: Integrate UnifiedTagPicker into VideoDetailPanel / MediaCard

Replace TagSelector usage in the Downloads (parsed_media) context.

**Files:**
- Modify: `frontend/components/MediaCard.tsx` (uses TagSelector)
- Modify: `frontend/components/VideoDetailPanel.tsx` (if it has inline tags)

**Step 1: Examine current MediaCard tag integration**

Read `MediaCard.tsx` to understand how `TagSelector` is used, and replace with `UnifiedTagPicker`.

The key difference: MediaCard takes a `videoId` (string) and loads tags lazily.
We need to lift tag state up or keep the lazy pattern but use UnifiedTagPicker for rendering.

Approach: Add `UnifiedTagPicker` as the tag display/edit UI inside MediaCard, using existing `getVideoTags` / `addTagsToVideo` / `removeTagFromVideo` via the unified service.

**Step 2: Verify build**

Run: `cd frontend && npm run build`
Expected: Build succeeds

**Step 3: Commit**

```bash
git add frontend/components/MediaCard.tsx frontend/components/VideoDetailPanel.tsx
git commit -m "refactor: replace TagSelector with UnifiedTagPicker in MediaCard/VideoDetailPanel"
```

---

### Task 6: Add i18n keys for unified tag picker

**Files:**
- Modify: `frontend/public/locales/en.json`
- Modify: `frontend/public/locales/zh.json`

**Step 1: Add missing i18n keys**

In both locale files, under `resources`:

```json
"searchTags": "Search tags...",
"createTag": "Create tag",
"systemTag": "System"
```

zh.json:
```json
"searchTags": "搜索标签...",
"createTag": "创建标签",
"systemTag": "系统"
```

**Step 2: Verify build**

Run: `cd frontend && npm run build`
Expected: Build succeeds

**Step 3: Commit**

```bash
git add frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat: add i18n keys for unified tag picker"
```

---

### Task 7: Clean up old tag components

Remove deprecated tag components that are no longer used.

**Files:**
- Check imports of: `TagSelector`, `ParserTagSelector`
- Remove if fully replaced; keep `ParserTagSelector` if still needed for parser flow

**Step 1: Check if TagSelector is still imported anywhere**

Run: `grep -r "TagSelector" frontend/components/ frontend/pages/ --include="*.tsx" --include="*.ts"`

If TagSelector is only used in places we've already replaced, delete the file.
If ParserTagSelector is still used by the parser flow (LandingPage), keep it — it has a different use case (pre-select tags before parsing, no entity ID).

**Step 2: Remove unused files**

```bash
# Only if confirmed unused:
git rm frontend/components/TagSelector.tsx
```

**Step 3: Verify build**

Run: `cd frontend && npm run build`
Expected: Build succeeds, no broken imports

**Step 4: Commit**

```bash
git commit -m "chore: remove deprecated TagSelector component (replaced by UnifiedTagPicker)"
```

---

## Verification Checklist

After all tasks:

1. `cd frontend && npm run build` — no errors
2. Open Resources page → select a file → InfoPanel shows UnifiedTagPicker
3. Add a tag → tag appears → refresh → tag persists
4. Remove a tag → tag disappears → refresh → tag removed
5. Create a new tag (type search, click Create) → tag created and assigned
6. Open ResourceDetail (double-click file) → same UnifiedTagPicker in Inspector
7. Open Downloads → select a video → tags use same picker UI
8. System tags show "system" label in dropdown
9. Recycle bin → tags are read-only (no add/remove buttons)
10. Tags created in Resources are visible when tagging in Downloads (global pool)
