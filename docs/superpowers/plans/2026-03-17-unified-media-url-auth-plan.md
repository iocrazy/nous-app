# Unified Media URL Building & Authentication Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate 21 duplicate `getApiUrl()` into a shared module, unify video URL building, adopt Signed URL authentication, and add professional error pages.

**Architecture:** Extract shared utilities (`apiConfig.ts`, `mediaUrl.ts`), add backend `POST /api/v1/auth/media-token` endpoint reusing existing HMAC signer, store media token in AuthContext, pass through to all URL builders. Cookie auth remains as fallback.

**Tech Stack:** React 19, TypeScript, Vite 7, FastAPI, Python HMAC, Supabase Auth, TailwindCSS, Lucide React icons

**Spec:** `docs/superpowers/specs/2026-03-17-unified-media-url-auth-design.md`

---

## Chunk 1: Frontend Utilities (Tasks 1-3)

### Task 1: Create shared `apiConfig.ts`

**Files:**
- Create: `frontend/utils/apiConfig.ts`

**Context:** All 21 files define identical `getApiUrl()` functions. One file (`mediaAuthService.ts`) returns `''` as fallback instead of `'http://localhost:8080'`. The shared module must use `'http://localhost:8080'` as default (matching 20/21 files). `mediaAuthService.ts` will handle its own empty-string case separately.

- [ ] **Step 1: Create `frontend/utils/apiConfig.ts`**

```typescript
// frontend/utils/apiConfig.ts

/**
 * Centralized API base URL accessor.
 * All frontend files should import from here instead of defining their own.
 */
export const getApiUrl = (): string => {
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    return (import.meta.env.VITE_API_URL || '').trim();
  }
  return 'http://localhost:8080';
};
```

- [ ] **Step 2: Verify build passes**

Run: `cd frontend && npx tsc --noEmit`
Expected: No new errors

- [ ] **Step 3: Commit**

```bash
git add frontend/utils/apiConfig.ts
git commit -m "feat: create shared apiConfig.ts with centralized getApiUrl()"
```

---

### Task 2: Replace all 21 local `getApiUrl()` definitions with shared import

**Files (Modify all 21):**
- `frontend/services/parserService.ts:6-13`
- `frontend/services/resourceService.ts:5-12`
- `frontend/services/dataService.ts:11-18`
- `frontend/services/tagsService.ts:10-17`
- `frontend/services/unifiedTagService.ts:11-18`
- `frontend/services/aiService.ts:28-35`
- `frontend/services/analysisService.ts:8-15`
- `frontend/services/cleanupService.ts:8-15`
- `frontend/services/searchService.ts:8-15`
- `frontend/services/apiKeyService.ts:10-17`
- `frontend/services/libraryService.ts:4-11`
- `frontend/services/projectsService.ts:4-11`
- `frontend/services/reviewService.ts:3-10`
- `frontend/services/systemService.ts:9-14`
- `frontend/services/projectTasksService.ts:3-10`
- `frontend/services/smartCollectionService.ts:9-16`
- `frontend/services/mediaAuthService.ts:11-16`
- `frontend/utils/awemeType.ts:71-78`
- `frontend/components/LogsPanel.tsx:49-56`
- `frontend/components/SystemMonitorPanel.tsx:25-32`
- `frontend/components/VideoReviewPage.tsx:22-29`

**For each file, the change is:**

1. Add import at top: `import { getApiUrl } from '../utils/apiConfig';`
   - For files in `utils/`: `import { getApiUrl } from './apiConfig';`
2. Delete the local `getApiUrl` function definition (7-8 lines)

**Special case — `mediaAuthService.ts`:** This file's `getApiUrl` returns `''` instead of `'http://localhost:8080'`. After importing the shared version, the empty-string behavior for relative paths is no longer needed (the shared version returns the correct base URL). No special handling required.

- [ ] **Step 1: Replace in all 18 service files**

For each file in `frontend/services/`:

```typescript
// DELETE these lines (the local getApiUrl function):
const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return (import.meta.env.VITE_API_URL || '').trim();
  }
  return 'http://localhost:8080';
};

// ADD this import at the top of the file (after other imports):
import { getApiUrl } from '../utils/apiConfig';
```

