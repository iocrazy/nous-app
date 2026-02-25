# Unified NAS File Architecture Design

## Problem

Three inconsistencies in the current NAS file storage:

1. **platform_id format**: Douyin uses pure ID (`7594434341069966618`), yt-dlp prefixes platform name (`bilibili_BV1AGZ5B9EnY`), causing redundant folder paths like `bilibili/bilibili_BV1xxx/`
2. **File naming**: Douyin uses `video.mp4` / `music.mp3`, yt-dlp uses `{platform_id}.mp4` / `{platform_id}_audio.mp3`
3. **Three path schemes coexist**: Old date-based (`2026-01/`), new parser (`global/resources/web/`), uploads (`teams/*/uploads/`)
4. **Bug**: `downloader.py:758` hardcodes `"douyin"` as platform in `download_music_by_platform_id()`

## Design Decisions

| Decision | Choice |
|----------|--------|
| Folder ID | `parsed_media.id` (Snowflake BIGINT) — not platform_id |
| Platform layer | Keep `{platform}/` for NAS browsability |
| File naming | Generic: `video.mp4` / `audio.mp3` / `cover.jpg` |
| Old files | Migrate all 98 records to new structure |
| Migration strategy | One-shot (not dual-path compat) |

## Target Path Structure

```
{DOWNLOAD_PATH}/
  global/resources/web/
    {platform}/                          # bilibili, douyin, youtube, etc.
      {parsed_media.id}/                 # Snowflake BIGINT ID
        video.mp4                        # unified naming
        audio.mp3
        cover.jpg
        hls/                             # HLS transcode output
          stream.m3u8
          480p/ 720p/ 1080p/
  teams/{scope_id}/uploads/              # user uploads (unchanged)
    {resource_id}/v{n}/{filename}
```

### Examples

Before (inconsistent):
```
global/resources/web/bilibili/bilibili_BV1AGZ5B9EnY/bilibili_BV1AGZ5B9EnY.mp4
global/resources/web/douyin/7594434341069966618/video.mp4
2026-01/7594434341069966618_some-title.mp4
```

After (unified):
```
global/resources/web/bilibili/278245204198021/video.mp4
global/resources/web/douyin/278262323012235/video.mp4
global/resources/web/douyin/278262323012236/video.mp4
```

## Code Changes

### 1. `backend/app/core/utils.py`

`create_web_resource_path(platform, identifier)` — no signature change needed, just the caller passes `str(media["id"])` instead of `platform_id`.

### 2. `backend/app/services/ytdlp_service.py`

- `download_video()`: output template `video.%(ext)s` (was `{platform_id}.%(ext)s`)
- `download_audio()`: output template `audio.%(ext)s` (was `{platform_id}_audio.%(ext)s`)
- `_find_downloaded_file()`: update to match new filenames

### 3. `backend/app/services/downloader.py`

- `download_video_by_platform_id()`: use `str(media["id"])` for path, filename stays `video.mp4`
- `download_music_by_platform_id()`:
  - Fix bug: use `media["source_platform"]` instead of hardcoded `"douyin"`
  - Rename output from `music.mp3` to `audio.mp3`
  - Use `str(media["id"])` for path
- `download_cover_by_platform_id()`: use `str(media["id"])` for path, filename stays `cover.jpg`
- `download_images_by_platform_id()`: use `str(media["id"])` for path

### 4. `backend/app/tasks/download_tasks.py`

- `_do_ytdlp_download()`: look up `parsed_media` to get Snowflake ID, pass to path function
- Update relative path construction for DB storage

### 5. DB field updates (via migration script)

For each `parsed_media` record:
- `download_path` → `global/resources/web/{source_platform}/{id}/video.mp4`
- `music_download_path` → `global/resources/web/{source_platform}/{id}/audio.mp3`
- `cover_download_path` → `global/resources/web/{source_platform}/{id}/cover.jpg`

For each `resources` record linked to migrated media:
- `file_path` → same as `parsed_media.download_path`
- `cover_image_path` → same as `parsed_media.cover_download_path`

## Migration Script

### Prerequisites
- Run on NAS where files physically exist
- Backup DB before running (`pg_dump`)
- Code deployed first (so new downloads use new format)

### Flow

```python
# Pseudocode
for media in all_parsed_media_with_download_path:
    platform = media["source_platform"] or "unknown"
    media_id = str(media["id"])

    new_dir = f"global/resources/web/{platform}/{media_id}/"
    mkdir(new_dir)

    # Move video
    if media["download_path"]:
        mv(old_path, new_dir + "video.mp4")  # or video.{ext}
        update_db("download_path", new_dir + "video.mp4")

    # Move audio
    if media["music_download_path"]:
        mv(old_audio, new_dir + "audio.mp3")
        update_db("music_download_path", new_dir + "audio.mp3")

    # Move cover
    if media["cover_download_path"]:
        mv(old_cover, new_dir + "cover.jpg")
        update_db("cover_download_path", new_dir + "cover.jpg")

    # Move HLS directory if exists
    if exists(old_hls_dir):
        mv(old_hls_dir, new_dir + "hls/")

    # Update linked resources table
    update_resources_paths(media_id, new_paths)

# Cleanup empty old directories
remove_empty_dirs("2026-*/")
remove_empty_dirs("global/resources/web/*/bilibili_*/")
```

### Safety

- **Dry-run mode**: Print planned changes without executing
- **Same-partition mv**: Instant (rename, no copy)
- **Rollback**: Keep old-path → new-path mapping in JSON log
- **Idempotent**: Skip already-migrated files
