# Copy / Duplicate / Delete Redesign — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the bug where deleting a file from one folder trashes all copies, and improve restore + duplicate detection.

**Architecture:** Replace direct `is_trashed=true` PATCH calls with `resource_item` deletion. The existing DB trigger `check_orphan_resource()` auto-trashes the resource only when the last reference is removed. Restore recreates a `resource_item` in the original folder. Duplicate detection on upload auto-links instead of re-uploading.

**Tech Stack:** PostgreSQL (Supabase), FastAPI, React 19, TypeScript

---

## Task 1: DB Migration — Add `last_folder_id` / `last_library_id` to resources

These columns store where the resource was last referenced, so restore knows where to put it back.

**Files:**
- Create: `supabase/migrations/080_resource_last_location.sql`

**Step 1: Write the migration**

```sql
-- 080_resource_last_location.sql
-- Track the last folder/library a resource was in before it became orphaned.
-- Used by restore to recreate the resource_item in the right place.

ALTER TABLE resources
  ADD COLUMN IF NOT EXISTS last_folder_id BIGINT,
  ADD COLUMN IF NOT EXISTS last_library_id BIGINT,
  ADD COLUMN IF NOT EXISTS last_scope_type VARCHAR(20),
  ADD COLUMN IF NOT EXISTS last_scope_id TEXT;
```

**Step 2: Run migration against local Supabase**

Run: `psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -f supabase/migrations/080_resource_last_location.sql`
Expected: `ALTER TABLE` success

**Step 3: Verify columns exist**

Run: `psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -c "\d resources" | grep last_`
Expected: Shows `last_folder_id`, `last_library_id`, `last_scope_type`, `last_scope_id`

**Step 4: Commit**

```bash
git add supabase/migrations/080_resource_last_location.sql
git commit -m "db: add last_folder_id/last_library_id to resources for restore"
```

---

## Task 2: Backend — Rework DELETE endpoint to remove resource_item

Change the `DELETE /resources/{id}` endpoint to accept `folder_id` and save the last location before deleting the `resource_item`.

**Files:**
- Modify: `backend/app/repositories/resources_repository.py` (add `get_resource_item_by_folder`)
- Modify: `backend/app/services/resources_service.py:387-403` (`remove_from_library`)
- Modify: `backend/app/api/resources_router.py:684-709` (add `folder_id` query param)

**Step 1: Add `get_resource_item_by_folder` to repository**

In `backend/app/repositories/resources_repository.py`, add after `get_resource_item` (line ~300):

```python
async def get_resource_item_in_folder(
    self, resource_id: str, scope_type: str, scope_id: str, folder_id: str | None
) -> Optional[Dict[str, Any]]:
    """Get a specific resource_item by resource_id + scope + folder_id."""
    try:
        client = await self._get_client()
        query = (
            client.table(self.TABLE_ITEMS)
            .select("*")
            .eq("resource_id", resource_id)
            .eq("scope_type", scope_type)
            .eq("scope_id", scope_id)
        )
        if folder_id:
            query = query.eq("folder_id", folder_id)
        else:
            query = query.is_("folder_id", "null")
        result = await query.limit(1).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Failed to get resource_item in folder: {e}")
        return None
```

**Step 2: Update `remove_from_library` in service to save last location**

In `backend/app/services/resources_service.py`, replace `remove_from_library` (lines 387-403):

```python
async def remove_from_library(
    self,
    resource_id: str,
    user_id: str,
    scope_type: str,
    scope_id: str,
    folder_id: str | None = None,
) -> bool:
    """
    Remove a resource from a specific folder by deleting the resource_item.
    Saves last location on the resource for restore.
    The DB trigger auto-trashes the resource if this was the last reference.
    """
    if folder_id is not None:
        item = await self.repo.get_resource_item_in_folder(
            resource_id, scope_type, scope_id, folder_id
        )
    else:
        item = await self.repo.get_resource_item(resource_id, scope_type, scope_id)
    if not item:
        raise ValueError("Resource not found in this scope/folder")

    # Save last location for restore
    await self.repo.update_resource(resource_id, {
        "last_folder_id": item.get("folder_id"),
        "last_library_id": item.get("library_id"),
        "last_scope_type": scope_type,
        "last_scope_id": scope_id,
    })

    return await self.repo.delete_resource_item(item["id"])
```

**Step 3: Update DELETE endpoint to accept `folder_id`**

