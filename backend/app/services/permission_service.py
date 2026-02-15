# app/services/permission_service.py

"""
Permission Service

Implements the ReBAC effective role algorithm (§4.3 of design doc).
Resolves a user's effective role on an object by checking:
1. Direct access override on the object
2. Parent folder hierarchy
3. Library-level access
4. Team role fallback
"""

from typing import Dict, List

from loguru import logger

from app.repositories.permission_repository import PermissionRepository

# Role → capabilities mapping
CAPABILITIES: Dict[str, List[str]] = {
    "admin": [
        "view", "download", "upload", "update",
        "copy", "move", "delete", "share", "manage",
    ],
    "editor": [
        "view", "download", "upload", "update",
        "copy", "move", "share",
    ],
    "viewer": ["view", "download"],
    "none": [],
}

# Team role → effective role mapping
TEAM_ROLE_MAP: Dict[str, str] = {
    "owner": "admin",
    "admin": "admin",
    "member": "viewer",
}


class PermissionService:
    """ReBAC permission resolution service."""

    def __init__(self):
        self._repo = PermissionRepository()

    async def get_effective_role(
        self,
        user_id: str,
        object_type: str,
        object_id: str,
        team_id: str,
    ) -> Dict:
        """
        Resolve the effective role for a user on an object.

        Algorithm (§4.3):
        1. Check direct override on this object
        2. Walk up hierarchy (folder→parent→library→team)
        3. Fall back to team role mapping

        Returns:
            {"role": str, "capabilities": list[str]}
        """
        try:
            role = await self._resolve_role(
                user_id, object_type, object_id, team_id
            )
        except Exception as e:
            logger.error(
                f"Error resolving role for user={user_id} "
                f"object={object_type}/{object_id}: {e}"
            )
            role = "viewer"

        capabilities = CAPABILITIES.get(role, [])
        return {"role": role, "capabilities": capabilities}

    async def _resolve_role(
        self,
        user_id: str,
        object_type: str,
        object_id: str,
        team_id: str,
    ) -> str:
        """Core resolution logic with hierarchy walk."""

        # Step 1: Check direct override
        override = await self._repo.get_access_override(
            object_type, object_id, user_id
        )
        if override:
            return override["role"]

        # Step 2: Walk up hierarchy based on object type
        if object_type == "folder":
            return await self._resolve_folder_role(
                user_id, object_id, team_id
            )
        elif object_type == "library":
            return await self._resolve_library_role(
                user_id, object_id, team_id
            )
        elif object_type == "resource":
            return await self._resolve_resource_role(
                user_id, object_id, team_id
            )

        # Step 3: Fall back to team role
        return await self._get_team_effective_role(user_id, team_id)

    async def _resolve_folder_role(
        self, user_id: str, folder_id: str, team_id: str
    ) -> str:
        """Walk folder → parent folder → library → team."""
        folder = await self._repo.get_folder_by_id(folder_id)
        if not folder:
            return await self._get_team_effective_role(user_id, team_id)

        # Check parent folder (if exists)
        if folder.get("parent_id"):
            parent_override = await self._repo.get_access_override(
                "folder", folder["parent_id"], user_id
            )
            if parent_override:
                return parent_override["role"]
            # Recurse up (max depth guarded by DB structure)
            return await self._resolve_folder_role(
                user_id, folder["parent_id"], team_id
            )

        # No parent → fall back to team role
        return await self._get_team_effective_role(user_id, team_id)

    async def _resolve_library_role(
        self, user_id: str, library_id: str, team_id: str
    ) -> str:
        """Check library visibility, then fall back to team role."""
        library = await self._repo.get_library_by_id(library_id)
        if not library:
            return await self._get_team_effective_role(user_id, team_id)

        # If library has restricted visibility and no override → none
        if library.get("visibility") == "restricted":
            return "none"

        return await self._get_team_effective_role(user_id, team_id)

    async def _resolve_resource_role(
        self, user_id: str, resource_id: str, team_id: str
    ) -> str:
        """Resolve via the resource's scope → folder → team."""
        scope = await self._repo.get_resource_item_scope(resource_id)
        if not scope:
            return await self._get_team_effective_role(user_id, team_id)

        # If resource is in a folder, check folder permissions
        if scope.get("folder_id"):
            folder_override = await self._repo.get_access_override(
                "folder", scope["folder_id"], user_id
            )
            if folder_override:
                return folder_override["role"]
            return await self._resolve_folder_role(
                user_id, scope["folder_id"], team_id
            )

        return await self._get_team_effective_role(user_id, team_id)

    async def _get_team_effective_role(
        self, user_id: str, team_id: str
    ) -> str:
        """Map team membership role to effective object role."""
        team_role = await self._repo.get_team_member_role(user_id, team_id)
        if not team_role:
            return "none"
        return TEAM_ROLE_MAP.get(team_role, "viewer")
