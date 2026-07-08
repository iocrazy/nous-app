"""Repository for inspiration_api_tokens (migration 349).

Pure data access via the service-role supabase client (inspiration_repository
template). Ownership checks live in the service layer. Only the SHA-256 hex
digest (`token_hash`) is stored — the plaintext PAT is never persisted
(spec §3.3, secret-at-rest).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


def _bigint(value: Any) -> int:
    if isinstance(value, int):
        return value
    return int(str(value))


class InspirationTokenRepository:
    TABLE = "inspiration_api_tokens"

    async def _client(self):
        return await get_async_supabase_admin()

    async def create(
        self, user_id: str, name: str, token_hash: str
    ) -> Optional[Dict[str, Any]]:
        try:
            client = await self._client()
            payload = {
                "user_id": user_id,
                "name": name,
                "token_hash": token_hash,
            }
            result = await client.table(self.TABLE).insert(payload).execute()
            return result.data[0] if result and result.data else None
        except Exception as e:
            logger.error(f"inspiration token create failed (user={user_id}): {e}")
            return None

    async def list_by_user(
        self, user_id: str, *, include_revoked: bool = False
    ) -> List[Dict[str, Any]]:
        try:
            client = await self._client()
            query = client.table(self.TABLE).select("*").eq("user_id", user_id)
            if not include_revoked:
                query = query.is_("revoked_at", "null")
            result = await query.order("id", desc=True).execute()
            return result.data or []
        except Exception as e:
            logger.error(f"inspiration token list failed (user={user_id}): {e}")
            return []

    async def find_active_by_hash(self, token_hash: str) -> Optional[Dict[str, Any]]:
        """Return the non-revoked token row for this hash, else None."""
        try:
            client = await self._client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("token_hash", token_hash)
                .is_("revoked_at", "null")
                .maybe_single()
                .execute()
            )
            return result.data if result and result.data else None
        except Exception as e:
            logger.error(f"inspiration token lookup failed: {e}")
            return None

    async def touch_last_used(self, token_id: Any) -> None:
        try:
            client = await self._client()
            await client.table(self.TABLE).update(
                {"last_used_at": datetime.now(timezone.utc).isoformat()}
            ).eq("id", _bigint(token_id)).execute()
        except Exception as e:
            logger.warning(f"inspiration token touch_last_used({token_id}) failed: {e}")

    async def revoke(self, token_id: Any, user_id: str) -> bool:
        """Revoke a token owned by user_id. Returns False if not found/owned."""
        try:
            client = await self._client()
            result = (
                await client.table(self.TABLE)
                .update({"revoked_at": datetime.now(timezone.utc).isoformat()})
                .eq("id", _bigint(token_id))
                .eq("user_id", user_id)
                .is_("revoked_at", "null")
                .execute()
            )
            return bool(result and result.data)
        except Exception as e:
            logger.error(f"inspiration token revoke({token_id}) failed: {e}")
            return False


_repo: Optional[InspirationTokenRepository] = None


def get_inspiration_token_repository() -> InspirationTokenRepository:
    global _repo
    if _repo is None:
        _repo = InspirationTokenRepository()
    return _repo
