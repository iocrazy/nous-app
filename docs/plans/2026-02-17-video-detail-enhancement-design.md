# Video Detail Page Enhancement — Player + HLS + Versions + Review

## Overview

Comprehensive enhancement of the video detail page covering 4 modules:
- **A. Player Enhancement** — Custom player fills content area + speed control + keyboard shortcuts
- **B. HLS Transcoding** — ffmpeg multi-bitrate transcoding → hls.js quality switching
- **C. Version Control** — Version dropdown + upload new version + version compare
- **D. Review Mode** — Timestamped comments + canvas annotations + approval status

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│ Top Bar: [☰ Back] [◀ filename v2 ▼ ▶] [Share Download ⋯]│
├────────────┬─────────────────────┬───────────────────────┤
│ File List  │   Video Player      │  Inspector Panel      │
│ (optional) │   (full area)       │  - Properties         │
│            │   Custom controls:  │  - Tags               │
│            │   ▶ ◀◀ ▶▶ 🔊       │  - Notes              │
│            │   [1.0x] [1080 HD]  │  - Source              │
│            │   [⚙️] [⛶]         │  - Versions           │
│            ├─────────────────────┤  - Comments / Review  │
│            │  Annotation Layer   │    (new section)      │
│            │  (drawings overlay) │                       │
└────────────┴─────────────────────┴───────────────────────┘
```

| Module | Description | DB Changes |
|--------|-------------|------------|
| **A. Player Enhancement** | Custom VideoPlayer fills area + speed + shortcuts + help panel | None |
| **B. HLS Transcoding** | Backend ffmpeg → m3u8 multi-bitrate → frontend hls.js quality switch | `resource_versions` add HLS fields |
| **C. Version Control** | Header version dropdown + upload new version + manage modal + side-by-side compare | `resource_versions` backfill + path migration |
| **D. Review Mode** | Timestamped comments + canvas annotations (arrow/rect/freehand/text) + approval status | `review_comments`, `review_annotations`, `review_status` tables |

---

## Module A: Player Enhancement

### Problem

`ResourceDetail.tsx` uses native `<video controls>` which renders tiny. A custom `VideoPlayer.tsx` component already exists with HLS support and frame-stepping but is not used in the detail page.

### Solution

Replace native `<video>` in `FilePreview` with `VideoPlayer` component, filling the entire preview area. Enhance VideoPlayer with speed control, full keyboard shortcuts, and help panel.

### Control Bar Layout

```
[◀◀] [▶/⏸] [▶▶]  [🔊 ━━]  00:12.05 [F361] / 07:06
                    ───────── spacer ─────────
                    [1.0x ▼]  [1080 HD ▼]  [⚙️]  [⛶]
```

| New Control | Description |
|-------------|-------------|
| **Speed button** `1.0x` | Click to show options: 0.25x / 0.5x / 1.0x / 1.5x / 2.0x / 3.0x |
| **Quality button** `1080 HD` | Click to show options: Auto / Original / 1080p / 720p / 480p (only when HLS available) |
| **Settings gear** `⚙️` | Opens keyboard shortcuts help panel |

### Keyboard Shortcuts (Full Mapping)

| Key | Action | Status |
|-----|--------|--------|
| `Space` | Play / Pause | **New** |
| `F` | Toggle fullscreen | **New** |
| `M` | Toggle mute | **New** |
| `←` / `→` | Backward / Forward 5 seconds | **Remap** (currently frame-step) |
| `,` / `.` | Backward / Forward 1 frame (paused) | **New** (replaces old ←→ frame) |
| `Shift+,` / `Shift+.` | Backward / Forward 10 frames | **New** |
| `[` / `]` | Slow down / Speed up by 0.25x | **New** |
| `\` | Reset speed to 1.0x | **New** |
| `?` | Open keyboard shortcuts help | **New** |

---

## Module B: HLS Transcoding

### Backend Flow

```
Upload/Download complete → Celery task transcode_to_hls()
                    │
                    ▼
            ffmpeg transcode multi-bitrate:
            ├── 480p  (854x480,  800kbps)
            ├── 720p  (1280x720, 2500kbps)
            └── 1080p (1920x1080, 5000kbps)
            + Skip tiers above original resolution
                    │
                    ▼
            Generate master.m3u8 (multi-bitrate index)
            Store to {resource_path}/v{n}/hls/
                    │
                    ▼
            Update resource_versions: hls_path, transcode_status
