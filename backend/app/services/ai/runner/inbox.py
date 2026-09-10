"""Inbox items as the runner sees them: claim at a step boundary, render as
an owned ``<inbox_message>`` frame (spec §1-③).

The claim is wrapped as a DBOS step when a DBOS workflow is on the stack, so
a replayed workflow re-reads the items it already claimed instead of
claiming again; outside a workflow the wrapper is transparent and runs the
function directly.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from app.boundary.frame_markers import escape_frame_attr, escape_frame_prose
from app.repositories.agent_run_inbox_repository import (
    Target,
    get_agent_run_inbox_repository,
)

INBOX_FRAME = "inbox_message"

#: Longest free text an ``inbox_claimed`` payload carries. A transcript row is
#: written once and re-read on every replay, fold and export, so the projection
#: is bounded — the whole body lives in ``agent_run_inbox`` and the UI links to
#: it. 500 is enough for a sub-agent's answer to read as an answer.
CLAIMED_TEXT_MAX = 500


@dataclass(frozen=True)
class InboxItem:
    id: int
    target_kind: str
    target_id: int
    kind: str
    content: dict[str, Any]
    created_at: Optional[dt.datetime]
    user_id: Optional[str] = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "InboxItem":
        return cls(
            id=int(row["id"]),
            target_kind=str(row["target_kind"]),
            target_id=int(row["target_id"]),
            kind=str(row["kind"]),
            content=dict(row.get("content") or {}),
            created_at=row.get("created_at"),
            user_id=str(row["user_id"]) if row.get("user_id") is not None else None,
        )

    def body(self) -> str:
        """Human text of the item: ``content.body`` for steers, the answer
        value for answers, else the whole content as JSON."""
        c = self.content
        if self.kind == "subagent_result":
            # The envelope's other fields (cost, tokens, status) are already
            # in the frame's attributes or of no use to the model — putting
            # the whole JSON here would burn tokens on nothing it can act on.
            return str(c.get("summary") or "")
        if isinstance(c.get("body"), str):
            return c["body"]
        if self.kind == "answer" and "value" in c:
            v = c["value"]
            return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        return json.dumps(c, ensure_ascii=False, default=str)


def _clip(text: Any) -> str:
    """``text`` as a string, bounded by ``CLAIMED_TEXT_MAX``, saying so when it
    was cut. A silently truncated summary reads as a complete short answer."""
    s = "" if text is None else str(text)
    return s if len(s) <= CLAIMED_TEXT_MAX else s[: CLAIMED_TEXT_MAX - 1] + "…"


def claimed_event_content(item: InboxItem) -> dict[str, Any]:
    """The bounded projection of ``item.content`` that rides on the
    ``inbox_claimed`` transcript event.

    NOT ``render_inbox_message`` — that one is what the MODEL reads and is
    priced per turn; this one is what the transcript stores and the trajectory
    folds. Keeping them apart is why the envelope's cost and token counts can
    appear here without appearing in the model's context.

    Until Task 7b the event carried no content at all, and every field the
    frontend fold reads defaulted. Three of the defaults merely lost
    information; ``status`` defaulted to ``completed``, so a FAILED sub-agent
    rendered as ``✓ Done`` (2026-09-10 UI walkthrough, MH-80). Every key is
    therefore always present, with ``None`` for "the envelope did not say" —
    a reader must never have to tell that apart from "this arm forgot to ask".

    ``source`` is passed through WHOLE and only when present: it is a small
    fixed shape (``kind`` / ``schedule_id`` / ``created_by``), it is what the
    wake-up provenance chip reads, and clipping it would corrupt an id.
    """
    c = item.content
    if item.kind == "subagent_result":
        return {
            "child_run_id": c.get("child_run_id"),
            "subagent_type": c.get("subagent_type"),
            "description": c.get("description"),
            "status": c.get("status"),
            "summary": _clip(c.get("summary") or ""),
            "cost_cents": c.get("cost_cents"),
            "tokens_used": c.get("tokens_used"),
        }

    # Every other kind mig 461 allows (steer / answer / pause / resume /
    # budget_reply) is one piece of text plus optional provenance. The text is
    # read from the shapes those producers actually write — ``text`` from the
    # wake-up and comment paths, ``body`` from the older steer shape, ``value``
    # from an answer — and never from a JSON dump of the whole row, which would
    # put bookkeeping like ``dedupe_key`` into the transcript forever.
    text = c.get("text")
    if text is None:
        text = c.get("body")
    if text is None and item.kind == "answer":
        v = c.get("value")
        text = (
            v
            if isinstance(v, str)
            else (json.dumps(v, ensure_ascii=False) if v is not None else None)
        )
    out: dict[str, Any] = {"text": _clip(text)}
    source = c.get("source")
    if isinstance(source, dict):
        out["source"] = source
    return out


def render_inbox_message(item: InboxItem) -> str:
    """The frame the model reads. We own ``<inbox_message>`` (registered in
    OWNED_FRAMES): attributes go through ``escape_frame_attr``, the body
    through ``escape_frame_prose`` so user text can neither close the frame
    nor forge a sibling element inside it."""
    at = item.created_at.isoformat() if item.created_at else ""
    extra = ""
    if item.kind == "subagent_result":
        # Which child this is, so the parent can name it in the next turn or
        # continue it with ``child_run_id``. The slug came from the model's own
        # Task call, so it is escaped like any other attacker-reachable value.
        c = item.content
        extra = (
            f' child_run_id="{escape_frame_attr(str(c.get("child_run_id") or ""))}"'
            f' subagent_type="{escape_frame_attr(str(c.get("subagent_type") or ""))}"'
        )
    return (
        f'<{INBOX_FRAME} kind="{escape_frame_attr(item.kind)}"'
        f' at="{escape_frame_attr(at)}"{extra}>\n'
        f"{escape_frame_prose(item.body())}\n"
        f"</{INBOX_FRAME}>"
    )


async def resolve_targets(
    *, issue_id: Optional[int] = None, conversation_id: Optional[int] = None
) -> list[Target]:
    """Every target a run serves: its issue, its conversation, and — when the
    conversation is an issue's session — that issue too."""
    targets: list[Target] = []
    if issue_id is not None:
        targets.append(("issue", int(issue_id)))
    if conversation_id is not None:
        targets.append(("conversation", int(conversation_id)))
        if issue_id is None:
            linked = await get_agent_run_inbox_repository().issue_id_for_conversation(
                int(conversation_id)
            )
            if linked is not None:
                targets.append(("issue", linked))
    return targets


async def _claim_impl(
    targets: list[Target], run_id: int, turn: int, step: int
) -> list[dict]:
    return await get_agent_run_inbox_repository().claim(
        targets=[tuple(t) for t in targets], run_id=run_id, turn=turn, step=step
    )


try:  # DBOS present (always, in this backend) → a replay-safe step
    from dbos import DBOS

    _claim_step = DBOS.step(name="inbox_claim")(_claim_impl)
except Exception:  # noqa: BLE001 — pragma: no cover (dbos is a hard dependency)
    _claim_step = _claim_impl


async def claim_for_step(
    targets: Sequence[Target], run_id: int, turn: int, step: int
) -> list[InboxItem]:
    rows = await _claim_step(
        [tuple(t) for t in targets], int(run_id), int(turn), int(step)
    )
    return [InboxItem.from_row(r) for r in rows]


__all__ = [
    "INBOX_FRAME",
    "InboxItem",
    "claim_for_step",
    "render_inbox_message",
    "resolve_targets",
]
