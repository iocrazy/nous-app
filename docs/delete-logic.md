# Delete Logic — Resource Lifecycle & Recycle Bin

This document describes the complete delete/cleanup flow for resources in MediaHub, covering both **uploaded resources** (My Resources) and **downloaded videos** (My Downloads / Parser).

## Overview

```
┌─────────────────┐     trash      ┌──────────────┐   15 days   ┌────────────────┐
│  Active Resource │──────────────▶│  Recycle Bin  │────────────▶│  Permanent Del  │
│  (is_trashed=F)  │               │ (is_trashed=T)│  auto-clean │  (hard delete)  │
└─────────────────┘               └──────────────┘             └────────────────┘
        ▲                                │                            │
        │          restore               │                            │
        └────────────────────────────────┘                            │
                                                                      ▼
                                                          ┌────────────────────┐
                                                          │ Delete physical    │
                                                          │ files + resource   │
                                                          │ record + video     │
                                                          │ record (if any)    │
                                                          └────────────────────┘
```

## Data Isolation

| View | Data Source | Filter |
|------|------------|--------|
| **My Resources** | `resource_items` JOIN `resources` | `source_type != 'web'` AND `is_trashed = false` |
| **My Downloads** | `resource_items` JOIN `resources` | `source_type = 'web'` |
| **Recycle Bin** | `resources` | `is_trashed = true` (via backend API) |

Key: `source_type='web'` resources come from the Parser (downloaded from Douyin, YouTube, etc.); `source_type='upload'` resources are user-uploaded files.

## Delete Flows

### 1. My Resources — Uploaded Files

**User action**: Right-click → "Move to Trash" or select + Delete key

**Flow**:
1. Frontend calls `PATCH /api/v1/resources/{id}` with `{ is_trashed: true }`
2. Backend sets `is_trashed = true`, `trashed_at = now()`
3. Resource disappears from My Resources, appears in Recycle Bin
4. User can **restore** from Recycle Bin → `POST /api/v1/resources/{id}/restore`

**Code path**:
- Frontend: `resourceService.ts → trashResource()`
- Backend: `resources_router.py → update_resource()` → sets `is_trashed` + `trashed_at`

### 2. My Downloads — Parser-Downloaded Videos

**User action**: Click Delete button in RipVaultView (single or batch)

**Flow**:
1. Frontend calls `POST /api/v1/resources/by-platform-id/{platform_id}/trash`
2. Backend looks up resource by `platform_id`, sets `is_trashed = true`, `trashed_at = now()`
3. Resource disappears from My Downloads, appears in Recycle Bin
4. User can restore from Recycle Bin (same as uploaded files)

**Code path**:
- Frontend: `resourceService.ts → trashResourceByPlatformId()`
- Backend: `resources_router.py → trash_resource_by_platform_id()`

> **Note**: Previously, My Downloads used hard-delete (`unlinkResourceByPlatformId`), which directly deleted the `resource_item` record with no way to recover. This was changed to soft-delete in commit `234276c`.

### 3. Recycle Bin — Permanent Delete (Manual)

**User action**: Click "Delete Permanently" button in Recycle Bin

**Flow**:
1. Frontend shows **confirmation dialog** (red warning, "This action cannot be undone")
2. On confirm, calls `DELETE /api/v1/resources/{id}/permanent`
3. Backend:
   a. Deletes physical files from disk (file + cover + thumbnail directory)
   b. Deletes the `resources` record (CASCADE deletes `resource_items`, `resource_versions`, etc.)
   c. If the resource had an associated `video_id`, deletes the `videos` table record too

**Code path**:
- Frontend: `ResourcesView.tsx → handlePermanentDelete()` → opens dialog → `confirmPermanentDelete()`
- Backend: `resources_service.py → permanent_delete()` → `_delete_physical_files()` + `_delete_video_record()`

### 4. Recycle Bin — Auto-Cleanup (Scheduled)

**Schedule**: Runs daily via Celery Beat

**Flow**:
1. Celery task `cleanup_trashed_resources()` fires
2. Queries all resources where `is_trashed = true` AND `trashed_at < now() - 15 days`
3. For each expired resource:
   a. Deletes physical files from disk
   b. Deletes the `resources` record
   c. If associated `video_id` exists, deletes the `videos` record

**Code path**:
- `scheduled_tasks.py → cleanup_trashed_resources()` → `resources_service.py → cleanup_expired_trash(older_than_days=15)`

## Database Schema (Relevant Fields)

### `resources` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `creator_id` | UUID | FK to auth.users |
| `source_type` | TEXT | `'upload'` or `'web'` |
| `video_id` | BIGINT | FK to `videos.id` (nullable, SET NULL on delete) |
| `is_trashed` | BOOLEAN | Soft-delete flag (default: false) |
| `trashed_at` | TIMESTAMPTZ | When the resource was trashed (null if active) |
| `file_path` | TEXT | Relative path to physical file on disk |

### `videos` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | BIGINT | Primary key (Snowflake ID) |
| `platform_id` | TEXT | Platform-specific video ID (e.g., Douyin aweme_id) |
| `user_id` | UUID | FK to auth.users |

### `resource_items` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `resource_id` | UUID | FK to resources |
| `scope_type` | TEXT | `'personal'` or `'team'` |
| `scope_id` | UUID | User ID or Team ID |
| `folder_id` | UUID | FK to folders (nullable) |

## Physical File Cleanup

When permanently deleting, `_delete_physical_files()` handles two patterns:

1. **Directory-based** (Parser downloads): Files stored in `global/resources/web/{platform}/{id}/` — the entire directory is removed via `shutil.rmtree()`
2. **Individual files** (Uploads): Files stored in `teams/{scope_id}/uploads/{resource_id}/` — the parent directory is removed

Cover images at a separate path are also cleaned up if present.

## API Endpoints Summary

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `PATCH /api/v1/resources/{id}` | PATCH | Soft-delete (set `is_trashed: true`) |
| `POST /api/v1/resources/by-platform-id/{pid}/trash` | POST | Soft-delete by platform_id |
| `POST /api/v1/resources/{id}/restore` | POST | Restore from recycle bin |
| `DELETE /api/v1/resources/{id}/permanent` | DELETE | Permanent delete (with confirmation) |
| `GET /api/v1/resources/trash` | GET | List trashed resources |

## UI Behavior

- **Recycle Bin notice**: A banner at the top displays "Items in the recycle bin will be automatically deleted after 15 days."
- **Permanent delete confirmation**: A modal dialog with red warning icon, explaining the action is irreversible. User must click "Delete Forever" to proceed.
- **My Downloads delete toast**: Shows "Moved to Recycle Bin" instead of the previous "Removed from library".
