"""Part 1 — full-key reveal for copy-to-clipboard (owner-only, audited).

The copy button used to copy only the display prefix (useless). The reveal
endpoint decrypts the encrypt-at-rest ``key_value`` and returns the FULL
``dk_`` key so the frontend can copy it (copy-only, never rendered to the DOM).

Two layers exercised, both without a DB / network:
  * ``reveal_full_key(row)`` — pure decrypt helper: ciphertext → plaintext dk_
    key, or None when the row has no recoverable key material (NULL / a value
    that does not decrypt to a dk_ key, e.g. a pre-encryption hash-only row).
  * ``reveal_api_key`` router — owner ok / missing 404 / non-owner 403 /
    unrecoverable 422, with the repo + auth mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.core import secret_box
from app.core.deps import AuthContext
from app.repositories.api_key_repository import reveal_full_key

pytestmark = pytest.mark.unit

OWNER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


def _auth(user_id: str = OWNER) -> AuthContext:
    return AuthContext(user_id=user_id, auth_type="jwt", scopes=None, api_key_id=None)


# ─── reveal_full_key — pure decrypt helper ───────────────────────────────


def test_reveal_full_key_decrypts_ciphertext_to_dk_key():
    full_key = "dk_" + "a" * 64
    row = {"key_value": secret_box.encrypt(full_key)}
    assert reveal_full_key(row) == full_key


def test_reveal_full_key_passes_legacy_plaintext_through():
    """Legacy pre-encryption rows store the plaintext dk_ key; decrypt passes
    non-Fernet values through, so they are still recoverable."""
    full_key = "dk_" + "b" * 64
    assert reveal_full_key({"key_value": full_key}) == full_key


def test_reveal_full_key_none_when_value_absent():
    assert reveal_full_key({"key_value": None}) is None
    assert reveal_full_key({}) is None
    assert reveal_full_key({"key_value": ""}) is None


def test_reveal_full_key_none_when_not_a_dk_key():
    """A stored value that decrypts to something that is not a dk_ key (e.g. a
    pre-encryption SHA-256 hash-only row) is NOT recoverable — never return a
    wrong value."""
    hash_like = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert reveal_full_key({"key_value": hash_like}) is None


# ─── reveal_api_key router — status semantics ────────────────────────────


@pytest.mark.asyncio
async def test_reveal_owner_ok_returns_full_key():
    from app.api.api_key_router import reveal_api_key

    full_key = "dk_" + "c" * 64
    repo = AsyncMock()
    repo.get_by_key_id.return_value = {
        "key_id": "kid",
        "user_id": OWNER,
        "key_value": secret_box.encrypt(full_key),
    }
    with patch("app.api.api_key_router.get_api_key_repository", return_value=repo):
        out = await reveal_api_key("kid", _auth(OWNER))
    assert out.key == full_key


@pytest.mark.asyncio
async def test_reveal_missing_key_404():
    from app.api.api_key_router import reveal_api_key

    repo = AsyncMock()
    repo.get_by_key_id.return_value = None
    with patch("app.api.api_key_router.get_api_key_repository", return_value=repo):
        with pytest.raises(HTTPException) as exc:
            await reveal_api_key("nope", _auth(OWNER))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_reveal_non_owner_403():
    from app.api.api_key_router import reveal_api_key

    repo = AsyncMock()
    repo.get_by_key_id.return_value = {
        "key_id": "kid",
        "user_id": OWNER,
        "key_value": secret_box.encrypt("dk_" + "d" * 64),
    }
    with patch("app.api.api_key_router.get_api_key_repository", return_value=repo):
        with pytest.raises(HTTPException) as exc:
            await reveal_api_key("kid", _auth(OTHER))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_reveal_unrecoverable_key_422():
    """Owner is fine, but the row has no recoverable key material — return 422
    (rotate) rather than a wrong value."""
    from app.api.api_key_router import reveal_api_key

    repo = AsyncMock()
    repo.get_by_key_id.return_value = {
        "key_id": "kid",
        "user_id": OWNER,
        "key_value": None,
    }
    with patch("app.api.api_key_router.get_api_key_repository", return_value=repo):
        with pytest.raises(HTTPException) as exc:
            await reveal_api_key("kid", _auth(OWNER))
    assert exc.value.status_code == 422
