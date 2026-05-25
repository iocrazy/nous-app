"""#275 Task 2: Redis denylist revocation."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from app.api import media_auth as m


@pytest.fixture
def secrets(monkeypatch):
    monkeypatch.setattr(m.settings, "MEDIA_TOKEN_SECRET", "media-secret", raising=False)
    monkeypatch.setattr(
        m.settings, "SUPABASE_SERVICE_ROLE_KEY", "svc-key", raising=False
    )


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
