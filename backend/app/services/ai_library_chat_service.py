"""AI Library chat service — thin layer over AgentRunner + ai_sessions/ai_messages.

Replaces the legacy ``AgentService`` + ``AISessionService`` pair. Every
chat turn goes through the exact same AgentRunner + RunRecorder stack
that powers script_ai / summarize / storyboard, so:

- agent_runs rows land automatically (sidebar pulse, Runs tab, Usage
  dashboard all light up for chat too)
- monthly budget caps apply — a paused agent rejects chat pre-flight
- skills bound to the agent are callable mid-conversation via the Skill
  tool loop

Session storage still lives in the shared ``ai_sessions`` /
``ai_messages`` tables; only the execution path moved.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from loguru import logger

from app.core.config import settings
from app.db.supabase_client import get_async_supabase_admin
from app.repositories.agent_repository import AgentRepository
from app.repositories.skill_repository import SkillRepository
from app.services.agent_runner import AgentRunner  # noqa: F401  patched in tests
from app.services.ai_adapters.factory import get_adapter, provider_key_for_model
from app.services.ai_library_chat_wiring import build_agent_runner_stack
from app.services.prompt_composer import ComposerInput, PromptComposer
from app.services.run_recorder import AgentPausedError, RunRecorder
from app.services.skill_tool_service import SkillToolService  # noqa: F401


class AILibraryChatService:
    """Session + chat operations bound to the AI Library framework.

    Stateless — instantiated per request. Reads/writes flow through the
    Supabase admin client because server-side ownership checks (user_id
    match) happen inline; RLS on ai_sessions would add a second layer
    but isn't required when the service admits a user_id and cross-
    references it against row.user_id on every op.
    """

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    async def create_session(
        self,
        *,
        user_id: UUID,
        agent_slug: str,
        title: str,
        project_id: Optional[int] = None,
        team_id: Optional[int] = None,
        context_type: Optional[str] = None,
        context_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create an ai_sessions row bound to ``agent_slug``.

        We resolve ``agent_slug`` → ``agent_id`` at create-time so later
        chat turns don't have to refetch, and so renaming the slug
        doesn't orphan sessions. Raises 404 when the agent is missing.
        """
        agent = await AgentRepository().get_by_slug(agent_slug)
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"agent not found: {agent_slug}",
            )

        supabase = await get_async_supabase_admin()
        row: Dict[str, Any] = {
            "user_id": str(user_id),
            "agent_id": agent["id"],
            "agent_slug": agent_slug,
            "title": title,
            "status": "active",
            "total_tokens": 0,
            "message_count": 0,
        }
        if project_id is not None:
            row["project_id"] = project_id
        if team_id is not None:
            row["team_id"] = team_id
        if context_type is not None:
            row["context_type"] = context_type
        if context_id is not None:
            row["context_id"] = context_id

        resp = await supabase.table("ai_sessions").insert(row).execute()
        if not resp.data:
            logger.error(f"[ChatService] create_session failed for user={user_id}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create AI session",
            )
        return resp.data[0]

    async def list_sessions(
        self,
        *,
        user_id: UUID,
        agent_slug: Optional[str] = None,
        project_id: Optional[int] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Return the caller's sessions, newest-updated first.

        Filters out soft-deleted (``status='deleted'``) rows. Optional
        agent_slug + project_id narrow the result set to what the UI is
        currently viewing.
        """
        supabase = await get_async_supabase_admin()
        query = (
            supabase.table("ai_sessions")
            .select("*")
            .eq("user_id", str(user_id))
            .neq("status", "deleted")
            .order("updated_at", desc=True)
            .limit(limit)
        )
        if agent_slug is not None:
            query = query.eq("agent_slug", agent_slug)
        if project_id is not None:
            query = query.eq("project_id", project_id)

        resp = await query.execute()
        return resp.data or []

    async def get_session(self, session_id: UUID, *, user_id: UUID) -> Dict[str, Any]:
        """Fetch a single session, enforcing ownership."""
        supabase = await get_async_supabase_admin()
        resp = (
            await supabase.table("ai_sessions")
            .select("*")
            .eq("id", str(session_id))
            .maybe_single()
            .execute()
        )
        session = resp.data if resp and resp.data else None
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="session not found"
            )
        if str(session.get("user_id")) != str(user_id):
            # 404 rather than 403 — don't leak session existence.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="session not found"
            )
        return session

    async def get_messages(
        self, session_id: UUID, *, user_id: UUID, limit: int = 200
    ) -> List[Dict[str, Any]]:
        """Fetch messages for a session in chronological order."""
        await self.get_session(session_id, user_id=user_id)
        supabase = await get_async_supabase_admin()
        resp = (
            await supabase.table("ai_messages")
            .select("*")
            .eq("session_id", str(session_id))
            .order("created_at", desc=False)
            .limit(limit)
            .execute()
        )
        return resp.data or []

    async def update_session(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Rename a session. Only the owner can rename."""
        await self.get_session(session_id, user_id=user_id)
        updates: Dict[str, Any] = {}
        if title is not None:
            updates["title"] = title
        if not updates:
            return await self.get_session(session_id, user_id=user_id)
        supabase = await get_async_supabase_admin()
        resp = (
            await supabase.table("ai_sessions")
            .update(updates)
            .eq("id", str(session_id))
            .execute()
        )
        return (
            resp.data[0]
            if resp.data
            else await self.get_session(session_id, user_id=user_id)
        )

    async def delete_session(self, session_id: UUID, *, user_id: UUID) -> None:
        """Soft-delete a session (status='deleted'). Messages stay for audit."""
        await self.get_session(session_id, user_id=user_id)
        supabase = await get_async_supabase_admin()
        await (
            supabase.table("ai_sessions")
            .update({"status": "deleted"})
            .eq("id", str(session_id))
            .execute()
        )

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    async def chat(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        content: str,
    ) -> Dict[str, Any]:
        """Send ``content`` as a user turn, get an assistant response.

        Flow:
          1. Load session (404 if not owner)
          2. Load message history for this session
          3. Persist the new user message
          4. Compose via PromptComposer (IDENTITY / SOUL / AGENT + skills XML)
          5. Call AgentRunner inside a RunRecorder — this is where tokens,
             cost, budget guard, cancel polling, skill tool-calls all live
          6. Persist the assistant message
          7. Bump session counters
          8. Return {message, usage, run_id}

        The RunRecorder.agent_id links the agent_runs row back to the
        agent that produced this turn; the session_id field ties
        multiple turns together for the UsagePage / Runs tab.
        """
        session = await self.get_session(session_id, user_id=user_id)
        agent_slug = session.get("agent_slug")
        if not agent_slug:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="session has no agent_slug bound — cannot chat",
            )

        history = await self.get_messages(session_id, user_id=user_id)

        supabase = await get_async_supabase_admin()

        # Persist the user turn BEFORE calling the model so partial
        # failures (LLM timeout, budget pause) still leave a record of
        # what the user tried to ask.
        user_msg_resp = (
            await supabase.table("ai_messages")
            .insert(
                {
                    "session_id": str(session_id),
                    "role": "user",
                    "content": content,
                }
            )
            .execute()
        )
        user_msg = user_msg_resp.data[0] if user_msg_resp.data else None

        # M1.5 wiring: load agent record so we can read budget/fallback,
        # then build the full runner stack (HookRegistry pre-populated,
        # fallback chain wrapping adapter, memory recall pre-fetched).
        agent_repo = AgentRepository()
        skill_repo = SkillRepository()
        agent_record = await agent_repo.get_by_slug(agent_slug)
        if not agent_record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"agent slug not found: {agent_slug}",
            )

        stack = await build_agent_runner_stack(
            agent=agent_record,
            skill_repo=skill_repo,
            user_id=user_id,
            session_id=session_id,
            user_query=content,
            settings=settings,
        )

        # Compose prompt with recalled memories injected after cache_boundary.
        # M (G→J→F→I→H follow-up): prompt now explicitly notes that the
        # Delegate tool can hand off to persistent specialists listed in
        # <available_workers>. Without this hint the LLM tends to do the
        # work itself even when a better specialist exists. Use await=true
        # if you need the result in the same turn.
        composer = PromptComposer(agent_repo, skill_repo)
        composed = await composer.compose(
            ComposerInput(
                agent_slug=agent_slug,
                request_instructions=(
                    "You are in an interactive chat session with the user. "
                    "Respond conversationally. Use the Skill tool when a "
                    "bound skill is clearly applicable; otherwise answer "
                    "directly in natural language. "
                    "If <available_workers> lists a specialist agent that's "
                    "a clearly better fit for the request than you are "
                    "(e.g. summarize for transcript condensation, analyze "
                    "for visual analysis), call Delegate(agent_slug=..., "
                    "prompt=..., await=true) and weave the returned result "
                    "into your reply. Use Delegate only when the specialist "
                    "is a clear win — for general chat, just answer directly."
                ),
                recalled_memories=stack.recalled_memories,
            )
        )

        runner = stack.runner

        # Build the message history payload: prior turns + new user turn.
        # We always pass role+content; tool-call stubs that AgentRunner
        # generated in a previous turn aren't replayed (they're ephemeral
        # — the persisted assistant message captures the final content).
        user_messages: List[Dict[str, Any]] = []
        for msg in history:
            role = msg.get("role")
            if role not in ("user", "assistant", "system"):
                continue
            user_messages.append({"role": role, "content": msg.get("content") or ""})
        user_messages.append({"role": "user", "content": content})

        # M1.5 wiring: compact the message list if it has grown past the
        # threshold. Compactor preserves tool_use/result pairs so the
        # next API call won't 400. Failure degrades to "send full history
        # and let the model deal with it" — never breaks the chat.
        user_messages = await self._maybe_compact(user_messages)

        model = composed.model or ""
        try:
            provider = provider_key_for_model(model) if model else None
        except ValueError:
            provider = None

        # Wrap in RunRecorder. agent_id comes from ``composed`` so we
        # don't re-query. team_id / project_id tag the run for Usage's
        # team/project scope queries.
        try:
            async with RunRecorder(
                agent_id=composed.agent_id,
                user_id=user_id,
                trigger="chat",
                session_id=session_id,
                team_id=session.get("team_id"),
                project_id=session.get("project_id"),
                model=model or None,
                provider=provider,
                input_summary=content,
                metadata={"full_input": content},
            ) as recorder:
                result = await runner.run_turn(
                    composed,
                    user_messages=user_messages,
                    recorder=recorder,
                )
                run_id = recorder.run_id
                # Pull usage off the recorder — that's the single source
                # of truth for what just got written to agent_runs.
                usage_snapshot = {
                    "prompt_tokens": recorder.prompt_tokens,
                    "completion_tokens": recorder.completion_tokens,
                }
                assistant_content = result.get("content") or ""
                recorder.set_summaries(output_summary=assistant_content)
        except AgentPausedError as err:
            logger.warning(f"[ChatService] agent paused: {err}")
            # Mark the user message with a hint so the UI can show "the
            # agent is paused" without a separate error path.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"agent paused: {err}",
            )

        if result.get("error"):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"agent runner error: {result.get('error')}",
            )

        # Persist the assistant turn.
        asst_resp = (
            await supabase.table("ai_messages")
            .insert(
                {
                    "session_id": str(session_id),
                    "role": "assistant",
                    "content": assistant_content,
                    "agent_id": str(composed.agent_id),
                    "prompt_tokens": usage_snapshot["prompt_tokens"],
                    "completion_tokens": usage_snapshot["completion_tokens"],
                    "metadata_json": {"run_id": str(run_id)} if run_id else {},
                }
            )
            .execute()
        )
        asst_msg = asst_resp.data[0] if asst_resp.data else None

        # Bump session counters. Cheap single update; ignore ON CONFLICT
        # since this session is owned by this user and exists.
        turn_tokens = (usage_snapshot["prompt_tokens"] or 0) + (
            usage_snapshot["completion_tokens"] or 0
        )
        prior_total = int(session.get("total_tokens") or 0)
        prior_count = int(session.get("message_count") or 0)
        await (
            supabase.table("ai_sessions")
            .update(
                {
                    "total_tokens": prior_total + turn_tokens,
                    "message_count": prior_count + 2,
                }
            )
            .eq("id", str(session_id))
            .execute()
        )

        return {
            "user_message": user_msg,
            "assistant_message": asst_msg,
            "usage": usage_snapshot,
            "run_id": str(run_id) if run_id else None,
        }

    async def _maybe_compact(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Compact long histories. Failure → return original messages.

        Compaction calls a cheap auxiliary LLM to summarise the
        head; degrading on failure is fine since the LLM call itself
        will eventually 400 if context truly overflows, and the user
        will see a clear error instead of a silent corruption.
        """
        from app.services.llm_compactor import (
            DEFAULT_AUTO_COMPACTION_INPUT_TOKENS,
            compact_messages,
            estimate_tokens,
        )

        if estimate_tokens(messages) < DEFAULT_AUTO_COMPACTION_INPUT_TOKENS:
            return messages

        async def _summarizer(head: List[Dict[str, Any]]) -> str:
            try:
                cheap_model = "qwen-turbo"
                adapter = get_adapter(cheap_model, settings)
                from uuid import UUID as _UUID

                from app.schemas.ai_library import ComposedSystemPrompt

                composed = ComposedSystemPrompt(
                    agent_id=_UUID(int=0),
                    agent_slug="compactor",
                    model=cheap_model,
                    temperature=0.0,
                    max_tokens=2048,
                    system_message=(
                        "You summarise chat history. Capture decisions made, "
                        "facts established, and the current task state. "
                        "Be terse. No preamble."
                    ),
                    tools=[],
                    skill_manifest=[],
                    cache_fingerprint="compactor_v1",
                )
                resp = await adapter.call(
                    composed,
                    [{"role": "user", "content": _format_history_for_summary(head)}],
                )
                return resp["choices"][0]["message"].get("content") or ""
            except Exception:
                logger.exception(
                    "[chat] compactor summarizer failed; using empty summary"
                )
                return "[history truncated for context length]"

        try:
            result = await compact_messages(messages, summarizer=_summarizer)
            if result.compacted:
                logger.info(
                    "[chat] compacted: %d → %d tokens (%d head messages summarised)",
                    result.estimated_input_tokens_before,
                    result.estimated_input_tokens_after,
                    result.head_message_count,
                )
                return result.messages
        except Exception:
            logger.exception("[chat] compact_messages crashed; sending full history")

        return messages


def _format_history_for_summary(messages: List[Dict[str, Any]]) -> str:
    """Render head messages as a numbered transcript for the summariser."""
    parts = []
    for i, msg in enumerate(messages, start=1):
        role = msg.get("role") or "?"
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = " ".join(str(p) for p in content)
        truncated = content[:1000] + ("..." if len(content) > 1000 else "")
        parts.append(f"[{i}] {role}: {truncated}")
    return "\n".join(parts)
