"""Part 2 — inspiration write access as a dk_ API-key SCOPE.

Inspiration is no longer a standalone mhk_ token surface; it is an optional
scope (``inspiration:write``) on the main dk_ API keys. ``get_inspiration_actor``
gains a third auth path: a valid dk_ key BEARING that scope. The mhk_ PAT path
and the JWT path are unchanged.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.inspiration_router import get_inspiration_actor
from app.core.api_key_scopes import ApiKeyScope, get_valid_scopes

INSPIRATION_SCOPE = "inspiration:write"


# ─── scope registry ──────────────────────────────────────────────────────


def test_inspiration_scope_registered():
    """The scope exists in the enum and is accepted by create/update validation
    (so it shows up in the registry-driven modal picker)."""
    assert ApiKeyScope.INSPIRATION_WRITE.value == INSPIRATION_SCOPE
    assert INSPIRATION_SCOPE in get_valid_scopes()


def test_inspiration_scope_in_available_list():
    from app.core.api_key_scopes import AVAILABLE_SCOPES

    entry = next(s for s in AVAILABLE_SCOPES if s["scope"] == INSPIRATION_SCOPE)
    assert entry["name"] == "Inspiration Notes"
    assert entry["description"]


# ─── get_inspiration_actor — dk_ API-key path ────────────────────────────


def _repo_returning(scopes):
    repo = AsyncMock()
    repo.validate_key.return_value = {
        "key_id": "kid",
        "user_id": "owner-9",
        "scopes": scopes,
    }
    return repo


@pytest.mark.asyncio
async def test_actor_dk_key_with_scope_returns_actor():
    repo = _repo_returning([INSPIRATION_SCOPE])
    with patch("app.api.inspiration_router.get_api_key_repository", return_value=repo):
        actor = await get_inspiration_actor("Bearer dk_" + "a" * 64)
    assert actor["id"] == "owner-9"
    assert actor["auth_via"] == "api_key"
    repo.validate_key.assert_awaited_once()


@pytest.mark.asyncio
async def test_actor_dk_key_without_scope_403():
    repo = _repo_returning(["videos:search"])  # valid key, wrong scope
    with patch("app.api.inspiration_router.get_api_key_repository", return_value=repo):
        with pytest.raises(HTTPException) as exc:
            await get_inspiration_actor("Bearer dk_" + "b" * 64)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_actor_dk_key_invalid_401():
    repo = AsyncMock()
    repo.validate_key.return_value = None  # unknown / revoked / expired
    with patch("app.api.inspiration_router.get_api_key_repository", return_value=repo):
        with pytest.raises(HTTPException) as exc:
            await get_inspiration_actor("Bearer dk_" + "c" * 64)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_actor_dk_key_wildcard_scope_ok():
    """A key with the resources wildcard must still NOT reach inspiration — the
    inspiration scope is independent. Only inspiration:write (or its wildcard)
    grants access."""
    repo = _repo_returning(["resources:*"])
    with patch("app.api.inspiration_router.get_api_key_repository", return_value=repo):
        with pytest.raises(HTTPException) as exc:
            await get_inspiration_actor("Bearer dk_" + "d" * 64)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_actor_jwt_path_unchanged():
    jwt_user = {"id": "jwt-user", "email": "a@b.c"}
    with patch(
        "app.api.inspiration_router.get_current_user",
        AsyncMock(return_value=jwt_user),
    ):
        actor = await get_inspiration_actor("Bearer eyJhbGci.jwt.sig")
    assert actor == jwt_user
