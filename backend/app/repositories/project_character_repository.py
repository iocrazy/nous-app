"""Data access for the project character library (mig 357).

Snowflake BIGINT ids ride as strings at the API boundary (bigIntSafeFetch
discipline); everything here passes ids through ``str()`` on the way out.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


def _serialize(row: Dict[str, Any]) -> Dict[str, Any]:
    """BIGINT ids → str so JS never sees a >2^53 number."""
    out = dict(row)
    for key in ("id", "project_id"):
        if out.get(key) is not None:
            out[key] = str(out[key])
    return out


class ProjectCharacterRepository:
    TABLE = "project_characters"

    async def _client(self):
        return await get_async_supabase_admin()

    async def list_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .select("*")
            .eq("project_id", project_id)
            .order("sort_order")
            .order("created_at")
            .execute()
        )
        return [_serialize(r) for r in (result.data or [])]

    async def create(self, project_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .insert({**fields, "project_id": project_id})
            .execute()
        )
        if not result.data:
            raise RuntimeError("project character insert returned no row")
        return _serialize(result.data[0])

    async def update(
        self, project_id: str, character_id: str, fields: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Patch one character; None when the row isn't in this project."""
        if not fields:
            return await self._get(project_id, character_id)
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .update(fields)
            .eq("id", character_id)
            .eq("project_id", project_id)
            .execute()
        )
        return _serialize(result.data[0]) if result.data else None

    async def delete(self, project_id: str, character_id: str) -> bool:
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .delete()
            .eq("id", character_id)
            .eq("project_id", project_id)
            .execute()
        )
        return bool(result.data)

    async def upsert_by_name(
        self, project_id: str, names: List[str]
    ) -> List[Dict[str, Any]]:
        """Extract-from-script: materialize derived character names as rows.

        Idempotent on (project_id, name) — existing rows (including manually
        edited ones) are left untouched (ignore_duplicates), so re-running
        Extract never clobbers curation.
        """
        cleaned = [n.strip() for n in names if n and n.strip()]
        if not cleaned:
            return []
        client = await self._client()
        try:
            await client.table(self.TABLE).upsert(
                [
                    {"project_id": project_id, "name": name, "source": "script"}
                    for name in cleaned
                ],
                on_conflict="project_id,name",
                ignore_duplicates=True,
            ).execute()
        except Exception as e:  # noqa: BLE001
            logger.error(f"character extract upsert failed for {project_id}: {e}")
            raise
        return await self.list_by_project(project_id)

    async def _get(
        self, project_id: str, character_id: str
    ) -> Optional[Dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .select("*")
            .eq("id", character_id)
            .eq("project_id", project_id)
            .execute()
        )
        return _serialize(result.data[0]) if result.data else None


def get_project_character_repository() -> ProjectCharacterRepository:
    return ProjectCharacterRepository()
