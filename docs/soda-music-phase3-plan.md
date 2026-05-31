# Soda Music Phase 3 — Cookie Integration Plan

> Subagent-driven, TDD. Builds on Phase 2 (PR #397). Branch `feature/soda-cookie` stacked on `feature/soda-single-track` (PR base = that branch until #397 merges to master, then rebase).

**Goal:** Replace the Phase 2 env stopgap with the existing per-user `user_cookies` table (`platform='qishui'`), add a "Soda Music" card to Settings → Cookies, and validate the cookie on save (login check → `is_valid`).

**Zero new tables / migrations** — `user_cookies` already supports any platform string via `UNIQUE(user_id, platform)`.

## Integration Map (verified)
- `user_cookies` table: `id, user_id, platform, cookie_text, cookie_file, is_valid, error_message, created_at, updated_at`; `UNIQUE(user_id, platform)`; RLS own-rows.
- `app/repositories/cookies_repository.py`: `get_by_user_and_platform(user_id, platform)`, `upsert(user_id, platform, data)` (forces is_valid=True), `mark_invalid(user_id, platform, error_message)`.
- `app/api/user_settings_router.py:266` `SUPPORTED_PLATFORMS = ["douyin","bilibili","youtube"]`; `set_cookie` PUT handler (~340-383) upserts, NO save-time validation.
- `app/services/media/parsers/soda_music/cookie_source.py`: `async get_soda_cookie(user_id) -> str` (currently env).
- `app/services/media/parsers/soda_music/soda_api.py`: `SodaApiClient(cookie, *, client_factory=None)` with async `get_me() -> dict` (returns `my_info.id`).
- Frontend `frontend/components/CookiesSettings.tsx:13-17` `PLATFORMS` array (data-driven cards); `frontend/services/cookiesService.ts` generic (no change). Status badge: has_cookie/is_valid → Configured/Invalid/Not configured.

**Platform string = `"qishui"`** (matches URLRouter + blueprint).

---

## Task 1: `cookie_source` reads `user_cookies` (platform='qishui')

**Files:** Modify `backend/app/services/media/parsers/soda_music/cookie_source.py`; Test `backend/tests/soda/test_soda_cookie_source.py` (rewrite — env behavior removed).

- [ ] Step 1 — failing test (inject a fake repo):

```python
import asyncio
import pytest
from app.services.media.parsers.soda_music.cookie_source import get_soda_cookie


class _FakeRepo:
    def __init__(self, row): self._row = row; self.calls = []
    async def get_by_user_and_platform(self, user_id, platform):
        self.calls.append((user_id, platform)); return self._row


def test_get_soda_cookie_reads_user_cookies_text():
    repo = _FakeRepo({"cookie_text": "sessionid=abc", "cookie_file": None})
    assert asyncio.run(get_soda_cookie("u1", repo=repo)) == "sessionid=abc"
    assert repo.calls == [("u1", "qishui")]


def test_get_soda_cookie_falls_back_to_cookie_file():
    repo = _FakeRepo({"cookie_text": None, "cookie_file": "ck.txt-contents"})
    assert asyncio.run(get_soda_cookie("u1", repo=repo)) == "ck.txt-contents"


def test_get_soda_cookie_empty_when_no_row():
    repo = _FakeRepo(None)
    assert asyncio.run(get_soda_cookie("u1", repo=repo)) == ""


def test_get_soda_cookie_empty_when_no_user():
    repo = _FakeRepo({"cookie_text": "x"})
    assert asyncio.run(get_soda_cookie(None, repo=repo)) == ""
```

- [ ] Step 2 — run, confirm FAIL (signature lacks `repo`; reads env).
- [ ] Step 3 — implement:

```python
"""Soda login cookie provider — reads the per-user user_cookies table."""

from __future__ import annotations

from typing import Any

SODA_COOKIE_PLATFORM = "qishui"


async def get_soda_cookie(user_id: str | None, *, repo: Any | None = None) -> str:
    """Return the Soda cookie for a user from user_cookies (empty if none)."""
    if not user_id:
        return ""
    if repo is None:
        from app.repositories.cookies_repository import CookiesRepository

        repo = CookiesRepository()
    row = await repo.get_by_user_and_platform(user_id, SODA_COOKIE_PLATFORM)
    if not row:
        return ""
    return row.get("cookie_text") or row.get("cookie_file") or ""
```

- [ ] Step 4 — run tests (4 pass) + `uv run pytest tests/soda/ -q` no regression.
- [ ] Step 5 — black/isort/ruff + commit `feat(soda): cookie_source reads user_cookies (platform=qishui)`.

---

## Task 2: Soda cookie validation helper

**Files:** Create `backend/app/services/media/parsers/soda_music/cookie_validate.py`; Test `backend/tests/soda/test_soda_cookie_validate.py`.

A login check via `get_me()`: valid cookie → `my_info.id` present → `(True, None)`; failure → `(False, error)`.

- [ ] Step 1 — failing test (fake api):

```python
import asyncio
import pytest
from app.services.media.parsers.soda_music.cookie_validate import validate_soda_cookie


class _OkApi:
    async def get_me(self): return {"my_info": {"id": "12345"}}
class _NoUserApi:
    async def get_me(self): return {"my_info": {}}
class _RaiseApi:
    async def get_me(self): raise RuntimeError("403 forbidden")


def test_validate_ok():
    assert asyncio.run(validate_soda_cookie("ck", api=_OkApi())) == (True, None)


def test_validate_no_user_id():
    ok, err = asyncio.run(validate_soda_cookie("ck", api=_NoUserApi()))
    assert ok is False and err


def test_validate_request_error():
    ok, err = asyncio.run(validate_soda_cookie("ck", api=_RaiseApi()))
    assert ok is False and "403" in err


def test_validate_empty_cookie():
    ok, err = asyncio.run(validate_soda_cookie("", api=_OkApi()))
    assert ok is False and err
```

- [ ] Step 2 — run, confirm FAIL.
- [ ] Step 3 — implement:

```python
"""Validate a Soda cookie by performing a lightweight authenticated call.

Uses /luna/pc/me (get_me): a logged-in cookie returns my_info.id. VIP /
full-stream capability is NOT checked here — that surfaces at download time as
SodaPreviewError (§A.4). Returns (is_valid, error_message)."""

from __future__ import annotations

from typing import Any

from app.services.media.parsers.soda_music.soda_api import SodaApiClient


async def validate_soda_cookie(
    cookie: str, *, api: Any | None = None
) -> tuple[bool, str | None]:
    if not cookie:
        return False, "empty cookie"
    client = api or SodaApiClient(cookie=cookie)
    try:
        me = await client.get_me()
    except Exception as exc:  # noqa: BLE001 — any failure means the cookie is unusable
        return False, str(exc)[:200]
    uid = (me.get("my_info", {}) or {}).get("id")
    if not uid:
        return False, "not logged in (no my_info.id)"
    return True, None
```

- [ ] Step 4 — run tests (4 pass) + soda suite no regression.
- [ ] Step 5 — black/isort/ruff + commit `feat(soda): cookie validation via get_me login check`.

---

## Task 3: Register `qishui` platform + validate on save

**Files:** Modify `backend/app/api/user_settings_router.py` (read 260-413 first); Test `backend/tests/soda/test_qishui_cookie_endpoint.py`.

- [ ] Step 1 — failing test for the pure decision + (if feasible) the handler. Minimum: a unit test that `"qishui" in SUPPORTED_PLATFORMS`. Plus a test of a small helper `apply_soda_validation` if extracted.

```python
def test_qishui_in_supported_platforms():
    from app.api.user_settings_router import SUPPORTED_PLATFORMS
    assert "qishui" in SUPPORTED_PLATFORMS
```

- [ ] Step 2 — run, confirm FAIL.
- [ ] Step 3 — implement:
  (a) Add `"qishui"` to `SUPPORTED_PLATFORMS` (line 266).
  (b) In `set_cookie`, AFTER the existing `repo.upsert(...)`, add a qishui-only post-validation block (read the handler first to match its style and the `repo`/`auth` names):

```python
    if platform == "qishui":
        from app.services.media.parsers.soda_music.cookie_validate import (
            validate_soda_cookie,
        )

        cookie_val = request.cookie_text or request.cookie_file or ""
        ok, err = await validate_soda_cookie(cookie_val)
        if not ok:
            await repo.mark_invalid(auth.user_id, platform, err or "validation failed")
```

  This keeps the generic flow (upsert forces is_valid=True) and only downgrades qishui when the login check fails. Other platforms unchanged.

- [ ] Step 4 — `uv run pytest tests/soda/test_qishui_cookie_endpoint.py -q` + `uv run pytest -k cookie -q` (existing cookie tests pass) + `uv run python -c "import app.api.user_settings_router"`.
- [ ] Step 5 — black/isort/ruff + commit `feat(soda): register qishui cookie platform + validate on save`.

> Note: validate_soda_cookie makes a network call on save. That's acceptable (cookie save is a deliberate user action, not hot path). If the Soda API is down, the cookie still saves but is marked invalid with the error — the user can retry.

---

## Task 4: Frontend "Soda Music" cookie card

**Files:** Modify `frontend/components/CookiesSettings.tsx:13-17`; Create `frontend/public/icons/qishui.svg`.

- [ ] Step 1 — add to the `PLATFORMS` array:

```tsx
const PLATFORMS: PlatformConfig[] = [
  { id: 'douyin', name: 'Douyin', icon: '/icons/douyin.svg' },
  { id: 'bilibili', name: 'Bilibili', icon: '/icons/bilibili.svg' },
  { id: 'youtube', name: 'YouTube', icon: '/icons/youtube.svg' },
  { id: 'qishui', name: 'Soda Music', icon: '/icons/qishui.svg' },
];
```

- [ ] Step 2 — create a simple `qishui.svg` (a minimal music-note / soda glyph; English-free; ~24x24 viewBox). Reuse an existing icon's style if cleaner.
- [ ] Step 3 — verify: `cd frontend && npm run build` succeeds (or `npx tsc --noEmit` if faster). The card renders from the array; service is generic (no change). Status badge driven by backend `is_valid`.
- [ ] Step 4 — commit `feat(soda): add Soda Music card to Cookie Management`.

> No i18n change needed (component text is currently hardcoded English, per the existing pattern). Don't introduce i18n keys here — that'd be scope creep on an established hardcoded component.

---

## Final
- [ ] `cd backend && uv run pytest tests/soda/ -q` all green; `uv run pytest -k "cookie or parse" -q` no regression
- [ ] black + isort + ruff clean; `npm run build` clean
- [ ] `/ship` → PR with **base `feature/soda-single-track`** (stacked). After #397 merges to master, rebase this onto master and retarget base.
- [ ] ⚠️ Phase 3 enables the real-cookie path; combined with Phase 2, a real VIP cookie stored via the new card unblocks the end-to-end smoke that gates #397.

## Self-Review
- Spec (B.10 item 3): reuse user_cookies (Task 1 ✓), platform='qishui' (✓), no new table/endpoint (✓ — reuses PUT /cookies/{platform}), frontend card (Task 4 ✓), first-store 试听/login validation → is_valid (Tasks 2+3 ✓). VIP full-stream distinction deferred to download-time SodaPreviewError (noted).
- Consistency: platform string "qishui" identical across cookie_source, validation, SUPPORTED_PLATFORMS, frontend, URLRouter. `get_soda_cookie(user_id, *, repo=None)` back-compatible (callers in parse_entry/soda_download_workflow pass only user_id).
- No placeholders; injected fakes for all unit tests; network call only on deliberate save.
