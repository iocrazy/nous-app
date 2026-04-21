"""Script Service — business logic for script projects and chapters."""

from typing import Any, Dict, List, Optional

DEFAULT_FORMAT_PRESET: Dict[str, Any] = {
    "format_preset": {
        "scene_heading": "场景N：场景名 – 时间 – 内/外景",
        "dialogue": "角色名：（动作描述）台词内容",
        "scene_separator": "hr",
        "action": "paragraph",
        "voiceover": "italic",
    }
}

from loguru import logger

from app.repositories.script_repository import (
    ScriptAssetRepository,
    ScriptChapterRepository,
    ScriptProjectRepository,
    ScriptStoryboardLinkRepository,
)
from app.services.display_code_service import generate_display_code


def _extract_text_from_content_json(content_json: dict) -> str:
    """Extract plain text from TipTap ProseMirror JSON document."""
    if not content_json or "content" not in content_json:
        return ""
    texts: List[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "text":
                texts.append(node.get("text", ""))
            for child in node.get("content", []):
                _walk(child)

    _walk(content_json)
    return "\n".join(texts)


class ScriptService:
    """Orchestrates script project, chapter, asset, and link operations."""

    def __init__(self) -> None:
        self.project_repo = ScriptProjectRepository()
        self.chapter_repo = ScriptChapterRepository()
        self.asset_repo = ScriptAssetRepository()
        self.link_repo = ScriptStoryboardLinkRepository()

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
            "settings_json": DEFAULT_FORMAT_PRESET,
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
            logger.warning("Failed to generate display_code for script: %s", exc)

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
        update_data = {**data}
        if update_data.get("content_json"):
            update_data["content"] = _extract_text_from_content_json(
                update_data["content_json"]
            )
        return await self.chapter_repo.update(chapter_id, update_data)

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
                    if ch_copy.get("content_json"):
                        ch_copy["content"] = _extract_text_from_content_json(
                            ch_copy["content_json"]
                        )
                    await self.chapter_repo.update(ch_id, ch_copy)

        chapters = await self.chapter_repo.get_by_script(script_id)
        return {"chapters": chapters}

    # ─── Asset operations ────────────────────────────────────────────

    async def create_asset(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return await self.asset_repo.create(data)

    async def update_asset(self, asset_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        return await self.asset_repo.update(asset_id, data)

    async def delete_asset(self, asset_id: str) -> None:
        await self.asset_repo.delete(asset_id)

    async def list_assets(
        self, script_id: str, asset_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        return await self.asset_repo.list_by_script(script_id, asset_type=asset_type)

    # ─── Script-Storyboard link operations ───────────────────────────

    async def create_storyboard_link(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return await self.link_repo.create(data)

    async def delete_storyboard_link(self, link_id: str) -> None:
        await self.link_repo.delete(link_id)

    async def list_links_by_chapter(self, chapter_id: str) -> List[Dict[str, Any]]:
        return await self.link_repo.list_by_chapter(chapter_id)
