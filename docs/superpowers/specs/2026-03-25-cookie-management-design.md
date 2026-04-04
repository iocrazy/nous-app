# Cookie Management for Platform Authentication

**Date**: 2026-03-25
**Status**: Draft
**Scope**: Backend API + Frontend UI + Parse Flow Integration

## Problem

MediaHub uses yt-dlp for multi-platform video parsing. Some platforms (Douyin, Bilibili, YouTube) return higher quality or restricted content when authenticated via cookies. Currently:

- yt-dlp reads cookies from a server-side directory (`COOKIES_DIR`), not per-user
- Douyin always attempts yt-dlp first, even though LightweightParser works without cookies
- Users have no way to provide their own cookies

## Solution

Add per-user cookie management: a `user_cookies` database table, backend API endpoints, and a frontend Settings page. The parse routing logic adapts based on whether a user has configured cookies for the target platform.

## Decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | Storage | New `user_cookies` table (per user per platform) |
| 2 | Input method | Text paste + file upload (Netscape cookies.txt) |
| 3 | Platforms | Douyin, Bilibili, YouTube (extensible) |
| 4 | Validation | No validation on save; mark `is_valid=false` on parse failure |
| 5 | Enable/disable | No toggle; cookie presence = enabled, delete = disabled |
| 6 | Douyin without cookie | Skip yt-dlp, go directly to LightweightParser + DouyinParser |
| 7 | Bilibili/YouTube without cookie | yt-dlp without cookie (unchanged) |

## Database

### Migration: `user_cookies` table

```sql
CREATE TABLE user_cookies (
    id BIGINT PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    platform VARCHAR(50) NOT NULL,  -- 'douyin', 'bilibili', 'youtube'
    cookie_text TEXT,               -- raw cookie string (paste method)
    cookie_file TEXT,               -- Netscape cookies.txt content (upload method)
    is_valid BOOLEAN NOT NULL DEFAULT true,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, platform)
);

-- RLS: users can only access their own cookies
ALTER TABLE user_cookies ENABLE ROW LEVEL SECURITY;
CREATE POLICY user_cookies_owner ON user_cookies
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- Auto-update timestamp
CREATE TRIGGER update_user_cookies_updated_at
    BEFORE UPDATE ON user_cookies
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
```

## Backend API

### Endpoints: `/api/v1/settings/cookies`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/settings/cookies` | List all platforms with cookie status |
| PUT | `/settings/cookies/{platform}` | Set/update cookie for a platform |
| DELETE | `/settings/cookies/{platform}` | Delete cookie for a platform |

### GET response

```json
{
  "cookies": [
    {
      "platform": "douyin",
      "has_cookie": true,
      "is_valid": true,
      "error_message": null,
      "updated_at": "2026-03-25T10:00:00Z"
    },
    {
      "platform": "bilibili",
      "has_cookie": false,
      "is_valid": true,
      "error_message": null,
      "updated_at": null
    }
  ]
}
```

### PUT request body

```json
{
  "cookie_text": "sessionid=xxx; csrf_token=yyy",
  "cookie_file": null
}
```

One of `cookie_text` or `cookie_file` must be provided. On save, `is_valid` resets to `true`.

### Security

- Cookie content never returned in GET responses (only `has_cookie` boolean)
- Cookies encrypted at rest (application-level encryption before DB write)
- Temporary cookie files cleaned up immediately after yt-dlp use

## Parse Routing Logic

### Current flow (all platforms)

```
URL → URLRouter → yt-dlp → [Douyin fallback] LightweightParser + DouyinParser
```

### New flow

```
URL → URLRouter → platform detection
  │
  ├─ Douyin:
  │   ├─ has_cookie? → yt-dlp(cookie) → fail → mark invalid → LightweightParser
  │   └─ no_cookie?  → skip yt-dlp → LightweightParser + DouyinParser
  │
  ├─ Bilibili:
  │   ├─ has_cookie? → yt-dlp(cookie)
  │   └─ no_cookie?  → yt-dlp(no cookie)
  │
  ├─ YouTube:
  │   ├─ has_cookie? → yt-dlp(cookie)
  │   └─ no_cookie?  → yt-dlp(no cookie)
  │
  └─ Other platforms: → yt-dlp (unchanged)
```

### Implementation changes

**`media_router.py`**: Before dispatching, query `user_cookies` for the detected platform. Pass `has_cookie` flag to download task.

**`ytdlp_service._get_cookie_args()`**: Accept `user_id` parameter. Query `user_cookies` table instead of reading from `COOKIES_DIR`. Write cookie content to a temporary file, return `["--cookies", tmp_path]`. Clean up after use.

**`download_tasks.py`**: Receive `has_cookie` flag. For Douyin without cookie, skip yt-dlp and call LightweightParser directly.

### Cookie invalidation

When yt-dlp fails with a cookie-related error (401, 403, or "login required" in stderr):
1. Update `user_cookies` row: `is_valid = false`, `error_message = <reason>`
2. Continue with fallback parser (Douyin) or return error (other platforms)

## Frontend UI

### Settings sidebar

Add **Cookies** menu item below existing items (between Tags and AI). Show red badge dot when any platform has `is_valid=false`.

### Cookies page layout

Three platform cards in a grid:

```
┌─────────────────────────┐  ┌─────────────────────────┐
│  🎵 Douyin              │  │  📺 Bilibili            │
│  ● Configured           │  │  ○ Not configured       │
│  Updated: 2026-03-25    │  │                         │
│  [Edit] [Delete]        │  │  [Configure]            │
└─────────────────────────┘  └─────────────────────────┘
┌─────────────────────────┐
│  ▶️ YouTube              │
│  ⚠ Cookie expired       │  ← red warning when is_valid=false
│  Error: 401 Unauthorized│
│  [Edit] [Delete]        │
└─────────────────────────┘
```

### Cookie input modal

Two tabs:
- **Paste**: Textarea for raw cookie string
- **Upload**: Drag & drop / file picker for cookies.txt

Cookie content is write-only — never displayed back to the user after saving.

## File changes

| File | Change |
|------|--------|
| `supabase/migrations/108_user_cookies.sql` | New table + RLS |
| `backend/app/repositories/user_cookies_repository.py` | New: CRUD for user_cookies |
| `backend/app/api/user_settings_router.py` | Add cookie endpoints |
| `backend/app/services/ytdlp_service.py` | Modify `_get_cookie_args()` to query DB |
| `backend/app/api/media_router.py` | Cookie-aware parse routing |
| `backend/app/tasks/download_tasks.py` | Pass cookie flag, skip yt-dlp for Douyin |
| `frontend/services/settingsService.ts` | New: cookie API calls |
| `frontend/components/CookiesSettings.tsx` | New: cookie management UI |
| `frontend/components/SettingsView.tsx` | Add Cookies tab |
