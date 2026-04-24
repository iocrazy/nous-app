"""Backend executor for the Skill tool — reads DB row, returns body/file to model.

Binary-ref handling
-------------------
``skill_files`` rows may be ``binary-ref`` (PDFs, images, etc. — content
is NULL, the URL lives on ``binary_url``). A naive "return content" path
would hand the agent an empty string with no signal, and it would
silently give up. Instead we return an explicit diagnostic payload
including the URL and a short note so the agent knows to fetch the
binary via its own file-reader tool (or report the limitation back to
the user). Inlining binary content would need a server-side extractor
(pdftotext / pdfplumber / etc.); that's V6+ work.
"""

from __future__ import annotations

from typing import Any, Optional

from app.repositories.skill_repository import SkillRepository

_BINARY_NOTE = (
    "This file is a binary reference (PDF/image/etc.) and cannot be "
    "returned inline. Use the URL with your own file-reader tool, or "
    "report the limitation back to the user if you cannot fetch it."
)


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

            file_type = f.get("file_type")
            binary_url = f.get("binary_url")

            # Binary references have NULL content — surface the URL + a
            # human-readable note so the agent has something actionable
            # instead of an empty string it silently ignores.
            if file_type == "binary-ref" or (binary_url and not f.get("content")):
                return {
                    "skill": slug,
                    "file": file_path,
                    "description": skill.get("description", ""),
                    "prompt": _BINARY_NOTE,
                    "file_type": file_type,
                    "binary_url": binary_url,
                    "note": _BINARY_NOTE,
                }

            return {
                "skill": slug,
                "file": file_path,
                "description": skill.get("description", ""),
                "prompt": f.get("content") or "",
                "file_type": file_type,
            }

        return {
            "skill": slug,
            "description": skill.get("description", ""),
            "prompt": skill.get("body_md") or "",
        }
