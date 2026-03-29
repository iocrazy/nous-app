"""Script Service — business logic for script projects and chapters."""

from typing import Any, Dict, List, Optional

from loguru import logger

from app.repositories.script_repository import (
    ScriptChapterRepository,
    ScriptProjectRepository,
)
from app.services.display_code_service import generate_display_code


class ScriptService:
    """Orchestrates script project and chapter operations."""

    def __init__(self) -> None:
        self.project_repo = ScriptProjectRepository()
        self.chapter_repo = ScriptChapterRepository()

    async def create_project(
        self,
        team_id: str,
        user_id: str,
        project_id: int,
        name: str,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "team_id": team_id,
            "created_by": user_id,
            "project_id": project_id,
            "name": name,
        }
        if description is not None:
            data["description"] = description

        project = await self.project_repo.create(data)

        try:
            display_code = await generate_display_code(int(team_id), "S")
            project = await self.project_repo.update(
                project["id"], {"display_code": display_code}
            )
        except Exception as exc:
            logger.warning(f"Failed to generate display_code for script: {exc}")

        return project

    async def list_projects(
        self,
        project_id: int,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self.project_repo.list_by_project(
            project_id=project_id,
            page=page,
            limit=limit,
            search=search,
        )

    async def get_project_full(self, script_id: str) -> Optional[Dict[str, Any]]:
        project = await self.project_repo.get_by_id(script_id)
        if not project:
            return None
        chapters = await self.chapter_repo.get_by_script(script_id)
        return {"project": project, "chapters": chapters}

    async def update_project(
        self, script_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        return await self.project_repo.update(script_id, data)

    async def soft_delete_project(self, script_id: str) -> None:
        await self.project_repo.soft_delete(script_id)

    async def update_viewport(
        self, script_id: str, viewport_json: Dict[str, Any]
    ) -> None:
        await self.project_repo.update(script_id, {"viewport_json": viewport_json})

    # ─── Chapter operations ───────────────────────────────────────────

    async def create_chapter(
        self, script_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        data_with_script = {**data, "script_id": script_id}
        return await self.chapter_repo.create(data_with_script)

    async def update_chapter(
        self, chapter_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        return await self.chapter_repo.update(chapter_id, data)

    async def delete_chapter(self, chapter_id: str) -> None:
        await self.chapter_repo.delete(chapter_id)

    async def sync_canvas(
        self,
        script_id: str,
        added: List[Dict[str, Any]],
        updated: List[Dict[str, Any]],
        deleted_ids: List[str],
    ) -> Dict[str, Any]:
        if deleted_ids:
            for cid in deleted_ids:
                await self.chapter_repo.delete(cid)

        if added:
            await self.chapter_repo.bulk_upsert(script_id, added)

        if updated:
            for ch in updated:
                ch_copy = {**ch}
                ch_id = ch_copy.pop("id", None)
                if ch_id:
                    await self.chapter_repo.update(ch_id, ch_copy)

        chapters = await self.chapter_repo.get_by_script(script_id)
        return {"chapters": chapters}
