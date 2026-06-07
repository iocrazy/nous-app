# Media Token Hardening (#276 + #275) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes. This is SECURITY code — follow TDD exactly, no shortcuts.

**Goal:** (#276) Sign media tokens with a dedicated `MEDIA_TOKEN_SECRET` instead of reusing `SUPABASE_SERVICE_ROLE_KEY`, with a dual-secret grace so existing tokens survive. (#275) Make already-issued media tokens revocable on logout / password change via a Redis denylist.

**Architecture:** `media_auth.py` is the single chokepoint — `_authenticate_media_request` (main.py:285-296) validates both `?token=` and the cookie through `validate_media_cookie`. We (a) move signing to `MEDIA_TOKEN_SECRET` (fallback to `SERVICE_ROLE_KEY` + warn), verifying against BOTH secrets during grace; (b) add `issued_at` to the token (`user_id.issued_at.expires_at.sig`, 4-part), accepting legacy 3-part during grace; (c) add a Redis denylist `revoke:media:{user_id}=cutoff_ts` set on logout/password-change, checked after HMAC+expiry — reject when `issued_at <= cutoff` (legacy 3-part tokens, which lack `issued_at`, are conservatively rejected when any cutoff exists).

**Tech Stack:** Python 3.12, FastAPI, `hmac`/`hashlib`, `app/core/redis.py` async client, pytest.

**Design decisions (locked with user 2026-05-25):** D1 add `issued_at` (4-part). D2 `MEDIA_TOKEN_SECRET` optional → fallback to `SERVICE_ROLE_KEY` + warn (deploy can't break before NAS sets the env). D3 one PR for both.

**Source issues:** #276 (dedicated secret), #275 (revocation list). Both confirmed still unfixed in `media_auth.py` (master @ #346).

---

## Key facts (verified against code)

- Token/cookie format today: `user_id.expires_at.sig` (3-part), `sig = hmac_sha256(secret, "user_id.expires_at")[:32]`. Used for BOTH the 7d cookie and the 4h query token via one `_sign_cookie`.
- `validate_media_cookie(cookie_value) -> Optional[str]` is **sync** today; its only callers are in `main.py::_authenticate_media_request` (an `async def`, lines 290 & 296) — so it can safely become `async`.
- Auth flows to hook: `supabase_auth_router.py` `/signout` (POST, has `background_tasks` + extracts `user_id`), `PUT /me` `update_user` (has `request.password`), and `media_auth.py` `delete_media_session` (logout; currently just clears the cookie, takes `request`).
- Redis: `app/core/redis.py` exposes an async client (find the accessor, e.g. `get_async_redis()`).
- Constants in `media_auth.py`: `COOKIE_MAX_AGE = 7*24*3600`, `MEDIA_TOKEN_MAX_AGE = 4*3600`.

---

## Task 1: Dedicated secret + issued_at token format (crypto layer, no Redis yet)

**Files:**
- Modify: `backend/app/core/config.py` (add `MEDIA_TOKEN_SECRET`)
- Modify: `backend/app/api/media_auth.py` (secret resolution + 4-part sign + dual-secret/dual-format verify)
- Test: `backend/tests/test_media_auth_token.py` (new)

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_media_auth_token.py`:

```python
"""#276/#275 Task 1: dedicated secret + issued_at token format (crypto only)."""
from __future__ import annotations

import time

import pytest

from app.api import media_auth as m


def _patch_secrets(monkeypatch, *, media: str, service: str):
    monkeypatch.setattr(m.settings, "MEDIA_TOKEN_SECRET", media, raising=False)
    monkeypatch.setattr(m.settings, "SUPABASE_SERVICE_ROLE_KEY", service, raising=False)


def test_sign_and_verify_roundtrip_new_format(monkeypatch):
    _patch_secrets(monkeypatch, media="media-secret", service="svc-key")
    now = int(time.time())
    tok = m._sign_token("user-1", now, now + 100)
    assert tok.count(".") == 3  # user_id.issued_at.expires_at.sig
    parsed = m._verify_token(tok)
    assert parsed is not None
    assert parsed.user_id == "user-1"
    assert parsed.issued_at == now


def test_expired_token_rejected(monkeypatch):
    _patch_secrets(monkeypatch, media="media-secret", service="svc-key")
    now = int(time.time())
    tok = m._sign_token("user-1", now - 200, now - 100)  # already expired
    assert m._verify_token(tok) is None


def test_tampered_signature_rejected(monkeypatch):
    _patch_secrets(monkeypatch, media="media-secret", service="svc-key")
    now = int(time.time())
    tok = m._sign_token("user-1", now, now + 100)
    assert m._verify_token(tok[:-1] + ("0" if tok[-1] != "0" else "1")) is None


def test_legacy_3part_token_accepted_during_grace(monkeypatch):
    # Old token signed with the OLD service-role key, 3-part format.
    _patch_secrets(monkeypatch, media="media-secret", service="svc-key")
    now = int(time.time())
    import hashlib
    import hmac

    payload = f"user-1.{now + 100}"
    sig = hmac.new(b"svc-key", payload.encode(), hashlib.sha256).hexdigest()[:32]
    legacy = f"{payload}.{sig}"
    parsed = m._verify_token(legacy)
    assert parsed is not None
    assert parsed.user_id == "user-1"
    assert parsed.issued_at is None  # legacy has no issued_at


def test_media_secret_falls_back_to_service_role_when_unset(monkeypatch):
    _patch_secrets(monkeypatch, media="", service="svc-key")
    assert m._signing_secret() == "svc-key"


def test_signing_prefers_media_secret(monkeypatch):
    _patch_secrets(monkeypatch, media="media-secret", service="svc-key")
    assert m._signing_secret() == "media-secret"
```

- [ ] **Step 2: Run → fail**

`cd backend && uv run pytest tests/test_media_auth_token.py -v` → FAIL (`_sign_token`/`_verify_token`/`_signing_secret`/`MEDIA_TOKEN_SECRET` missing).

- [ ] **Step 3: Implement**

In `backend/app/core/config.py`, add to the Settings model (near the other Supabase keys):

```python
    MEDIA_TOKEN_SECRET: str = ""  # dedicated HMAC key for media tokens (#276); falls back to SUPABASE_SERVICE_ROLE_KEY when empty
```

In `backend/app/api/media_auth.py`, replace `_get_secret` / `_sign_cookie` / `validate_media_cookie` with:

```python
from dataclasses import dataclass

_warned_fallback = False


def _signing_secret() -> str:
    """Secret used to SIGN new tokens: MEDIA_TOKEN_SECRET if set, else the
    service-role key (with a one-time warning — #276)."""
    global _warned_fallback
    secret = settings.MEDIA_TOKEN_SECRET or settings.SUPABASE_SERVICE_ROLE_KEY
    if not settings.MEDIA_TOKEN_SECRET and not _warned_fallback:
        logger.warning(
            "MEDIA_TOKEN_SECRET not set — falling back to SUPABASE_SERVICE_ROLE_KEY "
            "for media-token signing. Set MEDIA_TOKEN_SECRET to decouple (#276)."
        )
        _warned_fallback = True
    if not secret:
        raise RuntimeError("No media-token secret configured")
    return secret


def _verify_secrets() -> list[str]:
    """Secrets to TRY when verifying (dual-secret grace): the dedicated secret
    AND the legacy service-role key, deduped, non-empty."""
    out: list[str] = []
    for s in (settings.MEDIA_TOKEN_SECRET, settings.SUPABASE_SERVICE_ROLE_KEY):
        if s and s not in out:
            out.append(s)
    return out


def _hmac(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def _sign_token(user_id: str, issued_at: int, expires_at: int) -> str:
    """New 4-part token: user_id.issued_at.expires_at.sig (#275 adds issued_at)."""
    payload = f"{user_id}.{issued_at}.{expires_at}"
    return f"{payload}.{_hmac(_signing_secret(), payload)}"


@dataclass(frozen=True)
class _ParsedToken:
    user_id: str
    issued_at: Optional[int]  # None for legacy 3-part tokens
    expires_at: int


def _verify_token(value: str) -> Optional[_ParsedToken]:
    """Verify signature + expiry for BOTH new (4-part) and legacy (3-part)
    tokens, trying each grace secret. Returns parsed token or None. Pure /
    sync — no denylist check here (that is async, added in Task 2)."""
    if not value:
        return None
    parts = value.split(".")
    if len(parts) == 4:
        user_id, issued_str, expires_str, sig = parts
        payload = f"{user_id}.{issued_str}.{expires_str}"
        try:
            issued_at: Optional[int] = int(issued_str)
        except ValueError:
            return None
    elif len(parts) == 3:  # legacy grace
        user_id, expires_str, sig = parts
        payload = f"{user_id}.{expires_str}"
        issued_at = None
    else:
        return None

    try:
        expires_at = int(expires_str)
    except ValueError:
        return None
    if time.time() > expires_at:
        return None

    for secret in _verify_secrets():
        if hmac.compare_digest(sig, _hmac(secret, payload)):
            return _ParsedToken(user_id=user_id, issued_at=issued_at, expires_at=expires_at)
    return None
```

Keep the public `validate_media_cookie` working for now by delegating (Task 2 makes it async + adds the denylist):

```python
def validate_media_cookie(cookie_value: str) -> Optional[str]:
    parsed = _verify_token(cookie_value)
    return parsed.user_id if parsed else None
```

Update the two issuers to the new format:
- `create_media_session`: `now = int(time.time()); expires_at = now + COOKIE_MAX_AGE; cookie_value = _sign_token(user_id, now, expires_at)`
- `create_media_token`: `now = int(time.time()); expires_at = now + MEDIA_TOKEN_MAX_AGE; media_token = _sign_token(user_id, now, expires_at)`

Remove the now-unused `_sign_cookie` / `_get_secret` (or keep `_sign_cookie` as a thin wrapper if other modules import it — grep first: `grep -rn "_sign_cookie\|_get_secret" backend/app`; if external importers exist, keep shims).

- [ ] **Step 4: Run → pass**

`cd backend && uv run pytest tests/test_media_auth_token.py -v` → all pass. Also `uv run pytest tests/ -k media -q` (no regression).

- [ ] **Step 5: black/isort/flake8 on the 3 files, then commit**

```bash
git add backend/app/core/config.py backend/app/api/media_auth.py backend/tests/test_media_auth_token.py
git commit -m "feat(media-auth): dedicated MEDIA_TOKEN_SECRET + issued_at token format (#276/#275)"
```

---

## Task 2: Redis denylist + async revocation check

**Files:**
- Modify: `backend/app/api/media_auth.py` (`revoke_media_tokens` + async `validate_media_cookie`)
- Modify: `backend/app/main.py` (await the now-async `validate_media_cookie` at lines ~290 & ~296)
- Test: `backend/tests/test_media_auth_revocation.py` (new)

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_media_auth_revocation.py`:

```python
"""#275 Task 2: Redis denylist revocation."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from app.api import media_auth as m


@pytest.fixture
def secrets(monkeypatch):
    monkeypatch.setattr(m.settings, "MEDIA_TOKEN_SECRET", "media-secret", raising=False)
    monkeypatch.setattr(m.settings, "SUPABASE_SERVICE_ROLE_KEY", "svc-key", raising=False)


def _fake_redis(get_value=None):
    r = AsyncMock()
    r.get = AsyncMock(return_value=get_value)
    r.set = AsyncMock()
    return r


@pytest.mark.asyncio
async def test_valid_token_no_denylist_entry(secrets, monkeypatch):
    r = _fake_redis(get_value=None)
    monkeypatch.setattr(m, "_get_redis", AsyncMock(return_value=r))
    now = int(time.time())
    tok = m._sign_token("u1", now, now + 100)
    assert await m.validate_media_cookie(tok) == "u1"


@pytest.mark.asyncio
async def test_token_issued_before_cutoff_rejected(secrets, monkeypatch):
    now = int(time.time())
    r = _fake_redis(get_value=str(now).encode())  # cutoff = now
    monkeypatch.setattr(m, "_get_redis", AsyncMock(return_value=r))
    tok = m._sign_token("u1", now - 10, now + 100)  # issued before cutoff
    assert await m.validate_media_cookie(tok) is None


@pytest.mark.asyncio
async def test_token_issued_after_cutoff_accepted(secrets, monkeypatch):
    now = int(time.time())
    r = _fake_redis(get_value=str(now - 50).encode())  # cutoff in the past
    monkeypatch.setattr(m, "_get_redis", AsyncMock(return_value=r))
    tok = m._sign_token("u1", now, now + 100)  # issued after cutoff
    assert await m.validate_media_cookie(tok) == "u1"


@pytest.mark.asyncio
async def test_legacy_token_rejected_when_cutoff_present(secrets, monkeypatch):
    now = int(time.time())
    r = _fake_redis(get_value=str(now).encode())
    monkeypatch.setattr(m, "_get_redis", AsyncMock(return_value=r))
    import hashlib
    import hmac

    payload = f"u1.{now + 100}"
    sig = hmac.new(b"svc-key", payload.encode(), hashlib.sha256).hexdigest()[:32]
    legacy = f"{payload}.{sig}"
    assert await m.validate_media_cookie(legacy) is None  # conservative


@pytest.mark.asyncio
async def test_revoke_media_tokens_writes_key(secrets, monkeypatch):
    r = _fake_redis()
    monkeypatch.setattr(m, "_get_redis", AsyncMock(return_value=r))
    await m.revoke_media_tokens("u1")
    r.set.assert_awaited_once()
    args, kwargs = r.set.call_args
    assert args[0] == "revoke:media:u1"
    # TTL set to the 7d cookie lifetime
    assert kwargs.get("ex") == m.COOKIE_MAX_AGE or (len(args) >= 3)


@pytest.mark.asyncio
async def test_denylist_redis_error_does_not_blanket_reject(secrets, monkeypatch):
    # If Redis is down, fail OPEN for an otherwise-valid token (availability),
    # since the HMAC+expiry already proved authenticity. Document the tradeoff.
    r = AsyncMock()
    r.get = AsyncMock(side_effect=RuntimeError("redis down"))
    monkeypatch.setattr(m, "_get_redis", AsyncMock(return_value=r))
    now = int(time.time())
    tok = m._sign_token("u1", now, now + 100)
    assert await m.validate_media_cookie(tok) == "u1"
```

- [ ] **Step 2: Run → fail**

`cd backend && uv run pytest tests/test_media_auth_revocation.py -v` → FAIL (`revoke_media_tokens`/`_get_redis` missing; `validate_media_cookie` not async).

- [ ] **Step 3: Implement**

First find the async redis accessor: `grep -nE "def get_async_redis|async def get_redis|aioredis" backend/app/core/redis.py`. Use it inside a thin wrapper so tests can patch one name:

```python
async def _get_redis():
    from app.core.redis import get_async_redis  # adjust to the real accessor name

    return await get_async_redis()


def _revoke_key(user_id: str) -> str:
    return f"revoke:media:{user_id}"


async def revoke_media_tokens(user_id: str) -> None:
    """Revoke all media tokens for a user issued at-or-before now (#275).
    Best-effort: a Redis failure is logged, not raised (logout must not 500)."""
    if not user_id:
        return
    try:
        r = await _get_redis()
        await r.set(_revoke_key(user_id), str(int(time.time())), ex=COOKIE_MAX_AGE)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"revoke_media_tokens failed for {user_id}: {e}")


async def validate_media_cookie(cookie_value: str) -> Optional[str]:
    """Verify (sig + expiry) then check the revocation denylist (#275)."""
    parsed = _verify_token(cookie_value)
    if parsed is None:
        return None

    # Denylist check. Fail-open on Redis error (token already proven authentic).
    try:
        r = await _get_redis()
        raw = await r.get(_revoke_key(parsed.user_id))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"media denylist check failed (fail-open) for {parsed.user_id}: {e}")
        return parsed.user_id

    if raw is not None:
        try:
            cutoff = int(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
        except (ValueError, AttributeError):
            cutoff = None
        if cutoff is not None:
            if parsed.issued_at is None:
                return None  # legacy token can't prove it post-dates the cutoff
            if parsed.issued_at <= cutoff:
                return None
    return parsed.user_id
```

In `backend/app/main.py`, the two `validate_media_cookie(...)` calls (~lines 290 & 296) are inside the async `_authenticate_media_request` — add `await`:
```python
user_id = await validate_media_cookie(token)
...
user_id = await validate_media_cookie(cookie_value)
```

- [ ] **Step 4: Run → pass**

`cd backend && uv run pytest tests/test_media_auth_revocation.py tests/test_media_auth_token.py -v` → all pass. Then `uv run pytest tests/ -k "media or auth" -q` → no regression. Also verify `_verify_token` (sync) is still what Task 1's tests call (they do).

- [ ] **Step 5: black/isort/flake8, commit**

```bash
git add backend/app/api/media_auth.py backend/app/main.py backend/tests/test_media_auth_revocation.py
git commit -m "feat(media-auth): Redis denylist revocation for media tokens (#275)"
```

---

## Task 3: Hook revocation into logout + password change

**Files:**
- Modify: `backend/app/api/media_auth.py` (`delete_media_session` revokes for the cookie's user)
- Modify: `backend/app/api/supabase_auth_router.py` (`/signout`, `PUT /me`)
- Test: `backend/tests/test_media_auth_revocation_hooks.py` (new)

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_media_auth_revocation_hooks.py`:

```python
"""#275 Task 3: revocation hooks on logout / password change."""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api import media_auth as m


@pytest.mark.asyncio
async def test_delete_media_session_revokes_cookie_user(monkeypatch):
    monkeypatch.setattr(m.settings, "MEDIA_TOKEN_SECRET", "media-secret", raising=False)
    monkeypatch.setattr(m.settings, "SUPABASE_SERVICE_ROLE_KEY", "svc", raising=False)
    revoked = {}
    monkeypatch.setattr(
        m, "revoke_media_tokens",
        AsyncMock(side_effect=lambda uid: revoked.update(uid=uid)),
    )
    now = int(time.time())
    cookie = m._sign_token("u9", now, now + m.COOKIE_MAX_AGE)
    req = SimpleNamespace(cookies={m.COOKIE_NAME: cookie})
    await m.delete_media_session(req)
    assert revoked.get("uid") == "u9"
```

(For the `supabase_auth_router` hooks, assert the router calls `revoke_media_tokens` — mirror the existing test style in `tests/` for that router; mock `SupabaseAuthService.get_user` to return `{"id": "u9"}` and `revoke_media_tokens`, then assert it's awaited for signout and for `PUT /me` when `password` is set, and NOT called for `PUT /me` without a password.)

- [ ] **Step 2: Run → fail**

`cd backend && uv run pytest tests/test_media_auth_revocation_hooks.py -v` → FAIL.

- [ ] **Step 3: Implement**

`delete_media_session` (media_auth.py) — revoke for the cookie's user before clearing:
```python
@router.delete("/media-session")
async def delete_media_session(request: Request):
    """Clear the media session cookie AND revoke the user's media tokens (#275)."""
    cookie_value = request.cookies.get(COOKIE_NAME, "")
    parsed = _verify_token(cookie_value)
    if parsed:
        await revoke_media_tokens(parsed.user_id)
    response = JSONResponse(content={"success": True})
    response.delete_cookie(key=COOKIE_NAME, path="/media", secure=True, samesite="lax")
    logger.info("Media session cleared")
    return response
```

`supabase_auth_router.py`:
- In `/signout`, where `user_id` is known (after `sign_out()`), add a background task:
  ```python
  from app.api.media_auth import revoke_media_tokens
  if user_id:
      background_tasks.add_task(revoke_media_tokens, user_id)
  ```
  (alongside the existing `log_user_action` background task.)
- In `PUT /me` `update_user`, when `request.password` is set and the update succeeded, resolve the user_id (the bearer token's user) and revoke:
  ```python
  if request.password and result.get("success"):
      try:
          user = await auth_service.get_user(token)
          uid = user.get("id") if user else None
          if uid:
              from app.api.media_auth import revoke_media_tokens
              await revoke_media_tokens(uid)
      except Exception as e:  # noqa: BLE001
          logger.warning(f"media revoke after password change failed: {e}")
  ```

> `reset_password` (forgot-password by email, unauthenticated) is NOT hooked here — it has no user_id/session and is a distinct flow; note it as a follow-up.

- [ ] **Step 4: Run → pass**

`cd backend && uv run pytest tests/test_media_auth_revocation_hooks.py -v` → pass. Then full media/auth set: `uv run pytest tests/ -k "media or auth" -q`.

- [ ] **Step 5: black/isort/flake8, commit**

```bash
git add backend/app/api/media_auth.py backend/app/api/supabase_auth_router.py backend/tests/test_media_auth_revocation_hooks.py
git commit -m "feat(auth): revoke media tokens on logout + password change (#275)"
```

---

## Task 4: Final verification + security review

- [ ] Full suite: `cd backend && uv run pytest tests/ -q` → all pass.
- [ ] Chain import: `uv run python -c "import app.api.media_auth, app.main, app.api.supabase_auth_router; print('OK')"`.
- [ ] Grep for any remaining `_sign_cookie`/`_get_secret` importers that broke: `grep -rn "_sign_cookie\|_get_secret\|validate_media_cookie" backend/app | grep -v media_auth.py` — every `validate_media_cookie` caller must now `await`.
- [ ] **Deploy note for the PR body:** NAS must set `MEDIA_TOKEN_SECRET` (long random string) in the backend env; until then it falls back to `SERVICE_ROLE_KEY` (warned). The token format changes to 4-part — existing 3-part tokens stay valid during grace (verified against the service-role key); they cannot be precisely revoked (rejected wholesale once a user has a cutoff). After all clients refresh (~7d), the legacy 3-part path + service-role verify secret can be removed in a follow-up.
- [ ] Dispatch an adversarial security reviewer over the diff: focus on timing-safety (`compare_digest` everywhere), fail-open denylist tradeoff (is it acceptable? — yes: token is already HMAC-authentic; Redis-down shouldn't lock everyone out), no secret logged, legacy-grace can't be abused to bypass revocation (legacy tokens ARE rejected once a cutoff exists), and that `await` was added to every `validate_media_cookie` call site.

## Self-review checklist
- #276: `MEDIA_TOKEN_SECRET` signs, dual-secret verify grace ✓ (Task 1). #275: issued_at + Redis denylist + hooks ✓ (Tasks 1-3).
- Type consistency: `_verify_token -> Optional[_ParsedToken]`, `validate_media_cookie` async `-> Optional[str]`, `revoke_media_tokens(user_id)` async. All call sites updated.
- Known limitations (documented): legacy 3-part tokens can't be precisely revoked (wholesale-rejected under a cutoff); `reset_password` flow not hooked; denylist fails open on Redis error.
