"""fh5 A2: what history a chat turn starts from, and where its summary goes.

Before A2 every turn re-read the newest 200 ``messages`` rows and, past the
compaction threshold, re-summarized the same head on EVERY turn: one extra
model call per turn, and a model-visible prefix that changed each time (so no
provider prefix cache could ever hold). Now an accepted summary is stored in
``conversation_memory`` (``summary_md`` = raw text, ``last_seq_summarized`` =
the largest ``messages.seq`` it covers) and later turns start from:

    [render_summary_message(summary)] + rows with seq > watermark + new user

Three pieces, shared by ``AILibraryChatService._run_session_turn_inner`` and
``tests/benchmarks/test_long_session_continuation.py`` so the benchmark
measures what production runs:

* ``load_turn_history`` — summary + rows after its watermark for a
  ``direct_agent`` conversation with a stored row; the newest-200 window
  otherwise (no row yet, a group conversation, a store without the seam, or a
  sidecar read failure — the turn never fails on the sidecar).
* ``assemble_turn_messages`` — the model list plus a parallel ``message_seqs``
  (watermark for the frame, ``row.seq`` for rows, ``None`` for the new user
  message). The runner hands the seqs to the compactor so an accepted summary
  can name what it covers.
* ``persist_compaction_summary`` — best-effort upsert of an accepted summary.
  The repository's monotonic guard (``last_seq_summarized < excluded``) makes
  a DBOS recovery re-execution a no-op.

A new summary SUPERSEDES the stored one: the carried frame sits at index 0 of
the head the compactor summarizes, so the summarizer merges it — one row per
conversation, never a chain. None of these functions is a DBOS step; they run
inside whatever step the turn runs in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

from app.agent_framework.context_compactor import CarriedSummary
from app.boundary.summary_frame import render_summary_message
from app.repositories.conversation_memory_repository import (
    get_conversation_memory_repository,
)

#: The newest-window size the turn path always used (``get_messages`` default).
HISTORY_LIMIT = 200
#: The only conversation type whose turns read / write the 1:1 summary. Group
#: conversations keep their own rolling summary in the same table
#: (``services/chat/conversation_memory_service``); the two never share a row.
MEMORY_CONVERSATION_TYPE = "direct_agent"
_HISTORY_ROLES = ("user", "assistant", "system")


@dataclass(frozen=True)
class TurnHistory:
    """Rows (legacy message shape, seq ASC) plus the summary they follow."""

    rows: list[dict[str, Any]]
    carried: Optional[CarriedSummary]
    # True when this turn may write conversation_memory (a direct_agent
    # conversation on the conversations store).
    memory_eligible: bool


@dataclass(frozen=True)
class AssembledTurn:
    messages: list[dict[str, Any]]
    # 1:1 with ``messages``; None when the rows could not be aligned.
    message_seqs: Optional[list[Optional[int]]]


async def _newest(store: Any, session_id: Any) -> list[dict[str, Any]]:
    return await store.get_messages(
        session_id=session_id, limit=HISTORY_LIMIT, newest=True
    )


async def _stored_summary(session_id: Any) -> Optional[CarriedSummary]:
    """The conversation's stored summary; None when absent, blank or unreadable."""
    try:
        row = await get_conversation_memory_repository().load(int(session_id))
    except Exception as exc:  # noqa: BLE001 — the sidecar never fails a turn
        logger.error(
            f"[turn_history] conversation_memory read failed for {session_id}; "
            f"using the newest-{HISTORY_LIMIT} window: {exc!r}"
        )
        return None
    if not row or not str(row.get("summary_md") or "").strip():
        return None
    return CarriedSummary(
        text=str(row["summary_md"]),
        covers_up_to_seq=int(row["last_seq_summarized"]),
    )