Note: `systemService.ts` and `mediaAuthService.ts` have slightly different bodies (no `@ts-ignore`, different fallback) — same replacement applies.

- [ ] **Step 2: Replace in `frontend/utils/awemeType.ts`**

Delete lines 71-78 (the local `getApiUrl` function). Add at the top:
```typescript
import { getApiUrl } from './apiConfig';
```

- [ ] **Step 3: Replace in 3 component files**

For `LogsPanel.tsx`, `SystemMonitorPanel.tsx`, `VideoReviewPage.tsx`:

Delete the local `getApiUrl` function. Add:
```typescript
import { getApiUrl } from '../utils/apiConfig';
```

- [ ] **Step 4: Verify build passes**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS — no errors

- [ ] **Step 5: Verify dev server starts**

Run: `cd frontend && npm run dev` (quick manual check that the app loads)

- [ ] **Step 6: Commit**

```bash
git add frontend/utils/apiConfig.ts frontend/services/*.ts frontend/utils/awemeType.ts frontend/components/LogsPanel.tsx frontend/components/SystemMonitorPanel.tsx frontend/components/VideoReviewPage.tsx
git commit -m "refactor: replace 21 local getApiUrl() with shared apiConfig import"
```

---

### Task 3: Create unified `mediaUrl.ts`

**Files:**
- Create: `frontend/utils/mediaUrl.ts`

- [ ] **Step 1: Create `frontend/utils/mediaUrl.ts`**

```typescript
// frontend/utils/mediaUrl.ts
import { getApiUrl } from './apiConfig';

/**
 * Check if a URL is already absolute (http/https).
 */
const isAbsoluteUrl = (url: string): boolean =>
  url.startsWith('http://') || url.startsWith('https://');

/**
 * Append a signed media token as a query parameter.
 */
const appendToken = (url: string, token?: string): string => {
  if (!token) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}token=${encodeURIComponent(token)}`;
};

/**
 * Convert a relative or absolute file path to a backend /media/ URL.
 *
 * Supports:
 * - Already-absolute URLs (returned as-is)
 * - Relative paths: "2026-01/video.mp4" → "{apiUrl}/media/2026-01/video.mp4"
 * - Absolute paths: "/data/media/2026-01/video.mp4" → "{apiUrl}/media/2026-01/video.mp4"
 */
export function buildMediaUrl(path: string, token?: string): string {
  if (isAbsoluteUrl(path)) return path;

  const base = getApiUrl();
  let url: string;

  if (!path.startsWith('/')) {
    url = `${base}/media/${path}`;
  } else {
    const match = path.match(/(\d{4}-\d{2}\/.+)$/);
    url = match ? `${base}/media/${match[1]}` : `${base}/media${path}`;
  }

  return appendToken(url, token);
}

/**
 * Get HLS stream URL.
 */
export function buildStreamUrl(hlsPath: string): string {
  return `${getApiUrl()}/stream/${hlsPath}`;
}

/**
 * Get video playback URL.
 * Priority: HLS > download_path > undefined
 */
export function getPlaybackUrl(
  data: {
    media_format?: string;
    hls_path?: string;
    download_path?: string;
  },
  token?: string,
): string | undefined {
  if (data.media_format === 'hls' && data.hls_path) {
    return buildStreamUrl(data.hls_path);
  }
  if (data.download_path && data.download_path !== '#') {
    return buildMediaUrl(data.download_path, token);
  }
  return undefined;
}

/**
 * Get cover image URL.
 * Priority: cover_download_path > cover_urls[0] > dynamic_cover_url > image_download_urls[0]
 */
export function getCoverImageUrl(
  data: {
    cover_download_path?: string;
    cover_urls?: string[];
    dynamic_cover_url?: string;
    image_download_urls?: string[];
  },
  token?: string,
): string | undefined {
  if (data.cover_download_path && data.cover_download_path !== '#') {
    return buildMediaUrl(data.cover_download_path, token);
  }
  if (data.cover_urls?.[0] && data.cover_urls[0] !== '#') {
    return data.cover_urls[0];
  }
  if (data.dynamic_cover_url && data.dynamic_cover_url !== '#') {
    return data.dynamic_cover_url;
  }
  return data.image_download_urls?.[0];
}