```

### Versioned Storage Structure

```
# My Downloads
global/resources/web/douyin/{id}/
├── v1/
│   ├── original.mp4
│   ├── thumbnail.jpg
│   └── hls/
│       ├── master.m3u8
│       ├── 480p/  (stream.m3u8 + .ts segments)
│       ├── 720p/
│       └── 1080p/
└── v2/
    ├── original.mp4
    ├── thumbnail.jpg
    └── hls/
        └── ...

# My Resources (uploads)
teams/{scope_id}/uploads/{resource_id}/
├── v1/
│   ├── original.mp4
│   ├── thumbnail.jpg
│   └── hls/
│       └── ...
└── v2/
    └── ...
```

### Database Changes

`resource_versions` table — new fields:

| Field | Type | Description |
|-------|------|-------------|
| `hls_path` | TEXT | Relative path to master.m3u8 (null = not transcoded) |
| `transcode_status` | VARCHAR(20) | `pending` / `processing` / `completed` / `failed` |
| `transcode_at` | TIMESTAMPTZ | When transcoding completed |

### Frontend Logic

- `VideoPlayer` already supports hls.js — pass `master.m3u8` URL
- Quality button uses `hls.levels` to get available tiers, `hls.currentLevel = index` to switch
- If `hls_path` is null (not yet transcoded), play original MP4, show resolution label (not switchable)
- While transcoding: show "Transcoding..." badge

### Trigger Points

| Source | Trigger | Storage Path |
|--------|---------|--------------|
| **My Downloads** (`source_type='web'`) | After Parser download completes | `global/resources/web/{platform}/{id}/v{n}/hls/` |
| **My Resources** (`source_type='upload'`) | After user upload completes | `teams/{scope_id}/uploads/{resource_id}/v{n}/hls/` |

Only triggered for `mime_type` starting with `video/`.

### Playback Logic

```
User selects version v2
    │
    ├── v2 has hls_path? → Play hls/master.m3u8 (quality switchable)
    │
    └── v2 no hls_path? → Play original.mp4 (show resolution label, not switchable)
                          └── If transcode_status = 'processing'
                              → Show "Transcoding..." badge
```

---

## Module C: Version Control

### Storage Migration (One-Time, Dev Environment)

No fallback needed — direct migration:

```sql
-- Ensure every resource has at least one resource_versions record
INSERT INTO resource_versions (resource_id, version_number, filename, file_path,
  file_size_bytes, mime_type, duration_seconds, resolution, thumbnail_path, uploaded_by)
SELECT id, COALESCE(current_version, 1), filename, file_path,
  file_size_bytes, mime_type, duration_seconds, resolution, thumbnail_path, creator_id
FROM resources r
WHERE NOT EXISTS (
  SELECT 1 FROM resource_versions rv WHERE rv.resource_id = r.id
);

-- Ensure current_version is not null
UPDATE resources SET current_version = 1 WHERE current_version IS NULL;
```

Python script moves all existing files into `v1/` subdirectories and updates DB paths.

### Header Version Dropdown

```
[◀] 📄 video_name.mp4  [v2 ▼] [▶]
                          │
                          ▼ dropdown
                        ┌──────────────┐
                        │ ● v2 current │
                        │   v1         │
                        ├──────────────┤
                        │ ⬆ Upload New │
                        │ ⚙ Manage     │
                        └──────────────┘
```

- Select version → switch player source (load that version's HLS or MP4)
- Inspector panel properties update (size, resolution, duration from selected version)
- "Upload New" → open upload dialog
- "Manage" → open version management modal

### Version Management Modal

```
┌─────────────────────────────────────────┐
│  Manage Versions                     ✕  │
├─────────────────────────────────────────┤
│  v2 ● current                           │
│  [thumb] filename_v2.mp4                │
│  User Name · Feb 17, 2026 11:30        │
│  Notes: "Color grading updated"    [⋯] │
│─────────────────────────────────────────│
│  v1                                     │
│  [thumb] filename_v1.mp4                │
│  User Name · Feb 17, 2026 10:52        │
│  Notes: "Initial upload"           [⋯] │
├─────────────────────────────────────────┤
│  [⬆ Upload New Version]                │
└─────────────────────────────────────────┘
```

Per-version `[⋯]` menu:
- **Set as Current** — make this the active version
- **Download** — download original file
- **Delete** — delete version (must keep at least one)

### Upload New Version Flow

```
User uploads new file → Create resource_versions record (v{n+1})
    → Update resources.current_version = n+1
    → Store file to {base}/v{n+1}/original.mp4
    → Generate thumbnail → {base}/v{n+1}/thumbnail.jpg
    → Trigger Celery: transcode_to_hls(version_id)