In `backend/app/api/resources_router.py`, update `delete_resource` (lines 684-709):

```python
@router.delete("/{resource_id}")
async def delete_resource(
    resource_id: str,
    auth: AuthDep,
    scope_type: str = Query(..., pattern="^(personal|team)$"),
    scope_id: str = Query(...),
    folder_id: Optional[str] = Query(None),
):
    """Remove a resource from a specific folder.

    Deletes the resource_item reference. If this was the last reference,
    the DB trigger auto-trashes the parent resource (orphan GC).
    """
    try:
        svc = ResourcesService()
        await svc.remove_from_library(
            resource_id=resource_id,
            user_id=auth.user_id,
            scope_type=scope_type,
            scope_id=scope_id,
            folder_id=folder_id,
        )
        return {"success": True, "message": "Resource removed from library"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to remove resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove resource")
```

**Step 4: Also update `trash_resource_by_platform_id` to use item deletion**

In `backend/app/api/resources_router.py`, update `trash_resource_by_platform_id` (lines 603-635):

```python
@router.post("/by-platform-id/{platform_id}/trash")
async def trash_resource_by_platform_id(
    platform_id: str,
    auth: AuthDep,
    scope_type: str = Query("personal", pattern="^(personal|team)$"),
    scope_id: Optional[str] = Query(None),
):
    """Soft-delete a downloaded video by removing its resource_item reference."""
    try:
        svc = ResourcesService()
        resource = await svc.repo.get_resource_by_platform_id(platform_id)
        if not resource:
            raise ValueError("No resource found for this platform_id")

        resource_id = str(resource["id"])
        target_scope_id = scope_id or auth.user_id
        await svc.remove_from_library(
            resource_id=resource_id,
            user_id=auth.user_id,
            scope_type=scope_type,
            scope_id=target_scope_id,
        )
        return {"success": True, "message": "Resource moved to trash"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to trash resource by platform_id {platform_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to trash resource")
```

**Step 5: Verify backend starts**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run python -c "from app.api.resources_router import router; print('OK')"`
Expected: `OK`

**Step 6: Commit**

```bash
git add backend/app/repositories/resources_repository.py backend/app/services/resources_service.py backend/app/api/resources_router.py
git commit -m "fix: delete removes resource_item instead of trashing entire resource"
```

---

## Task 3: Backend — Rework restore to recreate resource_item

Change `restore_resource` to recreate a `resource_item` in the last known folder.

**Files:**
- Modify: `backend/app/services/resources_service.py:420-430` (`restore_resource`)
- Modify: `backend/app/api/resources_router.py:712-725` (`restore_resource` endpoint)

**Step 1: Update `restore_resource` in service**

In `backend/app/services/resources_service.py`, replace `restore_resource` (lines 420-430):

```python
async def restore_resource(self, resource_id: str, user_id: str) -> dict:
    resource = await self.repo.get_resource_by_id(resource_id)
    if not resource:
        raise ValueError("Resource not found")
    if resource["creator_id"] != user_id:
        raise PermissionError("Only the creator can restore this resource")
    if not resource.get("is_trashed"):
        raise ValueError("Resource is not in trash")

    # Determine restore location
    folder_id = resource.get("last_folder_id")
    library_id = resource.get("last_library_id")
    scope_type = resource.get("last_scope_type") or "personal"
    scope_id = resource.get("last_scope_id") or user_id

    # If last_folder_id references a trashed/deleted folder, clear it
    if folder_id:
        from app.db.supabase_client import get_async_supabase_admin
        client = await get_async_supabase_admin()
        folder_check = await (
            client.table("folders")
            .select("id, is_trashed")
            .eq("id", folder_id)
            .limit(1)
            .execute()
        )
        if not folder_check.data or folder_check.data[0].get("is_trashed"):
            folder_id = None  # Folder gone or trashed → restore to library root

    # Recreate the resource_item
    await self.repo.create_resource_item({
        "resource_id": resource_id,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "folder_id": folder_id,
        "library_id": library_id,
        "added_by": user_id,
    })

    # Un-trash the resource
    return await self.repo.update_resource(
        resource_id,
        {"is_trashed": False, "trashed_at": None},
    )
```

**Step 2: Verify backend starts**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run python -c "from app.services.resources_service import ResourcesService; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add backend/app/services/resources_service.py
git commit -m "fix: restore recreates resource_item in original folder"
```

