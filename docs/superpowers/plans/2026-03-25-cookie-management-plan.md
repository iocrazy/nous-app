# Cookie Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-user cookie management so users can configure platform cookies (Douyin/Bilibili/YouTube) to improve parse quality, with parse routing that adapts based on cookie availability.

**Architecture:** New `user_cookies` table stores cookies per user per platform. Backend API exposes CRUD endpoints under `/settings/cookies`. `ytdlp_service._get_cookie_args()` reads from DB instead of filesystem. Parse routing in `media_router.py` skips yt-dlp for Douyin when no cookie is set. Frontend adds a Cookies tab in Settings with platform cards.

**Tech Stack:** PostgreSQL (Supabase), FastAPI, React + TypeScript, TailwindCSS

---

### Task 1: Database Migration

**Files:**
- Create: `supabase/migrations/108_user_cookies.sql`

- [ ] **Step 1: Write migration SQL**

```sql
-- 108_user_cookies.sql
-- Per-user cookie storage for platform authentication

CREATE TABLE IF NOT EXISTS user_cookies (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    platform VARCHAR(50) NOT NULL,
    cookie_text TEXT,
    cookie_file TEXT,
    is_valid BOOLEAN NOT NULL DEFAULT true,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, platform)
);

-- RLS
ALTER TABLE user_cookies ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can manage own cookies"
    ON user_cookies FOR ALL
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Service role full access"
    ON user_cookies FOR ALL
    USING (auth.jwt()->>'role' = 'service_role');

-- Auto-update timestamp
CREATE TRIGGER update_user_cookies_updated_at
    BEFORE UPDATE ON user_cookies
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Index for fast lookup
CREATE INDEX idx_user_cookies_user_platform ON user_cookies(user_id, platform);
```

- [ ] **Step 2: Execute migration locally**

Run: `psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -f supabase/migrations/108_user_cookies.sql`

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/108_user_cookies.sql
git commit -m "feat: add user_cookies table for per-user platform cookie storage"
```

---

### Task 2: Backend Repository

**Files:**
- Create: `backend/app/repositories/cookies_repository.py`

- [ ] **Step 1: Create repository**

```python
"""Repository for user_cookies table."""

from typing import Any, Dict, List, Optional
from loguru import logger
from app.db.supabase_client import get_async_supabase_admin


