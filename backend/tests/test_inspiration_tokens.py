"""PAT tokens: service hashing/auth + router dual-auth + /tokens endpoints."""

import hashlib
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.inspiration_router import (
    create_token,
    get_inspiration_actor,
    list_tokens,
    revoke_token,
)
from app.schemas.inspiration import ApiTokenCreateIn
from app.services.inspiration.token_service import (
    TOKEN_PREFIX,
    InspirationTokenService,
    _hash,
)

USER = {"id": "u1"}


def _svc(repo=None):
    s = InspirationTokenService()
    s._repo = repo or AsyncMock()
    return s


# ─── Service: minting stores hash, not plaintext ─────────────────────────────


@pytest.mark.asyncio
async def test_create_mints_prefixed_plaintext_and_stores_only_hash():
    repo = AsyncMock()
    repo.create.return_value = {"id": 1, "name": "cli", "created_at": "t"}
    result = await _svc(repo).create("u1", "cli")
    assert result is not None
    row, plaintext = result
    assert plaintext.startswith(TOKEN_PREFIX)
    # repo was handed the SHA-256 hex of the plaintext — never the plaintext.
    _, _, stored_hash = repo.create.call_args.args
    assert stored_hash == hashlib.sha256(plaintext.encode()).hexdigest()
    assert plaintext not in stored_hash


@pytest.mark.asyncio
async def test_create_returns_none_on_persist_failure():
    repo = AsyncMock()
    repo.create.return_value = None
    assert await _svc(repo).create("u1", "cli") is None


# ─── Service: authenticate ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_authenticate_valid_returns_user_and_touches_last_used():
    repo = AsyncMock()
    repo.find_active_by_hash.return_value = {"id": 9, "user_id": "owner-1"}
    svc = _svc(repo)
    token = TOKEN_PREFIX + "abc"
    uid = await svc.authenticate(token)
    assert uid == "owner-1"
    repo.find_active_by_hash.assert_awaited_once_with(_hash(token))
    repo.touch_last_used.assert_awaited_once_with(9)


@pytest.mark.asyncio
async def test_authenticate_unknown_or_revoked_returns_none():
    repo = AsyncMock()
    repo.find_active_by_hash.return_value = None  # revoked rows are filtered in SQL
    assert await _svc(repo).authenticate(TOKEN_PREFIX + "x") is None


@pytest.mark.asyncio
async def test_authenticate_non_pat_prefix_never_hits_db():
    repo = AsyncMock()
    # A JWT (no mhk_ prefix) must be short-circuited before any DB lookup.
    assert await _svc(repo).authenticate("eyJhbGciOi.jwt.sig") is None
    assert await _svc(repo).authenticate("") is None
    repo.find_active_by_hash.assert_not_awaited()


# ─── Router: dual-auth dependency ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_actor_pat_path_returns_pat_actor():
    tok_svc = AsyncMock()
    tok_svc.authenticate.return_value = "owner-2"
    with patch(
        "app.api.inspiration_router.get_inspiration_token_service",
        return_value=tok_svc,
    ):
        actor = await get_inspiration_actor(f"Bearer {TOKEN_PREFIX}secret")
    assert actor["id"] == "owner-2"
    assert actor["auth_via"] == "pat"


@pytest.mark.asyncio
async def test_actor_invalid_pat_401():
    tok_svc = AsyncMock()
    tok_svc.authenticate.return_value = None
    with patch(
        "app.api.inspiration_router.get_inspiration_token_service",
        return_value=tok_svc,
    ):
        with pytest.raises(HTTPException) as exc:
            await get_inspiration_actor(f"Bearer {TOKEN_PREFIX}bad")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_actor_jwt_path_delegates_to_get_current_user():
    jwt_user = {"id": "jwt-user", "email": "a@b.c"}
    with patch(
        "app.api.inspiration_router.get_current_user",
        AsyncMock(return_value=jwt_user),
    ):
        actor = await get_inspiration_actor("Bearer eyJhbGci.jwt.sig")
    assert actor == jwt_user


# ─── Router: /tokens management (JWT-only) ───────────────────────────────────


@pytest.mark.asyncio
async def test_create_token_returns_plaintext_once():
    svc = AsyncMock()
    svc.create.return_value = (
        {"id": 5, "name": "cli", "created_at": "t"},
        f"{TOKEN_PREFIX}plaintext",
    )
    with patch(
        "app.api.inspiration_router.get_inspiration_token_service", return_value=svc
    ):
        out = await create_token(ApiTokenCreateIn(name="cli"), current_user=USER)
    assert out.token == f"{TOKEN_PREFIX}plaintext"
    assert out.id == "5"  # bigint → str


@pytest.mark.asyncio
async def test_create_token_502_on_failure():
    svc = AsyncMock()
    svc.create.return_value = None
    with patch(
        "app.api.inspiration_router.get_inspiration_token_service", return_value=svc
    ):
        with pytest.raises(HTTPException) as exc:
            await create_token(ApiTokenCreateIn(name="cli"), current_user=USER)
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_list_tokens_delegates_to_service():
    svc = AsyncMock()
    svc.list.return_value = [{"id": 1, "name": "cli", "created_at": "t"}]
    with patch(
        "app.api.inspiration_router.get_inspiration_token_service", return_value=svc
    ):
        out = await list_tokens(current_user=USER)
    assert out[0]["name"] == "cli"
    svc.list.assert_awaited_once_with("u1")


@pytest.mark.asyncio
async def test_revoke_token_404_when_not_owned():
    svc = AsyncMock()
    svc.revoke.return_value = False
    with patch(
        "app.api.inspiration_router.get_inspiration_token_service", return_value=svc
    ):
        with pytest.raises(HTTPException) as exc:
            await revoke_token("9", current_user=USER)
    assert exc.value.status_code == 404
