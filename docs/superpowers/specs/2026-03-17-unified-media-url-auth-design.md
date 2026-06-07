# Unified Media URL Building & Authentication Design

## Goal

Consolidate 21 duplicate `getApiUrl()` definitions into a single shared module, unify two divergent video URL building strategies (Downloads/PlayerPage vs Resources/ResourceDetail), adopt Signed URL authentication (industry standard), and add professional error pages for auth failures.

## Background

### Current Problems

1. **`getApiUrl()` duplication**: 21 files each define their own copy. A recent bug (trailing `\n` in `VITE_API_URL`) required patching all 21 independently.
2. **Two URL building strategies**: `awemeType.ts` handles Downloads (PlayerPage) with `convertPathToUrl()` + `getVideoUrl()`. `resourceService.ts` handles Resources (ResourceDetail) with `getResourceMediaUrl()` + `getResourceFileUrl()`. Both do the same thing differently.
3. **Cookie auth fragility**: httpOnly cookie scoped to `/media` path breaks on iOS Safari cross-origin, and Vercel proxy doesn't forward cookies reliably.
4. **No error pages**: 401/403/404/500 all show generic inline text. No dedicated error page components exist.

### Why Signed URL

| Approach | Pros | Cons |
|----------|------|------|
| Cookie | Browser sends automatically | Cross-origin issues, iOS Safari problems, hard to share |
| Bearer Header | Standard for APIs | `<video>`/`<img>` tags can't send headers |
| **Signed URL** | Works everywhere, shareable, no cookie/CORS issues | Token visible in URL (mitigated by short TTL) |

Used by: YouTube, Vimeo, AWS CloudFront, Google Cloud CDN, Cloudflare R2.

## Architecture

### New Files

| File | Purpose |
|------|---------|
| `frontend/utils/apiConfig.ts` | Single `getApiUrl()` definition |
| `frontend/utils/mediaUrl.ts` | Unified media URL builder with token support |
| `frontend/components/ErrorPage.tsx` | Professional error page component (401/403/404/500) |
| `backend/app/api/media_token.py` | `POST /api/v1/auth/media-token` endpoint (or extend `media_auth.py`) |

### Modified Files

| File | Change |
|------|--------|
| 21 files with `getApiUrl()` | Replace local definition with import from `apiConfig.ts` |
| `frontend/utils/awemeType.ts` | `getVideoUrl()`/`convertPathToUrl()` delegate to `mediaUrl.ts` |
| `frontend/services/resourceService.ts` | `getResourceMediaUrl()` delegates to `mediaUrl.ts`; all callsites updated to pass `token` |
| `frontend/contexts/AuthContext.tsx` | Store media token in state, refresh on `TOKEN_REFRESHED` |
| `backend/app/main.py` | `/media/{path}` route accepts `?token=` query param alongside cookie |
| `frontend/components/ResourceDetail.tsx` | Use unified `getPlaybackUrl()` with token |
| `frontend/pages/PlayerPage.tsx` | Use unified `getPlaybackUrl()` with token |
| `frontend/components/VideoPlayer.tsx` | Auth-aware error handling, show ErrorPage on 401 |

## Detailed Design

### 1. Shared `getApiUrl()` Module

```typescript
// frontend/utils/apiConfig.ts
const getApiUrl = (): string => {
  return (import.meta.env.VITE_API_URL || '').trim();
};
export { getApiUrl };
```

All 21 files replace their local `getApiUrl()` with:
```typescript
import { getApiUrl } from '../utils/apiConfig';
```

### 2. Unified Media URL Builder

```typescript
// frontend/utils/mediaUrl.ts
import { getApiUrl } from './apiConfig';

/**
 * Convert a relative or absolute file path to a backend media URL.
 * Optionally appends a signed token for authentication.
 */
export function buildMediaUrl(path: string, token?: string): string {
  if (path.startsWith('http://') || path.startsWith('https://')) return path;

  const base = getApiUrl();
  let url: string;

  if (!path.startsWith('/')) {
    // Relative path: 2026-01/xxx.mp4
    url = `${base}/media/${path}`;
  } else {
    // Absolute path: extract year-month pattern
    const match = path.match(/(\d{4}-\d{2}\/.+)$/);
    url = match ? `${base}/media/${match[1]}` : `${base}/media${path}`;
  }

  if (token) {
    const sep = url.includes('?') ? '&' : '?';
    url += `${sep}token=${encodeURIComponent(token)}`;
  }
  return url;
}

/** Get HLS stream URL */
export function buildStreamUrl(hlsPath: string): string {
  return `${getApiUrl()}/stream/${hlsPath}`;
}

/**
 * Get video playback URL.
 * Priority: HLS > download_path > undefined
 */
export function getPlaybackUrl(data: {
  media_format?: string;
  hls_path?: string;
  download_path?: string;
}, token?: string): string | undefined {
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
export function getCoverImageUrl(data: {
  cover_download_path?: string;
  cover_urls?: string[];
  dynamic_cover_url?: string;
  image_download_urls?: string[];
}, token?: string): string | undefined {
  if (data.cover_download_path && data.cover_download_path !== '#') {
    return buildMediaUrl(data.cover_download_path, token);
  }
  if (data.cover_urls?.[0] && data.cover_urls[0] !== '#') {
    return data.cover_urls[0]; // External CDN URL, no token needed
  }
  if (data.dynamic_cover_url && data.dynamic_cover_url !== '#') {
    return data.dynamic_cover_url;
  }
  return data.image_download_urls?.[0];
}
```

**Backward compatibility**: `awemeType.ts` keeps `getVideoUrl` and `getCoverUrl` as thin wrappers calling `getPlaybackUrl` and `getCoverImageUrl`.

