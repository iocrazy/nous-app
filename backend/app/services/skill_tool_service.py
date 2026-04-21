"""Backend executor for the Skill tool — reads DB row, returns body/file to model."""
from __future__ import annotations

from typing import Any, Optional

from app.repositories.skill_repository import SkillRepository


class SkillToolService:
    def __init__(self, skill_repo: SkillRepository) -> None:
        self.skill_repo = skill_repo

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        slug = (args.get("skill") or "").strip()
        if not slug:
            return {"error": "skill name required"}

        skill = await self.skill_repo.get_by_slug(slug)
        if not skill:
            return {"error": f"unknown skill: {slug}"}

        file_path: Optional[str] = args.get("file")
        if file_path:
            f = await self.skill_repo.get_file(int(skill["id"]), file_path)
            if not f:
                return {"error": f"unknown file '{file_path}' in skill '{slug}'"}
            return {
                "skill": slug,
                "file": file_path,
                "description": skill.get("description", ""),
                "prompt": f.get("content") or "",
                "file_type": f.get("file_type"),
            }

        return {
            "skill": slug,
            "description": skill.get("description", ""),
            "prompt": skill.get("body_md") or "",
        }