class CookiesRepository:
    """CRUD operations for user_cookies table."""

    async def _get_table(self):
        supabase = await get_async_supabase_admin()
        return supabase.table("user_cookies")

    async def get_all_by_user(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all cookie records for a user."""
        table = await self._get_table()
        result = await table.select("*").eq("user_id", user_id).execute()
        return result.data or []

    async def get_by_user_and_platform(
        self, user_id: str, platform: str
    ) -> Optional[Dict[str, Any]]:
        """Get cookie for a specific user + platform."""
        table = await self._get_table()
        result = (
            await table.select("*")
            .eq("user_id", user_id)
            .eq("platform", platform)
            .maybe_single()
            .execute()
        )
        return result.data if result else None

    async def upsert(
        self, user_id: str, platform: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Create or update cookie for a user + platform."""
        record = {
            "user_id": user_id,
            "platform": platform,
            "is_valid": True,
            "error_message": None,
            **data,
        }
        table = await self._get_table()
        result = await table.upsert(
            record, on_conflict="user_id,platform"
        ).execute()
        if result.data and len(result.data) > 0:
            logger.info(f"Cookie saved: user={user_id}, platform={platform}")
            return result.data[0]
        return None

    async def delete(self, user_id: str, platform: str) -> bool:
        """Delete cookie for a user + platform."""
        table = await self._get_table()
        result = (
            await table.delete()
            .eq("user_id", user_id)
            .eq("platform", platform)
            .execute()
        )
        deleted = bool(result.data)
        if deleted:
            logger.info(f"Cookie deleted: user={user_id}, platform={platform}")
        return deleted

    async def mark_invalid(
        self, user_id: str, platform: str, error_message: str
    ) -> None:
        """Mark a cookie as invalid after parse failure."""
        table = await self._get_table()
        await (
            table.update({"is_valid": False, "error_message": error_message})
            .eq("user_id", user_id)
            .eq("platform", platform)
            .execute()
        )
        logger.warning(
            f"Cookie marked invalid: user={user_id}, platform={platform}, error={error_message}"
        )
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/repositories/cookies_repository.py
git commit -m "feat: add CookiesRepository for user_cookies CRUD"
```

---

### Task 3: Backend API Endpoints

**Files:**
- Modify: `backend/app/api/user_settings_router.py`
- Modify: `backend/app/api/__init__.py`

- [ ] **Step 1: Add cookie endpoints to settings router**

Add to `user_settings_router.py` after existing endpoints:

```python
from app.repositories.cookies_repository import CookiesRepository

SUPPORTED_PLATFORMS = ["douyin", "bilibili", "youtube"]


@router.get("/cookies")
async def get_cookies(auth: AuthDep):
    """Get cookie status for all supported platforms."""
    try:
        repo = CookiesRepository()
        records = await repo.get_all_by_user(auth.user_id)
        records_map = {r["platform"]: r for r in records}

        cookies = []
        for platform in SUPPORTED_PLATFORMS:
            record = records_map.get(platform)
            cookies.append({
                "platform": platform,
                "has_cookie": record is not None,
                "is_valid": record["is_valid"] if record else True,
                "error_message": record.get("error_message") if record else None,
                "updated_at": record.get("updated_at") if record else None,
            })
        return {"cookies": cookies}
    except Exception as e:
        logger.error(f"Failed to get cookies: {e}")
        raise HTTPException(status_code=500, detail="Failed to get cookies")


@router.put("/cookies/{platform}")
async def set_cookie(platform: str, auth: AuthDep, request: Request):
    """Set or update cookie for a platform."""
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported platform. Must be one of: {SUPPORTED_PLATFORMS}",
        )
    try:
        body = await request.json()
        cookie_text = body.get("cookie_text")
        cookie_file = body.get("cookie_file")

        if not cookie_text and not cookie_file:
            raise HTTPException(
                status_code=400,
                detail="Either cookie_text or cookie_file must be provided",
            )

        repo = CookiesRepository()
        data = {}
        if cookie_text:
            data["cookie_text"] = cookie_text
            data["cookie_file"] = None
        if cookie_file:
            data["cookie_file"] = cookie_file
            data["cookie_text"] = None

        result = await repo.upsert(auth.user_id, platform, data)
        return {"success": True, "platform": platform}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to set cookie: {e}")
        raise HTTPException(status_code=500, detail="Failed to save cookie")


