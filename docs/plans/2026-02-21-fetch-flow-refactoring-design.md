# Fetch Flow Refactoring Design

## Problem

Current `POST /api/v1/videos/fetch` is a monolithic endpoint that handles both first-time parsing AND subsequent downloads. Every call re-parses the original URL (even when metadata already exists), only video downloads have dedup protection, and when `music_download_urls` is NULL/expired the download silently fails.

## Goals

1. **Split endpoints** — separate first-time parse from subsequent per-type fetch
2. **Full dedup** — all types (video/music/cover/image) get Orchestrator dedup protection
3. **Auto re-parse** — when download URLs are missing or expired, automatically re-parse to get fresh URLs
4. **Unified Celery** — all downloads go through Celery (no change to current approach)
5. **Structured logging** — every major step gets a log line for auditability

## Endpoint Design

### `POST /api/v1/videos/fetch` (first-time parse)

**When**: User submits a new URL from the Parser page.

**Flow** (unchanged from current, minus the bugs fixed):
1. Extract valid URL
2. Points check
3. Platform detection (douyin / ytdlp)
4. Parse metadata (LightHTTP → BrowserAuto fallback)
5. `save_metadata_only()` → write `parsed_media` + `resources`
6. Per-type Orchestrator dedup check (NEW: all types, not just video)
7. Dispatch `download_unified_task.delay()` for non-deduped types
8. Return `{platform_id, task_id, ...metadata}`

**Request body** (unchanged):
```json
{
  "url": "https://...",
  "video_bool": true,
  "music_bool": false,
  "cover_bool": true
}
```

### `POST /api/v1/videos/{platform_id}/fetch` (subsequent fetch)

**When**: User clicks Fetch Video / Fetch Audio / Fetch Cover buttons on PlayerPage.

**Flow**:
1. Load `parsed_media` by `platform_id` (404 if not found)
2. Points check
3. Load/create user `resources` record
4. Per-type Orchestrator dedup check
5. URL availability check — if URLs missing/empty for requested types → **auto re-parse** `original_url`
6. Dispatch `download_unified_task.delay()` for non-deduped types
7. Return `{task_id, types_submitted, types_skipped, types_cached}`

**Request body**:
```json
{
  "types": ["video", "music", "cover"]
}
```

### `POST /api/v1/videos/retry/{platform_id}` (retry)

Refactor to internally use the same logic as `/{platform_id}/fetch` with status reset:
1. Reset `*_download_status` to `pending` for requested types (both `parsed_media` and `resources`)
2. Clear dedup locks for requested types
3. Dispatch download (same as `/{platform_id}/fetch`)

## Full-Type Dedup

### Current

Only video gets a dedup key: `task:download:{platform_id}`.

### New

Each type gets its own dedup key:

```
task:download:video:{platform_id}
task:download:music:{platform_id}
task:download:cover:{platform_id}
task:download:image:{platform_id}
```

The `acquire_or_subscribe` call happens per-type:

```python
types_to_download = []
for media_type in requested_types:
    result = await orchestrator.acquire_or_subscribe(
        task_type=f"download:{media_type}",
        dedup_identifier=platform_id,
        user_id=user_id,
        resource_id=resource_id,
    )
    if result["action"] == "created":
        types_to_download.append(media_type)
    elif result["action"] == "subscribed":
        types_subscribed.append(media_type)
    # "completed" → skip

if types_to_download:
    download_unified_task.delay(
        platform_id=platform_id,
        user_id=user_id,
        download_video="video" in types_to_download,
        download_music="music" in types_to_download,
        download_cover="cover" in types_to_download,
        ...
    )
```

## Auto Re-parse on Missing/Expired URLs

### Trigger Conditions

- `*_download_urls` field is NULL or empty array
- Download attempt returns HTTP 403/410 (URL expired)

### Re-parse Strategy

Inside the Celery task (`download_unified_task`), before actual download:

```python
media = get_from_db(platform_id)

# Check if needed URLs are available
if need_music and not media.get("music_download_urls"):
    logger.info(f"[Download/URL] music URLs missing for {platform_id}, re-parsing...")
    aweme_detail = await LightweightParser.parse(media["original_url"])
    if aweme_detail:
        new_parsed = await DouyinParser.parse_aweme_detail(aweme_detail, ...)
        new_urls = new_parsed.get("music_download_urls")
        if new_urls:
            update_db(platform_id, {"music_download_urls": new_urls})
            media["music_download_urls"] = new_urls
        else:
            results["music"] = "failed"  # genuinely no music
            logger.warning(f"[Download/URL] No music URLs after re-parse for {platform_id}")
```

Same pattern for video/cover/image URLs.

On HTTP 403/410 during download, retry with re-parse:
```python
try:
    download_file(url)
except HTTPError as e:
    if e.status in (403, 410):
        logger.info(f"[Download/URL] URL expired, triggering re-parse...")
        # Re-parse and retry once
```

## Structured Logging

Every major step gets a tagged log line:

| Tag | When | Example |
|-----|------|---------|
| `[Fetch/Parse]` | First-time parse entry | `User X parsing URL, platform=douyin, method=LightHTTP` |
| `[Fetch/Save]` | Metadata saved | `platform_id=X, is_new=true, dedup_hit=false` |
| `[Download/Init]` | Download request entry | `platform_id=X, types=[video,music], user=Y` |
| `[Download/Dedup]` | Per-type dedup result | `video=created, music=subscribed, cover=completed` |
| `[Download/URL]` | URL availability check | `music: urls_missing=true, re_parse=triggered` |
| `[Download/Exec]` | Actual download start/end | `video: downloading... → completed (3.2s)` |
| `[Download/DB]` | Status updates | `parsed_media.video_download_status: pending→completed` |
| `[Download/Done]` | Task summary | `completed: video,cover / failed: music / skipped: image` |

## Frontend Changes

### parserService.ts

New function:
```typescript
export const fetchMediaByType = async (
  platformId: string,
  types: string[],
): Promise<FetchResponse> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/videos/${platformId}/fetch`, {
    method: 'POST',
    headers: await buildHeaders(),
    body: JSON.stringify({ types }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
  return response.json();
};
```

### PlayerPage.tsx

`handleFetchMedia` changes from:
```typescript
await parseShareLink(video.original_url, { video_bool, music_bool, cover_bool });
```
to:
```typescript
await fetchMediaByType(video.platform_id, types);
```

## Files Affected

| File | Change |
|------|--------|
| `backend/app/api/media_router.py` | Add `/{platform_id}/fetch` endpoint; refactor existing `/fetch` |
| `backend/app/services/task_orchestrator.py` | Dedup key format change; no API change |
| `backend/app/tasks/download_tasks.py` | Add URL check + auto re-parse before download |
| `backend/app/schemas/media.py` | Add `MediaDownloadRequest` schema (types array) |
| `frontend/services/parserService.ts` | Add `fetchMediaByType()` |
| `frontend/pages/PlayerPage.tsx` | `handleFetchMedia` calls new endpoint |

## Non-Goals

- Changing Celery task structure (stays as `download_unified_task`)
- Adding WebSocket/SSE progress (existing Supabase Realtime is sufficient)
- Changing the dedup lock TTL or retry strategy
