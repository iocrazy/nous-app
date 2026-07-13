"""Data access for the generalized project library (mig 358) — locations +
props in one table, keyed by entity_type. Mirrors project_character_repository
(snowflake ids ride as strings; Extract upsert never clobbers curation).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


def _serialize(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    for key in ("id", "project_id"):
        if out.get(key) is not None:
            out[key] = str(out[key])
    return out


class ProjectLibEntityRepository:
    TABLE = "project_lib_entities"

    async def _client(self):
        return await get_async_supabase_admin()

    async def list_by_project(
        self, project_id: str, entity_type: str
    ) -> List[Dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .select("*")
            .eq("project_id", project_id)
            .eq("entity_type", entity_type)
            .order("sort_order")
            .order("created_at")
            .execute()
        )
        return [_serialize(r) for r in (result.data or [])]

    async def create(
        self, project_id: str, entity_type: str, fields: Dict[str, Any]
    ) -> Dict[str, Any]:
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .insert({**fields, "project_id": project_id, "entity_type": entity_type})
            .execute()
        )
        if not result.data:
            raise RuntimeError("lib entity insert returned no row")
        return _serialize(result.data[0])

    async def update(
        self, project_id: str, entity_type: str, entity_id: str, fields: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not fields:
            return await self._get(project_id, entity_type, entity_id)
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .update(fields)
            .eq("id", entity_id)
            .eq("project_id", project_id)
            .eq("entity_type", entity_type)
            .execute()
        )
        return _serialize(result.data[0]) if result.data else None

    async def delete(self, project_id: str, entity_type: str, entity_id: str) -> bool:
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .delete()
            .eq("id", entity_id)
            .eq("project_id", project_id)
            .eq("entity_type", entity_type)
            .execute()
        )
        return bool(result.data)

    async def upsert_by_name(
        self, project_id: str, entity_type: str, names: List[str]
    ) -> List[Dict[str, Any]]:
        """Extract: materialize derived names as rows. Idempotent on
        (project, type, name); ignore_duplicates so re-running never clobbers
        curated rows."""
        cleaned = [n.strip() for n in names if n and n.strip()]
        if not cleaned:
            return []
        client = await self._client()
        try:
            await client.table(self.TABLE).upsert(
                [
                    {
                        "project_id": project_id,
                        "entity_type": entity_type,
                        "name": name,
                        "source": "script",
                    }
                    for name in cleaned
                ],
                on_conflict="project_id,entity_type,name",
                ignore_duplicates=True,
            ).execute()
        except Exception as e:  # noqa: BLE001
            logger.error(
                f"lib entity extract upsert failed for {project_id}/{entity_type}: {e}"
            )
            raise
        return await self.list_by_project(project_id, entity_type)

    async def _get(
        self, project_id: str, entity_type: str, entity_id: str
    ) -> Optional[Dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .select("*")
            .eq("id", entity_id)
            .eq("project_id", project_id)
            .eq("entity_type", entity_type)
            .execute()
        )
        return _serialize(result.data[0]) if result.data else None


def get_project_lib_entity_repository() -> ProjectLibEntityRepository:
    return ProjectLibEntityRepository()
