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
        """Wave F (F7) + Wave G (G1): handle the built-in 'remember' tool.

        args contract:
          summary (str, required): the fact to remember
          when_to_use (str, required): retrieval cue (embedding source)
          scope (str, optional): one of session/agent_user/user_global/
                                 team_agent/root_tree (default agent_user)
          agent_id (str, required): identity context
          user_id (str, required): identity context
          session_id (str, optional)

        G1 wires the persistor through to the real MemoryWriter — agent
        can now actually persist a memory mid-turn (extracted_from='active_call').
        """
        from uuid import UUID
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
        if not user_id:
            return {
                "skill": "remember",
                "error": "user_id required for remember()",
            }

        ctx = RememberContext(
            agent_id=agent_id,
            user_id=user_id,
            session_id=session_id,
        )

        async def _real_persistor(request, context):
            """Embed when_to_use → INSERT one row into agent_memories."""
            from app.db.supabase_client import get_async_supabase_admin
            from app.services.embedding_service import EmbeddingService

            try:
                embedder = EmbeddingService()
                embedding = await embedder.generate_embedding(request.when_to_use)
                if embedding is None:
                    return None
                sb = await get_async_supabase_admin()
                result = await (
                    sb.table("agent_memories")
                    .insert(
                        {
                            "agent_id": str(UUID(context.agent_id)),
                            "user_id": str(UUID(context.user_id)),
                            "scope": request.scope,
                            "summary": request.summary,
                            "when_to_use": request.when_to_use,
                            "extracted_from": request.extracted_from,
                            "embedding": embedding,
                            "metadata_json": {},
                        }
                    )
                    .execute()
                )
                rows = result.data or []
                if not rows:
                    return None
                new_id = str(rows[0]["id"])
                from app.agent_framework._metrics_helper import inc_metric
                inc_metric("memory_active_remember_persisted")

                # Wave J (J6): post-insert contradiction check.
                # Same logic as MemoryWriter F3 — find HIGH-similarity
                # neighbors + classify; replaces / contradicts mark old
                # superseded_by=new. Skip silently if no Qwen key (cheap
                # LLM unavailable) — the memory is already saved.
                try:
                    await _active_remember_contradiction_check(
                        sb=sb,
                        new_id=new_id,
                        new_summary=request.summary,
                        new_embedding=embedding,
                        agent_id=str(UUID(context.agent_id)),
                        user_id=str(UUID(context.user_id)),
                        scope=request.scope,
                    )
                except Exception:
                    pass  # never block insert on contradiction-check fail

                return new_id
            except Exception:
                return None

        result = await handle_remember(
            summary=summary,
            when_to_use=when_to_use,
            scope=scope,
            context=ctx,
            persistor=_real_persistor,
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


async def _active_remember_contradiction_check(
    *,
    sb: Any,
    new_id: str,
    new_summary: str,
    new_embedding: list,
    agent_id: str,
    user_id: str,
    scope: str,
) -> None:
    """Wave J (J6): mirror of MemoryWriter F3 contradiction check for
    the active_remember tool path. Same logic, just inlined here so
    the SkillToolService doesn't have to construct a full MemoryWriter."""
    from app.core.config import settings
    from app.services.memory.contradiction import (
        HIGH_SIMILARITY,
        classify_pair,
        select_supersede_targets,
    )

    api_key = (
        getattr(settings, "DASHSCOPE_API_KEY", None)
        or getattr(settings, "QWEN_API_KEY", None)
    )
    if not api_key:
        return  # no cheap LLM available — skip contradiction check

    # Pull HIGH-similarity neighbors (cosine in Python; same shape as
    # MemoryWriter._nearest_existing).
    try:
        result = (
            await sb.table("agent_memories")
            .select("id, summary, embedding")
            .eq("agent_id", agent_id)
            .eq("user_id", user_id)
            .eq("scope", scope)
            .eq("status", "active")
            .neq("id", new_id)
            .limit(50)
            .execute()
        )
    except Exception:
        return

    rows = result.data or []
    scored = []
    for row in rows:
        emb = row.get("embedding")
        if not emb:
            continue
        sim = _cosine_helper(new_embedding, emb)
        if sim >= HIGH_SIMILARITY:
            scored.append((sim, row))
    scored.sort(reverse=True, key=lambda t: t[0])
    neighbors = [r for _, r in scored[:3]]
    if not neighbors:
        return

    # Cheap classifier closure
    async def _classifier(prompt: str) -> str:
        try:
            from app.schemas.ai_library import ComposedSystemPrompt
            from app.services.ai_provider import QwenAdapter
            adapter = QwenAdapter(api_key=api_key, model="qwen-turbo")
            cs = ComposedSystemPrompt(
                agent_id=None,  # type: ignore[arg-type]
                agent_slug="active_remember_classifier",
                model="qwen-turbo",
                temperature=0.0,
                max_tokens=128,
                system_message="Classify two memories. Output one word.",
                tools=[], skill_manifest=[],
                cache_fingerprint="active_remember_classifier_v1",
            )
            resp = await adapter.call(cs, [{"role": "user", "content": prompt}])
            return resp.get("content") or ""
        except Exception:
            return ""

    decisions = []
    for old in neighbors:
        d = await classify_pair(
            old_summary=old["summary"],
            old_id=old["id"],
            new_summary=new_summary,
            classifier=_classifier,
        )
        if d is not None:
            decisions.append(d)

    target_ids = select_supersede_targets(decisions)
    for old_id in target_ids:
        try:
            await (
                sb.table("agent_memories")
                .update({"status": "superseded", "superseded_by": new_id})
                .eq("id", old_id)
                .execute()
            )
            from app.agent_framework._metrics_helper import inc_metric
            inc_metric("memory_superseded_by_contradiction")
        except Exception:
            pass


def _cosine_helper(a: list, b: list) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
