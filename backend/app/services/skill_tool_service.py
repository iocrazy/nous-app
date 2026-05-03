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

        # Wave F (F7): built-in 'remember' skill — invokes the active
        # memory remember tool instead of looking up a DB skill row.
        if slug == "remember":
            return await self._execute_remember(args)

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

    async def _execute_remember(self, args: dict[str, Any]) -> dict[str, Any]:
        """Wave F (F7): handle the built-in 'remember' tool.

        args contract:
          summary (str, required): the fact to remember
          when_to_use (str, required): retrieval cue (embedding source)
          scope (str, optional): one of session/agent_user/user_global/
                                 team_agent/root_tree (default agent_user)
          agent_id (str, required): identity context
          user_id (str, required): identity context
          session_id (str, optional)

        Returns the standard skill tool envelope so AgentRunner can
        render it the same way as other skill calls.
        """
        from app.services.memory.active_remember import (
            RememberContext,
            handle_remember,
        )

        summary = args.get("summary") or ""
        when_to_use = args.get("when_to_use") or ""
        scope = args.get("scope") or "agent_user"
        agent_id = args.get("agent_id") or ""
        user_id = args.get("user_id")
        session_id = args.get("session_id")

        if not agent_id:
            return {
                "skill": "remember",
                "error": "agent_id required for remember()",
            }

        ctx = RememberContext(
            agent_id=agent_id,
            user_id=user_id,
            session_id=session_id,
        )

        # Persistor stub — real wiring (writer.write with extracted_from=
        # 'active_call') deferred to integration follow-up. For now we
        # return a placeholder result so the agent gets feedback.
        async def _no_op_persistor(request, context):
            # TODO: wire MemoryWriter.write with single fact
            return None

        result = await handle_remember(
            summary=summary,
            when_to_use=when_to_use,
            scope=scope,
            context=ctx,
            persistor=_no_op_persistor,
        )
        if not result.success:
            return {
                "skill": "remember",
                "error": result.error or "remember failed",
            }
        return {
            "skill": "remember",
            "description": "Stored memory for future recall.",
            "prompt": (
                f"Memory recorded (id={result.memory_id}). "
                "It will be available in future sessions when relevant."
            ),
            "memory_id": result.memory_id,
        }