/**
 * Check if a URL is a direct playable media URL (not a webpage).
 */
export const isPlayableUrl = (url: string): boolean => {
  try {
    const host = new URL(url).hostname;
    const pageHosts = [
      'www.bilibili.com', 'bilibili.com',
      'www.youtube.com', 'youtube.com', 'youtu.be',
      'www.douyin.com', 'douyin.com',
      'www.tiktok.com', 'tiktok.com',
      'www.xiaohongshu.com', 'xiaohongshu.com',
      'twitter.com', 'x.com',
    ];
    return !pageHosts.includes(host);
  } catch {
    return true;
  }
};
```

- [ ] **Step 2: Verify build passes**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/utils/mediaUrl.ts
git commit -m "feat: create unified mediaUrl.ts with buildMediaUrl, getPlaybackUrl, getCoverImageUrl"
```

---

## Chunk 2: Migrate Existing URL Builders (Tasks 4-5)

### Task 4: Migrate `awemeType.ts` to delegate to `mediaUrl.ts`

**Files:**
- Modify: `frontend/utils/awemeType.ts`

**Context:** `awemeType.ts` currently has its own `convertPathToUrl`, `getStreamUrl`, `getVideoUrl`, `getCoverUrl`, `isPlayableUrl`, `formatResolution`. After this task, the URL-building functions delegate to `mediaUrl.ts`. Keep backward-compatible exports so existing callers (PlayerPage, MediaCard, LibraryFeed, CompactMediaCard, LibraryTable, DownloadsView) don't need changes yet.

- [ ] **Step 1: Rewrite `awemeType.ts`**

Replace the file content with:

```typescript
/**
 * media_type mapping (generalized from aweme_type)
 */

export const MEDIA_TYPE_MAP: Record<string | number, string> = {
  'video': 'Video',
  'carousel': 'Album',
  'image_text': 'Gallery',
  'special': 'Video',
  'short': 'Short',
  'live_clip': 'Live Clip',
  '0': 'Video',
  '2': 'Album',
  '4': 'Video',
  '61': 'Video',
  '68': 'Gallery',
  0: 'Video',
  2: 'Album',
  4: 'Video',
  61: 'Video',
  68: 'Gallery',
};

export const AWEME_TYPE_MAP = MEDIA_TYPE_MAP;

export const isVideoType = (mediaType?: string | number): boolean => {
  if (mediaType === undefined || mediaType === null) return false;
  const type = String(mediaType);
  return type === 'video' || type === 'special' || type === 'short' || type === 'live_clip' ||
         type === '0' || type === '4' || type === '61';
};

export const isAlbumType = (mediaType?: string | number): boolean => {
  if (mediaType === undefined || mediaType === null) return false;
  const type = String(mediaType);
  return type === 'carousel' || type === 'image_text' ||
         type === '2' || type === '68';
};

export const getMediaTypeLabel = (mediaType?: string | number): string => {
  if (mediaType === undefined || mediaType === null) return 'Unknown';
  return MEDIA_TYPE_MAP[mediaType] || MEDIA_TYPE_MAP[String(mediaType)] || 'Unknown';
};

export const getAwemeTypeLabel = getMediaTypeLabel;

export const formatResolution = (resolution?: string): string => {
  if (!resolution) return '';
  return resolution.replace(/:/g, 'x');
};

// --- Delegated to mediaUrl.ts ---
import {
  buildMediaUrl,
  buildStreamUrl,
  getPlaybackUrl,
  getCoverImageUrl,
  isPlayableUrl,
} from './mediaUrl';

// Re-export for backward compatibility
export { buildStreamUrl as getStreamUrl, isPlayableUrl };

/**
 * Get video playback URL (backward-compatible wrapper).
 * Callers: PlayerPage, MediaCard, LibraryFeed, CompactMediaCard, LibraryTable, DownloadsView
 */
export const getVideoUrl = (data: {
  media_format?: string;
  hls_path?: string;
  download_path?: string;
}, token?: string): string | undefined => {
  return getPlaybackUrl(data, token);
};

/**
 * Get cover image URL (backward-compatible wrapper).
 * Callers: LibraryFeed, CompactMediaCard, MediaCard, LibraryTable, DownloadsView
 */
export const getCoverUrl = (data: {
  cover_download_path?: string;
  cover_urls?: string[];
  dynamic_cover_url?: string;
  image_download_urls?: string[];
}, token?: string): string | undefined => {
  return getCoverImageUrl(data, token);
};
```

