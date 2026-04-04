# Douyin Carousel & Image-Text Support

## Goal

Fix Douyin multi-image (type 2) and image-text mixed (type 68) content to properly save as resources, download standalone audio, and display with a slide player.

## Problem

1. Carousel downloads work but **no resource record** is created (only parsed_media)
2. Carousel music is **standalone audio** (not embedded in video) — currently not downloaded
3. Frontend has **no player** for multi-image content

## Content Types

| aweme_type | Content | Current | Fix |
|---|---|---|---|
| 0/4/61 | Standard video | ✅ Works | — |
| 2 | Image carousel (photos + standalone music) | ⚠️ Downloads OK, no resource | Create resource + download audio |
| 68 | Image-text mixed (photos + short clips + music) | ⚠️ Downloads OK, no resource | Create resource + download audio |

## Storage Structure

```
global/resources/web/{platform}/{media_id}/
  ├── slides/                       # Carousel content folder
  │   ├── 001.jpg                   # Static image
  │   ├── 002.jpg                   # Static image
  │   ├── 003.mp4                   # Short clip (type 68)
  │   └── 004.jpg                   # Static image
  ├── audio.mp3                     # Background music (loop)
  └── cover.jpg                     # Cover image
```

Files in `slides/` are numbered sequentially (001, 002...) preserving original order.
Extension determined by actual content type (.jpg/.png/.webp for images, .mp4 for video clips).

## Backend Changes

### 1. `backend/app/services/downloader.py`

**`download_images_by_platform_id()`**:
- Save images and video clips into `slides/` subfolder (currently saves to root)
- Rename files to sequential numbering: `001.jpg`, `002.mp4`, etc.
- After download completes, create resource record via existing backfill logic:
  - `file_path` → folder path (e.g., `global/resources/web/douyin/{media_id}`)
  - `mime_type` → `image/jpeg` (primary type)
  - `media_id` → parsed_media ID
  - `source_type` → `web`
  - `creator_id` → user_id

**Standalone music download**:
- For type 2/68, extract `music.play_url` from `aweme_detail`
- Download directly to `{media_id}/audio.mp3` (no FFmpeg extraction)
- Update `music_download_status` and `music_download_path`

### 2. `backend/app/services/douyin_parser.py`

- Ensure `music.play_url` is extracted and stored in parsed_media
- Add field `music_play_url` to parsed data if not already present

### 3. Backend API — slides listing endpoint

Add endpoint to list slides in a resource folder:
```
GET /api/v1/resources/{resource_id}/slides
→ [{ name: "001.jpg", type: "image" }, { name: "002.mp4", type: "video" }, ...]
```

Or reuse `/media/{media_id}` with a query param to list folder contents.

## Frontend Changes

### 1. `frontend/components/SlidePlayer.tsx` (NEW)

Carousel player component:
- Reads slide list from API
- Image slides: full-screen display with swipe/arrow navigation
- Video slides: auto-play (muted, since background music plays separately)
- Bottom indicators (dots showing current position)
- Swipe left/right to navigate

### 2. Background music

- `<audio>` element with `loop` attribute
- Source: `audio.mp3` from resource folder
- Mute/unmute toggle button
- Plays independently of slide navigation

### 3. `frontend/pages/DownloadDetailPage.tsx` — unified layout

**Same page, adaptive player area.** Page layout stays identical (left player + right detail panel). Only the player area adapts:

```
DownloadDetailPage
  ├── Left: Player Area (adaptive)
  │   ├── media_type = video → <VideoPlayer>
  │   └── media_type = 2/68  → <SlidePlayer> (carousel + bg music)
  └── Right: Detail Panel (shared, unchanged)
      ├── Overview tab (stats, tags, notes)
      ├── Transcript tab
      └── Analysis tab
```

- Detect `media_type` from parsed_media data
- Swap `<VideoPlayer>` for `<SlidePlayer>` in the same container
- All other UI (header, detail panel, actions) stays identical

## Files Changed

| File | Change |
|---|---|
| `backend/app/services/downloader.py` | slides/ subfolder, resource creation, standalone audio download |
| `backend/app/services/douyin_parser.py` | Extract music.play_url |
| `backend/app/api/media_router.py` | Add slides listing endpoint |
| `frontend/components/SlidePlayer.tsx` | **New** — carousel + background music (used inside DownloadDetailPage) |
| `frontend/pages/DownloadDetailPage.tsx` | Adaptive player area: VideoPlayer or SlidePlayer based on media_type |

## Out of Scope

- Changing parsed_media table schema
- Changing resources table schema
- Re-parsing existing carousel content (only new downloads)
