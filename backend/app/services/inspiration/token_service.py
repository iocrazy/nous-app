"""Personal Access Token (PAT) service for inspiration external ingestion
(spec §3.3 / §4).

Tokens are `mhk_<urlsafe>` strings. The plaintext is returned exactly once at
creation and never stored — only its SHA-256 hex digest lives in the DB
(secret-at-rest). PAT scope is fixed to inspiration read/write; a PAT is NOT a
general session credential and cannot manage tokens.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Dict, List, Optional, Tuple

from app.repositories.inspiration_token_repository import (
    get_inspiration_token_repository,
)

TOKEN_PREFIX = "mhk_"


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


class InspirationTokenService:
    def __init__(self) -> None:
        self._repo = get_inspiration_token_repository()

    async def create(self, user_id: str, name: str) -> Optional[Tuple[Dict, str]]:
        """Mint a token. Returns (row, plaintext) or None on persist failure."""
        plaintext = TOKEN_PREFIX + secrets.token_urlsafe(32)
        row = await self._repo.create(user_id, name, _hash(plaintext))
        if row is None:
            return None
        return row, plaintext

    async def list(self, user_id: str) -> List[Dict]:
        return await self._repo.list_by_user(user_id)

    async def revoke(self, user_id: str, token_id: str) -> bool:
        return await self._repo.revoke(token_id, user_id)

    async def authenticate(self, plaintext: str) -> Optional[str]:
        """Resolve a PAT to its owning user_id.

        Returns the user_id for a valid, non-revoked token, else None. The
        prefix gate keeps non-PAT Bearer tokens (JWTs) from hitting the DB.
        """
        if not plaintext or not plaintext.startswith(TOKEN_PREFIX):
            return None
        row = await self._repo.find_active_by_hash(_hash(plaintext))
        if row is None:
            return None
        await self._repo.touch_last_used(row["id"])
        return str(row["user_id"])


_service: Optional[InspirationTokenService] = None


def get_inspiration_token_service() -> InspirationTokenService:
    global _service
    if _service is None:
        _service = InspirationTokenService()
    return _service