- [ ] **Step 2: Verify build passes**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS — all existing callers of `getVideoUrl`, `getCoverUrl`, `getStreamUrl`, `isPlayableUrl`, `formatResolution` still work without changes.

- [ ] **Step 3: Commit**

```bash
git add frontend/utils/awemeType.ts
git commit -m "refactor: delegate awemeType URL builders to unified mediaUrl.ts"
```

---

### Task 5: Migrate `resourceService.ts` URL builders to `mediaUrl.ts`

**Files:**
- Modify: `frontend/services/resourceService.ts`

**Context:** `resourceService.ts` defines three URL builders (lines ~549-570):
- `getResourceMediaUrl(filePath)` → builds `/media/{path}` URL
- `getResourceFileUrl(resourceId, token?)` → builds `/api/v1/resources/{id}/file` URL
- `getResourceCoverUrl(resourceId, token?)` → builds `/api/v1/resources/{id}/cover` URL

`getResourceMediaUrl` does the same as `buildMediaUrl`. Replace it. `getResourceFileUrl` and `getResourceCoverUrl` build API URLs (not `/media/` paths), so they stay but should use `getApiUrl` from `apiConfig` (already done in Task 2).

**Callers:**
- `getResourceMediaUrl`: ResourceDetail.tsx (4 calls)
- `getResourceFileUrl`: ResourceDetail.tsx (2), ResourcesView.tsx (2)
- `getResourceCoverUrl`: ResourceDetail.tsx (1), ResourceCard.tsx (1), FolderCard.tsx (1), ResourceInfoPanel.tsx (1), FolderInfoPanel.tsx (1)

- [ ] **Step 1: Update `resourceService.ts`**

Add import at top:
```typescript
import { buildMediaUrl } from '../utils/mediaUrl';
```

Replace the `getResourceMediaUrl` function (~line 549):
```typescript
// BEFORE:
export function getResourceMediaUrl(filePath: string): string {
  const apiUrl = getApiUrl();
  return `${apiUrl}/media/${filePath}`;
}

// AFTER:
export function getResourceMediaUrl(filePath: string, token?: string): string {
  return buildMediaUrl(filePath, token);
}
```

Keep `getResourceFileUrl` and `getResourceCoverUrl` unchanged (they build `/api/v1/` URLs, not `/media/` URLs).

- [ ] **Step 2: Verify build passes**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS — callers pass 1 arg, new `token` param is optional.

- [ ] **Step 3: Commit**

```bash
git add frontend/services/resourceService.ts
git commit -m "refactor: delegate getResourceMediaUrl to unified buildMediaUrl"
```

---

## Chunk 3: Backend Signed URL (Tasks 6-7)

### Task 6: Add `POST /api/v1/auth/media-token` endpoint

**Files:**
- Modify: `backend/app/api/media_auth.py`

**Context:** `media_auth.py` already has `_sign_cookie()` and `validate_media_cookie()` that produce/validate tokens in format `user_id.expires_at.signature`. We reuse these for signed URL tokens — same format, shorter TTL (4 hours vs 7 days for cookies).

- [ ] **Step 1: Add constant and endpoint to `media_auth.py`**

Add after `COOKIE_MAX_AGE` definition (line 35):

```python
MEDIA_TOKEN_MAX_AGE = 4 * 3600  # 4 hours (refreshed on every Supabase TOKEN_REFRESHED event)
```

Add new endpoint after the `delete_media_session` function (after line 148):

