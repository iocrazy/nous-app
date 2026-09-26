"""fh5 A2: the preflight's half of summary persistence.

A turn may start from a summary stored in ``conversation_memory`` (the chat
service puts ``render_summary_message(text)`` at index 0 and hands the runner
a ``CarriedSummary``). Two things happen here, both after the compactor ran:

* ``expose_compaction`` puts the compactor's stats on the recorder
  (``recorder.last_compaction``) so the chat service can persist an accepted
  summary. ``RunRecorder.note_compaction`` only runs when tokens were saved,
  so the stats cannot ride that call.
* ``report_carried_summary`` writes ``compaction_summary{path:"stored"}``
  when no fresh summary was accepted but the model still receives the carried
  frame. It lands BEFORE the ``user`` event (the preflight runs first), with
  no start/end bracket — the Runs counter counts ``compaction_end`` only, and
  nothing was compacted this turn. Replay and fork rebuild the head from this
  event exactly as they do from a fresh one.

Plain functions, not DBOS steps: they run inside whatever step the turn runs
in (``tests/services/ai/chat/test_turn_history.py`` pins that).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.agent_framework.context_compactor import CarriedSummary, CompactionStats
from app.agent_framework.tokenizer import count_messages_tokens
from app.boundary.summary_frame import render_summary_message
from app.services.ai.runner.events import emit

logger = logging.getLogger(__name__)

STORED_PATH = "stored"


def expose_compaction(recorder: Any, stats: Optional[CompactionStats]) -> None:
    """``recorder.last_compaction = stats`` on recorders that declare it."""
    if recorder is None or stats is None or not hasattr(recorder, "last_compaction"):
        return
    try:
        recorder.last_compaction = stats
    except Exception as exc:  # noqa: BLE001 — telemetry never fails a turn
        logger.warning("[stored_summary] could not expose compaction stats: %r", exc)


async def report_carried_summary(
    recorder: Any,
    carried: Optional[CarriedSummary],
    user_messages: list[dict],
    stats: Optional[CompactionStats],
    model: str,
) -> bool:
    """Emit the ``stored`` event; True when it was emitted.

    Skipped when there is nothing carried, when a fresh summary replaced it
    (that turn already has its own ``compaction_summary``), or when the list
    no longer opens on the exact carried frame — the emergency cap may have
    truncated it, and claiming the full text would make replay rebuild a
    history the model never saw.
    """
    if carried is None or recorder is None:
        return False
    if stats is not None and stats.summary_text:
        return False
    frame = render_summary_message(carried.text)
    if not user_messages or user_messages[0] != frame:
        return False
    payload = {
        "path": STORED_PATH,
        "summary": carried.text,
        "covers_up_to_seq": int(carried.covers_up_to_seq),
        "summary_tokens": count_messages_tokens([frame], model),
    }
    return await emit(recorder, "compaction_summary", payload)


__all__ = ["STORED_PATH", "expose_compaction", "report_carried_summary"]
