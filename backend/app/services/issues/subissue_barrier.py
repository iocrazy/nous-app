"""Sub-issue completion barrier — a MediaHub port of multica's fan-in wake.

When every sub-issue of a parent reaches a terminal status, the parent is
"unblocked": we (a) write ONE roll-up report into the parent's timeline and
(b) — if the parent is an active agent-assigned issue — wake that agent to
continue. This is the fan-in half of a fan-out/fan-in delegation pattern.

Behaviour contract (v2)
-----------------------
1. Trigger: evaluated on a child's non-terminal → terminal edge only
   (terminal = {done, cancelled}). A repeat-save landing the SAME status never
   fires — the prev/new comparison is the first de-dup gate.
2. Barrier: load the parent and ALL siblings; the barrier closes only when
   EVERY sibling is terminal. While it is open we stay COMPLETELY SILENT — no
   per-child chatter (multica's hard-won lesson: fan-in noise drowns the signal).
3. On close, two actions:
   a. Report — one system roll-up into the parent timeline: each child's
      identifier + title + final status (cancelled is labelled truthfully) +
      an excerpt of that child's last substantive message (<=500 chars/child,
      <=4000 total).
   b. Wake — only when the parent has an ``assignee_agent_id`` AND its status is
      neither terminal nor ``backlog``. Any guard failing means report-only
      (a human-assigned or parked parent reads the timeline themselves).
4. Idempotency: the wake is dispatched under a pinned DBOS workflow id
   ``subwake:{parent_id}:{n_children}`` — two children closing the barrier in a
   race compute the SAME id, and DBOS de-dups by workflow id. The report-only
   paths stamp the same key into message meta and skip if already present.
5. Best-effort: the whole hook is wrapped so any failure is logged and never
   blocks the child's own status flow.

Two call sites, because status is written on two disjoint paths (both must be
covered — there is no single choke point):
  * ``IssueRepository.transition_status`` — the router /transition endpoint and
    the project-stage close both go through it (repo post-callback).
  * ``issue_lifecycle.execute_issue`` — the agent workflow lands terminal via a
    raw ``set_status`` @DBOS.step (direct SQL, bypassing the repo), so the
    workflow BODY fires the hook after routing. It must be the body, never the
    step: dispatching a workflow inside a @DBOS.step raises a bare
    AssertionError (see bug_retry_failed_downloads_two_layer).

Testability: all I/O is funnelled through a ``BarrierGateway`` so the decision
logic can be unit-tested with a fake (mirrors the injected-deps style of
``issue_lifecycle._run_dispatch_with_continuation`` and the stage-issue hook).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

# Statuses that count as "this sub-issue is finished" for the barrier.
TERMINAL_STATUSES = frozenset({"done", "cancelled"})

# Excerpt caps for the roll-up report (per-child and grand total).
CHILD_EXCERPT_CAP = 500
TOTAL_REPORT_CAP = 4000


def is_terminal(status: Optional[str]) -> bool:
    return status in TERMINAL_STATUSES


def barrier_key(parent_id: int, n_children: int) -> str:
    """Pinned DBOS workflow id for the wake — the whole idempotency substrate.

    Keyed on ``n_children`` (not a nonce) so a racing double-close computes an
    identical id (DBOS de-dups) AND a reopen→re-complete of the same sibling set
    recomputes the same id, absorbing the second wake."""
    return f"subwake:{parent_id}:{n_children}"


def should_wake(parent_row: Dict[str, Any]) -> bool:
    """Wake guards: parent has an agent, and is neither terminal nor backlog."""
    if not parent_row.get("assignee_agent_id"):
        return False
    status = parent_row.get("status")
    if is_terminal(status):
        return False
    if status == "backlog":
        return False
    return True


def _truncate(text: str, cap: int) -> str:
    text = (text or "").strip()
    if len(text) <= cap:
        return text
    # Reserve room for the ellipsis marker so the cap is a hard ceiling.
    return text[: max(0, cap - 1)].rstrip() + "…"


def build_report_body(children: List[Dict[str, Any]]) -> str:
    """Render the roll-up. ``children`` items carry identifier / title / status /
    last_message. Per-child excerpt capped at CHILD_EXCERPT_CAP; the whole body
    capped at TOTAL_REPORT_CAP (never mid-splitting a child block below its
    header line)."""
    header = "All sub-issues are complete — here is the roll-up:"
    lines: List[str] = [header]
    used = len(header)
    for child in children:
        ident = str(child.get("identifier") or child.get("id") or "?")
        title = (child.get("title") or "").strip()
        status = child.get("status") or "?"
        last = _truncate(child.get("last_message") or "", CHILD_EXCERPT_CAP)
        head = f"\n• {ident} — {title} [{status}]"
        block = head + (f"\n    {last}" if last else "")
        if used + len(block) > TOTAL_REPORT_CAP:
            # Stop cleanly at a child boundary rather than emit a torn block.
            lines.append("\n• … (more sub-issues omitted)")
            break
        lines.append(block)
        used += len(block)
    return "".join(lines)


class BarrierGateway:
    """All external I/O the barrier needs, behind one seam for testing.

    The default implementation binds to the real repositories / stores / DBOS
    dispatch; unit tests pass a fake with the same async surface.
    """

    async def get_issue(self, issue_id: int) -> Optional[Dict[str, Any]]:
        from app.repositories.issue_repository import issue_repository

        return await issue_repository.get_by_id(int(issue_id))

    async def list_children(self, parent_id: int) -> List[Dict[str, Any]]:
        from app.repositories.issue_repository import issue_repository

        return await issue_repository.list_children(int(parent_id))

    async def last_substantive_message(self, issue_row: Dict[str, Any]) -> str:
        """Last message with real text for a sub-issue — dual read path.

        Session path (child has ai_session_id): read the conversation via
        ConversationsAiStore. Legacy path (no session): read issue_messages.
        Either way, return the newest non-empty body/content, or ''."""
        session_id = issue_row.get("ai_session_id")
        if session_id:
            from app.services.ai.chat.conversations_ai_store import (
                ConversationsAiStore,
            )

            rows = await ConversationsAiStore().get_messages(
                session_id=int(session_id), limit=200
            )
            for row in reversed(rows):
                content = (row.get("content") or "").strip()
                if content:
                    return content
            return ""

        # Legacy path: issue_messages table (issues predating any session).
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import IssueMessages

        async with read_scope() as session:
            objs = (
                (
                    await session.execute(
                        select(IssueMessages)
                        .where(IssueMessages.issue_id == int(issue_row["id"]))
                        .order_by(IssueMessages.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
        for obj in objs:
            body = (getattr(obj, "body", None) or "").strip()
            if body:
                return body
        return ""

    async def already_reported(self, parent_row: Dict[str, Any], key: str) -> bool:
        """Has a report for this exact barrier key already been written? Guards
        the report-only paths against a double-close race / workflow replay.

        Only the report-only writers stamp ``meta.barrier_key``; the wake path
        relies on DBOS's own pinned-id de-dup instead, so it is not checked
        here."""
        session_id = parent_row.get("ai_session_id")
        if session_id:
            from app.services.ai.chat.conversations_ai_store import (
                ConversationsAiStore,
            )

            rows = await ConversationsAiStore().get_messages(
                session_id=int(session_id), limit=200
            )
            return any(
                (r.get("metadata_json") or {}).get("barrier_key") == key for r in rows
            )

        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import IssueMessages

        async with read_scope() as session:
            objs = (
                (
                    await session.execute(
                        select(IssueMessages).where(
                            IssueMessages.issue_id == int(parent_row["id"])
                        )
                    )
                )
                .scalars()
                .all()
            )
        return any(
            (getattr(o, "meta", None) or {}).get("barrier_key") == key for o in objs
        )

    async def ensure_session(self, issue_id: int) -> Optional[str]:
        from app.services.issues.issue_session import get_or_create_issue_session

        return await get_or_create_issue_session(int(issue_id))

    async def write_report_to_session(
        self, *, session_id: int, owner_id: str, body: str, key: str
    ) -> None:
        """Report-only into an agent parent's session (barrier closed but the
        wake guards did not pass). Stored as a user-role message so the agent
        reads it as context on its next wake, carrying the barrier_key marker."""
        from app.repositories.conversation_repository import (
            get_conversation_repository,
        )

        await get_conversation_repository().send_message(
            conversation_id=int(session_id),
            sender_id=owner_id,
            sender_type="user",
            type="text",
            body={
                "text": body,
                "meta": {"barrier_key": key, "subissue_barrier": True},
            },
            parent_id=None,
        )

    async def write_report_to_issue_messages(
        self, *, issue_id: int, owner_id: Optional[str], body: str, key: str
    ) -> None:
        """Report-only into a human (no-agent) parent's timeline.

        Rendered as a ``comment`` (frontend renders its body verbatim with no
        change needed). The author-required CHECK on issue_messages forbids a
        comment with both author ids null, so we author it as the parent's
        owner — the human who owns and is reading this issue."""
        from sqlalchemy import insert

        from app.db.session import write_scope
        from app.models import IssueMessages

        async with write_scope() as session:
            await session.execute(
                insert(IssueMessages).values(
                    {
                        "issue_id": int(issue_id),
                        "kind": "comment",
                        "author_user_id": owner_id,
                        "body": body,
                        "meta": {"barrier_key": key, "subissue_barrier": True},
                    }
                )
            )

    async def dbos_enabled(self) -> bool:
        from app.services.infra import dbos_orchestrator

        return dbos_orchestrator.is_enabled()

    async def dispatch_wake(
        self, *, parent_id: int, owner_id: str, body: str, key: str
    ) -> None:
        """Wake the parent agent to continue, feeding it the roll-up as the reply
        text. Reuses the issue_messages_router pinned-id dispatch helper: the
        respond_to_issue_reply workflow appends ``body`` to the session AND runs
        one agent turn, so the wake carries the report — no separate write. The
        pinned id (``key``) makes a double-close idempotent at the DBOS layer."""
        from app.services.issues.issue_reply_dispatch import (
            dispatch_respond_to_issue_reply,
        )

        dispatch_respond_to_issue_reply(int(parent_id), str(owner_id), body, None, key)


async def on_child_issue_terminal(
    child_id: int,
    prev_status: Optional[str],
    new_status: str,
    *,
    gateway: Optional[BarrierGateway] = None,
) -> Dict[str, Any]:
    """Evaluate the barrier for one child's status transition. Best-effort:
    always returns a result dict describing the outcome, never raises."""
    gw = gateway or BarrierGateway()
    try:
        return await _evaluate(child_id, prev_status, new_status, gw)
    except Exception as exc:  # noqa: BLE001 — the child transition is primary
        logger.warning(
            f"[subissue_barrier] evaluation failed for child {child_id} "
            f"({prev_status}→{new_status}): {exc!r}"
        )
        return {"fired": False, "reason": "error", "error": repr(exc)}


async def _evaluate(
    child_id: int,
    prev_status: Optional[str],
    new_status: str,
    gw: BarrierGateway,
) -> Dict[str, Any]:
    # ── Gate 1: real non-terminal → terminal edge ────────────────────────────
    if not is_terminal(new_status):
        return {"fired": False, "reason": "new_not_terminal"}
    if is_terminal(prev_status):
        # Repeat-save / edit that kept a terminal status — never re-fire.
        return {"fired": False, "reason": "prev_already_terminal"}

    # ── Gate 2: the child must belong to a parent ────────────────────────────
    child = await gw.get_issue(child_id)
    if not child:
        return {"fired": False, "reason": "child_missing"}
    parent_id = child.get("parent_id")
    if not parent_id:
        return {"fired": False, "reason": "no_parent"}
    parent_id = int(parent_id)

    # ── Gate 3: barrier — ALL siblings terminal, else stay silent ────────────
    siblings = await gw.list_children(parent_id)
    if not siblings:
        return {"fired": False, "reason": "no_siblings"}
    if not all(is_terminal(s.get("status")) for s in siblings):
        return {"fired": False, "reason": "barrier_open"}  # SILENT — no chatter

    n_children = len(siblings)
    key = barrier_key(parent_id, n_children)

    parent = await gw.get_issue(parent_id)
    if not parent:
        return {"fired": False, "reason": "parent_missing"}

    # ── Build the roll-up (each sibling's last substantive message) ──────────
    entries: List[Dict[str, Any]] = []
    for sib in sorted(siblings, key=lambda s: str(s.get("identifier") or s.get("id"))):
        last = await gw.last_substantive_message(sib)
        entries.append(
            {
                "identifier": sib.get("identifier"),
                "id": sib.get("id"),
                "title": sib.get("title"),
                "status": sib.get("status"),
                "last_message": last,
            }
        )
    body = build_report_body(entries)

    owner_id = parent.get("created_by_user_id") or parent.get("assignee_user_id")
    has_agent = bool(parent.get("assignee_agent_id"))

    # ── Action: wake (report rides along) or report-only ─────────────────────
    if should_wake(parent) and await gw.dbos_enabled():
        # The wake workflow appends the roll-up itself; pinned id de-dups it.
        await gw.dispatch_wake(
            parent_id=parent_id, owner_id=str(owner_id), body=body, key=key
        )
        return {
            "fired": True,
            "reason": "waked",
            "wake": True,
            "barrier_key": key,
            "n_children": n_children,
        }

    # Report-only — de-dup against a racing close / replay via the marker.
    if await gw.already_reported(parent, key):
        return {"fired": False, "reason": "already_reported", "barrier_key": key}

    if has_agent:
        # Agent parent whose wake guards failed (terminal / backlog): the GET
        # path reads the session, so the report MUST go into the session (never
        # issue_messages — that row would flash then vanish, the #1405 trap).
        session_id = await gw.ensure_session(parent_id)
        if session_id:
            await gw.write_report_to_session(
                session_id=int(session_id),
                owner_id=str(owner_id),
                body=body,
                key=key,
            )
            return {
                "fired": True,
                "reason": "reported_session_no_wake",
                "wake": False,
                "barrier_key": key,
                "n_children": n_children,
            }

    # No agent (or no resolvable session): write into issue_messages, which is
    # exactly where the GET falls back to when the parent has no session.
    await gw.write_report_to_issue_messages(
        issue_id=parent_id,
        owner_id=str(owner_id) if owner_id else None,
        body=body,
        key=key,
    )
    return {
        "fired": True,
        "reason": "reported_issue_messages",
        "wake": False,
        "barrier_key": key,
        "n_children": n_children,
    }


__all__ = [
    "on_child_issue_terminal",
    "BarrierGateway",
    "barrier_key",
    "build_report_body",
    "should_wake",
    "is_terminal",
    "TERMINAL_STATUSES",
    "CHILD_EXCERPT_CAP",
    "TOTAL_REPORT_CAP",
]
