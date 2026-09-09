"""AI Library chat service — thin layer over AgentRunner + the conversations store.

Replaces the legacy ``AgentService`` + ``AISessionService`` pair. Every
chat turn goes through the exact same AgentRunner + RunRecorder stack
that powers script_ai / summarize / storyboard, so:

- agent_runs rows land automatically (sidebar pulse, Runs tab, Usage
  dashboard all light up for chat too)
- monthly budget caps apply — a paused agent rejects chat pre-flight
- skills bound to the agent are callable mid-conversation via the Skill
  tool loop

Session storage (Conversations Phase 3, Task 6): the 1:1 compatibility
layer (the Supabase-backed legacy store + the dual-store router) has
been retired — ``ConversationsAiStore`` (``conversations`` /
``conversation_members`` / ``conversation_ai_meta`` / ``messages``,
migration 327 + 332) is now the sole ``MessageStore`` implementation.
Only the execution path moved; the row shape a caller sees is unchanged
(see ``message_store.py``'s Protocol docstring).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from loguru import logger

from app.agent_framework.abort_controller import RunAborted
from app.boundary.frame_markers import escape_frame_body
from app.core.config import settings
from app.repositories.agent_repository import get_agent_repository
from app.repositories.skill_repository import get_skill_repository
from app.services.ai.adapters.factory import provider_key_for_model
from app.services.ai.adapters.response import adapter_text
from app.services.ai.chat.ai_library_chat_wiring import build_agent_runner_stack
from app.services.ai.chat.asset_ref_resolver import (
    AssetRefFailure,
    coerce_asset_id,
    resolve_asset_refs,
)
from app.services.ai.chat.conversations_ai_store import ConversationsAiStore
from app.services.ai.chat.message_store import MessageStore
from app.services.ai.chat.resource_ref_resolver import (
    fetch_resource_meta,
    resolve_resource_refs,
)
from app.services.ai.permissions.high_risk_caps import (
    high_risk_caps,
    media_kill_switch_engaged,
)
from app.services.ai.prompts.prompt_composer import (
    ComposerInput,
    PromptComposer,
    render_available_resources,
)
from app.services.ai.runner.agent_runner import (  # noqa: F401  patched in tests
    AgentRunner,
)
from app.services.ai.runner.run_recorder import AgentPausedError, RunRecorder
from app.services.ai.skills.skill_tool_service import SkillToolService  # noqa: F401

# How many `asset_ref` attachments one turn may resolve.
#
# ASSET REFS ONLY, and the asymmetry is the whole reason the cap exists (final
# review I2). A `resource_ref` costs ONE BATCHED QUERY no matter how many the
# turn carries, so capping those would refuse a working path for no cost
# reason. P5's `asset_ref` costs about FIVE SERIAL round trips EACH (loadouts,
# files, two link traversals, one accessible-assets lookup), and
# `ChatMessageRequest.attachments` has no `max_length` — so before this cap any
# logged-in caller could POST 200 asset refs and spend a single request on
# ~1000 serial queries, the connection-pool starvation this repo has already
# paid for once.
#
# Deliberately the same number as the binary bucket's
# `chat_attachment_resolver.MAX_ATTACHMENTS_PER_TURN`, but a SEPARATE constant:
# the two bound different costs (bytes fetched versus database round trips) and
# coupling them would make one move for the other's reason.
#
# Over-cap refs are NOT resolved and NOT silently dropped: each gets an
# `attachment_limit_exceeded` entry in `attachment_failures`, indexed into the
# caller's full attachment list. Silent truncation is the flaw already recorded
# against the binary path; repeating it here would be a choice, not an
# inheritance.
#
# ⚠️ MIRRORED IN TYPESCRIPT. `frontend/components/chat/attachmentLimits.ts`
# holds the same 8 because the banner interpolates it into user-facing copy.
# `tests/services/ai/chat/test_attachment_limit_frontend_mirror.py` reads that
# file and fails if the two disagree — change one, change both.
MAX_ASSET_REF_ATTACHMENTS: int = 8

# The reason code the cap reports. Part of the closed `attachment_failures`
# vocabulary declared in `asset_ref_resolver.AssetRefFailureReason`; emitted
# HERE rather than in the resolver because the resolver is handed a list that
# has already been capped — it cannot see what was refused.
ATTACHMENT_LIMIT_REASON: str = "attachment_limit_exceeded"


@dataclass(frozen=True)
class _ChatAnswer:
    """A validated chat-side answer (or supersede), recorded once the user
    message that carries it has been persisted."""

    message_id: Any
    question_id: str
    run_id: Optional[str]
    value: Optional[str]
    superseded: bool


from app.services.ai.runner.run_recorder import (  # noqa: E402 — test seam
    event_writer_for_run as _event_writer_for_run,
)


class AILibraryChatService:
    """Session + chat operations bound to the AI Library framework.

    Stateless — instantiated per request. Reads/writes flow through
    ``ConversationsAiStore`` (the sole ``MessageStore`` implementation);
    server-side ownership checks (user_id match) happen inline in this
    service on every op.
    """

    def __init__(self, store: Optional[MessageStore] = None) -> None:
        self._store = store or ConversationsAiStore()

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
        agent = await get_agent_repository().get_by_slug(agent_slug)
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"agent not found: {agent_slug}",
            )

        row = await self._store.create_session(
            user_id=str(user_id),
            agent_slug=agent_slug,
            agent_id=agent["id"],
            title=title,
            project_id=project_id,
            team_id=team_id,
            context_type=context_type,
            context_id=context_id,
        )
        if not row:
            logger.error(f"[ChatService] create_session failed for user={user_id}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create AI session",
            )
        return row

    async def list_sessions(
        self,
        *,
        user_id: UUID,
        agent_slug: Optional[str] = None,
        project_id: Optional[int] = None,
        limit: int = 50,
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return the caller's sessions, newest-updated first.

        Filters out soft-deleted (``status='deleted'``) rows. Optional
        agent_slug + project_id narrow the result set to what the UI is
        currently viewing; ``search`` is a server-side (ILIKE) title filter
        so the whole history is searchable, not just the returned page.
        """
        return await self._store.list_sessions(
            user_id=str(user_id),
            agent_slug=agent_slug,
            project_id=project_id,
            limit=limit,
            search=search,
        )

    async def get_session(self, session_id: str, *, user_id: UUID) -> Dict[str, Any]:
        """Fetch a single session, enforcing ownership."""
        session = await self._store.get_session(session_id=session_id)
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
        self, session_id: str, *, user_id: UUID, limit: int = 200
    ) -> List[Dict[str, Any]]:
        """Fetch messages for a session in chronological order."""
        await self.get_session(session_id, user_id=user_id)
        return await self._store.get_messages(session_id=session_id, limit=limit)

    async def update_session(
        self,
        session_id: str,  # ai_sessions.id BIGINT Snowflake (mig 231)
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
        row = await self._store.rename_session(
            session_id=session_id, title=updates["title"]
        )
        return row or await self.get_session(session_id, user_id=user_id)

    async def delete_session(self, session_id: str, *, user_id: UUID) -> None:
        """Soft-delete a session (status='deleted'). Messages stay for audit."""
        await self.get_session(session_id, user_id=user_id)
        await self._store.soft_delete_session(session_id=session_id)

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    async def chat_stream(
        self,
        session_id: str,  # ai_sessions.id BIGINT Snowflake (mig 231)
        *,
        user_id: UUID,
        content: str,
        plan_mode: Optional[str] = None,
        attachments: Optional[list] = None,
        script_context: Optional[dict] = None,
        answer_to: Optional[str] = None,
    ):
        """P2: real streaming variant of chat.

        Yields event dicts: {type: 'delta' | 'done' | 'error', data: {...}}.

        Strategy:
          - Drive ``self.chat`` with a chunk_callback that pushes each
            adapter-level delta onto an asyncio.Queue.
          - Concurrently consume the queue and yield SSE deltas.
          - When chat() returns, drain remaining queue items, yield 'done'.

        TTFT (time-to-first-token) is now bounded by the model's first
        token emission, not the full turn. Tool-using turns still work —
        runner.stream_turn executes tool_calls between iterations and
        re-streams; synthetic "→ Running skill..." text arrives as
        delta chunks.

        Each ``delta`` event carries (text, offset). ``done`` has usage
        + run_id + assistant message id + tool_calls trace + total_chars.
        """
        import asyncio as _asyncio

        queue: _asyncio.Queue[Optional[str]] = _asyncio.Queue()

        async def _on_chunk(text: str) -> None:
            await queue.put(text)

        # Run chat() in the background; consume queue as deltas arrive.
        chat_task = _asyncio.create_task(
            self.chat(
                session_id,
                user_id=user_id,
                content=content,
                plan_mode=plan_mode,
                chunk_callback=_on_chunk,
                attachments=attachments,
                script_context=script_context,
                answer_to=answer_to,
            ),
            name=f"chat-stream-{session_id}",
        )

        # Sentinel-on-done: when chat completes, push None so the
        # consumer loop exits.
        async def _close_queue() -> None:
            try:
                await chat_task
            except Exception:
                # exception will be re-raised below when we await chat_task
                pass
            finally:
                await queue.put(None)

        closer_task = _asyncio.create_task(_close_queue(), name="chat-stream-closer")

        offset = 0
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield {
                    "type": "delta",
                    "data": {"text": item, "offset": offset},
                }
                offset += len(item)
        except _asyncio.CancelledError:
            chat_task.cancel()
            raise
        finally:
            # Make sure background tasks finish cleanly
            await closer_task

        # Surface chat()'s outcome
        try:
            result = await chat_task
        except Exception as exc:
            # The SSE consumer turns this into an in-stream error event
            # (HTTP stays 200) — log it or the failure is invisible
            # server-side.
            logger.exception(f"[chat-stream] turn failed session={session_id}: {exc}")
            from app.core.provider_errors import stream_error_data

            yield {"type": "error", "data": stream_error_data(exc)}
            return

        message = result.get("assistant_message") or {}
        full_text = message.get("content") or ""
        yield {
            "type": "done",
            "data": {
                "message_id": message.get("id"),
                "usage": result.get("usage"),
                "run_id": result.get("run_id"),
                "tool_calls": result.get("tool_calls", []),
                "total_chars": len(full_text),
                # M2: surface attachment failures so streaming UI can show
                # "couldn't read X.pdf" — empty list on success.
                "attachment_failures": result.get("attachment_failures", []),
            },
        }

    async def _resolve_chat_answer(
        self,
        session_id: str,
        user_id: UUID,
        content: str,
        answer_to: Optional[str],
        *,
        session: Optional[dict] = None,
    ) -> Optional["_ChatAnswer"]:
        """Decide whether ``content`` answers / supersedes the open question
        on the latest assistant message. Raises 409 ``no_open_question`` or
        400 ``answer_shape`` for a bad ``answer_to``; runs the kind's
        ``on_answer`` (it may refuse) for a real answer. None = ordinary
        message on a conversation with no open question.

        An ISSUE session (``context_type == "issue"``) is fenced off entirely:
        its questions are answered through the issue thread (marker +
        ``POST /issues/{id}/messages``, Task 3); answering them here as well
        would run ``on_answer`` twice and leave the issue marker open.

        Known window: two requests answering the same question before the
        first ``mark_question_answered`` lands both pass this check (the
        per-user concurrency gate admits more than one turn). Same shape as
        the issue path's ``answered_at`` stamp; kinds must therefore make
        ``on_answer`` idempotent."""
        from app.services.ai.runner.question import (
            AnswerContext,
            AnswerRejected,
            answer_matches,
            on_answer_for,
        )

        if (session or {}).get("context_type") == "issue":
            if answer_to is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "use_issue_thread",
                        "message": "answer this question on the issue thread",
                    },
                )
            return None

        open_q = await self._store.latest_assistant_open_question(session_id=session_id)
        question = (open_q or {}).get("question") or {}
        open_qid = question.get("question_id")
        if answer_to is not None:
            if not open_q or open_qid != answer_to:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "no_open_question",
                        "message": f"no open question {answer_to!r} in this chat",
                    },
                )
            if not answer_matches(question, content):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "answer_shape",
                        "message": "answer must equal one of the option labels"
                        + (
                            " or be free text"
                            if question.get("allow_free_text")
                            else ""
                        ),
                    },
                )
            superseded = False
        elif not open_q:
            return None
        else:
            labels = {
                o.get("label")
                for o in (question.get("options") or [])
                if isinstance(o, dict)
            }
            if labels:
                superseded = content not in labels  # a plain next message moves on
            else:
                # An open-ended question (no options): the next message IS the
                # answer when it has any text; a blank one moves on.
                superseded = not (
                    question.get("allow_free_text", True) and content.strip()
                )
        if not superseded:
            kind = str(question.get("kind") or "user")
            try:
                handler = on_answer_for(kind)
            except KeyError:
                raise HTTPException(
                    status_code=500,
                    detail={"code": "unknown_question_kind", "message": kind},
                )
            try:
                await handler(
                    {"session_id": str(session_id)},
                    content,
                    AnswerContext(
                        target={"session_id": str(session_id)},
                        user_id=str(user_id),
                        marker=question,
                    ),
                )
            except AnswerRejected as rej:
                raise HTTPException(
                    status_code=rej.status,
                    detail={"code": rej.code, "message": str(rej)},
                )
        return _ChatAnswer(
            message_id=open_q["message_id"],
            question_id=str(open_qid),
            run_id=(str(question["run_id"]) if question.get("run_id") else None),
            value=None if superseded else content,
            superseded=superseded,
        )

    async def _commit_chat_answer(self, answer: "_ChatAnswer") -> None:
        """After the user message landed: ``question_answered`` on the asking
        run + ``answered`` stamped on the asking message. Both best-effort."""
        from app.services.ai.runner.question import QUESTION_ANSWERED

        payload = {
            "question_id": answer.question_id,
            "value": answer.value,
            "superseded": answer.superseded,
        }
        if answer.run_id:
            try:
                writer = await _event_writer_for_run(answer.run_id)
                await writer.append(QUESTION_ANSWERED, payload, turn=None, step=None)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"[chat] question_answered for run {answer.run_id} not recorded: {exc!r}"
                )
        else:
            logger.warning(
                f"[chat] open question {answer.question_id} has no run_id; "
                "question_answered not recorded"
            )
        await self._store.mark_question_answered(
            message_id=answer.message_id,
            value=answer.value,
            superseded=answer.superseded,
        )

    async def chat(
        self,
        session_id: str,  # ai_sessions.id BIGINT Snowflake (mig 231)
        *,
        user_id: UUID,
        content: str,
        plan_mode: Optional[str] = None,
        chunk_callback: Optional[Callable[[str], Awaitable[None]]] = None,
        attachments: Optional[list] = None,
        script_context: Optional[dict] = None,
        answer_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send ``content`` as a user turn, get an assistant response.

        Guards: 404 if session not found or not owned, 400 if session
        has no agent_slug. Then delegates to ``run_session_turn`` which
        owns the full turn execution.

        Phase M (M4): when ``plan_mode='prompt_user'`` or 'dry_run', the
        prompt composer prepends the PLAN_PROMPT instructing the LLM to
        emit a structured plan instead of executing. The chat response
        is the plan markdown; user replies approve/reject in next turn.

        ``script_context`` (§5.3): optional selection handle
        ({scene_id, element_ids, ...}) rendered into a <user_selection>
        instruction block — see ``format_script_context_block``.
        """
        session = await self.get_session(session_id, user_id=user_id)
        if not session.get("agent_slug"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="session has no agent_slug bound — cannot chat",
            )
        return await self.run_session_turn(
            session_id,
            user_id=user_id,
            content=content,
            trigger="chat",
            plan_mode=plan_mode,
            chunk_callback=chunk_callback,
            attachments=attachments,
            script_context=script_context,
            answer_to=answer_to,
        )

    async def run_session_turn(
        self,
        session_id: str,
        *,
        user_id: UUID,
        content: str,
        trigger: str = "chat",
        plan_mode: Optional[str] = None,
        chunk_callback: Optional[Callable[[str], Awaitable[None]]] = None,
        attachments: Optional[list] = None,
        attribution: Optional[str] = None,
        script_context: Optional[dict] = None,
        answer_to: Optional[str] = None,
        fork_of: Optional[tuple[int, int]] = None,
        fork_steer: bool = False,
    ) -> Dict[str, Any]:
        """Per-user concurrency gate around the turn. Both chat (.chat) and
        issue (run_issue_reply_step) funnel through here, so one gate caps a
        user's concurrent agent turns. See agent_concurrency.

        ``attribution`` (W3c) forwards a two-level cost tag to RunRecorder —
        the issue-dispatch path passes 'rule_owner' for routine/pipeline fires;
        interactive chat leaves it None (→ direct_human)."""
        from app.services.ai.chat.agent_concurrency import user_slot

        async with user_slot(str(user_id)):
            return await self._run_session_turn_inner(
                session_id,
                user_id=user_id,
                content=content,
                trigger=trigger,
                plan_mode=plan_mode,
                chunk_callback=chunk_callback,
                attachments=attachments,
                attribution=attribution,
                script_context=script_context,
                answer_to=answer_to,
                fork_of=fork_of,
                fork_steer=fork_steer,
            )

    async def _run_session_turn_inner(
        self,
        session_id: str,  # ai_sessions.id BIGINT Snowflake (mig 231)
        *,
        user_id: UUID,
        content: str,
        trigger: str = "chat",
        plan_mode: Optional[str] = None,
        chunk_callback: Optional[Callable[[str], Awaitable[None]]] = None,
        attachments: Optional[list] = None,
        attribution: Optional[str] = None,
        script_context: Optional[dict] = None,
        answer_to: Optional[str] = None,
        fork_of: Optional[tuple[int, int]] = None,
        fork_steer: bool = False,
    ) -> Dict[str, Any]:
        """Execute a single turn against a session.

        Self-contained: loads the session, runs the full agent turn
        (history load, user-message persist, compose, RunRecorder,
        assistant-message persist, counter bump, fire-and-forget
        memory/commitment harvest), and returns the result dict.

        ``trigger`` is forwarded to ``RunRecorder`` so issue execution
        (trigger="issue") produces distinct rows in agent_runs while
        reusing the exact same turn logic as interactive chat.

        Flow:
          1. Load session (404 if not owner)
          2. Load message history for this session
          3. Persist the new user message
          4. Compose via PromptComposer (IDENTITY / SOUL / AGENT + skills XML)
          5. Call AgentRunner inside a RunRecorder — this is where tokens,
             cost, budget guard, cancel polling, skill tool-calls all live
          6. Persist the assistant message
          7. Bump session counters
          8. Return {user_message, assistant_message, usage, run_id,
                     tool_calls, attachment_failures, approval_request_id}

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

        # Normalize attachments to dicts up front so both the persisted
        # user message (display metadata) and the S4/G2 resolution below
        # work regardless of whether they arrived as Pydantic
        # AttachmentRequest objects (HTTP path) or plain dicts (test path).
        _att_dicts: list[dict] = []
        for _att in attachments or []:
            if hasattr(_att, "model_dump"):
                _att_dicts.append(_att.model_dump())
            elif isinstance(_att, dict):
                _att_dicts.append(_att)
            # silently drop anything else — same behavior as before

        # Persist the user turn BEFORE calling the model so partial
        # failures (LLM timeout, budget pause) still leave a record of
        # what the user tried to ask. Attachment display metadata rides
        # along (kind/resource_id/mime/alt_text — never data_url bytes)
        # so history reloads can re-render the image in the bubble.
        # Phase 2a chat answer channel (spec §1): does this message answer
        # (or supersede) a typed question parked on the latest assistant
        # message? Chat turns only — issue turns answer through the issue
        # marker + message endpoint (Task 3); detecting here as well would
        # record every issue answer twice. Validated BEFORE anything is
        # persisted (409 / 400 leave no trace); recorded right after the
        # user message lands, because that message IS the delivery.
        chat_answer = (
            await self._resolve_chat_answer(
                session_id, user_id, content, answer_to, session=session
            )
            if trigger == "chat"
            else None
        )

        user_msg = await self._store.append_user_message(
            session_id=session_id,
            user_id=str(user_id),
            content=content,
            attachments=ConversationsAiStore.display_attachments(_att_dicts),
        )
        if chat_answer is not None:
            await self._commit_chat_answer(chat_answer)

        # M1.5 wiring: load agent record so we can read budget/fallback,
        # then build the full runner stack (HookRegistry pre-populated,
        # fallback chain wrapping adapter, memory recall pre-fetched).
        agent_repo = get_agent_repository()
        skill_repo = get_skill_repository()
        # Agent-overrides (mig 341): 1:1 chat resolves the CALLER's
        # customization — user layer over the session's team layer.
        agent_record = await agent_repo.get_by_slug(
            agent_slug,
            override_user_id=user_id,
            override_team_id=session.get("team_id"),
        )
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

        # Compose prompt with graph facts + user context injected after
        # cache_boundary.
        # M (G→J→F→I→H follow-up): prompt now explicitly notes that the
        # Delegate tool can hand off to persistent specialists listed in
        # <available_workers>. Without this hint the LLM tends to do the
        # work itself even when a better specialist exists. Use await=true
        # if you need the result in the same turn.
        #
        # P1-4: route through ContextEngineRegistry when available
        # (Sprint 6.5 wire-up). Falls back to direct PromptComposer when
        # the registry isn't on app.state — keeps unit tests + scripts
        # that don't go through FastAPI lifespan working unchanged.
        # Phase M (M4): if caller asked for plan mode, swap the entire
        # request_instructions for the plan prompt template. The agent
        # responds with structured JSON plan; user approves/rejects in
        # next turn (no PlanMode flag → default execute behavior).
        if plan_mode in ("prompt_user", "dry_run"):
            from app.agent_framework.plan_mode import build_plan_prompt

            request_instructions = build_plan_prompt()
        else:
            request_instructions = (
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
            )

        # Wave G (G8): on the FIRST turn of a session, fire any
        # pending NEXT_SESSION commitments and inject reminders into
        # request_instructions. Best-effort — failure logs + skips.
        is_first_turn = len(history) == 0
        if is_first_turn and user_id:
            request_instructions = await _surface_next_session_commitments(
                # agent_record, NOT composed — compose() runs further down;
                # referencing it here was a NameError silently swallowed by
                # the best-effort catch, so G8 reminders never fired at all.
                agent_id=str(agent_record["id"]),
                user_id=str(user_id),
                request_instructions=request_instructions,
            )

        # §5.3: structured selection handle → <user_selection> block. Only
        # a handle (scene_id / element_ids) — no quoted_text, the selection
        # text is already folded into `content` by the frontend, so
        # duplicating it here would just double the token spend.
        selection_block = format_script_context_block(script_context)
        if selection_block:
            request_instructions = selection_block + "\n\n" + request_instructions

        # P1-6: link-injection wire-up. Pull URLs out of the latest user
        # message, fetch via boundary-safe link_understanding, prepend
        # rendered blocks to request_instructions. Failures (4xx/5xx,
        # boundary reject, timeout) get explicit placeholder blocks so
        # the agent doesn't hallucinate URL contents.
        # Best-effort: any error here just skips link injection — the
        # chat must never break because URL fetch failed.
        try:
            from app.services.ai.prompts.link_injection import (
                extract_urls,
                fetch_and_render,
            )

            urls = extract_urls(content, max_urls=3)
            if urls:
                injection = await fetch_and_render(urls)
                if injection.has_content:
                    request_instructions = (
                        injection.joined + "\n\n" + request_instructions
                    )
                    logger.info(
                        f"link_injection: injected {len(injection.blocks)} "
                        f"block(s) for {len(urls)} URL(s); "
                        f"failures={len(injection.failures)}"
                    )
        except Exception as li_exc:
            logger.warning(f"link_injection failed (non-fatal): {li_exc}")
        composed = None
        engine = None
        try:
            from app.main import app as _app  # late import to avoid cycle

            engine = getattr(_app.state, "context_engines", None)
            if engine is not None:
                engine = engine.get("chat")
        except Exception:
            engine = None

        if engine is not None:
            # Keep this dict and the ComposerInput below field-for-field
            # identical: the engine branch is the only one that runs in
            # production, so anything passed only to the fallback is
            # effectively dead. tests/test_chat_context_engine_parity.py
            # enforces it.
            payload = await engine.assemble(
                {
                    "agent_slug": agent_slug,
                    "request_instructions": request_instructions,
                    "session_id": session_id,
                    "graph_facts": stack.graph_facts,
                    "user_context": stack.user_context,
                    "agent_memory_facts": stack.agent_memory_facts,
                    "override_user_id": user_id,
                    "override_team_id": session.get("team_id"),
                }
            )
            composed = payload.metadata["composed"]
        else:
            composer = PromptComposer(agent_repo, skill_repo)
            composed = await composer.compose(
                ComposerInput(
                    agent_slug=agent_slug,
                    request_instructions=request_instructions,
                    session_id=session_id,
                    graph_facts=stack.graph_facts,
                    user_context=stack.user_context,
                    agent_memory_facts=stack.agent_memory_facts,
                    override_user_id=user_id,
                    override_team_id=session.get("team_id"),
                )
            )

        runner = stack.runner

        # Vision capability decided once per turn: gates the new-message
        # multipart build, the history image replay, AND the runner's
        # promotion of image-bearing tool results (ResourceFetch).
        from app.services.ai.model_capabilities import model_supports_vision

        supports_vision = await model_supports_vision(composed.model)
        runner.vision_capable = supports_vision

        # Build the message history payload: prior turns + new user turn.
        # We always pass role+content; tool-call stubs that AgentRunner
        # generated in a previous turn aren't replayed (they're ephemeral
        # — the persisted assistant message captures the final content).
        # Recent image attachments ARE replayed (budgeted) so follow-up
        # turns like "now look at the top-left corner" still see the image
        # — the live request only inlines the CURRENT turn's bytes.
        from app.services.ai.chat.history_image_replay import (
            build_history_messages,
        )

        try:
            user_messages: List[Dict[str, Any]] = await build_history_messages(
                history,
                user_id=str(user_id),
                supports_vision=supports_vision,
            )
        except Exception as replay_exc:
            logger.warning(
                f"[chat] history image replay failed (text-only history): "
                f"{replay_exc!r}"
            )
            user_messages = []
            for msg in history:
                role = msg.get("role")
                if role not in ("user", "assistant", "system"):
                    continue
                user_messages.append(
                    {"role": role, "content": msg.get("content") or ""}
                )

        # S4 Task 6 / P5 Task 3: split attachments by kind before resolution.
        # THREE buckets, each explicit — a kind that falls through to
        # `binary_atts` by accident does not fail quietly, it fails wrongly:
        # chat_attachment_resolver raises "unsupported attachment kind" and the
        # user is told their attachment could not be READ, when in fact their
        # reference was never resolved.
        #   resource_ref → resource_ref_resolver (metadata only; content loaded
        #                  lazily via the ResourceFetch tool during the turn)
        #   asset_ref    → asset_ref_resolver (library entity → consistency
        #                  prompt + a primary image folded in below)
        #   everything else → the existing G2 binary path (image/pdf/audio)
        #
        # `binary_source_index` maps a binary failure's index back to the
        # caller's FULL attachment list. Without it `attachment_failures` would
        # carry two different index bases in one list — binary indices counted
        # among binaries, asset indices counted among all attachments — and any
        # consumer that points at the n-th chip would point at the wrong one as
        # soon as a turn mixed the two.

        # (`_att_dicts` normalized above, before the user-message persist.)
        #
        # `MAX_ASSET_REF_ATTACHMENTS` is applied HERE, to the `asset_ref`
        # bucket ONLY. `resource_ref` is deliberately uncapped: however many a
        # turn carries, they cost one batched query, so a cap there would
        # refuse a working path for no cost reason. Over-cap asset refs keep
        # their POSITION (see `_asset_input` below) and get a typed failure;
        # they are never resolved.
        #
        # Positions, not distinct ids: a repeated `asset_id` past the cap is
        # reported as over-cap even though the earlier chip delivered it. The
        # alternative — de-duplicating before counting — would let a caller
        # send 200 copies of one id and still spend 200 slots' worth of
        # request body, which is the thing being bounded.
        ref_atts: list = []
        binary_atts: list = []
        binary_source_index: list[int] = []
        asset_att_indices: list[int] = []
        over_cap_failures: list[dict] = []
        over_cap_indices: set[int] = set()
        _asset_refs_seen = 0
        for _i, _att in enumerate(_att_dicts):
            _kind = _att.get("kind")
            if _kind == "resource_ref":
                ref_atts.append(_att)
            elif _kind == "asset_ref":
                _asset_refs_seen += 1
                if _asset_refs_seen > MAX_ASSET_REF_ATTACHMENTS:
                    over_cap_indices.add(_i)
                    over_cap_failures.append(
                        {
                            "index": _i,
                            "kind": _kind,
                            "reason": ATTACHMENT_LIMIT_REASON,
                        }
                    )
                    continue
                asset_att_indices.append(_i)
            else:
                binary_atts.append(_att)
                binary_source_index.append(_i)
        if over_cap_failures:
            logger.warning(
                f"[chat] {len(over_cap_failures)} asset_ref attachment(s) over "
                f"MAX_ASSET_REF_ATTACHMENTS={MAX_ASSET_REF_ATTACHMENTS} — "
                f"not resolved, reported as {ATTACHMENT_LIMIT_REASON}"
            )

        # Resource-ref path: resolve metadata, extend system message, register tool.
        resource_refs: list = []
        ref_warnings: list = []
        try:
            resource_refs, ref_warnings = await resolve_resource_refs(
                ref_atts, user_id=str(user_id)
            )
        except Exception as rr_exc:
            logger.warning(
                f"[chat] resource_ref resolution failed (non-fatal): {rr_exc}"
            )

        # Asset-ref path (P5). The resolver gets the FULL attachment list, not
        # the asset bucket: AssetRefFailure.index counts positions among all
        # attachments, so handing it a pre-filtered bucket would make every
        # reported index point at the wrong chip.
        #
        # Over-cap entries are blanked rather than removed: the resolver counts
        # indices against the list it is handed, so dropping them would shift
        # every later index and point each failure at the wrong chip. A dict
        # with no `kind` is skipped by the resolver's own filter and holds the
        # slot.
        asset_refs: list = []
        asset_failures: list = []
        if asset_att_indices:
            _asset_input = [
                ({} if _i in over_cap_indices else _att)
                for _i, _att in enumerate(_att_dicts)
            ]
            try:
                asset_refs, asset_failures = await resolve_asset_refs(
                    _asset_input, user_id=str(user_id)
                )
            except Exception as ar_exc:
                # Non-fatal for the turn, but NOT silent: one typed failure per
                # asset the user attached, so the banner says the references
                # were dropped instead of the assets simply never appearing.
                # `asset_not_accessible` is the honest reason here — we do not
                # know whether these assets exist, and inventing a more
                # specific one would be a guess the UI presents as fact.
                logger.warning(
                    f"[chat] asset_ref resolution failed (non-fatal): {ar_exc!r}"
                )
                asset_refs = []
                asset_failures = [
                    AssetRefFailure(index=i, reason="asset_not_accessible")
                    for i in asset_att_indices
                ]

        if asset_refs:
            asset_refs, extra_refs, primary_failures = (
                await self._merge_asset_primaries(
                    asset_refs,
                    resource_refs,
                    attachments=_att_dicts,
                    user_id=str(user_id),
                )
            )
            resource_refs = resource_refs + extra_refs
            asset_failures = asset_failures + primary_failures

        if resource_refs or asset_refs:
            resources_block = render_available_resources(resource_refs, asset_refs)
            if resources_block:
                composed = composed.model_copy(
                    update={
                        "system_message": (
                            composed.system_message + "\n\n" + resources_block
                        )
                    }
                )

        # The tool is gated on RESOURCE refs, not on the block being rendered:
        # a turn that mentions only a `prompt`-type asset has a block to show
        # and nothing to fetch, and advertising ResourceFetch with an empty
        # accessible set invites a call that can only fail.
        if resource_refs:
            # Build ResourceFetch tool spec and register a closure on the runner.
            resource_fetch_spec = {
                "type": "function",
                "function": {
                    "name": "ResourceFetch",
                    "description": (
                        "Load content for a resource listed in "
                        "<available_resources>. Call with the resource id "
                        "and an optional mode."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "resource_id": {
                                "type": "string",
                                "description": "id attribute from <available_resources>.",
                            },
                            "mode": {
                                "type": "string",
                                "description": (
                                    "How to read the resource. Defaults vary "
                                    "by kind — see system message for details."
                                ),
                            },
                            "args": {
                                "type": "object",
                                "description": "Optional extra args (e.g. {page: 2} for PDF).",
                            },
                        },
                        "required": ["resource_id"],
                    },
                },
            }
            composed = composed.model_copy(
                update={"tools": list(composed.tools or []) + [resource_fetch_spec]}
            )
            # Bind the closure to the runner so the tool dispatch layer can call it.
            _available_refs: set = {r["id"] for r in resource_refs}
            _request_cache: Dict[str, Any] = {}
            _bound_user_id: str = str(user_id)

            async def _resource_fetch_handler(args: dict) -> dict:
                from app.services.ai.tools.resource_fetch_tool import resource_fetch

                return await resource_fetch(
                    resource_id=str(args.get("resource_id", "")),
                    mode=args.get("mode"),
                    args=args.get("args"),
                    user_id=_bound_user_id,
                    available_refs=_available_refs,
                    request_cache=_request_cache,
                )

            runner.resource_fetch_handler = _resource_fetch_handler
            logger.info(
                f"[chat] resource_ref: {len(resource_refs)} ref(s) wired "
                f"(incl. primaries of {len(asset_refs)} asset ref(s)); "
                f"ResourceFetch tool registered"
            )

        # Gated injection: GenerateImage + GenerateVideo tools (Plan-2 / genmedia).
        # A1 (screenwriting agent layer): registration is now per-agent, driven
        # by ``capability_profile.capabilities.media`` (see high_risk_caps.py),
        # not by a single install-wide flag. FEATURE_AGENT_MEDIA_TOOLS is kept
        # ONLY as a kill switch that can force media tools off everywhere; it
        # can never grant — an unset/truthy value is a no-op and the
        # capability_profile grant is what actually enables a tool per agent.
        # HighRiskCapabilityGateHook (registered in ai_library_chat_wiring.py)
        # re-checks BOTH the capability grant and media_kill_switch_engaged()
        # itself on every GenerateImage/GenerateVideo call — it is the real
        # enforcement point regardless of what happens here. This block only
        # decides whether to bother advertising the tool spec to the model
        # (skip it here too so the kill switch also has the UX benefit of not
        # dangling an unusable tool in front of the LLM).
        if not media_kill_switch_engaged():
            _media_caps = high_risk_caps(agent_record).media
            _media_specs: list[dict] = []
            if _media_caps.image:
                from app.services.ai.tools.generate_media_specs import (
                    generate_image_tool_spec,
                )

                _media_specs.append(generate_image_tool_spec())
            if _media_caps.video:
                from app.services.ai.tools.generate_media_specs import (
                    generate_video_tool_spec,
                )

                _media_specs.append(generate_video_tool_spec())

            if _media_specs:
                from app.services.ai.tools.generate_media_tools import (
                    GenerateMediaTools,
                )

                composed = composed.model_copy(
                    update={"tools": list(composed.tools or []) + _media_specs}
                )
                _media_tools = GenerateMediaTools()
                if _media_caps.image:
                    runner.generate_image_handler = _media_tools.generate_image
                if _media_caps.video:
                    runner.generate_video_handler = _media_tools.generate_video
                logger.info(
                    "[chat] media tools registered per capability grant: "
                    f"image={_media_caps.image} video={_media_caps.video}"
                )

        # Spec-2: issue-context turns expose FinishIssue so the agent can
        # declare its outcome (completed | needs_input | continue). The
        # execute_issue workflow reads the declaration from the returned
        # tool_calls trace; chat turns never see this tool.
        if trigger in ("issue_dispatch", "issue_dispatch_auto", "issue_reply"):
            from app.services.ai.tools.finish_issue_tool import (
                FINISH_ISSUE_INSTRUCTION,
                finish_issue_handler,
                finish_issue_spec,
            )

            composed = composed.model_copy(
                update={
                    "tools": list(composed.tools or []) + [finish_issue_spec()],
                    # Directive so the agent reliably DECLARES — without it the
                    # tool is latent and the workflow falls back to in_review.
                    "system_message": (
                        composed.system_message + "\n\n" + FINISH_ISSUE_INSTRUCTION
                    ),
                }
            )
            runner.finish_issue_handler = finish_issue_handler
            logger.info("[chat] FinishIssue tool registered for issue turn")

        # Phase 2a: AskUser on BOTH roads (issue and chat) — the agent's one
        # verb for "ask the human to pick"; the runner parks the turn after it.
        from app.services.ai.tools.ask_user_tool import ask_user_spec

        composed = composed.model_copy(
            update={"tools": list(composed.tools or []) + [ask_user_spec()]}
        )

        # Prepend ref warnings to the user content so the agent sees them.
        effective_content = content
        if ref_warnings:
            warning_lines = "\n".join(f"⚠️ {w}" for w in ref_warnings)
            effective_content = warning_lines + "\n\n" + content
            logger.info(
                f"[chat] resource_ref: {len(ref_warnings)} warning(s) prepended"
            )

        # G2: resolve binary attachments → multimodal Attachment[] → vision-aware
        # user message. Failures degrade gracefully (text-only message
        # with placeholder describing what was skipped).
        new_user_msg: Dict[str, Any]
        # One list, one shape, one index basis. Entries are `{index, kind,
        # reason}` dicts from here on rather than two dataclasses that happen
        # to be spelled differently — the asset path counts indices against the
        # caller's full attachment list, so the binary path's indices are
        # translated through `binary_source_index` to match.
        attachment_failures: list = [
            {"index": f.index, "kind": "asset_ref", "reason": f.reason}
            for f in asset_failures
        ]
        # Built during the bucket split above, already on the caller's index
        # basis. Merged here so every typed failure leaves this method through
        # one list with one shape.
        attachment_failures.extend(over_cap_failures)
        if binary_atts:
            try:
                from app.agent_framework.multimodal import build_user_message
                from app.schemas.ai_library_chat import AttachmentRequest
                from app.services.ai.chat.chat_attachment_resolver import (
                    resolve_attachments,
                    vision_gate_failures,
                )

                # resolve_attachments reads `.kind`/`.url`/... as attributes —
                # passing the normalized dicts crashes it ('dict' object has
                # no attribute 'kind') and the whole turn silently degrades
                # to text-only. Re-hydrate to AttachmentRequest objects.
                resolved = await resolve_attachments(
                    [AttachmentRequest.model_validate(a) for a in binary_atts]
                )
                _binary_failures = list(resolved.failures)
                # The vision gate drops attachments SILENTLY (see
                # vision_gate_failures) — record one typed failure per
                # dropped request so the user is told, instead of the
                # image simply never arriving.
                _binary_failures.extend(
                    vision_gate_failures(
                        binary_atts, resolved, supports_vision=supports_vision
                    )
                )
                attachment_failures.extend(
                    {
                        "index": binary_source_index[f.request_index],
                        "kind": f.kind,
                        "reason": f.reason,
                    }
                    for f in _binary_failures
                )

                new_user_msg = build_user_message(
                    effective_content,
                    resolved.attachments,
                    supports_vision=supports_vision,
                )
                if _binary_failures:
                    logger.info(
                        f"[chat] G2 attachment failures: "
                        f"{len(_binary_failures)} of {len(binary_atts)} "
                        f"could not be resolved"
                    )
            except Exception as att_exc:
                logger.warning(
                    f"[chat] attachment resolution failed (text-only fallback): {att_exc}"
                )
                new_user_msg = {"role": "user", "content": effective_content}
        else:
            new_user_msg = {"role": "user", "content": effective_content}
        user_messages.append(new_user_msg)

        # Wave G (G5): per-message size cap. Defends against the
        # "user pasted 200k log line" case that bypasses compaction
        # entirely (compaction works at message-list level, not single-
        # message level). Default cap = 50k tokens ≈ 200KB; rare and
        # typically machine-generated when triggered.
        try:
            from app.agent_framework import cap_messages_tokens

            outcomes = cap_messages_tokens(
                user_messages,
                model=model_for_estimate(composed) if False else "",  # noqa
            )
            # Replace the message list with possibly-truncated versions
            user_messages = [o.message for o in outcomes]
            truncated_count = sum(1 for o in outcomes if o.truncated)
            if truncated_count:
                logger.info(
                    f"[chat] per-message cap truncated {truncated_count} "
                    f"oversized message(s)"
                )
        except Exception as cap_exc:
            logger.warning(f"per-message cap skipped (non-fatal): {cap_exc}")

        # M1.5 wiring: compact the message list if it has grown past the
        # threshold. Compactor preserves tool_use/result pairs so the
        # next API call won't 400. Failure degrades to "send full history
        # and let the model deal with it" — never breaks the chat.
        user_messages = await self._maybe_compact(
            user_messages, session_id=session_id, user_id=user_id
        )

        model = composed.model or ""
        try:
            provider = provider_key_for_model(model) if model else None
        except ValueError:
            provider = None

        # Wrap in RunRecorder. agent_id comes from ``composed`` so we
        # don't re-query. team_id / project_id tag the run for Usage's
        # team/project scope queries.
        # P2: when chunk_callback is provided, drive runner.stream_turn
        # and emit deltas to the callback as they arrive — TTFT drops
        # from "after model finishes" to "as model emits". Tool-using
        # turns still work (stream_turn executes tool_calls between
        # iterations and re-streams).
        # P3 Task 6: agent_runs.session_id still FKs the (soon to be
        # dropped, Wave 2) ai_sessions table — a conversations.id would
        # violate that FK. agent_runs.conversation_id (mig 331) is the
        # structural link for conversations-backed sessions. Dispatch off
        # the session row's store_kind marker (stamped by
        # ConversationsAiStore._to_legacy_shape) so a conversations-backed
        # session links via conversation_id. Defensive default: a row with
        # no store_kind key (shouldn't happen post-collapse) keeps the
        # byte-identical session_id path.
        _is_conv_store = session.get("store_kind") == "conversations"

        # A4 review (Important 3): this is the PRIMARY chat surface, and it
        # used to stamp session["project_id"] raw with no episode at all.
        # That made the episode dimension surface-dependent — the same agent
        # on the same conversation got episode-narrowed scope when summoned
        # via @-mention (conversation_agent_turn) but FULL-project scope
        # here, so a narrowed agent could widen itself just by switching
        # surfaces.
        #
        # The project keeps coming from the session row IN MEMORY and is
        # passed explicitly, NOT re-derived from the conversation. Both
        # values are the same column in production (create_session writes
        # conversations.project_id from what the caller passed), but a
        # re-read can FAIL, and on the issue-dispatch path a lost project_id
        # silently disables the autopilot daily spend cap, which filters
        # agent_runs by exactly this column. The conversation is consulted
        # only to ADD an episode; if that lookup fails the run is simply not
        # narrowed. See resolve_dispatch_scope's explicit-project branch.
        from app.services.ai.scope.scope_binding import resolve_dispatch_scope

        _dispatch_scope = await resolve_dispatch_scope(
            agent=agent_record,
            project_id=session.get("project_id"),
            conversation_id=int(session_id) if _is_conv_store else None,
        )

        try:
            async with RunRecorder(
                agent_id=composed.agent_id,
                user_id=user_id,
                trigger=trigger,
                session_id=None if _is_conv_store else session_id,
                conversation_id=int(session_id) if _is_conv_store else None,
                team_id=session.get("team_id"),
                **_dispatch_scope.as_recorder_kwargs(),
                model=model or None,
                provider=provider,
                input_summary=content,
                attribution=attribution,
                metadata={"full_input": content},
                # phase 2b-1 §2.3: a forked run points back at its origin
                fork_of_run_id=int(fork_of[0]) if fork_of else None,
                fork_at_seq=int(fork_of[1]) if fork_of else None,
            ) as recorder:
                if fork_of is not None:
                    # First event of a forked run — before user / step_start —
                    # so replay / the UI see the branch point at seq 1.
                    from app.services.ai.runner.events import emit

                    await emit(
                        recorder,
                        "fork",
                        {
                            "of_run_id": int(fork_of[0]),
                            "at_seq": int(fork_of[1]),
                            "steer": bool(fork_steer),
                        },
                    )
                try:
                    if chunk_callback is None:
                        # Buffered path — unchanged
                        result = await runner.run_turn(
                            composed,
                            user_messages=user_messages,
                            recorder=recorder,
                        )
                        assistant_content = result.get("content") or ""
                        tool_calls_trace = result.get("tool_calls") or []
                    else:
                        # Streaming path: accumulate chunks + forward to caller
                        accumulated: list[str] = []
                        tool_calls_trace = []
                        stream_cancelled = False
                        stream_awaiting_input = False
                        stream_stop_reason: Optional[str] = None
                        stream_hook_decision: Optional[str] = None
                        stream_approval_reason: str = ""
                        try:
                            async for chunk in runner.stream_turn(
                                composed,
                                user_messages=user_messages,
                                recorder=recorder,
                                auto_recorder=False,  # we already own the context
                            ):
                                if chunk.delta_text:
                                    accumulated.append(chunk.delta_text)
                                    try:
                                        await chunk_callback(chunk.delta_text)
                                    except Exception as cb_exc:
                                        # Callback failure must not kill the turn
                                        logger.warning(
                                            f"[chat] chunk_callback raised: {cb_exc}"
                                        )
                                if chunk.tool_call_delta:
                                    # Surface tool-call-start hints to UI; the
                                    # synthetic "→ Running X..." text comes
                                    # through delta_text on the next chunk
                                    pass
                                # Bugfix: stream_turn's terminal chunk(s) carry
                                # the executed-tool-call trace (see StreamChunk
                                # in adapters/base.py). Without this the
                                # streaming path always returned tool_calls=[]
                                # — FinishIssue outcomes and any other tool
                                # results were invisible to callers (issue
                                # lifecycle routing, sub-task cards).
                                if chunk.tool_call_trace is not None:
                                    tool_calls_trace = chunk.tool_call_trace
                                # A hook STOP ends the stream with a terminal
                                # chunk carrying usage.stop_reason (see
                                # AgentRunner.stream_turn); it is the truth the
                                # issue workflow routes on (paused / cancelled /
                                # awaiting_input), so keep it verbatim.
                                chunk_stop = (chunk.usage or {}).get("stop_reason")
                                if chunk_stop:
                                    stream_stop_reason = chunk_stop
                                if chunk_stop == "awaiting_input":
                                    stream_awaiting_input = True
                                # A hook decision rides the same terminal
                                # chunk (both stream routes file it); without
                                # this the approval row below is never
                                # written on a chunk_callback turn.
                                chunk_hook = (chunk.usage or {}).get("hook_decision")
                                if chunk_hook:
                                    stream_hook_decision = chunk_hook
                                    stream_approval_reason = str(
                                        (chunk.usage or {}).get("approval_reason") or ""
                                    )
                        except RunAborted as abort_exc:
                            # User cancel mid-stream. The buffered path
                            # (run_turn) returns {"cancelled": True} instead
                            # of raising — mirror that so a cancel degrades to
                            # a persisted partial turn, not an unhandled 500.
                            # Partial text + any trace gathered so far are
                            # kept (tools that ran before the cancel DID run).
                            logger.info(
                                f"[chat] stream turn aborted by user: {abort_exc}"
                            )
                            stream_cancelled = True
                        assistant_content = "".join(accumulated)
                finally:
                    # Clear per-turn @-ref state so a subsequent turn on the
                    # same runner instance doesn't inherit stale refs or cache.
                    if resource_refs:
                        runner.resource_fetch_handler = None
                        _request_cache.clear()
                    # Spec-2: drop the per-turn FinishIssue handler so a later
                    # non-issue turn on the same runner can't accept it.
                    runner.finish_issue_handler = None

                run_id = recorder.run_id
                # Pull usage off the recorder — that's the single source
                # of truth for what just got written to agent_runs.
                usage_snapshot = {
                    "prompt_tokens": recorder.prompt_tokens,
                    "completion_tokens": recorder.completion_tokens,
                }
                # Tool call trace from this turn (Skill / Delegate / FinishIssue
                # dispatches in LLM emission order) — surfaced into the
                # response so the chat UI can render sub-task cards inline,
                # and so callers like issue_agent_executor can read
                # FinishIssue outcomes via extract_issue_outcome(). Empty
                # list when the LLM answered directly. Populated identically
                # on both the buffered path (result["tool_calls"] above) and
                # the streaming path (stream_turn's terminal StreamChunk —
                # see the tool_call_trace capture in the loop above).
                if chunk_callback is None:
                    # tool_calls_trace already set from result above
                    pass
                recorder.set_summaries(output_summary=assistant_content)
                # In the streaming path, ``result`` was never built; backfill
                # what downstream code references. Same shape as run_turn's
                # cancel return ({"cancelled": True}) when the user aborted.
                if chunk_callback is not None:
                    result = {
                        "content": assistant_content,
                        "tool_calls": tool_calls_trace,
                    }
                    if stream_cancelled:
                        result["cancelled"] = True
                    if stream_stop_reason:
                        result["stop_reason"] = stream_stop_reason
                    if stream_hook_decision == "await_approval":
                        result["awaiting_approval"] = True
                        result["approval_reason"] = stream_approval_reason
                    elif stream_hook_decision == "abort":
                        result["aborted"] = True
                    if stream_awaiting_input:
                        from app.services.ai.runner.question import payload_from_view

                        parked = (recorder.views.get("view") or {}).get("question")
                        result["awaiting_input"] = True
                        result["question"] = (
                            payload_from_view(parked) if parked else None
                        )
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

        # G1: hook-induced await_approval — persist the request so the
        # frontend can show it in an approvals UI and resume the run
        # later. Without this row, the runner's awaiting_approval=true
        # signal is invisible to the user.
        approval_row_id = None
        if result.get("awaiting_approval"):
            try:
                from app.repositories.approval_requests_repository import (
                    get_approval_requests_repository,
                )

                _ar_repo = get_approval_requests_repository()
                _row = await _ar_repo.create(
                    user_id=user_id,
                    agent_id=composed.agent_id,
                    hook_name=str(result.get("hook_name") or "unknown"),
                    reason=str(result.get("approval_reason") or ""),
                    payload=result.get("approval_payload") or {},
                    session_id=session_id,
                    run_id=run_id,
                )
                if _row:
                    approval_row_id = str(_row.id)
                    logger.info(
                        f"[chat] G1 await_approval persisted id={_row.id} "
                        f"reason={_row.reason!r}"
                    )
            except Exception as ar_exc:
                logger.warning(
                    f"[chat] persist await_approval failed (non-fatal): {ar_exc}"
                )

        # Persist the assistant turn. We fold the tool_calls trace into
        # metadata_json so a fresh page-load (which refetches history)
        # still renders the sub-task cards. Top-level ``tool_calls`` in
        # the response stays for clients that want the raw payload.
        asst_metadata: dict[str, Any] = {}
        if run_id:
            asst_metadata["run_id"] = str(run_id)
        if tool_calls_trace:
            asst_metadata["tool_calls"] = tool_calls_trace
        # Phase 4.5 Plan Mode: fold the paused-for-approval state into the
        # persisted message so a fresh page-load still renders the inline
        # approval card (top-level approval_request_id only lives in this
        # one response).
        if result.get("awaiting_approval"):
            asst_metadata["awaiting_approval"] = {
                "approval_id": approval_row_id,
                "hook": str(result.get("hook_name") or ""),
                "reason": str(result.get("approval_reason") or ""),
            }
        # Phase 2a: same seat for a typed question — the payload shape from
        # question.Question.to_payload() plus the asking run, so a reload
        # re-renders the QuestionCard and the answer can name the run.
        if result.get("awaiting_input"):
            if isinstance(result.get("question"), dict):
                asst_metadata["awaiting_input"] = {
                    **result["question"],
                    "run_id": str(run_id) if run_id else None,
                }
            else:
                # The turn parked but the question never reached the view —
                # the card cannot render and nobody can answer. Say so.
                logger.error(
                    f"[chat] run {run_id} parked on awaiting_input without a "
                    "question payload; the QuestionCard cannot be rendered"
                )
        asst_msg = await self._store.append_assistant_message(
            session_id=session_id,
            agent_id=composed.agent_id,
            content=assistant_content,
            prompt_tokens=usage_snapshot["prompt_tokens"],
            completion_tokens=usage_snapshot["completion_tokens"],
            metadata=asst_metadata,
        )

        # Bump session counters. Cheap single update; ignore ON CONFLICT
        # since this session is owned by this user and exists.
        turn_tokens = (usage_snapshot["prompt_tokens"] or 0) + (
            usage_snapshot["completion_tokens"] or 0
        )
        prior_total = int(session.get("total_tokens") or 0)
        prior_count = int(session.get("message_count") or 0)
        await self._store.bump_counters(
            session_id=session_id,
            add_tokens=prior_total + turn_tokens,
            add_messages=prior_count + 2,
        )

        # Wave 5b (B4): fire-and-forget session-memory updater. Doesn't
        # await — we return to the user immediately. The updater itself
        # handles errors silently (see SessionMemoryService docstring).
        # All-messages list = full history + new user + new assistant.
        try:
            import asyncio as _asyncio

            from app.repositories.session_memory_repository import (
                get_session_memory_repository,
            )
            from app.services.ai.runner.session_memory_runner import (
                maybe_update_session_memory,
            )

            full_messages = user_messages + [
                {"role": "assistant", "content": assistant_content}
            ]
            _asyncio.create_task(
                maybe_update_session_memory(
                    session_id=str(session_id),
                    messages=full_messages,
                    model=model,
                    repo=get_session_memory_repository(),
                ),
                name=f"session-memory-update-{session_id}",
            )
        except Exception as sm_exc:
            logger.warning(f"session_memory dispatch skipped (non-fatal): {sm_exc}")

        # Wave F (F8): fire-and-forget commitment harvester. Pre-filter
        # makes ~95% of turns skip without an LLM call. Real persistor
        # writes to agent_commitments via CommitmentRepository.
        try:
            import asyncio as _asyncio

            from app.repositories.commitment_repository import (
                get_commitment_repository,
            )
            from app.services.ai.runner.commitment_harvester import (
                HarvestContext,
                HarvestedCommitment,
                harvest_commitments,
            )

            commitment_ctx = HarvestContext(
                agent_id=str(composed.agent_id),
                user_id=str(user_id) if user_id else None,
                session_id=str(session_id),
                run_id=str(run_id) if run_id else None,
            )
            commitment_repo = get_commitment_repository()

            # Cheap-LLM extraction summarizer + persistor closures. Both
            # capture by name so the asyncio.create_task dispatch is clean.
            async def _harvest_summarizer(prompt: str) -> str:
                try:
                    # Model-assignment fix (2026-07 audit): the commitment
                    # harvester is a cheap maintenance-tier summarizer, the
                    # same class as the chat compactor (_summarizer above)
                    # and the session/agent-memory summarizers. Route through
                    # the admin-overridable maintenance model
                    # (system_settings.maintenance_llm_model, DB-only per
                    # 铁律 2026-07-07) via resolve_db_adapter — NOT a hardcoded
                    # QwenAdapter(qwen-turbo). The old hardcode ignored the
                    # admin's maintenance-model choice AND silently no-op'd on
                    # any deployment without a DashScope key (last-mile
                    # hardcode class; cf. session_memory_runner Audit #17).
                    from app.schemas.ai_library import ComposedSystemPrompt
                    from app.services.ai.providers.ai_provider_helpers import (
                        get_maintenance_model,
                        resolve_db_adapter,
                    )

                    cheap_model = await get_maintenance_model()
                    # user_id is routing context, not a credential: if the
                    # maintenance model is a codex-local row, the call has to
                    # reach THIS user's own paired machine.
                    adapter = await resolve_db_adapter(
                        cheap_model,
                        "chat",
                        user_id=str(user_id) if user_id else None,
                    )
                    cs = ComposedSystemPrompt(
                        agent_id=composed.agent_id,
                        agent_slug="commitment_harvester",
                        model=cheap_model,
                        temperature=0.0,
                        max_tokens=512,
                        system_message="Extract commitments. Output strict JSON.",
                        tools=[],
                        skill_manifest=[],
                        cache_fingerprint="commitment_harvester_v1",
                    )
                    resp = await adapter.call(cs, [{"role": "user", "content": prompt}])
                    return adapter_text(resp)
                except Exception as exc:
                    # Was a bare `return ""` with no log at all — the harvester
                    # could fail every turn and look identical to "the
                    # conversation contained no commitments".
                    logger.warning(f"[chat] commitment harvester failed: {exc!r}")
                    return ""

            async def _harvest_persistor(
                commitment: HarvestedCommitment, context: HarvestContext
            ):
                from app.agent_framework.commitments import (
                    Commitment,
                    TriggerType,
                )

                try:
                    obj = Commitment(
                        agent_id=context.agent_id,
                        user_id=context.user_id,
                        session_id=context.session_id,
                        description=commitment.description,
                        trigger_type=TriggerType(commitment.trigger_type),
                        trigger_at=commitment.trigger_at,
                        trigger_event=commitment.trigger_event,
                    )
                    created = await commitment_repo.create(obj)
                    return str(created.id) if created and created.id else None
                except Exception:
                    return None

            _asyncio.create_task(
                harvest_commitments(
                    response_text=assistant_content or "",
                    context=commitment_ctx,
                    summarizer=_harvest_summarizer,
                    persistor=_harvest_persistor,
                ),
                name=f"commitment-harvest-{session_id}",
            )
        except Exception as ch_exc:
            logger.warning(
                f"commitment harvester dispatch skipped (non-fatal): {ch_exc}"
            )

        return {
            "user_message": user_msg,
            "assistant_message": asst_msg,
            "usage": usage_snapshot,
            "run_id": str(run_id) if run_id else None,
            "tool_calls": tool_calls_trace,
            # G2/P5: surface any attachment failures so the chat UI can show
            # "I couldn't read X.pdf" or "that character is no longer
            # accessible" — empty list on success. Already `{index, kind,
            # reason}` dicts with `index` counted against the caller's full
            # attachment list, for both the binary and the asset_ref path.
            # Ordered by index so the banner lists them the way the chips sit.
            "attachment_failures": sorted(
                attachment_failures, key=lambda f: f["index"]
            ),
            # G1: when the run paused for human approval, this is the
            # row id the frontend can subscribe / poll for resolution.
            # None on the common case (turn ran to completion).
            "approval_request_id": approval_row_id,
            # True when the user cancelled mid-turn (both paths: run_turn's
            # buffered cancel return and the streaming RunAborted handler).
            # Partial content, if any, is still persisted above.
            "cancelled": bool(result.get("cancelled")),
            # Phase 2a: the turn parked on a typed question (AskUser). The
            # issue workflow parks the issue with it; Task 4 mirrors it into
            # the assistant message metadata for chat.
            "awaiting_input": bool(result.get("awaiting_input")),
            "question": result.get("question"),
            # Phase 2a Task 5: the hook STOP reason, verbatim
            # (turn_end.STOP_REASON_TO_TURN_END), from run_turn's stopped
            # result or the stream's terminal chunk. None on a normal end.
            "stop_reason": result.get("stop_reason"),
        }

    async def _merge_asset_primaries(
        self,
        asset_refs: List[Any],
        resource_refs: List[dict],
        *,
        attachments: List[dict],
        user_id: str,
    ) -> tuple[List[Any], List[dict], List[AssetRefFailure]]:
        """Fold each asset's primary image into the turn's resource refs.

        Returns ``(asset_refs, extra_resource_refs, failures)``. Ruling F: an
        ``<asset>`` entry does not carry a picture, it carries a
        ``primary_resource_id`` pointing at an ordinary ``<resource … />`` line
        that the model can fetch. Nothing else synthesizes that line — the
        renderer takes the two lists as given — so it is made here, and made
        through ``fetch_resource_meta`` so the name, mime, scope label and AI
        status come from the same query the ``@``-mention path uses.

        Two things this method exists to get right:

        **Deduplication.** A user can @-mention a character AND, separately,
        the very PNG that is its reference sheet. Rendering that resource twice
        would spend the tokens twice and invite two fetches of one file, so a
        primary already present among ``resource_refs`` is not re-added — the
        ``<asset>`` still points at it, because the id is the same id.

        **Truthfulness about the picture.** ``has_image`` is computed by the
        resolver through a SYSTEM-scoped read (it has to be: an asset's file
        rows are readable via the asset, not via the caller's team
        membership). ``ResourceFetch``, by contrast, only serves ids in this
        turn's accessible set. So an asset can arrive with ``has_image=True``
        and a primary the caller cannot read as a resource — a system preset
        whose files live outside every team they belong to is the concrete
        case. Advertising it would hand the model an id that fails when used,
        with nothing saying why. Instead the entry is rewritten to state what
        is true (``has_image=False``, no ``primary_resource_id`` attribute —
        exactly the "there is no picture to fetch" shape the README pins) and
        a typed ``asset_no_primary_image`` failure tells the user.
        """
        import dataclasses

        from app.services.assets.chat_ref import expects_primary_image

        wanted: List[str] = []
        known: set = {str(r["id"]) for r in resource_refs}
        for ref in asset_refs:
            pid = ref.primary_resource_id
            if pid and pid not in known and pid not in wanted:
                wanted.append(pid)

        metas: dict = {}
        if wanted:
            try:
                metas = await fetch_resource_meta(user_id, wanted)
            except Exception as fm_exc:
                # Same posture as the resolver failures above: degrade to "no
                # picture", never to a picture the model cannot fetch.
                logger.warning(
                    f"[chat] asset primary-image lookup failed (non-fatal): "
                    f"{fm_exc!r}"
                )
                metas = {}

        # First attachment position per asset id, so a failure points at the
        # chip the user actually sees. Same normalization the resolver used —
        # `coerce_asset_id` is shared rather than re-implemented.
        #
        # FIRST, not every: attaching one asset twice yields ONE ref (the
        # resolver dedupes by id) and therefore ONE failure, reported against
        # the earlier chip — matching how the resolver reports its own. Two
        # entries for one asset would read as two separate problems.
        first_index: dict = {}
        for idx, att in enumerate(attachments):
            if not isinstance(att, dict) or att.get("kind") != "asset_ref":
                continue
            aid = coerce_asset_id(att.get("asset_id"))
            if aid is not None and aid not in first_index:
                first_index[aid] = idx

        out_assets: List[Any] = []
        extra_refs: List[dict] = []
        failures: List[AssetRefFailure] = []
        for ref in asset_refs:
            pid = ref.primary_resource_id
            if not pid or pid in known:
                out_assets.append(ref)
                continue
            meta = metas.get(pid)
            if meta is None:
                logger.info(
                    f"[chat] asset {ref.asset_id} primary resource {pid} is not "
                    f"readable by user={user_id} — delivered without a picture"
                )
                out_assets.append(
                    dataclasses.replace(ref, primary_resource_id=None, has_image=False)
                )
                if expects_primary_image(ref.asset_type):
                    idx = first_index.get(ref.asset_id)
                    if idx is not None:
                        failures.append(
                            AssetRefFailure(index=idx, reason="asset_no_primary_image")
                        )
                continue
            out_assets.append(ref)
            extra_refs.append(meta)
            known.add(pid)
        return out_assets, extra_refs, failures

    async def _maybe_compact(
        self,
        messages: List[Dict[str, Any]],
        *,
        session_id: Optional[Any] = None,
        user_id: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """Compact long histories. Failure → return original messages.

        Compaction calls a cheap auxiliary LLM to summarise the
        head; degrading on failure is fine since the LLM call itself
        will eventually 400 if context truly overflows, and the user
        will see a clear error instead of a silent corruption.

        Wave 5b (B5): when ``session_id`` is provided, looks up the
        cached session_memory and uses it as the head summary instead
        of calling the cheap LLM — saves a round-trip + makes the
        summary structurally consistent (fixed schema).
        """
        from app.services.ai.llm.llm_compactor import (
            DEFAULT_AUTO_COMPACTION_INPUT_TOKENS,
            compact_messages,
            estimate_tokens,
        )

        if estimate_tokens(messages) < DEFAULT_AUTO_COMPACTION_INPUT_TOKENS:
            return messages

        # Wave 5b (B5): build session_memory_loader closure if we have a
        # session_id. Loader returns body_md or None; compactor decides.
        session_memory_loader = None
        if session_id is not None:
            from app.repositories.session_memory_repository import (
                get_session_memory_repository,
            )

            _sm_repo = get_session_memory_repository()

            async def _load_session_memory() -> Optional[str]:
                row = await _sm_repo.load(session_id)
                return row.body_md if row else None

            session_memory_loader = _load_session_memory

        async def _summarizer(head: List[Dict[str, Any]]) -> str:
            try:
                # DB-only credentials (铁律 2026-07-07): the cheap summary
                # model is the maintenance-tier catalog default (admin-
                # overridable via system_settings.maintenance_llm_model). A
                # catalog miss raises and the compactor falls back to its
                # emergency cap.
                from app.services.ai.providers.ai_provider_helpers import (
                    get_maintenance_model,
                    resolve_db_adapter,
                )

                cheap_model = await get_maintenance_model()
                # Routing context (see the harvester above): a codex-local
                # maintenance model must dial this user's own machine.
                adapter = await resolve_db_adapter(
                    cheap_model, "chat", user_id=str(user_id) if user_id else None
                )
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
                        "rejected options (and why), "
                        "facts established, "
                        "user style preferences expressed, "
                        "and the current task state. "
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
                return adapter_text(resp)
            except Exception:
                logger.exception(
                    "[chat] compactor summarizer failed; using empty summary"
                )
                return "[history truncated for context length]"

        try:
            result = await compact_messages(
                messages,
                summarizer=_summarizer,
                session_memory_loader=session_memory_loader,
            )
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


def format_script_context_block(script_context: Optional[dict]) -> str:
    """script_context handle → <user_selection> 指令块（空/无效返回 ""）。
    只带 id 与标签，不带文本——文本已折叠在用户消息里，重复注入是双份 token。"""
    sc = script_context or {}
    scene_id = sc.get("scene_id")
    element_ids = [e for e in (sc.get("element_ids") or []) if e]
    if not scene_id and not element_ids:
        return ""
    lines = ["<user_selection>"]
    if sc.get("scene_label"):
        lines.append(f"scene: {escape_frame_body(sc['scene_label'])}")
    if scene_id:
        lines.append(f"scene_id: {scene_id}")
    if element_ids:
        lines.append(f"element_ids: {', '.join(element_ids)}")
    if sc.get("element_type"):
        lines.append(f"element_type: {escape_frame_body(sc['element_type'])}")
    if sc.get("cross_scene"):
        lines.append("spans multiple scenes")
    if element_ids:
        lines.append(
            "The user's message refers to this selection. Use these ids "
            "directly (ReadScene the scene, then target the selected "
            "elements with ProposeEdit/ApplyEdit) instead of re-locating "
            "the text by content."
        )
    else:
        lines.append(
            "The user's message refers to this scene. Use this scene_id "
            "directly (ReadScene the scene) instead of re-locating it by "
            "content."
        )
    lines.append("</user_selection>")
    return "\n".join(lines)


async def _surface_next_session_commitments(
    *,
    agent_id: str,
    user_id: str,
    request_instructions: str,
) -> str:
    """G8: prepend pending NEXT_SESSION commitment reminders.

    Returns the (possibly) augmented request_instructions; surfaced
    commitments are marked fulfilled so they fire once. Best-effort —
    any failure logs and returns the instructions unchanged."""
    try:
        from app.repositories.commitment_repository import (
            get_commitment_repository,
        )

        crepo = get_commitment_repository()
        pending = await crepo.list_next_session(agent_id=agent_id, user_id=user_id)
        if not pending:
            return request_instructions

        reminder_lines = [
            "<pending_followups>",
            f"You committed to {len(pending)} follow-up(s) "
            "in earlier sessions. Surface them naturally in "
            "your first reply if relevant:",
        ]
        for c in pending[:5]:  # cap on UI noise
            # The description came out of an earlier model turn about the
            # user's own words — untrusted for framing purposes.
            reminder_lines.append(f"  - {escape_frame_body(c.description)}")
        reminder_lines.append("</pending_followups>")

        # Mark them fulfilled so they don't fire again.
        for c in pending[:5]:
            if c.id is not None:
                try:
                    await crepo.mark_fulfilled(c.id, notes="surfaced at session open")
                except Exception:  # noqa: BLE001
                    pass
        logger.info(f"[chat] G8 surfaced {len(pending)} next_session commitments")
        return "\n".join(reminder_lines) + "\n\n" + request_instructions
    except Exception as g8_exc:  # noqa: BLE001
        logger.warning(f"next_session surface skipped (non-fatal): {g8_exc}")
        return request_instructions


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
