"""Issue Messages REST API — paperclip-style chat thread per issue (A8).

Endpoints (mounted at /api/v1/issues/{issue_id}):
  GET    /{id}/messages                 — list the thread, oldest first
  POST   /{id}/comment-trigger-preview  — what a comment would start, given the
                                          draft body (read-only; no side effect)
  POST   /{id}/messages                 — post a comment; wakes the issue's
                                          assigned agent unless suppressed
                                          or the body is a /note command

Whether a comment wakes an agent is driven by the ISSUE's assignee_agent_id —
NOT by the payload's `agent_id` field, which is vestigial and never read (see
IssueMessagePost). The decision lives in ONE place, services/issues/
comment_trigger.py, which both POST and the preview endpoint call, so the
composer chip can never promise something the send path won't do.

The status-change side of the timeline is auto-emitted by the database
trigger trg_issue_status_change_message — no explicit endpoint needed.

RLS at the DB layer enforces visibility cascades through `issues`. The
service-role admin client bypasses RLS, so this router re-checks
visibility in Python via the same pattern as issues_router.

Spec-1a (Task 5): GET /{issue_id}/messages has a dual read path.
  - Session path: when issues.ai_session_id is set, read the session's
    messages through ``ConversationsAiStore`` (the sole ``MessageStore``
    implementation since Conversations Phase 3 Task 6 — canonical
    ``conversations``/``messages`` tables, mig 327+332) and map each row to
    IssueMessage shape so the frontend needs no change. The legacy
    ``ai_sessions``/``ai_messages`` tables this used to read directly are
    retired in mig 333 (same PR) — no dual-read against them.
  - Legacy path: when ai_session_id is NULL, fall back to the issue_messages
    table unchanged (covers issues predating the ai_session wiring). This is
    a distinct, still-live table — unrelated to the mig 333 drop above.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from dbos import DBOS, SetWorkflowID
from fastapi import APIRouter, HTTPException, status
from loguru import logger

from app.agent_framework import input_gate
from app.core.deps import AuthDep
from app.repositories.issue_repository import (  # noqa: F401 — tests patch get_by_id via this module path
    issue_repository,
)
from app.schemas.issue_message import (
    CommentTriggerPreview,
    CommentTriggerPreviewRequest,
    IssueMessage,
    IssueMessageKind,
    IssueMessageList,
    IssueMessagePost,
    IssueMessagePostResponse,
)
from app.services.ai.chat.conversations_ai_store import ConversationsAiStore
from app.services.issues.comment_trigger import (
    apply_suppression,
    compute_comment_trigger,
)
from app.services.issues.issue_message_mapper import map_ai_message_to_issue_message
from app.services.issues.issue_session import get_or_create_issue_session
from app.services.issues.issue_visibility import assert_issue_visible
from app.workflows.issue_lifecycle import respond_to_issue_reply

router = APIRouter(prefix="/issues", tags=["Issue Messages"])

# The old direct-ai_messages query had no LIMIT (returned the whole thread);
# ConversationsAiStore.get_messages defaults to 200. Pass an explicit high
# ceiling here so the GET endpoint's contract (full thread, oldest first)
# doesn't silently regress into pagination for long-running issue chats.
_SESSION_MESSAGES_LIMIT = 10_000


def _dispatch_respond_to_issue_reply(
    issue_id: int,
    owner_id: str,
    body: str,
    attachments: list | None,
    wf_id: str,
) -> None:
    """Dispatch the respond_to_issue_reply DBOS workflow under a pinned wf id.

    Client-aware (gateway→DBOSClient prep, currently DORMANT): when the gateway
    has constructed a DBOSClient, enqueue through it into the `dbos_dispatch`
    queue. Otherwise (client is None — today's reality) fall back to the
    in-process `SetWorkflowID + DBOS.start_workflow` path. Zero behavior change
    while the client stays None. Positional args preserved exactly:
    (issue_id, owner_id, body, attachments).
    """
    from app.services.infra.dbos_orchestrator import (
        _resolve_pinned_app_version,
        get_dbos_client,
    )

    client = get_dbos_client()
    if client is not None:
        from dbos import EnqueueOptions

        opts: dict = {
            "workflow_name": "respond_to_issue_reply",
            "queue_name": "dbos_dispatch",
            "workflow_id": wf_id,
        }
        pinned = _resolve_pinned_app_version()
        if pinned:
            opts["app_version"] = pinned
        client.enqueue(EnqueueOptions(**opts), issue_id, owner_id, body, attachments)
        return

    with SetWorkflowID(wf_id):
        DBOS.start_workflow(
            respond_to_issue_reply,
            issue_id,
            owner_id,
            body,
            attachments,
        )


from app.services.ai.runner.run_recorder import (  # noqa: E402 — test seam
    event_writer_for_run as _event_writer_for_run,
)


@dataclass(frozen=True)
class _PendingAnswer:
    """A validated answer that has NOT been recorded yet — recording happens
    only after the reply was actually delivered (woken or dispatched)."""

    question_id: str
    value: str
    kind: str
    run_id: Optional[str]
    workflow_id: Optional[str]
    #: False when ``on_answer`` itself ended the issue (budget "Cancel" →
    #: cancelled): nothing to wake, the answer is just recorded.
    wake: bool = True


async def _still_runnable(issue_row: dict) -> bool:
    """Re-read the issue after ``on_answer``: a kind may have ended it (budget
    "Cancel" → cancelled). Waking then would let the loop overwrite the
    terminal status with in_progress. Unreadable → assume runnable (the loop
    re-checks PREEMPT_STATUSES at wake as well)."""
    from app.workflows.issue_lifecycle import PREEMPT_STATUSES

    try:
        fresh = await issue_repository.get_by_id(int(issue_row["id"]))
    except Exception as exc:  # noqa: BLE001 — the loop has its own guard
        logger.warning(f"[issue_reply] post-answer status read failed: {exc}")
        return True
    return (fresh or {}).get("status") not in PREEMPT_STATUSES


async def _validate_typed_answer(
    issue_row: dict, body: str, answer_to: Optional[str], user_id: str
) -> Optional[_PendingAnswer]:
    """Phase 2a answer channel (spec §1), step 1 of 2: decide whether this
    comment answers the parked question, and run the kind's ``on_answer``
    (it may refuse — e.g. budget still exhausted — BEFORE anything is woken).
    Returns None when the comment is not an answer.

    ``answer_to`` given: the marker must hold that exact, still-open question
    (409 ``no_open_question`` — also once ``answered_at`` is stamped, so a
    retry of the same answer cannot wake a second turn) and the body must
    match (400 ``answer_shape``). ``answer_to`` absent: a body equal to one
    label still counts (three-phase compat: old clients reply with the bare
    label)."""
    from app.services.ai.runner.question import (
        AnswerContext,
        AnswerRejected,
        answer_matches,
        on_answer_for,
    )

    wf_id = issue_row.get("dbos_workflow_id")
    marker = await _load_awaiting_marker(wf_id) if wf_id else None
    if marker and marker.get("answered_at"):
        marker = None  # already answered; the workflow just has not cleared it yet
    open_qid = (marker or {}).get("question_id")
    if answer_to is not None:
        if not marker or open_qid != answer_to:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "no_open_question",
                    "message": f"no open question {answer_to!r} on this issue",
                },
            )
        if not answer_matches(marker, body):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "answer_shape",
                    "message": "answer must equal one of the option labels"
                    + (" or be free text" if marker.get("allow_free_text") else ""),
                },
            )
    else:
        labels = {
            o.get("label")
            for o in ((marker or {}).get("options") or [])
            if isinstance(o, dict)
        }
        if not open_qid or body not in labels:
            return None

    kind = str(marker.get("kind") or "user")
    try:
        handler = on_answer_for(kind)
    except KeyError:
        raise HTTPException(
            status_code=500,
            detail={"code": "unknown_question_kind", "message": kind},
        )
    try:
        await handler(
            issue_row,
            body,
            AnswerContext(target=issue_row, user_id=user_id, marker=marker),
        )
    except AnswerRejected as rej:
        raise HTTPException(
            status_code=rej.status, detail={"code": rej.code, "message": str(rej)}
        )
    run_id = marker.get("run_id")
    return _PendingAnswer(
        question_id=open_qid,
        value=body,
        kind=kind,
        run_id=str(run_id) if run_id else None,
        workflow_id=str(wf_id) if wf_id else None,
        wake=await _still_runnable(issue_row),
    )


async def _commit_typed_answer(pending: _PendingAnswer) -> None:
    """Step 2 of 2, after delivery: ``question_answered`` on the asking run
    and ``answered_at`` on the marker. Both best-effort — the answer already
    travelled; a missing record is logged, never a failed reply."""
    from app.services.ai.runner.question import QUESTION_ANSWERED

    if pending.run_id:
        try:
            writer = await _event_writer_for_run(pending.run_id)
            await writer.append(
                QUESTION_ANSWERED,
                {
                    "question_id": pending.question_id,
                    "value": pending.value,
                    "superseded": False,
                },
                turn=None,
                step=None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"[issue_reply] question_answered for run {pending.run_id} "
                f"not recorded: {exc!r}"
            )
    else:
        logger.warning(
            f"[issue_reply] marker for question {pending.question_id} has no "
            "run_id; question_answered not recorded"
        )
    if pending.workflow_id:
        await input_gate.mark_question_answered(
            workflow_id=pending.workflow_id,
            question_id=pending.question_id,
            value=pending.value,
        )


async def _load_awaiting_marker(workflow_id: str) -> Optional[dict]:
    """按 dbos_workflow_id 取 issues.execution_state.awaiting_input；无则 None。

    权威标记位在 issues 行上（issue dispatch 没有 task_tracking 行——
    2026-08-03 E2E 实测修正，见 input_gate.mark_awaiting_input）。"""
    import json

    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import JSONB

    from app.db.session import read_scope
    from app.models import Issues

    async with read_scope() as session:
        marker = (
            await session.execute(
                select(
                    Issues.execution_state.op("->", return_type=JSONB)("awaiting_input")
                ).where(Issues.dbos_workflow_id == workflow_id)
            )
        ).scalar_one_or_none()
    if isinstance(marker, str):
        try:
            marker = json.loads(marker)
        except ValueError:
            return None
    return marker if isinstance(marker, dict) else None


async def _workflow_is_terminal(workflow_id: str) -> bool:
    """DBOS 视角 workflow 是否已终态（SUCCESS/ERROR/CANCELLED…）。
    查询失败按"终态"处理 —— 宁可走旧路径也不投递到虚空。"""
    from app.db import engine as db_engine

    try:
        row = await db_engine.fetch_one(
            "SELECT status FROM dbos.workflow_status WHERE workflow_uuid = :wf",
            {"wf": workflow_id},
        )
        return (row or {}).get("status") not in ("PENDING", "ENQUEUED")
    except Exception:  # noqa: BLE001
        return True


async def _try_wake_waiting_workflow(
    issue_row: dict, user_id: str, reply_text: str, attachments: Optional[list]
) -> bool:
    """回复分流（spec 2026-07-30 §4）：True=已投递给挂起等 needs_input 回复的
    dispatch workflow（原地续跑，不再另起 respond_to_issue_reply——后者会撞上
    dispatch 自己持有的 execution_locked_at 自旋 10 分钟后 defer）；False=调用方
    走旧路径 dispatch。

    已接受的竞态窗口：终态预检通过后、send 落地前 workflow 恰好超时终结（72h
    TTL 的最后几毫秒），消息被 DBOS 静默丢弃——与现状"dispatch 后 workflow 在
    跑 turn 前崩溃"同形（乐观评论已回给 UI 但无 turn 落库），用户重发即可。
    """
    wf_id = issue_row.get("dbos_workflow_id")
    if not wf_id:
        return False
    marker = await _load_awaiting_marker(wf_id)
    if not marker:
        return False
    if await _workflow_is_terminal(wf_id):
        return False
    return await input_gate.signal_user_reply(
        workflow_id=wf_id,
        issue_id=int(issue_row["id"]),
        reply_text=reply_text,
        user_id=user_id,
        attachments=attachments,
    )


async def _assert_issue_visible(issue_id: int, auth) -> dict:
    """Shared rule (services/issues/issue_visibility) — kept as a local name
    so the many call sites in this module read unchanged."""
    return await assert_issue_visible(issue_id, auth)


@router.get("/{issue_id}/messages", response_model=IssueMessageList)
async def list_issue_messages(issue_id: int, auth: AuthDep) -> IssueMessageList:
    """Fetch the chat thread for an issue, oldest first.

    Dual-path (Spec-1a Task 5, repointed onto ConversationsAiStore in the
    P3 Task 6 fix-up):
    - Session path: when the issue has ai_session_id set, reads the session's
      messages through ``ConversationsAiStore`` and maps each row to
      IssueMessage shape.
    - Legacy path: when ai_session_id is None, reads issue_messages directly
      (unchanged behaviour for issues predating the ai_session wiring).
    """
    issue_row = await _assert_issue_visible(issue_id, auth)
    ai_session_id: Optional[str] = issue_row.get("ai_session_id")

    # ── Session path ──────────────────────────────────────────────────────
    if ai_session_id:
        store = ConversationsAiStore()

        # Fetch the session's user_id once (needed for user-role message
        # mapping) via the store — same legacy-shaped row the AI Library
        # chat service consumes, so `user_id` is already a plain string.
        try:
            session_row = await store.get_session(session_id=int(ai_session_id))
        except Exception as exc:
            logger.exception(
                f"fetch conversations session failed (issue_id={issue_id}, "
                f"session_id={ai_session_id}): {exc}"
            )
            raise HTTPException(500, "failed to fetch session")

        session_user_id: Optional[UUID] = None
        if session_row and session_row.get("user_id"):
            try:
                session_user_id = UUID(str(session_row["user_id"]))
            except (ValueError, AttributeError):
                pass

        # Read the session's messages, oldest first (store's own SELECT is
        # ordered by `seq ASC`, matching the old `created_at ASC` ordering).
        try:
            rows = await store.get_messages(
                session_id=int(ai_session_id), limit=_SESSION_MESSAGES_LIMIT
            )
        except Exception as exc:
            logger.exception(
                f"fetch conversations messages failed (issue_id={issue_id}, "
                f"session_id={ai_session_id}): {exc}"
            )
            raise HTTPException(500, "failed to list messages")

        messages = [
            map_ai_message_to_issue_message(
                r, issue_id=issue_id, session_user_id=session_user_id
            )
            for r in rows
        ]
        return IssueMessageList(messages=messages, total=len(messages))

    # ── Legacy path (no ai_session) ───────────────────────────────────────
    # Unchanged: read issue_messages table so old issues render correctly.
    # (issue_messages is a distinct, still-live table — not one of the 6
    # dropped by mig 333.)
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import IssueMessages

    try:
        async with read_scope() as session:
            objs = (
                (
                    await session.execute(
                        select(IssueMessages)
                        .where(IssueMessages.issue_id == issue_id)
                        .order_by(IssueMessages.created_at.asc())
                    )
                )
                .scalars()
                .all()
            )
            messages = [
                IssueMessage.model_validate(o, from_attributes=True) for o in objs
            ]
    except Exception as exc:
        logger.exception(f"list issue_messages failed (issue_id={issue_id}): {exc}")
        raise HTTPException(500, "failed to list messages")

    return IssueMessageList(messages=messages, total=len(messages))


@router.post(
    "/{issue_id}/comment-trigger-preview", response_model=CommentTriggerPreview
)
async def comment_trigger_preview(
    issue_id: int, payload: CommentTriggerPreviewRequest, auth: AuthDep
) -> CommentTriggerPreview:
    """Predict what POST /{id}/messages would start — without posting.

    Read-only (no writes, no dispatch). Backs the composer chip ("Will start
    when sent · X" / "Quiet note · won't wake X"), which today is the ONLY
    signal that ⌘↩ spends money.

    Deliberately not GET /{id}/dispatch-preview: that mirrors dispatch_issue's
    guards, and the comment path honours none of them — commenting on a `done`
    issue, or mid-run, still wakes the agent. It would report `blocked` where
    this reports `will_wake`.

    POST (not GET) because it carries the draft body: a `/note` prefix flips the
    verdict to a silent note, so the predicate MUST see the same body the send
    path will. `body` is optional — a bodyless preview (armed composer, nothing
    typed yet) returns the assignee-based verdict. Routes through the SAME
    predicate as POST /messages so the chip can never promise something the send
    path won't do.
    """
    issue_row = await _assert_issue_visible(issue_id, auth)
    verdict = compute_comment_trigger(issue_row, payload.body)
    return CommentTriggerPreview(
        will_wake=verdict.will_wake,
        agent_id=verdict.agent_id,
        is_note=verdict.is_note,
    )


async def _insert_legacy_comment(
    issue_id: int, payload: IssueMessagePost, auth: AuthDep
) -> IssueMessagePostResponse:
    """Plain comment row for issues with no assigned agent (pre-ai_session).

    Only reachable when nothing can be woken. NEVER use this for an
    agent-assigned issue: GET takes the session path and never queries
    issue_messages, so the row would flash in via the Realtime subscription and
    vanish on the next refetch.
    """
    from sqlalchemy import insert

    from app.db.session import write_scope
    from app.models import IssueMessages

    comment_row = {
        "issue_id": issue_id,
        "kind": IssueMessageKind.COMMENT.value,
        "author_user_id": str(auth.user_id),
        "body": payload.body,
        "meta": {},
    }
    try:
        async with write_scope() as session:
            inserted = (
                (
                    await session.execute(
                        insert(IssueMessages)
                        .values(comment_row)
                        .returning(*IssueMessages.__table__.columns)
                    )
                )
                .mappings()
                .first()
            )
    except Exception as exc:
        logger.exception(f"insert comment failed (issue_id={issue_id}): {exc}")
        raise HTTPException(500, "comment insert failed")

    if not inserted:
        raise HTTPException(500, "comment insert returned no row")

    return IssueMessagePostResponse(
        comment=IssueMessage.model_validate(dict(inserted)), agent_run=None
    )


def _optimistic_comment(issue_id: int, body: str, auth: AuthDep) -> IssueMessage:
    """Synthesised row for immediate render; GET (the session) is canonical."""
    return IssueMessage(
        id=uuid.uuid4(),
        issue_id=issue_id,
        kind=IssueMessageKind.COMMENT,
        author_user_id=auth.user_id,
        body=body,
        meta={"optimistic": True},
        created_at=datetime.now(timezone.utc),
    )


def _resolve_owner(issue_row: dict) -> str:
    """The turn runs as the issue OWNER (BYO-key/adapter context), mirroring
    get_or_create_issue_session; the replying human's identity is not
    separately threaded (single-owner-issue assumption)."""
    owner_id = issue_row.get("created_by_user_id") or issue_row.get("assignee_user_id")
    if not owner_id:
        raise HTTPException(500, "issue has no owner to run the turn as")
    return str(owner_id)


async def _divert_to_inbox_if_running(
    issue_id: int,
    session_id: str,
    owner_id: str,
    auth: AuthDep,
    body: str,
    attachments_payload: list | None,
    *,
    paused: bool = False,
) -> Optional[str]:
    """Enqueue the comment on the issue's inbox when a ROOT run is running on
    it — or when the issue is PAUSED (phase 2a: the comment waits for the
    resumed run; a wake would start a turn on a paused target). Return the
    inbox id, or None when neither holds (caller falls through to the wake
    path). The comment row is persisted first so a failed enqueue never loses
    the human's words."""
    from app.repositories.agent_run_inbox_repository import (
        get_agent_run_inbox_repository,
    )
    from app.repositories.agent_runs_repository import get_agent_runs_repository

    running = await get_agent_runs_repository().running_root_run_id(
        issue_id=issue_id, conversation_id=int(session_id)
    )
    if running is None and not paused:
        return None
    await ConversationsAiStore().append_user_message(
        session_id=int(session_id),
        user_id=owner_id,
        content=body,
        attachments=ConversationsAiStore.display_attachments(attachments_payload),
    )
    row = await get_agent_run_inbox_repository().enqueue(
        target_kind="issue",
        target_id=issue_id,
        user_id=str(auth.user_id),
        kind="steer",
        content={"body": body, "attachments": attachments_payload or []},
    )
    why = f"run {running}" if running is not None else "issue paused"
    logger.info(f"[issue_reply] issue {issue_id}: diverted to inbox ({why})")
    return str(row["id"])


@router.post(
    "/{issue_id}/messages",
    response_model=IssueMessagePostResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_issue_message(
    issue_id: int, payload: IssueMessagePost, auth: AuthDep
) -> IssueMessagePostResponse:
    """Post a human comment on an issue.

    Three paths, chosen by the shared predicate (never by payload.agent_id):

    - Wake (Spec-1b): the issue has an assigned agent and the client did not
      suppress it AND the body is not a /note command. The comment drives
      another agent turn on the issue's session (respond_to_issue_reply).
      run_session_turn persists the human message and the agent reply; the
      returned comment is optimistic.
    - Note: the body opens with /note, OR the client suppressed this agent.
      Either way the message is appended to the session WITHOUT starting a turn
      — the agent reads it on its next wake (history loads every message in the
      conversation, unfiltered by role). The body is stored verbatim, /note
      prefix and all (multica parity — the literal text is the record).
    - Legacy: no assigned agent → plain issue_messages insert.
    """
    issue_row = await _assert_issue_visible(issue_id, auth)
    # Pass the body so a /note prefix short-circuits the wake, exactly as the
    # preview endpoint reported it. apply_suppression then layers the client's
    # explicit skip on top (a no-op once /note already zeroed will_wake).
    verdict = apply_suppression(
        compute_comment_trigger(issue_row, payload.body), payload.suppress_agent_ids
    )

    # ── Legacy path (nothing to wake) ─────────────────────────────────────
    if verdict.agent_id is None:
        if payload.answer_to is not None:
            # A typed answer with nobody to wake is not a comment; say so.
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "no_open_question",
                    "message": "this issue has no agent to answer",
                },
            )
        return await _insert_legacy_comment(issue_id, payload, auth)

    session_id = await get_or_create_issue_session(issue_id)
    if not session_id:
        raise HTTPException(500, "issue has an agent but no resolvable session")
    owner_id = _resolve_owner(issue_row)

    attachments_payload = (
        [a.model_dump() for a in payload.attachments] if payload.attachments else None
    )

    # Phase 2a: an answer to a parked typed question is validated and
    # recorded here, then travels down the ordinary wake path below.
    # An answer overrides the note / suppression paths (answering IS a wake),
    # skips inbox diversion (the parked workflow must be the one woken), and
    # is recorded only after delivery succeeded (see _commit_typed_answer).
    # While the issue is PAUSED an answer still goes through (spec §2: an
    # answer IS a wake): the parked workflow's recv TTL keeps counting during
    # a pause, so refusing answers would let a long pause strand the question.
    # The reply turn it starts is the one turn a pause does not gate; the
    # dispatch loop stops the next continuation on ``paused_at``.
    paused = bool(issue_row.get("paused_at"))
    answer = await _validate_typed_answer(
        issue_row, payload.body, payload.answer_to, owner_id
    )
    if answer is not None and not answer.wake:
        # The answer ended the issue (e.g. budget Cancel): record it, wake
        # nothing — a reply turn on a cancelled issue is a pointless run.
        await _commit_typed_answer(answer)
        return IssueMessagePostResponse(
            comment=_optimistic_comment(issue_id, payload.body, auth),
            agent_run=None,
            agent_dispatched=False,
        )

    # ── Note path (suppressed) ────────────────────────────────────────────
    if not verdict.will_wake and answer is None:
        try:
            await ConversationsAiStore().append_user_message(
                session_id=int(session_id),
                user_id=owner_id,
                content=payload.body,
                # Same reducer run_session_turn uses — a note and a real turn
                # must store identical shapes or history reload renders them
                # differently.
                attachments=ConversationsAiStore.display_attachments(
                    attachments_payload
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                f"append suppressed note failed (issue_id={issue_id}): {exc}"
            )
            raise HTTPException(500, "failed to save note")
        return IssueMessagePostResponse(
            comment=_optimistic_comment(issue_id, payload.body, auth), agent_run=None
        )

    # ── Inbox diversion (harness p4 §1-③, phase 2a pause) ────────────────
    # A root run is mid-turn on this issue (or the issue is paused): the
    # comment is a steer, claimed at the next step boundary of the running /
    # resumed run, not a second turn queued behind the lock.
    # The comment row is kept (same reducer as the note path) so the thread
    # reads the same whether the run picked it up or not.
    inbox_id = (
        None
        if answer is not None
        else await _divert_to_inbox_if_running(
            issue_id,
            session_id,
            owner_id,
            auth,
            payload.body,
            attachments_payload,
            paused=paused,
        )
    )
    if inbox_id is not None:
        return IssueMessagePostResponse(
            comment=_optimistic_comment(issue_id, payload.body, auth),
            agent_run=None,
            agent_dispatched=False,
            diverted_to_inbox=True,
            inbox_id=inbox_id,
        )

    # ── Wake path: waiting-workflow diversion first (spec 2026-07-30 §4) ──
    # A dispatch suspended on the needs_input gate consumes the reply in
    # place; only when no waiter exists (or delivery fails) does the reply
    # start a fresh respond_to_issue_reply turn (the pre-existing path).
    if await _try_wake_waiting_workflow(
        issue_row, owner_id, payload.body, attachments_payload
    ):
        logger.info(f"[issue_reply] issue {issue_id}: delivered to waiting workflow")
        if answer is not None:
            await _commit_typed_answer(answer)
        return IssueMessagePostResponse(
            comment=_optimistic_comment(issue_id, payload.body, auth),
            agent_run=None,
            agent_dispatched=True,
        )

    # ── Wake path (Spec-1b) ───────────────────────────────────────────────
    wf_id = f"issue-reply-{issue_id}-{uuid.uuid4()}"
    try:
        _dispatch_respond_to_issue_reply(
            issue_id,
            owner_id,
            payload.body,
            attachments_payload,
            wf_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            f"dispatch respond_to_issue_reply failed (issue_id={issue_id}): {exc}"
        )
        raise HTTPException(500, "failed to dispatch reply turn")
    if answer is not None:
        await _commit_typed_answer(answer)

    return IssueMessagePostResponse(
        comment=_optimistic_comment(issue_id, payload.body, auth),
        agent_run=None,
        agent_dispatched=True,
    )


@router.post("/{issue_id}/agent-runs/{run_id}/simulate-complete")
async def simulate_agent_run_complete(
    issue_id: int,
    # agent_runs.id is BIGINT Snowflake (mig 232) → numeric string, not UUID.
    run_id: str,
    auth: AuthDep,
    output_summary: Optional[str] = None,
) -> dict:
    """Dev/demo helper: flip an issue-scoped agent_run from running →
    completed with a sample summary. The mig 208 terminal trigger then
    UPDATEs the chat row in place; Realtime delivers it to subscribers.

    Mediahub doesn't have a real agent runtime listening for
    issue_reply-triggered runs yet, so without this endpoint the chat
    row sits at "Agent picking up…" forever. This isn't gated to admin
    by intent — it's a dev tool that's safe in any environment because
    it only affects rows the user can already see (issue visibility +
    explicit run_id)."""
    from datetime import datetime, timezone

    await _assert_issue_visible(issue_id, auth)

    from decimal import Decimal

    from sqlalchemy import select, update

    from app.db.session import read_scope, write_scope
    from app.models import AgentRuns

    # Confirm the run belongs to this issue and is still running.
    # agent_runs.id is BIGINT (mig 232) → bind int, not str.
    async with read_scope() as session:
        row = (
            (
                await session.execute(
                    select(
                        AgentRuns.id,
                        AgentRuns.status,
                        AgentRuns.issue_id,
                        AgentRuns.started_at,
                        AgentRuns.prompt_tokens,
                        AgentRuns.completion_tokens,
                    )
                    .where(AgentRuns.id == int(run_id))
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
    if not row:
        raise HTTPException(404, "agent_run not found")
    if row.get("issue_id") != issue_id:
        raise HTTPException(400, "run does not belong to this issue")
    if row.get("status") != "running":
        raise HTTPException(
            400, f"run already in status={row.get('status')}; cannot simulate"
        )

    summary = output_summary or (
        "(simulated) 我已经看完上下文，上面这条 reply 涉及 mediahub 的 paperclip-style "
        "心跳模型 + workforce 调度。简短结论：当前实现已经把 4 维 liveness 接通到 chat row，"
        "下一步可以让 workforce 创建 agent_runs 时回填 issue_id。"
    )

    # ended_at is timestamptz → bind a native datetime (asyncpg rejects ISO
    # strings); cost_cents is NUMERIC → bind a Decimal.
    now = datetime.now(timezone.utc)
    try:
        async with write_scope() as session:
            await session.execute(
                update(AgentRuns)
                .where(AgentRuns.id == int(run_id))
                .where(AgentRuns.status == "running")  # CAS guard
                .values(
                    status="completed",
                    ended_at=now,
                    output_summary=summary,
                    cost_cents=Decimal(4),
                    prompt_tokens=int(row.get("prompt_tokens") or 0) + 240,
                    completion_tokens=int(row.get("completion_tokens") or 0) + 180,
                )
            )
    except Exception as exc:
        logger.exception(f"simulate-complete update failed (run_id={run_id}): {exc}")
        raise HTTPException(500, "simulate-complete update failed")

    return {
        "run_id": str(run_id),
        "status": "completed",
        "summary_preview": summary[:80],
    }


__all__ = ["router"]