### 3. Signed URL Authentication

#### Backend: Token Generation

Extend `backend/app/api/media_auth.py`:

```python
MEDIA_TOKEN_MAX_AGE = 4 * 3600  # 4 hours (refreshed on every Supabase TOKEN_REFRESHED event, ~1hr)

@router.post("/media-token")
async def create_media_token(
    authorization: str = Header(...),
):
    """Generate a signed media token for URL-based auth."""
    token = authorization.replace("Bearer ", "")
    auth_service = SupabaseAuthService()
    user = await auth_service.get_user(token)
    if not user or not user.get("id"):
        raise HTTPException(status_code=401, detail="Invalid token")

    user_id = user["id"]
    expires_at = int(time.time()) + MEDIA_TOKEN_MAX_AGE
    media_token = _sign_cookie(user_id, expires_at)  # Reuse existing HMAC signer

    return {"token": media_token, "expires_at": expires_at}
```

#### Backend: Token Validation on `/media/` Route

In `main.py`, the `/media/{path}` handler adds token check:

```python
# Check auth: token > share_token > review_token > cookie > reject
token = request.query_params.get("token")
user_id = None
if token:
    user_id = validate_media_cookie(token)  # Same format, reuse validator
if not user_id:
    # Future: share_token and review_token validation (stubs exist)
    share_token = request.query_params.get("share_token")
    review_token = request.query_params.get("review_token")
    # TODO: validate share/review tokens when sharing feature is implemented
if not user_id:
    cookie = request.cookies.get("media_session")
    if cookie:
        user_id = validate_media_cookie(cookie)
if not user_id:
    raise HTTPException(status_code=401, detail="Unauthorized")
```

**Note**: Existing `share_token` and `review_token` query param stubs in `main.py` are preserved. The new `token` param takes priority. When the sharing feature is implemented, `share_token`/`review_token` will use separate validation logic (not HMAC user tokens).

#### Frontend: Token Lifecycle in AuthContext

```typescript
// In AuthContext.tsx
const [mediaToken, setMediaToken] = useState<string | null>(null);

// After login / token refresh:
const fetchMediaToken = async (accessToken: string) => {
  const res = await fetch(`${getApiUrl()}/api/v1/auth/media-token`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (res.ok) {
    const { token } = await res.json();
    setMediaToken(token);
  }
};
```

Exposed via `useAuth()` as `mediaToken`.

**Token Refresh Strategy**: `fetchMediaToken()` is called on:
1. Initial login (`SIGNED_IN` event)
2. Every `TOKEN_REFRESHED` event (~1 hour interval from Supabase)
3. On 401 retry in VideoPlayer (fallback if token expired between refreshes)

This ensures the 4-hour media token is refreshed well before expiry. If a user stays on a page for 4+ hours without any Supabase activity, the VideoPlayer retry mechanism catches the expired token and triggers a re-fetch.

### 4. Error Pages

```typescript
// frontend/components/ErrorPage.tsx
interface ErrorPageProps {
  code: 401 | 403 | 404 | 500;
  onAction?: () => void;
}
```

| Code | Icon | Title | Description | CTA |
|------|------|-------|-------------|-----|
| 401 | Lock | Sign in to continue | You need to be signed in to view this content. | Sign In |
| 403 | ShieldX | Access restricted | You don't have permission to view this content. | Go Home |
| 404 | FileQuestion | Page not found | This page doesn't exist or has been removed. | Go Home |
| 500 | AlertTriangle | Something went wrong | An unexpected error occurred. Please try again. | Retry / Go Home |

**Design**: Dark background (`bg-zinc-950`), centered card with icon, title, description, and CTA button. Consistent with existing app dark theme.

**Security**: For unauthenticated users, 403 and 404 both show 401 "Sign in to continue" (don't reveal if resource exists). For authenticated users, 403 shows "Access restricted" and 404 shows "Page not found" normally.

**Known Risks (Signed URL)**:
- Tokens appear in server access logs → media responses should not be logged to `api_request_logs`
- Tokens in Referer header → media responses set `Referrer-Policy: no-referrer`
- Browser history: not an issue since media URLs are only used in `<video src>`/`<img src>`, not navigation

### 5. Migration Strategy

1. **Phase 1**: Extract `getApiUrl()` → all 21 files import from `apiConfig.ts`
2. **Phase 2**: Create `mediaUrl.ts`, migrate `awemeType.ts` and `resourceService.ts` to use it
3. **Phase 3**: Add `POST /api/v1/auth/media-token` endpoint, update `/media/` route to accept `?token=`
4. **Phase 4**: Add `mediaToken` to AuthContext, wire through to URL builders
5. **Phase 5**: Create ErrorPage component, integrate into VideoPlayer and ResourceDetail
6. **Phase 6**: Cookie auth remains as fallback; can be removed later after Signed URL is stable

### 6. Scope Exclusions

- Feature alignment between Downloads and Resources pages (different data sources, different use cases)
- Share/review token system (future work, but Signed URL infrastructure enables it)
- HLS stream authentication — `/stream/` route is currently unauthenticated and served by Nginx. This is an accepted gap for now; HLS segments are ephemeral and not directly browsable. Will be addressed separately if needed.
- Removing cookie auth entirely (kept as fallback)

## Success Criteria

- Single `getApiUrl()` definition used by all frontend files
- Single `buildMediaUrl()` / `getPlaybackUrl()` used by both PlayerPage and ResourceDetail
- Video playback works on iOS Safari without cookie issues
- Professional error pages for 401/403/404/500
- Cookie auth still works as fallback
- No regression in existing video playback (Downloads or Resources)