async def load_turn_history(store: Any, session_id: Any) -> TurnHistory:
    """History for one turn. Never raises on the sidecar; a failed ``messages``
    read propagates exactly as the plain window read always did."""
    if getattr(store, "store_kind", None) != "conversations" or not hasattr(
        store, "get_messages_after"
    ):
        return TurnHistory(await _newest(store, session_id), None, False)
    ctype = await store.get_conversation_type(session_id=session_id)
    if ctype != MEMORY_CONVERSATION_TYPE:
        return TurnHistory(await _newest(store, session_id), None, False)
    carried = await _stored_summary(session_id)
    if carried is None:
        return TurnHistory(await _newest(store, session_id), None, True)
    rows = await store.get_messages_after(
        session_id=session_id,
        after_seq=carried.covers_up_to_seq,
        limit=HISTORY_LIMIT,
    )
    if (
        len(rows) >= HISTORY_LIMIT
        and rows[0].get("seq") != carried.covers_up_to_seq + 1
    ):
        # The compactor normally fires long before this; if it did not, the
        # rows between the watermark and this window reach the model as
        # neither summary nor text. Visible here rather than silent.
        logger.warning(
            f"[turn_history] conversation {session_id}: rows "
            f"{carried.covers_up_to_seq + 1}..{rows[0].get('seq')} are outside "
            f"both the stored summary and the newest-{HISTORY_LIMIT} window"
        )
    return TurnHistory(rows, carried, True)


def _row_seqs(
    rows: list[dict[str, Any]], history_messages: list[dict[str, Any]]
) -> Optional[list[Optional[int]]]:
    """Seqs of the rows ``build_history_messages`` kept (it keeps exactly the
    user / assistant / system rows, in order); None when they disagree."""
    kept = [r for r in rows if r.get("role") in _HISTORY_ROLES]
    if len(kept) != len(history_messages):
        logger.warning(
            f"[turn_history] {len(kept)} history rows vs {len(history_messages)} "
            "history messages; no watermark can be reported this turn"
        )
        return None
    return [r.get("seq") for r in kept]


def assemble_turn_messages(
    *,
    carried: Optional[CarriedSummary],
    rows: list[dict[str, Any]],
    history_messages: list[dict[str, Any]],
    new_user_message: dict[str, Any],
) -> AssembledTurn:
    """``[frame] + history_messages + [new_user_message]`` and its seqs."""
    head = [render_summary_message(carried.text)] if carried else []
    messages = head + list(history_messages) + [new_user_message]
    row_seqs = _row_seqs(rows, history_messages)
    if row_seqs is None:
        return AssembledTurn(messages, None)
    head_seqs: list[Optional[int]] = [carried.covers_up_to_seq] if carried else []
    return AssembledTurn(messages, head_seqs + row_seqs + [None])


async def persist_compaction_summary(
    stats: Any,
    *,
    conversation_id: Any,
    memory_eligible: bool,
    model: Optional[str],
) -> bool:
    """Store an accepted summary; True when the upsert ran.

    Nothing to store when the turn is not eligible, no summary was accepted
    (green / yellow / the emergency cap — I9), or the summary names no
    watermark. Failures are logged and swallowed: the turn already happened,
    and the next orange turn simply summarizes again.
    """
    text = getattr(stats, "summary_text", None)
    covers = getattr(stats, "covers_up_to_seq", None)
    if not memory_eligible or not text or covers is None:
        return False
    # Only the warm path runs on the conversation's own model; the legacy
    # path ran on the maintenance model, which this layer cannot name.
    recorded_model = model if getattr(stats, "summary_path", None) == "warm" else None
    try:
        await get_conversation_memory_repository().upsert(
            conversation_id=int(conversation_id),
            summary_md=text,
            last_seq_summarized=int(covers),
            model=recorded_model,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, never fails a turn
        logger.error(
            f"[turn_history] could not store the summary for conversation "
            f"{conversation_id} (covers seq {covers}): {exc!r}"
        )
        return False
    return True


__all__ = [
    "AssembledTurn",
    "HISTORY_LIMIT",
    "MEMORY_CONVERSATION_TYPE",
    "TurnHistory",
    "assemble_turn_messages",
    "load_turn_history",
    "persist_compaction_summary",
]