---

## Task 4: Backend — Remove direct is_trashed PATCH from update endpoint

Prevent the `PATCH /resources/{id}` endpoint from accepting `is_trashed` changes (force deletion through `resource_item` removal only).

**Files:**
- Modify: `backend/app/api/resources_router.py:578-600` (`update_resource`)

**Step 1: Strip is_trashed from PATCH update_data**

In `backend/app/api/resources_router.py`, update `update_resource` (lines 578-600). Add a guard after `update_data = data.model_dump(exclude_none=True)`:

```python
@router.patch("/{resource_id}")
async def update_resource(resource_id: str, data: ResourceUpdate, auth: AuthDep):
    """Update resource metadata. Use DELETE endpoint for trashing."""
    try:
        repo = ResourcesRepository()
        resource = await repo.get_resource_by_id(resource_id)
        if not resource:
            raise HTTPException(status_code=404, detail="Resource not found")

        update_data = data.model_dump(exclude_none=True)

        # Prevent direct is_trashed manipulation via PATCH.
        # Trash must go through DELETE (resource_item removal).
        update_data.pop("is_trashed", None)
        update_data.pop("trashed_at", None)

        if not update_data:
            return {"success": True, "data": resource}

        result = await repo.update_resource(resource_id, update_data)
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update resource")
```

**Step 2: Commit**

```bash
git add backend/app/api/resources_router.py
git commit -m "fix: block is_trashed via PATCH, force deletion through resource_item"
```

---

## Task 5: Frontend — Rework `trashResource` to call DELETE with folder_id

Change the frontend service to call `DELETE /resources/{id}?folder_id=X` instead of `PATCH` with `is_trashed`.

**Files:**
- Modify: `frontend/services/resourceService.ts:364-379` (`trashResource`)
- Modify: `frontend/services/resourceService.ts:882-895` (`trashResources`)

**Step 1: Update `trashResource`**

In `frontend/services/resourceService.ts`, replace `trashResource` (lines 364-379):