```python
@router.post("/media-token")
async def create_media_token(
    authorization: str = Header(...),
):
    """
    Generate a short-lived signed media token for URL-based auth.

    Used as ?token= query parameter on /media/ URLs.
    Enables <video>/<img> tags to authenticate without cookies.
    """
    try:
        token = authorization.replace("Bearer ", "")
        auth_service = SupabaseAuthService()
        user = await auth_service.get_user(token)

        if not user:
            raise HTTPException(status_code=401, detail="Invalid token")

        user_id = user.get("id")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid user")

        expires_at = int(time.time()) + MEDIA_TOKEN_MAX_AGE
        media_token = _sign_cookie(user_id, expires_at)

        return {"token": media_token, "expires_at": expires_at}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create media token: {e}")
        raise HTTPException(status_code=500, detail="Failed to create media token")
```

- [ ] **Step 2: Verify backend starts**

Run: `cd backend && uv run uvicorn app.main:app --reload --port 8080`
Expected: Server starts without import errors. Check log for registered routes.

- [ ] **Step 3: Quick manual test**

```bash
# Get a JWT token first (use existing Supabase session or sign in)
curl -X POST http://localhost:8080/api/v1/auth/media-token \
  -H "Authorization: Bearer <YOUR_JWT>" \
  -H "Content-Type: application/json"
# Expected: {"token": "uuid.timestamp.signature", "expires_at": 1742...}
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/media_auth.py
git commit -m "feat: add POST /api/v1/auth/media-token endpoint for signed URL auth"
```

---

### Task 7: Update `/media/{path}` route to accept `?token=` query param

**Files:**
- Modify: `backend/app/main.py:210-248`

**Context:** The `/media/{path}` route currently checks auth in order: `share_token` → `review_token` → cookie. We add `token` (signed media token) as highest priority, before the existing checks.

- [ ] **Step 1: Update the route handler in `main.py`**

Find the `serve_media_file` function (~line 210). Update the auth check section:

```python
@app.get("/media/{file_path:path}")
async def serve_media_file(
    file_path: str,
    request: Request,
    token: str | None = None,           # NEW: signed media token (highest priority)
    share_token: str | None = None,
    review_token: str | None = None,
):
    """Serve media files with signed URL or cookie-based auth.

    Authentication order:
    1. token query param (signed media token, preferred)
    2. share_token query param (future: validate sharing link)
    3. review_token query param (future: validate review access)
    4. media_session httpOnly cookie (legacy fallback)
    """
    import mimetypes

    from app.api.media_auth import COOKIE_NAME, validate_media_cookie

    # --- Auth check ---
    user_id = None

    # 1. Signed media token (preferred)
    if token:
        user_id = validate_media_cookie(token)  # Same format as cookie, reuse validator

    # 2. Future: share/review tokens
    if not user_id and share_token:
        pass  # TODO: validate share token
    if not user_id and review_token:
        pass  # TODO: validate review token

    # 3. Cookie fallback
    if not user_id:
        cookie_value = request.cookies.get(COOKIE_NAME, "")
        if cookie_value:
            user_id = validate_media_cookie(cookie_value)

    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    # --- Serve file (unchanged) ---
    full_path = (_media_base_path / file_path).resolve()
    if not str(full_path).startswith(str(_media_base_path)):
        raise HTTPException(status_code=403, detail="Access denied")
    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    mime_type = mimetypes.guess_type(str(full_path))[0] or "application/octet-stream"
    return FileResponse(
        str(full_path),
        media_type=mime_type,
        headers={"Referrer-Policy": "no-referrer"},  # Prevent token leak via Referer
    )
```

- [ ] **Step 2: Verify backend starts and existing cookie auth still works**

Run: `cd backend && uv run uvicorn app.main:app --reload --port 8080`
Expected: Server starts. Existing cookie-based media access still works.

- [ ] **Step 3: Test token-based access**

```bash
# First get a media token
TOKEN=$(curl -s -X POST http://localhost:8080/api/v1/auth/media-token \
  -H "Authorization: Bearer <JWT>" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

# Then access a media file with token
curl -I "http://localhost:8080/media/2026-01/test.mp4?token=$TOKEN"
# Expected: 200 OK (or 404 if file doesn't exist — NOT 401)
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py
git commit -m "feat: accept ?token= signed URL param on /media/ route with Referrer-Policy header"
```