@router.delete("/cookies/{platform}")
async def delete_cookie(platform: str, auth: AuthDep):
    """Delete cookie for a platform."""
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(status_code=400, detail="Unsupported platform")
    try:
        repo = CookiesRepository()
        deleted = await repo.delete(auth.user_id, platform)
        if not deleted:
            raise HTTPException(status_code=404, detail="Cookie not found")
        return {"success": True, "platform": platform}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete cookie: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete cookie")
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/api/user_settings_router.py
git commit -m "feat: add cookie CRUD endpoints to settings router"
```

---

### Task 4: yt-dlp Cookie Integration

**Files:**
- Modify: `backend/app/services/ytdlp_service.py`

- [ ] **Step 1: Modify `_get_cookie_args()` to support per-user DB cookies**

Replace the existing `_get_cookie_args` method:

```python
@staticmethod
def _get_cookie_args(url: str, user_id: str = None) -> list[str]:
    """Return ['--cookies', '/path/to/file'] if cookies available.

    Priority: user DB cookie > filesystem cookie.
    """
    platform, _ = URLRouter.detect_platform(url)
    if not platform or platform == "unknown":
        return []

    # Priority 1: per-user cookie from database
    if user_id:
        try:
            import asyncio
            import tempfile
            from app.repositories.cookies_repository import CookiesRepository

            repo = CookiesRepository()
            loop = asyncio.get_event_loop()
            record = loop.run_until_complete(
                repo.get_by_user_and_platform(user_id, platform)
            )
            if record:
                cookie_content = record.get("cookie_file") or record.get("cookie_text")
                if cookie_content:
                    tmp = tempfile.NamedTemporaryFile(
                        mode="w", suffix=f"_{platform}.txt", delete=False
                    )
                    tmp.write(cookie_content)
                    tmp.close()
                    logger.info(f"[yt-dlp] Using DB cookie for {platform}, user={user_id}")
                    return ["--cookies", tmp.name]
        except Exception as e:
            logger.warning(f"[yt-dlp] Failed to load DB cookie: {e}")

    # Priority 2: filesystem cookie (legacy)
    from app.core.config import settings
    cookies_dir = settings.COOKIES_DIR
    if cookies_dir:
        cookie_file = os.path.join(cookies_dir, f"{platform}.txt")
        if os.path.isfile(cookie_file):
            logger.info(f"[yt-dlp] Using filesystem cookie for {platform}")
            return ["--cookies", cookie_file]

    return []
```

- [ ] **Step 2: Add `user_id` parameter to methods that call `_get_cookie_args`**

Update `fetch_metadata()`, `download_video()`, `download_audio()` to accept and pass `user_id`.

- [ ] **Step 3: Add helper to check if user has cookie for platform**

```python
@staticmethod
async def user_has_cookie(user_id: str, platform: str) -> bool:
    """Check if user has a valid cookie for the given platform."""
    from app.repositories.cookies_repository import CookiesRepository
    repo = CookiesRepository()
    record = await repo.get_by_user_and_platform(user_id, platform)
    return record is not None
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/ytdlp_service.py
git commit -m "feat: ytdlp reads per-user cookies from DB, fallback to filesystem"
```

---

### Task 5: Parse Routing — Douyin Cookie Logic

**Files:**
- Modify: `backend/app/api/media_router.py`
- Modify: `backend/app/tasks/download_tasks.py`

- [ ] **Step 1: Update media_router fetch to check cookie before yt-dlp**

In `_handle_ytdlp_fetch()` or equivalent dispatch logic, add cookie check for Douyin:

```python
from app.services.ytdlp_service import YtdlpService

# Before dispatching to yt-dlp
platform, _ = URLRouter.detect_platform(url)
has_cookie = False
if platform in ("douyin", "bilibili", "youtube"):
    has_cookie = await YtdlpService.user_has_cookie(auth.user_id, platform)

# For Douyin without cookie: skip yt-dlp entirely
if platform == "douyin" and not has_cookie:
    # Go directly to LightweightParser + DouyinParser
    return await _douyin_parse_fallback(url, auth.user_id)
```

- [ ] **Step 2: Pass `user_id` to Celery task for cookie lookup during download**

Ensure `user_id` is passed through the task chain so `ytdlp_service` can look up cookies.

- [ ] **Step 3: Mark cookie invalid on yt-dlp auth failure**

After yt-dlp failure, check if error is cookie-related:

```python
async def _mark_cookie_if_auth_failure(user_id: str, platform: str, error: str):
    """Mark cookie invalid if yt-dlp fails with auth error."""
    auth_keywords = ["login", "401", "403", "cookie", "sign in", "authenticated"]
    if any(kw in error.lower() for kw in auth_keywords):
        from app.repositories.cookies_repository import CookiesRepository
        repo = CookiesRepository()
        await repo.mark_invalid(user_id, platform, error[:200])
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/media_router.py backend/app/tasks/download_tasks.py
git commit -m "feat: cookie-aware parse routing — Douyin skips yt-dlp without cookie"
```

---

### Task 6: Frontend Cookie Service

**Files:**
- Create: `frontend/services/cookiesService.ts`

- [ ] **Step 1: Create service**

```typescript
import { getApiUrl } from '../utils/apiConfig';
import { getAuthHeaders } from './parserService';

