"""The one "target is running → inbox / target is idle → dispatch" decision.

Three trigger paths need it and each used to answer it — or forget to answer
it — on its own: a human comment (``issue_messages_router``), a scheduled
wake-up, and a background sub-agent handing back its result. Every path
returns a typed ``DeliverResult``; a trigger that quietly does nothing is the
failure class CLAUDE.md names silent no-op.

Busy is THREE signals, not one:

* a ROOT run is running on the issue or its session conversation;
* the issue is PAUSED (a wake would start a turn on a paused target);
* a dispatch is in flight (phase 2b-2 §4.1 — the run row does not exist yet,
  so ``running_root_run_id`` still reads idle and a second turn would race
  the one being dispatched).

The two halves are exported separately because the comment path interposes a
step between them: a workflow parked on the needs_input gate consumes the
reply in place, and only when there is no such waiter does the reply start a
fresh turn. ``deliver_or_dispatch`` composes the halves for the paths that
have nothing to interpose.

Everything this module touches is resolved INSIDE the call: the router imports
it, so a module-level import back would be circular, and the worker process
that runs background sub-agents has no business importing FastAPI routers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal, Optional

from loguru import logger

from app.services.issues.issue_dispatch import is_dispatching

#: Statuses on which a background trigger must not start a turn. The COMMENT
#: path opts out (``check_terminal=False``): a comment on a done issue wakes
#: the agent on purpose — see ``comment_trigger``'s module docstring.
TERMINAL_STATUSES = ("done", "cancelled")


@dataclass(frozen=True)
class DeliverResult:
    """What actually happened. ``reason`` is filled on every outcome that is
    not self-evident, so a caller can echo a typed failure rather than shrug."""

    mode: Literal["inbox", "dispatched", "skipped"]
    inbox_id: Optional[int] = None
    workflow_id: Optional[str] = None
    reason: Optional[str] = None


async def deliver_or_dispatch(
    issue_id: int,
    *,
    kind: str,
    content: dict[str, Any],
    user_id: str,
    message_body: Optional[str] = None,
    source: Optional[dict[str, Any]] = None,
    already_enqueued: bool = False,
    dedupe_key: Optional[str] = None,
) -> DeliverResult:
    """Deliver ``content`` to an issue: onto its inbox while the issue is
    busy, or as a fresh agent turn while it is idle.

    ``message_body`` is the human-readable text this delivery carries. On the
    idle branch the ``issue_messages`` mirror row is written BEFORE the
    dispatch, and the body is handed to the turn, which appends it to the
    conversation itself (once) before answering it. ``source`` is its
    provenance, e.g.
    ``{"kind": "schedule", "schedule_id": …, "created_by": "agent"}``; it
    rides down to that single append so the thread the UI reads can tell a
    wake-up from a person typing (Task 7a defect 6).

    This path used to append the body to the conversation here AS WELL, and
    the turn appended it again 189 ms later — two identical "You commented"
    bubbles, and the same sentence twice in the agent's own history (defect
    7). One writer only.

    ``dedupe_key`` makes the delivery idempotent on BOTH arms, for a caller
    whose delivery can be REPLAYED — a DBOS workflow BODY resumed after a
    crash re-runs its own writes, which no step record covers. The inbox arm
    reuses an existing live item with that key; the dispatch arm pins it as
    the workflow id so DBOS collapses the second start. Callers that cannot
    be replayed pass nothing and keep a unique id per dispatch.
    """
    # Read the row and the session ONCE and hand both to the busy half: the
    # composed path would otherwise ask the database the same two questions
    # twice for every background delivery.
    issue, why_not = await _fresh_issue(issue_id, None)
    if issue is None:
        return DeliverResult("skipped", reason=why_not or "issue_missing")
    session_id = await _session_id(issue_id)

    diverted = await _decide(
        issue_id,
        kind=kind,
        content=content,
        user_id=user_id,
        row=issue,
        session_id=session_id,
        already_enqueued=already_enqueued,
        dedupe_key=dedupe_key,
    )
    if diverted.mode != "skipped" or diverted.reason != "idle":
        return diverted

    if message_body:
        await _mirror_to_issue_messages(
            issue_id, user_id=user_id, body=message_body, source=source
        )
    return await dispatch_issue_reply(
        issue_id,
        user_id=_owner_of(issue) or user_id,
        body=message_body or "",
        workflow_id=dedupe_key,
        source=source,
    )


async def divert_to_inbox_if_busy(
    issue_id: int,
    *,
    kind: str,
    content: dict[str, Any],
    user_id: str,
    message_body: Optional[str] = None,
    session_id: Optional[str] = None,
    issue: Optional[dict[str, Any]] = None,
    attachments: Optional[list] = None,
    append_as_user_id: Optional[str] = None,
    paused: Optional[bool] = None,
    check_terminal: bool = True,
    already_enqueued: bool = False,
) -> DeliverResult:
    """The busy half. ``mode='skipped', reason='idle'`` means the caller may
    go ahead and start a turn.

    ``message_body`` is appended to the conversation FIRST so a failed
    enqueue never loses the human's words. A caller that already put the row
    on the inbox — the worker, which enqueues the sub-agent's result and only
    then asks whether to also start a turn — passes ``already_enqueued`` and
    gets the decision without a second row.

    ``issue`` is the caller's row, used only when the fresh re-read fails:
    the marker is written by another process moments earlier, so deciding on
    a row loaded several awaits ago would miss exactly the window it guards.
    """
    row, why_not = await _fresh_issue(issue_id, issue)
    if row is None:
        return DeliverResult("skipped", reason=why_not or "issue_missing")
    if session_id is None:
        session_id = await _session_id(issue_id)
    return await _decide(
        issue_id,
        kind=kind,
        content=content,
        user_id=user_id,
        row=row,
        session_id=session_id,
        message_body=message_body,
        attachments=attachments,
        append_as_user_id=append_as_user_id,
        paused=paused,
        check_terminal=check_terminal,
        already_enqueued=already_enqueued,
    )


async def _decide(
    issue_id: int,
    *,
    kind: str,
    content: dict[str, Any],
    user_id: str,
    row: dict[str, Any],
    session_id: Optional[str],
    message_body: Optional[str] = None,
    attachments: Optional[list] = None,
    append_as_user_id: Optional[str] = None,
    paused: Optional[bool] = None,
    check_terminal: bool = True,
    already_enqueued: bool = False,
    dedupe_key: Optional[str] = None,
) -> DeliverResult:
    """The busy decision on a row and session the caller already read."""
    if check_terminal and (
        row.get("status") in TERMINAL_STATUSES or row.get("hidden_at")
    ):
        return DeliverResult("skipped", reason="issue_terminal")

    why = await _busy_reason(issue_id, row, session_id, paused=paused)
    if why is None:
        return DeliverResult("skipped", reason="idle")
    if already_enqueued:
        return DeliverResult("inbox", reason="already_enqueued")

    if message_body and session_id:
        await _append_to_conversation(
            session_id=session_id,
            user_id=append_as_user_id or user_id,
            body=message_body,
            attachments=attachments,
        )

    from app.repositories.agent_run_inbox_repository import (
        get_agent_run_inbox_repository,
    )

    enqueued = await get_agent_run_inbox_repository().enqueue(
        target_kind="issue",
        target_id=int(issue_id),
        user_id=str(user_id),
        kind=kind,
        content=content,
        dedupe_key=dedupe_key,
    )
    logger.info(f"[deliver] issue {issue_id}: {kind} diverted to inbox ({why})")
    return DeliverResult("inbox", inbox_id=int(enqueued["id"]))


async def dispatch_issue_reply(
    issue_id: int,
    *,
    user_id: str,
    body: str = "",
    attachments: Optional[list] = None,
    workflow_id: Optional[str] = None,
    source: Optional[dict[str, Any]] = None,
) -> DeliverResult:
    """The idle half. A unique workflow id per dispatch by DEFAULT — a fixed
    one would dedup in DBOS and the re-dispatch would become a silent no-op.

    ``workflow_id`` opts into exactly that dedup, for the one caller that
    wants it: a delivery whose caller may replay it (see ``dedupe_key``).

    An EMPTY ``body`` is not dispatchable. ``respond_to_issue_reply`` appends
    its body as the turn's user message, and neither ``_run_reply_turns`` nor
    ``run_issue_reply_step`` has a branch for an empty one — the turn would
    open with a blank user bubble and a blank ``input_summary``. A delivery
    that carries no text (a background sub-agent's result, which the run reads
    off the inbox instead) starts on the same continuation nudge the bounded
    continuation loop uses.

    ``source`` is the body's provenance, forwarded to the workflow so the user
    message the turn appends carries it (Task 7a defect 6). Nothing here reads
    it — this is a pass-through, and the value's shape is owned by the
    delivery caller.
    """
    from app.services.issues.issue_agent_executor import CONTINUATION_NUDGE
    from app.services.issues.issue_reply_dispatch import (
        dispatch_respond_to_issue_reply,
    )

    wf_id = workflow_id or f"issue-reply-{issue_id}-{uuid.uuid4()}"
    try:
        dispatch_respond_to_issue_reply(
            issue_id, user_id, body or CONTINUATION_NUDGE, attachments, wf_id, source
        )
    except Exception as exc:  # noqa: BLE001 — typed failure, never silent
        logger.opt(exception=True).error(
            f"[deliver] issue {issue_id}: dispatch failed: {exc}"
        )
        return DeliverResult("skipped", reason=f"dispatch_failed: {exc!s:.120}")
    return DeliverResult("dispatched", workflow_id=wf_id)


# ── the three busy signals ───────────────────────────────────────────────


async def _busy_reason(
    issue_id: int,
    issue: dict[str, Any],
    session_id: Optional[str],
    *,
    paused: Optional[bool] = None,
) -> Optional[str]:
    from app.repositories.agent_runs_repository import get_agent_runs_repository

    running = await get_agent_runs_repository().running_root_run_id(
        issue_id=issue_id,
        conversation_id=int(session_id) if session_id else None,
    )
    if running is not None:
        return f"run {running}"
    if paused if paused is not None else bool(issue.get("paused_at")):
        return "issue paused"
    if is_dispatching(issue):
        return "dispatch in flight"
    return None


# ── writes ───────────────────────────────────────────────────────────────


async def _append_to_conversation(
    *, session_id: str, user_id: str, body: str, attachments: Optional[list]
) -> None:
    from app.services.ai.chat.conversations_ai_store import ConversationsAiStore

    await ConversationsAiStore().append_user_message(
        session_id=int(session_id),
        user_id=str(user_id),
        content=body,
        attachments=ConversationsAiStore.display_attachments(attachments),
    )


async def _mirror_to_issue_messages(
    issue_id: int,
    *,
    user_id: str,
    body: str,
    source: Optional[dict[str, Any]],
) -> None:
    """Record ``body`` in ``issue_messages`` — the mirror, not the delivery.

    It does NOT append to the conversation. The turn this delivery is about to
    start appends the user message itself, and doing it here as well is how
    the same sentence landed in the thread twice (Task 7a defect 7). The
    provenance goes onto BOTH copies: this row's ``meta.source`` for the
    legacy read path and the audit trail, and the conversation message's own
    ``metadata_json.source`` (written by the turn) for the path the UI
    actually reads.

    ``issue_messages`` has NO ``author_kind`` column — mig 205 gave it only
    ``kind`` / ``author_user_id`` / ``author_agent_id`` / ``body`` / ``meta``.
    Provenance therefore lives at ``meta.source``, and the key is absent — not
    null — when there is nothing to say.
    """
    await _insert_issue_message(
        issue_id=issue_id,
        kind="comment",
        author_user_id=str(user_id),
        body=body,
        meta={"source": source} if source else {},
    )


async def _insert_issue_message(
    *,
    issue_id: int,
    kind: str,
    author_user_id: str,
    body: str,
    meta: dict[str, Any],
) -> None:
    """Best-effort: the thread row is a record, not the delivery. Losing it
    must not stop the turn the caller is about to start — but it is logged
    with its stack, never swallowed."""
    from sqlalchemy import insert

    from app.db.session import write_scope
    from app.models import IssueMessages

    try:
        async with write_scope() as session:
            await session.execute(
                insert(IssueMessages).values(
                    issue_id=int(issue_id),
                    kind=kind,
                    author_user_id=author_user_id,
                    body=body,
                    meta=meta,
                )
            )
    except Exception as exc:  # noqa: BLE001 — logged, not swallowed
        logger.opt(exception=True).error(
            f"[deliver] issue {issue_id}: thread row insert failed: {exc}"
        )


# ── reads ────────────────────────────────────────────────────────────────


async def _fresh_issue(
    issue_id: int, fallback: Optional[dict[str, Any]]
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """``(row, failure_reason)``. Re-read so the decision sees current
    ``execution_state``.

    THREE outcomes, and the last two must not be conflated: the row came back;
    the row is genuinely gone (``issue_missing``); or the read itself failed
    (``issue_unreadable``) and there is no caller row to fall back to. A probe
    that cannot reach its target has not proved the target is absent — the
    same mistake ``deploy-frontend.yml`` made for weeks. A read that fails
    WITH a caller row degrades to that row (stale, not wrong) and warns.
    """
    from app.repositories.issue_repository import issue_repository

    try:
        row = await issue_repository.get_by_id(int(issue_id))
    except Exception as exc:  # noqa: BLE001 — a sharpening, not a dependency
        if fallback is not None:
            logger.warning(
                f"[deliver] issue {issue_id}: could not re-read before the "
                f"decision ({exc!r}); deciding on the caller's row"
            )
            return (fallback, None)
        logger.error(
            f"[deliver] issue {issue_id}: read failed and no caller row to "
            f"fall back to ({exc!r}); the delivery is not attempted"
        )
        return (None, "issue_unreadable")
    if row:
        return (row, None)
    return (fallback, None) if fallback is not None else (None, "issue_missing")


async def _session_id(issue_id: int) -> Optional[str]:
    from app.services.issues.issue_session import get_or_create_issue_session

    try:
        return await get_or_create_issue_session(int(issue_id))
    except Exception as exc:  # noqa: BLE001 — a missing session is not a
        # reason to drop the delivery; the inbox row is keyed on the issue.
        logger.warning(f"[deliver] issue {issue_id}: session lookup failed: {exc!r}")
        return None


def _owner_of(issue: dict[str, Any]) -> Optional[str]:
    """The turn runs as the issue OWNER (BYO-key / adapter context), mirroring
    ``get_or_create_issue_session``."""
    owner = issue.get("created_by_user_id") or issue.get("assignee_user_id")
    return str(owner) if owner else None


__all__ = [
    "DeliverResult",
    "TERMINAL_STATUSES",
    "deliver_or_dispatch",
    "dispatch_issue_reply",
    "divert_to_inbox_if_busy",
]