---

## Chunk 4: Frontend Auth Integration (Tasks 8-9)

### Task 8: Add `mediaToken` to AuthContext

**Files:**
- Modify: `frontend/contexts/AuthContext.tsx`
- Modify: `frontend/services/mediaAuthService.ts`

**Context:** AuthContext manages auth state. We add `mediaToken` state, fetch it on `SIGNED_IN` and `TOKEN_REFRESHED` events, clear on logout. The token is exposed via `useAuth()` so components can pass it to URL builders.

- [ ] **Step 1: Add `fetchMediaToken` to `mediaAuthService.ts`**

Add this function at the end of `frontend/services/mediaAuthService.ts`:

```typescript
/**
 * Fetch a signed media token for URL-based auth.
 * Returns the token string, or null on failure.
 */
export async function fetchMediaToken(accessToken: string): Promise<string | null> {
  try {
    const res = await fetch(`${getApiUrl()}/api/v1/auth/media-token`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    if (!res.ok) return null;
    const data = await res.json();
    return data.token || null;
  } catch (err) {
    console.error('Failed to fetch media token:', err);
    return null;
  }
}
```

- [ ] **Step 2: Add `mediaToken` state and refresh logic to `AuthContext.tsx`**

Add to imports:
```typescript
import { createMediaSession, deleteMediaSession, fetchMediaToken } from '../services/mediaAuthService';
```

Add state (after line 73, after `isProfileModalOpen` state):
```typescript
const [mediaToken, setMediaToken] = useState<string | null>(null);
```

Add to `AuthState` interface (after `isProfileModalOpen`):
```typescript
mediaToken: string | null;
```

In the `handleSession` function, after the `createMediaSession` block (after line 164), add media token fetch. This is the **single place** where media token is fetched — `handleSession` is already called on both `SIGNED_IN` (via `getSession`) and `TOKEN_REFRESHED` (via `onAuthStateChange`), so no duplicate call is needed:

```typescript
// Fetch signed media token for URL-based auth (non-blocking)
if (session.access_token) {
  fetchMediaToken(session.access_token).then(token => {
    if (token) setMediaToken(token);
  });
}
```

**Note:** The `onAuthStateChange` handler already calls `await handleSession(session)` for both `SIGNED_IN` and `TOKEN_REFRESHED` events — no additional `fetchMediaToken` call needed there.

In `handleLogout`, add:
```typescript
setMediaToken(null);
```

Add `mediaToken` to the `value` object:
```typescript
const value: AuthContextValue = {
  // ... existing fields ...
  mediaToken,
  // ... existing methods ...
};
```