export interface CookieStatus {
  platform: string;
  has_cookie: boolean;
  is_valid: boolean;
  error_message: string | null;
  updated_at: string | null;
}

const baseUrl = () => getApiUrl();

export const fetchCookieStatuses = async (): Promise<CookieStatus[]> => {
  const response = await fetch(`${baseUrl()}/api/v1/settings/cookies`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const data = await response.json();
  return data.cookies;
};

export const setCookie = async (
  platform: string,
  cookieText?: string,
  cookieFile?: string,
): Promise<void> => {
  const response = await fetch(`${baseUrl()}/api/v1/settings/cookies/${platform}`, {
    method: 'PUT',
    headers: {
      ...(await getAuthHeaders()),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      cookie_text: cookieText || null,
      cookie_file: cookieFile || null,
    }),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Save failed' }));
    throw new Error(err.detail || `HTTP ${response.status}`);
  }
};

export const deleteCookie = async (platform: string): Promise<void> => {
  const response = await fetch(`${baseUrl()}/api/v1/settings/cookies/${platform}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
};
```

- [ ] **Step 2: Commit**

```bash
git add frontend/services/cookiesService.ts
git commit -m "feat: add cookiesService for cookie CRUD API calls"
```

---

### Task 7: Frontend Cookies Settings Page

**Files:**
- Create: `frontend/components/CookiesSettings.tsx`
- Modify: `frontend/components/SettingsModal.tsx`
- Modify: `frontend/components/SettingsView.tsx`

- [ ] **Step 1: Create CookiesSettings component**

Build the component with:
- Three platform cards (Douyin, Bilibili, YouTube)
- Each card shows: platform name, status badge (configured/not/invalid), last updated
- Click to expand: two tabs (Paste / Upload)
- Paste tab: textarea input
- Upload tab: file drop zone for cookies.txt
- Save / Delete buttons
- Red warning for `is_valid=false` with error message

- [ ] **Step 2: Add Cookies tab to SettingsModal sidebar**

In `SettingsModal.tsx`:
- Add `'cookies'` to `SettingsTab` type
- Add `{ id: 'cookies', label: 'Cookies', icon: Cookie }` to APP_SETTINGS items
- Import `Cookie` from `lucide-react`
- Add red badge dot logic: show when any cookie has `is_valid=false`

- [ ] **Step 3: Add Cookies panel rendering in SettingsView**

```typescript
{activeTab === 'cookies' && <CookiesSettings />}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/components/CookiesSettings.tsx frontend/components/SettingsModal.tsx frontend/components/SettingsView.tsx
git commit -m "feat: add Cookies settings page with platform card UI"
```

---

### Task 8: Integration Test & Deploy

- [ ] **Step 1: Test full flow locally**

1. Start backend: `uv run uvicorn app.main:app --reload`
2. Open Settings → Cookies tab
3. Paste a Douyin cookie → Save
4. Parse a Douyin URL → should use yt-dlp with cookie
5. Delete the cookie
6. Parse same URL → should skip yt-dlp, use LightweightParser

- [ ] **Step 2: Test cookie invalidation**

1. Set an expired/invalid cookie for Douyin
2. Parse a Douyin URL → yt-dlp fails → cookie marked invalid → red dot appears
3. Fallback to LightweightParser should still work

- [ ] **Step 3: Commit all remaining changes**

```bash
git add -A
git commit -m "feat: cookie management complete — per-user platform cookies with adaptive parse routing"
```

- [ ] **Step 4: Push and deploy**

```bash
git push origin master
```