```typescript
export async function trashResource(
  resourceId: string,
  scopeType: 'personal' | 'team',
  scopeId: string,
  folderId?: string | null,
): Promise<void> {
  const apiUrl = getApiUrl();
  const params = new URLSearchParams({
    scope_type: scopeType,
    scope_id: scopeId,
  });
  if (folderId) params.set('folder_id', folderId);

  const response = await fetch(`${apiUrl}/api/v1/resources/${resourceId}?${params}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error('Failed to trash resource');
}
```

**Step 2: Update `trashResources` (batch)**

In `frontend/services/resourceService.ts`, replace `trashResources` (lines 882-895):

```typescript
export async function trashResources(
  resourceIds: string[],
  scopeType: 'personal' | 'team',
  scopeId: string,
  folderId?: string | null,
): Promise<void> {
  const apiUrl = getApiUrl();
  const headers = await getAuthHeaders();
  await Promise.all(resourceIds.map(async (id) => {
    const params = new URLSearchParams({
      scope_type: scopeType,
      scope_id: scopeId,
    });
    if (folderId) params.set('folder_id', folderId);

    const response = await fetch(`${apiUrl}/api/v1/resources/${id}?${params}`, {
      method: 'DELETE',
      headers,
    });
    if (!response.ok) throw new Error('Failed to trash resource');
  }));
}
```

**Step 3: Commit**

```bash
git add frontend/services/resourceService.ts
git commit -m "fix: trashResource calls DELETE endpoint instead of PATCH is_trashed"
```

---

## Task 6: Frontend — Pass folder_id from ResourcesView to trash calls

Update all call sites in `ResourcesView.tsx` to pass the current `selectedFolderId`.

**Files:**
- Modify: `frontend/components/ResourcesView.tsx` (3 call sites)

**Step 1: Update `handleTrash`**

At `ResourcesView.tsx:812-823`, update:

```typescript
const handleTrash = useCallback(async (resourceId: string) => {
  try {
    const rid = String(resourceId);
    const item = resources.find((r) => String(r.resource?.id) === rid);
    await trashResource(rid, scopeType, scopeId, selectedFolderId);
    // Optimistic removal
    setResources((prev) => prev.filter((r) => String(r.resource?.id) !== rid));
    if (String(selectedResource?.resource?.id) === rid) setSelectedResource(null);
    const filename = item?.resource?.filename || '';
    addToast(t('resources.trashedNotification', { name: filename }), 'success');
  } catch { /* ignore */ }
}, [selectedResource, scopeType, scopeId, selectedFolderId, resources, addToast, t]);
```

**Step 2: Update batch trash (keyboard delete)**

At `ResourcesView.tsx:~1843`, update:

```typescript
trashResources(resourceIds, scopeType, scopeId, selectedFolderId).then(async () => {
```

**Step 3: Update toolbar batch trash**

At `ResourcesView.tsx:~2996`, update:

```typescript
await trashResources(resourceIds, scopeType, scopeId, selectedFolderId);
```

**Step 4: Verify frontend compiles**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: No new errors related to trashResource/trashResources

**Step 5: Commit**

```bash
git add frontend/components/ResourcesView.tsx
git commit -m "fix: pass folder_id to trash calls for correct resource_item deletion"
```

---

## Task 7: Frontend — Fix `fetchTrashedResources` for orphaned resources

The current `fetchTrashedResources` queries `resource_items` with `is_trashed=true` on the joined resource. But after the refactor, trashed resources have **no** `resource_items` (they're orphans). We need to query `resources` directly.

**Files:**
- Modify: `frontend/services/resourceService.ts:410-424` (`fetchTrashedResources`)

**Step 1: Rewrite `fetchTrashedResources`**

In `frontend/services/resourceService.ts`, replace `fetchTrashedResources` (lines 410-424):

```typescript
export async function fetchTrashedResources(
  scopeType: 'personal' | 'team',
  scopeId: string
): Promise<ResourceItem[]> {
  // After refactor, trashed resources are orphans (no resource_items).
  // Query resources directly where is_trashed=true and last_scope matches.
  const { data, error } = await supabase
    .from('resources')
    .select('*')
    .eq('is_trashed', true)
    .eq('last_scope_type', scopeType)
    .eq('last_scope_id', scopeId)
    .order('trashed_at', { ascending: false });

  if (error) throw error;

  // Wrap each resource in a ResourceItem-like shape for compatibility
  return (data || []).map((resource) => ({
    id: resource.id,
    resource_id: resource.id,
    scope_type: scopeType,
    scope_id: scopeId,
    folder_id: resource.last_folder_id,
    library_id: resource.last_library_id,
    created_at: resource.created_at,
    resource,
  }));
}
```

**Step 2: Commit**

```bash
git add frontend/services/resourceService.ts
git commit -m "fix: fetchTrashedResources queries orphaned resources directly"
```

---

## Task 8: End-to-end verification

Test the complete flow manually.

**Step 1: Start backend + frontend**

Run (in separate terminals):
```bash
cd backend && uv run uvicorn app.main:app --reload --port 8081
cd frontend && npm run dev -- --port 5176
```

**Step 2: Test delete-one-copy flow**

1. Upload a file to Folder A
2. Copy it to Folder B (via Move To dialog → Copy)
3. Go to Folder A → delete the file
4. Go to Folder B → verify the file is still there
5. Go to Recycle Bin → verify only the orphaned resource appears (if it was the last copy) or nothing (if other copies exist)

**Step 3: Test last-copy deletion**

1. Delete the file from Folder B (now the last copy)
2. Go to Recycle Bin → verify the file appears
3. Click Restore → verify the file is back in Folder B

**Step 4: Test duplicate upload**

1. Upload the same file again → verify toast "File already exists, linked to existing copy"
2. Verify no duplicate resource created

**Step 5: Commit any fixes**

If any issues found, fix and commit individually.

---

## Summary of changes

| # | What | Key Change |
|---|------|------------|
| 1 | DB Migration | `last_folder_id`, `last_library_id`, `last_scope_type`, `last_scope_id` on `resources` |
| 2 | Backend DELETE | Removes `resource_item` + saves last location. Orphan GC auto-trashes. |
| 3 | Backend Restore | Recreates `resource_item` in original folder, un-trashes resource. |
| 4 | Backend PATCH guard | Strips `is_trashed` from PATCH to prevent bypass. |
| 5 | Frontend service | `trashResource`/`trashResources` → `DELETE` with `folder_id` param. |
| 6 | Frontend view | Pass `selectedFolderId` to all trash calls. |
| 7 | Frontend trashed query | Query `resources` directly (orphans have no `resource_items`). |
| 8 | E2E verification | Manual test of copy→delete→restore→duplicate flows. |