- [ ] **Step 3: Verify build passes**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/services/mediaAuthService.ts frontend/contexts/AuthContext.tsx
git commit -m "feat: add mediaToken to AuthContext, refresh on login and TOKEN_REFRESHED"
```

---

### Task 9: Wire `mediaToken` through to URL builders in key pages

**Files:**
- Modify: `frontend/pages/PlayerPage.tsx`
- Modify: `frontend/components/ResourceDetail.tsx`
- Modify: `frontend/components/MediaCard.tsx`
- Modify: `frontend/components/CompactMediaCard.tsx`
- Modify: `frontend/components/LibraryFeed.tsx`
- Modify: `frontend/components/LibraryTable.tsx`
- Modify: `frontend/components/DownloadsView.tsx`
**Context:** All these files call `getVideoUrl()`, `getCoverUrl()`, or `getResourceMediaUrl()`. Now that those functions accept an optional `token` param, we pass `mediaToken` from `useAuth()`.

**Note:** `ResourceCard.tsx`, `FolderCard.tsx`, `ResourceInfoPanel.tsx`, `FolderInfoPanel.tsx` use `getResourceCoverUrl()` which builds `/api/v1/` URLs (not `/media/` URLs). Those endpoints handle auth via headers, so they do NOT need `mediaToken`. Only files using `getVideoUrl`, `getCoverUrl`, or `getResourceMediaUrl` need changes.

**Pattern for each file:**

1. Add `const { mediaToken } = useAuth();` (if not already using `useAuth`)
2. Pass `mediaToken` as second argument to `getVideoUrl(data, mediaToken)`, `getCoverUrl(data, mediaToken)`, `getResourceMediaUrl(path, mediaToken)`

- [ ] **Step 1: Update `PlayerPage.tsx`**

Already imports `useAuth`. Find calls to `getVideoUrl(video)` (lines ~529-532) and add `mediaToken`:

```typescript
const { mediaToken } = useAuth();
// ...
getVideoUrl(video, mediaToken ?? undefined)
```

- [ ] **Step 2: Update `ResourceDetail.tsx`**

Already imports `useAuth`. Find calls to `getResourceMediaUrl()` (lines ~501, 509, 518, 529) and add token:

```typescript
const { mediaToken } = useAuth();
// ...
getResourceMediaUrl(filePath, mediaToken ?? undefined)
```

- [ ] **Step 3: Update `MediaCard.tsx`**

Add `useAuth` import if not present. Update:
```typescript
const { mediaToken } = useAuth();
const videoUrl = getVideoUrl(data, mediaToken ?? undefined);
const coverUrl = getCoverUrl(data, mediaToken ?? undefined);
```

- [ ] **Step 4: Update `CompactMediaCard.tsx`**

```typescript
const { mediaToken } = useAuth();
const videoUrl = isVideo ? getVideoUrl(data, mediaToken ?? undefined) : undefined;
// getCoverUrl(data, mediaToken ?? undefined)
```

- [ ] **Step 5: Update `LibraryFeed.tsx`**

```typescript
const { mediaToken } = useAuth();
const videoUrl = getVideoUrl(item, mediaToken ?? undefined);
const coverUrl = getCoverUrl(item, mediaToken ?? undefined);
```

- [ ] **Step 6: Update `LibraryTable.tsx`**

```typescript
const { mediaToken } = useAuth();
// All getVideoUrl and getCoverUrl calls get mediaToken
```

- [ ] **Step 7: Update `DownloadsView.tsx`**

```typescript
const { mediaToken } = useAuth();
const videoUrl = getVideoUrl(v, mediaToken ?? undefined);
// getCoverUrl(selectedVideo, mediaToken ?? undefined)
```

- [ ] **Step 8: Verify build passes**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add frontend/pages/PlayerPage.tsx frontend/components/ResourceDetail.tsx frontend/components/MediaCard.tsx frontend/components/CompactMediaCard.tsx frontend/components/LibraryFeed.tsx frontend/components/LibraryTable.tsx frontend/components/DownloadsView.tsx
git commit -m "feat: pass mediaToken to all video/cover URL builders for signed URL auth"
```

---

## Chunk 5: Error Pages (Task 10)

### Task 10: Create `ErrorPage` component and integrate

**Files:**
- Create: `frontend/components/ErrorPage.tsx`
- Modify: `frontend/components/VideoPlayer.tsx`

**Context:** No error page components exist. VideoPlayer currently shows `<p className="text-zinc-400 text-sm">{loadError}</p>`. We create a reusable `ErrorPage` and integrate auth-aware error handling into VideoPlayer.

- [ ] **Step 1: Create `frontend/components/ErrorPage.tsx`**

