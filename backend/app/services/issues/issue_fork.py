"""Phase 2b-1 §2: fork an issue run at a step boundary.

Pure orchestration over an injectable ``deps`` object so the ORDER of side
effects is a unit-tested contract; ``default_deps()`` binds the real
repositories. The original run and its conversation are never written —
the only mark on the origin is a ``question_answered{superseded}`` event
when the fork abandons a question that run was parked on.

History the forked run starts from (spec §2 实施记录, Task 2): the origin
conversation's messages that predate the run, then this run's events up to
``at_seq`` rebuilt by ``replay.messages_from_events`` — the seam is
de-duplicated because the run's seq-1 ``user`` event is the same text the
conversation already holds. Tool work inside the run is not replayed.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional, Protocol

from loguru import logger

from app.services.ai.runner.replay import (
    SUMMARY_PREFIX,
    events_upto,
    is_step_boundary,
    messages_from_events,
)

# What the rebuild reads; tool_call bodies (the bulk) never leave the DB.
REPLAY_EVENT_TYPES = (
    "user",
    "assistant",
    "compaction_summary",
    "step_start",
    "turn_end",
)

TERMINAL = frozenset({"done", "cancelled", "closed"})
ROLES = ("user", "assistant", "system")
ORIGIN_MESSAGE_LIMIT = 5000


class ForkRejected(Exception):
    """Typed refusal; the endpoint maps ``status`` / ``code`` 1:1."""

    def __init__(self, code: str, status: int, message: str = ""):
        super().__init__(message or code)
        self.code, self.status = code, status


class ForkDeps(Protocol):

    async def get_run(self, run_id: int, user_id: str) -> Optional[dict]: ...

    async def get_issue(self, issue_id: int) -> Optional[dict]: ...

    async def list_events(self, run_id: int, at_seq: int) -> list[dict]: ...

    async def running_root_run_id(
        self, issue_id: int, conversation_id: Optional[int]
    ) -> Optional[int]: ...

    async def list_origin_messages(
        self, session_id: int, before: Any
    ) -> list[dict]: ...

    async def create_session(self, **kw: Any) -> dict: ...

    async def append_messages(
        self, session_id: str, messages: list[dict], meta: dict
    ) -> None: ...

    async def switch_session_and_mark(
        self, issue_id: int, session_id: str, forked_from: dict
    ) -> None: ...

    async def release_parked(self, workflow_id: str) -> None: ...

    async def supersede_question(self, run_id: int, question_id: str) -> None: ...

    async def dispatch(self, issue_id: int) -> str: ...

    async def restore_session(
        self, issue_id: int, session_id: Optional[int], paused_at: Any
    ) -> None: ...


def _clean_steer(steer: Optional[str]) -> Optional[str]:
    text = (steer or "").strip()
    return text or None


def _seed(origin: list[dict], run_messages: list[dict]) -> list[dict]:
    """Origin turns + this run's rebuilt messages; the seam collapses one
    identical (role, content) pair — the run's first ``user`` event is the
    text the conversation appended just before the run started. A run
    window that opens with a compaction summary already REPLACED everything
    before it — the origin is not prepended in front of its own summary."""
    if (
        run_messages
        and run_messages[0].get("role") == "system"
        and str(run_messages[0].get("content", "")).startswith(SUMMARY_PREFIX)
    ):
        return list(run_messages)
    seed = [
        {"role": m["role"], "content": m["content"]}
        for m in origin
        if m.get("role") in ROLES and str(m.get("content") or "").strip()
    ]
    if seed and run_messages and seed[-1] == run_messages[0]:
        run_messages = run_messages[1:]
    return seed + run_messages


async def fork_run(
    run_id: int, *, at_seq: int, steer: Optional[str], user_id: str, deps: ForkDeps
) -> dict:
    run = await deps.get_run(int(run_id), user_id)
    if not run:
        raise ForkRejected("not_found", 404, "run not found")
    issue_id = run.get("issue_id")
    if not issue_id:
        raise ForkRejected("not_an_issue_run", 409, "only issue runs can be forked")
    issue = await deps.get_issue(int(issue_id))
    if not issue or issue.get("hidden_at"):
        raise ForkRejected("not_found", 404, "issue not found")
    if issue.get("status") in TERMINAL:
        raise ForkRejected("issue_terminal", 409, "reopen the issue before forking")
    origin_session = issue.get("ai_session_id")
    try:
        live = await deps.running_root_run_id(
            int(issue_id), int(origin_session) if origin_session else None
        )
    except Exception as exc:  # noqa: BLE001 — typed 503, never a silent pass
        raise ForkRejected("run_state_unavailable", 503, str(exc)) from exc
    if live:
        raise ForkRejected("run_live", 409, "pause or cancel the running run first")
    # execute_issue holds execution_locked_at for its whole lifetime — also
    # while PARKED on a question (the agent_runs row is closed then, so the
    # check above cannot see it). A parked workflow is released deliberately
    # below (marker, cancel, lock — the reaper's recipe); any other holder
    # means the issue is busy and the fork would be skipped by atomic_checkout.
    awaiting = (issue.get("execution_state") or {}).get("awaiting_input") or {}
    parked_wf: Optional[str] = None
    if issue.get("execution_locked_at"):
        if (
            awaiting
            and not awaiting.get("answered_at")
            and issue.get("dbos_workflow_id")
        ):
            parked_wf = str(issue["dbos_workflow_id"])
        else:
            raise ForkRejected(
                "issue_busy", 409, "the issue's workflow is still running"
            )
    events = await deps.list_events(int(run_id), int(at_seq))
    if not is_step_boundary(events, at_seq):
        raise ForkRejected(
            "not_a_step_boundary", 400, "at_seq must be a step_start or turn_end seq"
        )

    run_messages = messages_from_events(events_upto(events, at_seq))
    # The run's own conversation first (a run forked earlier lives in a
    # session the issue no longer points at); the issue pointer is the
    # fallback for rows recorded before conversation_id was stamped.
    history_session = run.get("conversation_id") or origin_session
    origin: list[dict] = []
    if history_session:
        origin = await deps.list_origin_messages(
            int(history_session), run.get("started_at")
        )
    messages = _seed(origin, run_messages)

    steer_text = _clean_steer(steer)
    forked_from = {
        "run_id": int(run_id),
        "at_seq": int(at_seq),
        "steer": steer_text is not None,
        "steer_text": steer_text,
    }
    session = await deps.create_session(
        user_id=issue.get("created_by_user_id")
        or issue.get("assignee_user_id")
        or user_id,
        agent_id=issue.get("assignee_agent_id"),
        title=(issue.get("title") or "Issue")[:200],
        project_id=issue.get("project_id"),
        team_id=issue.get("team_id"),
        context_type="issue",
        context_id=str(issue_id),
    )
    session_id = str(session["id"])
    await deps.append_messages(
        session_id,
        messages,
        {"forked_from": {"run_id": int(run_id), "at_seq": int(at_seq)}},
    )
    if parked_wf is not None:
        await deps.release_parked(parked_wf)
    await deps.switch_session_and_mark(int(issue_id), session_id, forked_from)
    try:
        workflow_id = await deps.dispatch(int(issue_id))
    except Exception as exc:  # noqa: BLE001
        # The pointer and paused_at go back in one transaction; a released
        # parked workflow cannot be revived — the issue is then in the same
        # state the stale-wait reaper leaves (unlocked, no marker).
        await deps.restore_session(
            int(issue_id),
            int(origin_session) if origin_session else None,
            issue.get("paused_at"),
        )
        raise ForkRejected("dispatch_failed", 503, str(exc)) from exc
    # Only after the replacement is really dispatched: the abandoned question
    # (whichever run asked it) is recorded as superseded.
    if awaiting.get("question_id") and awaiting.get("run_id"):
        await deps.supersede_question(
            int(awaiting["run_id"]), str(awaiting["question_id"])
        )
    logger.info(
        f"[issue_fork] run {run_id} @seq {at_seq} → issue {issue_id} "
        f"session {session_id} wf {workflow_id} steer={steer_text is not None}"
    )
    return {
        "run_id": None,
        "session_id": session_id,
        "workflow_id": workflow_id,
        "issue_id": int(issue_id),
        "forked_from": {"run_id": int(run_id), "at_seq": int(at_seq)},
    }


# ── real bindings ────────────────────────────────────────────────────────────


def _ts(v: Any) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v
    if isinstance(v, str) and v:
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


class _RealDeps:
    async def get_run(self, run_id: int, user_id: str) -> Optional[dict]:
        from uuid import UUID

        from app.repositories.agent_runs_repository import get_agent_runs_repository

        return await get_agent_runs_repository().get_by_id(
            str(run_id), user_id=UUID(str(user_id))
        )

    async def get_issue(self, issue_id: int) -> Optional[dict]:
        from app.repositories.issue_repository import get_issue_repository

        return await get_issue_repository().get_by_id(int(issue_id))

    async def list_events(self, run_id: int, at_seq: int) -> list[dict]:
        from app.repositories.agent_runs_repository import get_agent_runs_repository

        return await get_agent_runs_repository().list_transcript_events(
            int(run_id), upto_seq=int(at_seq), event_types=list(REPLAY_EVENT_TYPES)
        )

    async def release_parked(self, workflow_id: str) -> None:
        from app.agent_framework.input_gate import release_parked_workflow

        await release_parked_workflow(workflow_id)

    async def running_root_run_id(
        self, issue_id: int, conversation_id: Optional[int]
    ) -> Optional[int]:
        from app.repositories.agent_runs_repository import get_agent_runs_repository

        return await get_agent_runs_repository().running_root_run_id(
            issue_id=int(issue_id), conversation_id=conversation_id
        )

    async def list_origin_messages(self, session_id: int, before: Any) -> list[dict]:
        from app.services.ai.chat.conversations_ai_store import ConversationsAiStore

        rows = await ConversationsAiStore().get_messages(
            session_id=int(session_id), limit=ORIGIN_MESSAGE_LIMIT
        )
        cutoff = _ts(before)
        out = []
        for r in rows:
            created = _ts(r.get("created_at"))
            if cutoff is not None and created is not None and created >= cutoff:
                break  # seq ASC — everything after belongs to the run or later
            out.append({"role": r.get("role"), "content": r.get("content") or ""})
        return out

    async def create_session(self, **kw: Any) -> dict:
        from uuid import UUID

        from app.repositories.agent_repository import get_agent_repository
        from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

        agent_id = kw.get("agent_id")
        agent = (
            await get_agent_repository().get_by_id(UUID(str(agent_id)))
            if agent_id
            else None
        )
        if not agent:
            raise ForkRejected(
                "run_state_unavailable", 503, f"assignee agent {agent_id} not found"
            )
        return await AILibraryChatService().create_session(
            user_id=UUID(str(kw["user_id"])),
            agent_slug=agent["slug"],
            title=kw["title"],
            project_id=kw.get("project_id"),
            team_id=kw.get("team_id"),
            context_type=kw.get("context_type"),
            context_id=kw.get("context_id"),
        )

    async def append_messages(
        self, session_id: str, messages: list[dict], meta: dict
    ) -> None:
        from app.services.ai.chat.conversations_ai_store import ConversationsAiStore

        store = ConversationsAiStore()
        sid = int(session_id)
        # The new session's owner / agent are on the session row we just
        # created (create_session bound the issue owner + assignee agent).
        sess = await store.get_session(session_id=sid) or {}
        owner = sess.get("user_id")
        agent_id = sess.get("agent_id")
        for m in messages:
            role, content = m["role"], m["content"]
            if role == "user":
                await store.append_user_message(
                    session_id=sid, user_id=str(owner), content=content
                )
            elif role == "assistant":
                await store.append_assistant_message(
                    session_id=sid,
                    agent_id=agent_id,
                    content=content,
                    prompt_tokens=0,
                    completion_tokens=0,
                    metadata=dict(meta),
                )
            else:
                await store.append_system_message(
                    session_id=sid, content=content, metadata=dict(meta)
                )

    async def switch_session_and_mark(
        self, issue_id: int, session_id: str, forked_from: dict
    ) -> None:
        from sqlalchemy import Text, cast, func, literal, text, update
        from sqlalchemy.dialects.postgresql import JSONB

        from app.db.session import write_scope
        from app.models import Issues

        state = func.coalesce(Issues.execution_state, cast(literal("{}"), JSONB))
        cleared = state.op("-", return_type=JSONB)(
            cast(literal("awaiting_input"), Text)
        )
        marked = cleared.op("||", return_type=JSONB)(
            func.jsonb_build_object(
                "forked_from", cast(literal(json.dumps(forked_from)), JSONB)
            )
        )
        async with write_scope() as session:
            # execution_state is service_role-only (mig 170 allowlist)
            await session.execute(text("SET LOCAL ROLE service_role"))
            await session.execute(
                update(Issues)
                .where(Issues.id == int(issue_id))
                .values(
                    ai_session_id=int(session_id),
                    paused_at=None,
                    execution_state=marked,
                )
            )

    async def supersede_question(self, run_id: int, question_id: str) -> None:
        from app.services.ai.runner.question import QUESTION_ANSWERED
        from app.services.ai.runner.run_recorder import event_writer_for_run

        try:
            writer = await event_writer_for_run(int(run_id))
            await writer.append(
                QUESTION_ANSWERED,
                {"question_id": question_id, "value": None, "superseded": True},
                turn=None,
                step=None,
            )
        except Exception as exc:  # noqa: BLE001 — the fork already switched away
            logger.warning(
                f"[issue_fork] question_answered(superseded) for run {run_id} "
                f"not recorded: {exc!r}"
            )

    async def dispatch(self, issue_id: int) -> str:
        from app.services.issues.issue_dispatch import start_execute_issue

        return await start_execute_issue(int(issue_id))

    async def restore_session(
        self, issue_id: int, session_id: Optional[int], paused_at: Any
    ) -> None:
        from sqlalchemy import Text, cast, func, literal, text, update
        from sqlalchemy.dialects.postgresql import JSONB

        from app.db.session import write_scope
        from app.models import Issues

        # One transaction: pointer, paused_at and the stamp go back together.
        state = func.coalesce(Issues.execution_state, cast(literal("{}"), JSONB))
        unstamped = state.op("-", return_type=JSONB)(cast(literal("forked_from"), Text))
        async with write_scope() as session:
            await session.execute(text("SET LOCAL ROLE service_role"))
            await session.execute(
                update(Issues)
                .where(Issues.id == int(issue_id))
                .values(
                    ai_session_id=session_id,
                    paused_at=_ts(paused_at),
                    execution_state=unstamped,
                )
            )


def default_deps() -> ForkDeps:
    return _RealDeps()


__all__ = ["ForkDeps", "ForkRejected", "default_deps", "fork_run"]
