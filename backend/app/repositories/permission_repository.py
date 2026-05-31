# app/repositories/permission_repository.py

"""
Permission Repository

Data access layer for the ReBAC permission system.
Queries access_overrides, folders, libraries, and team_members
to resolve effective roles.
"""

from typing import Any, Dict, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class PermissionRepository:
    """Permission data access (async)"""

    def __init__(self):
        pass

    async def _get_client(self):
        """Get async client (loop-aware, safe for Celery workers)."""
        return await get_async_supabase_admin()

    async def get_access_override(
        self, object_type: str, object_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get a direct access override for a specific object and user."""
        try:
            client = await self._get_client()
            result = (
                await client.table("access_overrides")
                .select("*")
                .eq("object_type", object_type)
                .eq("object_id", object_id)
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get access override: {e}")
            return None

    async def get_folder_by_id(self, folder_id: str) -> Optional[Dict[str, Any]]:
        """Get folder with parent_id and scope info."""
        try:
            client = await self._get_client()
            result = (
                await client.table("folders")
                .select("id, parent_id, scope_id, visibility")
                .eq("id", folder_id)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get folder {folder_id}: {e}")
            return None

    async def get_library_by_id(self, library_id: str) -> Optional[Dict[str, Any]]:
        """Get library with visibility and scope info.

        ``libraries`` has no ``team_id`` column — team scope lives in
        ``scope_type='team'`` + ``scope_id``. Earlier code selected
        ``team_id`` directly, which produced a steady stream of PG 42703
        ERRORs (37 in last 7d). Returning the canonical scope columns
        instead so callers can resolve team/user/project ownership.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table("libraries")
                .select("id, scope_type, scope_id, visibility")
                .eq("id", library_id)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get library {library_id}: {e}")
            return None

    async def get_team_member_role(self, user_id: str, team_id: str) -> Optional[str]:
        """Get a user's role in a team (owner/admin/member)."""
        try:
            client = await self._get_client()
            result = (
                await client.table("team_members")
                .select("role")
                .eq("user_id", user_id)
                .eq("team_id", team_id)
                .limit(1)
                .execute()
            )
            if result.data:
                return result.data[0]["role"]
            return None
        except Exception as e:
            logger.error(f"Failed to get team role for user {user_id}: {e}")
            return None

    async def get_resource_item_scope(
        self, resource_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get the scope (team/personal) for a resource via resource_items."""
        try:
            client = await self._get_client()
            result = (
                await client.table("resource_items")
                .select("scope_id, folder_id")
                .eq("resource_id", resource_id)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get resource scope for {resource_id}: {e}")
            return None