```

### Side-by-Side Version Compare

```
┌─────────────────┬─────────────────┐
│  v1 ▼           │  v2 ▼           │
│  [Video Player] │  [Video Player] │
│  ▶ 01:23/07:06  │  ▶ 01:23/07:06  │
│                 │                 │
│  [🔊]           │  [🔊]           │
└─────────────────┴─────────────────┘
      [🔗 Sync Playback: ON]
```

- Two VideoPlayer instances, left and right
- **Sync playback**: ON by default, both videos sync play/pause/seek
- Each side can select different version
- Audio mutually exclusive: only one side has sound at a time

---

## Module D: Review Mode

### Database Schema

```sql
-- Review comments (with timecode)
CREATE TABLE review_comments (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  resource_id     UUID NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  version_id      UUID REFERENCES resource_versions(id) ON DELETE SET NULL,
  author_id       UUID NOT NULL REFERENCES auth.users(id),
  timecode        FLOAT,             -- video seconds (null = general comment)
  frame_number    INTEGER,           -- corresponding frame number
  content         TEXT NOT NULL,      -- comment text (supports @mention)
  status          VARCHAR(20) NOT NULL DEFAULT 'open',  -- 'open' / 'resolved' / 'wontfix'
  parent_id       UUID REFERENCES review_comments(id) ON DELETE CASCADE,  -- reply thread
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Canvas annotations (bound to comment)
CREATE TABLE review_annotations (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  comment_id      UUID NOT NULL REFERENCES review_comments(id) ON DELETE CASCADE,
  tool_type       VARCHAR(20) NOT NULL,  -- 'arrow' / 'rect' / 'freehand' / 'text'
  data            JSONB NOT NULL,        -- coordinates, color, width, etc.
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Resource approval status
CREATE TABLE review_status (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  resource_id     UUID NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  version_id      UUID REFERENCES resource_versions(id) ON DELETE SET NULL,
  reviewer_id     UUID NOT NULL REFERENCES auth.users(id),
  status          VARCHAR(20) NOT NULL DEFAULT 'pending',  -- 'pending' / 'approved' / 'needs_changes' / 'rejected'
  comment         TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### Annotation Data JSONB Format

All coordinates use **0~1 normalized ratios** — resolution-independent.

```jsonc
// arrow
{ "x1": 0.3, "y1": 0.4, "x2": 0.7, "y2": 0.6, "color": "#ff4444", "width": 2 }

// rect
{ "x": 0.2, "y": 0.3, "w": 0.4, "h": 0.3, "color": "#ff4444", "width": 2 }

// freehand
{ "points": [[0.1,0.2],[0.15,0.25],...], "color": "#ff4444", "width": 2 }

// text
{ "x": 0.5, "y": 0.5, "text": "This needs fixing", "color": "#ff4444", "fontSize": 14 }
```

### Right Panel — Comments Section

```
┌─ REVIEW STATUS ──────────────────────┐
│  [🟡 Pending ▼]  Approve  Reject     │
├─ COMMENTS (3) ───────────────────────┤
│  🔵 01:23  @Designer                 │
│  "The color grading looks off here"  │
│  [🖼 annotation preview]             │
│  ├─ @Editor: "Will fix in v3"        │
│  └─ ✅ Resolved                      │
│──────────────────────────────────────│
│  🔵 03:45  @Director                 │
│  "Great transition!"                 │
│  └─ 💬 Open                         │
│──────────────────────────────────────│
│  📝 Add comment...          [📌 Pin] │
│  [🖊 Draw] [Send]                    │
└──────────────────────────────────────┘
```

- Click comment → video jumps to timecode + shows annotation overlay
- Timeline shows comment marker dots
- Filter by status: All / Open / Resolved
- Supports `@mention` team members

### Annotation Toolbar (Overlay on Video)

Click "Draw" → video pauses, toolbar appears at top:

```
┌──────────────────────────────────────────┐
│  [↗ Arrow] [▢ Rect] [✏ Freehand] [T Text]  │
│  Color: [🔴 🟡 🟢 🔵 ⚪]  Width: [━ ━━]     │
│  [↩ Undo] [✓ Done] [✕ Cancel]               │
└──────────────────────────────────────────┘
```

- After drawing, click Done → annotation attaches to comment input
- Annotations render on `<canvas>` overlay, do not affect video
- Each comment can have multiple annotations

### Timeline Marker Dots

```
━━━━━●━━━━━━━●━━━━━━━━━●━━━━━━━━
     🔵      🔵         🟢
   01:23    03:45      05:12
  (open)   (open)    (resolved)
```

- Blue = open, Green = resolved, Red = needs_changes
- Hover shows comment preview tooltip
- Click jumps to timecode

---

## Implementation Phases

### Phase 1: Player Enhancement (Frontend Only)

| File | Action | Description |
|------|--------|-------------|
| `frontend/components/VideoPlayer.tsx` | **Modify** | Speed control + all shortcuts + Space play + quality button (placeholder) |
| `frontend/components/ResourceDetail.tsx` | **Modify** | FilePreview uses VideoPlayer instead of native video + fill area |
| `frontend/components/KeyboardShortcutsDialog.tsx` | **New** | Keyboard shortcuts help dialog |
| `frontend/public/locales/en.json` | **Modify** | New i18n keys |
| `frontend/public/locales/zh.json` | **Modify** | Corresponding translations |

### Phase 2: Storage Migration + Version Control

| File | Action | Description |
|------|--------|-------------|
| `supabase/migrations/061_versioned_storage.sql` | **New** | resource_versions backfill + HLS fields |
| `backend/scripts/migrate_to_versioned_storage.py` | **New** | One-time migration: move files to v1/ + update DB paths |
| `backend/app/services/resources_service.py` | **Modify** | Versioned path logic (no fallback) |
| `backend/app/services/upload_service.py` | **Modify** | Upload writes to v{n}/ subdirectory |
| `backend/app/api/resources_router.py` | **Modify** | Version CRUD API + upload new version endpoint |
| `frontend/components/ResourceDetail.tsx` | **Modify** | Header version dropdown + version switch playback |
| `frontend/components/VersionManagerModal.tsx` | **New** | Version management modal |
| `frontend/components/VersionCompareView.tsx` | **New** | Side-by-side compare (dual player + sync) |
| `frontend/services/resourceService.ts` | **Modify** | Version-related API calls |

### Phase 3: HLS Transcoding

| File | Action | Description |
|------|--------|-------------|
| `backend/app/tasks/transcode_tasks.py` | **New** | Celery task: ffmpeg multi-bitrate transcoding |
| `backend/app/services/transcode_service.py` | **New** | Transcoding logic encapsulation |
| `backend/app/api/resources_router.py` | **Modify** | Transcoding status query + manual retry endpoint |
| `backend/app/services/resources_service.py` | **Modify** | Trigger transcoding after upload/download complete |
| `backend/app/tasks/download_tasks.py` | **Modify** | Trigger transcoding after Parser download |
| `frontend/components/VideoPlayer.tsx` | **Modify** | hls.js quality switch UI (hls.js foundation exists) |
| `backend/scripts/batch_transcode_existing.py` | **New** | One-time batch transcode existing videos |

### Phase 4: Review Mode

| File | Action | Description |
|------|--------|-------------|
| `supabase/migrations/062_review_system.sql` | **New** | review_comments + review_annotations + review_status tables |
| `backend/app/api/reviews_router.py` | **New** | Comment/annotation/approval CRUD API |
| `backend/app/services/review_service.py` | **New** | Review business logic |
| `backend/app/repositories/review_repository.py` | **New** | Review data access layer |
| `frontend/components/ReviewCommentsPanel.tsx` | **New** | Right panel comments section |
| `frontend/components/AnnotationCanvas.tsx` | **New** | Canvas annotation overlay + drawing tools |
| `frontend/components/AnnotationToolbar.tsx` | **New** | Annotation toolbar (Arrow/Rect/Freehand/Text) |
| `frontend/components/ReviewStatusBadge.tsx` | **New** | Approval status component |
| `frontend/components/ResourceDetail.tsx` | **Modify** | Integrate comments panel + annotation layer + timeline markers |
| `frontend/components/VideoPlayer.tsx` | **Modify** | Render comment marker dots on timeline |
| `frontend/services/reviewService.ts` | **New** | Review-related API calls |

### DB Migrations Summary

| Migration | Tables | Phase |
|-----------|--------|-------|
| `061_versioned_storage.sql` | `resource_versions` backfill + HLS fields | Phase 2 |
| `062_review_system.sql` | `review_comments` + `review_annotations` + `review_status` | Phase 4 |

---

## References

- [MediaTrack Online Review](https://www.mediatrack.cn/product/online-review) — Annotation tools, version compare, approval workflow
- [MediaTrack Side-by-Side Compare](https://mediatrack.marketup.cn/web/detail/3453587160801281) — Sync playback, text annotation tool
