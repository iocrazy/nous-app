# Storage Path Architecture Design

**Date**: 2026-02-17
**Status**: Approved
**Scope**: NAS file storage path restructuring with team isolation

## Background

Current file storage uses a flat `resources/{resource_id}/` structure for uploads, lacking team isolation. As the platform evolves to support team workspaces, project files, and AI-generated content, we need a structured path hierarchy that:

1. Isolates files by team (personal space = special team)
2. Separates different content sources (upload, project, AI, web download)
3. Supports future extensibility
4. Maintains backward compatibility during migration

## Current Storage Structure

```
DOWNLOAD_PATH (/app/downloads)
├── resources/
│   ├── {resource_id}/              ← uploaded files (flat, no team scope)
│   │   └── filename.mp4
│   └── web/
│       └── {platform}/{ext_id}/    ← downloaded/parsed videos (global)
│           ├── video.mp4
│           ├── music.mp3
│           └── cover.jpg

Supabase Storage (thumbnails bucket)
├── {scope_type}/{scope_id}/{resource_id}.jpg  ← already scoped!
```

### Problems

- Uploaded files have no team isolation — all files mixed under `resources/`
- No clear separation between user uploads, project files, and AI content
- Cannot easily audit or manage storage per team
- Path structure doesn't match the data model (`resource_items.scope_type/scope_id`)

## New Storage Structure

```
DOWNLOAD_PATH (/app/downloads)
│
├── teams/{team_id}/                    ← team isolation layer
│   │                                     (personal space = special team)
│   │
│   ├── uploads/{resource_id}/          ← user-uploaded files
│   │   └── filename.mp4                  1 resource = 1 file + versions
│   │
│   ├── projects/{project_id}/          ← project files (future)
│   │   └── asset.psd
│   │
│   └── ai/{create_id}/                ← AI-generated content (future)
│       ├── output_1.png                  1 creation task = N outputs
│       ├── output_2.png
│       └── output_3.mp4
│
└── global/                             ← no team ownership
    └── web/{platform}/{external_id}/   ← downloaded/parsed videos
        ├── video.mp4
        ├── music.mp3
        └── cover.jpg
```

### Design Principles

| Principle | Detail |
|-----------|--------|
| **Team isolation** | All owned content lives under `teams/{team_id}/` |
| **Personal = team** | Personal workspace is a special team, same path pattern |
| **Source separation** | `uploads/`, `projects/`, `ai/` are distinct sub-categories |
| **Global content** | Downloaded web content has no team ownership, stays global |
| **Relative paths in DB** | Database stores relative paths (e.g., `teams/abc/uploads/123/file.mp4`) |
| **ID-based directories** | Use IDs (not names) for directories to avoid rename issues |

### Database Path Examples

| Scenario | Relative Path | Source |
|----------|---------------|--------|
| Team upload | `teams/t_abc123/uploads/res_456/demo.mp4` | User upload |
| Personal upload | `teams/t_personal_789/uploads/res_012/photo.jpg` | User upload |
| Project file | `teams/t_abc123/projects/proj_345/asset.psd` | Project attachment |
| AI generation | `teams/t_abc123/ai/crt_678/output_1.png` | AI creation task |
| Web download | `global/web/douyin/ext_001/video.mp4` | Parser/downloader |

## Migration Plan

### Strategy: Full Migration (Option A)

Move all existing files to new paths and update database records. Chosen because:
- Development stage, small data volume
- Clean break, no dual-path complexity in code
- One-time effort, simpler long-term maintenance

### Migration Script Logic

```python
# Step 1: Migrate uploaded files (source_type = 'upload')
for resource in resources_with_upload_source:
    # Get team scope from resource_items table
    item = get_resource_item(resource.id)
    team_id = item.scope_id  # personal scope_id is also a team_id

    old_path = f"resources/{resource.id}/{filename}"
    new_path = f"teams/{team_id}/uploads/{resource.id}/{filename}"

    # Physical move
    move_file(DOWNLOAD_PATH / old_path, DOWNLOAD_PATH / new_path)

    # Update database
    update_resource_file_path(resource.id, new_path)

# Step 2: Migrate downloaded files (source_type = 'download')
for resource in resources_with_download_source:
    old_path = f"resources/web/{platform}/{ext_id}/..."
    new_path = f"global/web/{platform}/{ext_id}/..."

    # Physical move
    move_file(DOWNLOAD_PATH / old_path, DOWNLOAD_PATH / new_path)

    # Update all path fields (video_path, music_path, cover_path, images)
    update_resource_paths(resource.id, old_prefix, new_prefix)

# Step 3: Clean up empty directories
remove_empty_dirs(DOWNLOAD_PATH / "resources")
```

### Rollback

- Before migration, create a path mapping log (`migration_log.json`)
- If issues arise, reverse the moves using the log

## Code Changes Required

### Backend Files

| File | Change | Priority |
|------|--------|----------|
| `app/services/resources_service.py` | Upload path: `resources/{id}/` → `teams/{team_id}/uploads/{id}/` | P0 |
| `app/services/downloader.py` | Download path: `resources/web/` → `global/web/` | P0 |
| `app/tasks/download_tasks.py` | Async download path: same as downloader | P0 |
| `app/api/resources_router.py` | Pass `scope_id` to service for path construction | P0 |
| New: `scripts/migrate_storage_paths.py` | Migration script | P0 |

### Path Construction Change

**Before** (`resources_service.py`):
```python
save_dir = Path(settings.DOWNLOAD_PATH) / "resources" / resource_id
relative_path = f"resources/{resource_id}/{safe_name}"
```

**After**:
```python
save_dir = Path(settings.DOWNLOAD_PATH) / "teams" / scope_id / "uploads" / resource_id
relative_path = f"teams/{scope_id}/uploads/{resource_id}/{safe_name}"
```

**Before** (`downloader.py`):
```python
full_path = Path(settings.DOWNLOAD_PATH) / "resources" / "web" / platform / ext_id
relative_prefix = f"resources/web/{platform}/{ext_id}"
```

**After**:
```python
full_path = Path(settings.DOWNLOAD_PATH) / "global" / "web" / platform / ext_id
relative_prefix = f"global/web/{platform}/{ext_id}"
```

## Implementation Steps

### Step 1: Update upload path in resources_service.py
- Modify `upload_resource()` to accept `scope_id` and construct `teams/{scope_id}/uploads/{resource_id}/` path
- Update `resources_router.py` to pass `scope_id` through

### Step 2: Update download path in downloader.py + download_tasks.py
- Change `resources/web/` to `global/web/`
- Update `Utils.create_web_resource_path()` helper

### Step 3: Write and run migration script
- Query all resources + their resource_items for scope mapping
- Move files, update DB paths
- Generate migration log for rollback

### Step 4: Verify
- Upload a new file → confirm path is `teams/{team_id}/uploads/...`
- Download a video → confirm path is `global/web/...`
- Existing files accessible via new paths
- Thumbnails still work (Supabase Storage, no change needed)

## Future Extensibility

| Content Type | Path Pattern | When |
|-------------|-------------|------|
| Project files | `teams/{team_id}/projects/{project_id}/` | Phase: Projects |
| AI images | `teams/{team_id}/ai/{create_id}/` | Phase: AI Creation |
| AI videos | `teams/{team_id}/ai/{create_id}/` | Phase: AI Creation |
| Exports | `teams/{team_id}/exports/{export_id}/` | Phase: Export |

No structural changes needed — just add new sub-directories under `teams/{team_id}/`.