```tsx
import { Lock, ShieldX, FileQuestion, AlertTriangle } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';

interface ErrorPageProps {
  code: 401 | 403 | 404 | 500;
  onAction?: () => void;
  onSecondaryAction?: () => void;
  className?: string;
}

const ERROR_CONFIG = {
  401: {
    icon: Lock,
    title: 'Sign in to continue',
    description: 'You need to be signed in to view this content.',
    cta: 'Sign In',
  },
  403: {
    icon: ShieldX,
    title: 'Access restricted',
    description: "You don't have permission to view this content.",
    cta: 'Go Home',
  },
  404: {
    icon: FileQuestion,
    title: 'Page not found',
    description: "This page doesn't exist or has been removed.",
    cta: 'Go Home',
  },
  500: {
    icon: AlertTriangle,
    title: 'Something went wrong',
    description: 'An unexpected error occurred. Please try again.',
    cta: 'Retry',
  },
};

export default function ErrorPage({ code, onAction, onSecondaryAction, className = '' }: ErrorPageProps) {
  const { isAuthenticated, setShowAuthModal } = useAuth();

  // Security: unauthenticated users see 401 for both 403 and 404
  const effectiveCode = !isAuthenticated && (code === 403 || code === 404) ? 401 : code;
  const config = ERROR_CONFIG[effectiveCode];
  const Icon = config.icon;

  const handleAction = () => {
    if (effectiveCode === 401) {
      setShowAuthModal(true);
    } else if (onAction) {
      onAction();
    } else {
      window.location.href = '/';
    }
  };

  return (
    <div className={`flex items-center justify-center min-h-[400px] ${className}`}>
      <div className="text-center max-w-md px-6">
        <div className="flex justify-center mb-6">
          <div className="w-16 h-16 rounded-full bg-zinc-800/50 flex items-center justify-center">
            <Icon className="w-8 h-8 text-zinc-400" />
          </div>
        </div>
        <h2 className="text-xl font-semibold text-zinc-100 mb-2">
          {config.title}
        </h2>
        <p className="text-zinc-400 text-sm mb-6">
          {config.description}
        </p>
        <div className="flex items-center justify-center gap-3">
          <button
            onClick={handleAction}
            className="px-5 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors"
          >
            {config.cta}
          </button>
          {effectiveCode === 500 && onSecondaryAction && (
            <button
              onClick={onSecondaryAction}
              className="px-5 py-2 bg-zinc-700 hover:bg-zinc-600 text-zinc-200 text-sm font-medium rounded-lg transition-colors"
            >
              Go Home
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify build passes**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 3: Integrate into `VideoPlayer.tsx` — add 401 retry logic**

Read `VideoPlayer.tsx` first to find the error handling section. Add logic:

1. When video fails to load, check if the error might be auth-related (the `<video>` element's error event doesn't give HTTP status, so we do a HEAD request to check):

```typescript
import ErrorPage from './ErrorPage';
import { useAuth } from '../contexts/AuthContext';
import { fetchMediaToken } from '../services/mediaAuthService';

// Inside the component:
const { mediaToken, isAuthenticated } = useAuth();
const [authError, setAuthError] = useState<401 | 403 | null>(null);

// In the error handler:
const handleVideoError = async () => {
  if (!src) return;
  try {
    const res = await fetch(src, { method: 'HEAD' });
    if (res.status === 401) {
      setAuthError(401);
      return;
    }
    if (res.status === 403) {
      setAuthError(403);
      return;
    }
  } catch {
    // Network error — show generic error
  }
  setLoadError('Video failed to load');
};

// In the render:
if (authError) {
  return <ErrorPage code={authError} />;
}
```

**Note:** The exact integration depends on VideoPlayer's current structure. The implementer should read the file first, then add the error checking at the appropriate point in the existing error flow. The key principle: on video load failure, do a HEAD request to distinguish 401/403 from other errors.

- [ ] **Step 4: Verify build passes**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/components/ErrorPage.tsx frontend/components/VideoPlayer.tsx
git commit -m "feat: add ErrorPage component with auth-aware error handling in VideoPlayer"
```

---

## Final Verification

- [ ] **Step 1: Full build check**

```bash
cd frontend && npm run build
```
Expected: Build succeeds with no errors.

- [ ] **Step 2: Dev server smoke test**

```bash
cd frontend && npm run dev
```
- Navigate to Downloads page → videos should play
- Navigate to Resources page → videos should play
- Check browser DevTools Network tab: `/media/` requests should have `?token=` parameter
- Check that cookie-based auth still works (clear the token and verify cookie fallback)

- [ ] **Step 3: Final commit (if any cleanup needed)**

```bash
git status
# Review changes, then add specific files:
git add <changed-files>
git commit -m "chore: final cleanup for unified media URL and auth system"
```

**Rollback note:** If signed URL auth causes issues in production, revert Task 9 changes (remove `mediaToken` from URL builder calls) to fall back to cookie-only auth. The backend accepts both token and cookie, so cookie auth continues working.
