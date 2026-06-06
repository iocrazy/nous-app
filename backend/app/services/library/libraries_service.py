# app/services/libraries_service.py

"""
Libraries Service

Business logic for team libraries: creation, listing, and management.
"""

from typing import Optional

from app.repositories.libraries_repository import get_libraries_repository


class LibrariesService:
    """Library business logic"""

    def __init__(self):
        self.repo = get_libraries_repository()

    async def create_library(
        self,
        user_id: str,
        name: str,
        scope_id: str,
        icon: Optional[str] = None,
        color: Optional[str] = None,
    ) -> dict:
        data = {
            "name": name,
            "scope_type": "team",
            "scope_id": scope_id,
            "created_by": user_id,
            "icon": icon,
            "color": color,
        }
        return await self.repo.create(data)

    async def list_libraries(self, scope_id: str) -> list:
        return await self.repo.list_by_scope("team", scope_id)

    async def get_library(self, library_id: str) -> dict | None:
        return await self.repo.get_by_id(library_id)

    async def update_library(self, library_id: str, data: dict) -> dict:
        return await self.repo.update(library_id, data)

    async def delete_library(self, library_id: str) -> bool:
        return await self.repo.delete(library_id)
