"""The one renderer of the ``<system_note>`` frame.

Anthropic's Messages API has no mid-conversation ``system`` role: the only
system slot is the top-level ``system`` parameter. Our message lists do carry
mid-list ``role=system`` messages (the compaction summary, the loop-guard
warning, a forked conversation's persisted summary row), and the Claude
adapter used to drop them silently.

The adapter now delivers each one in place as a user turn whose text is this
frame. Moving it into the top-level ``system`` would lose its position and
invalidate the system-prompt cache prefix on every trip.

The frame is authored here, not in the adapter, because the adapter is a
transport: it reshapes messages other modules wrote and authors no
model-visible text of its own.

Escaping: every untrusted span inside a system message is escaped at its own
render site (``summary_frame`` escapes the summary body, ``loop_guard``
escapes the tool name). Running ``escape_frame_body`` over the whole content
would rewrite the summary's own ``</conversation_summary>`` closer and break
the inner frame, so this wrapper defuses only ``</system_note>``.
"""

from __future__ import annotations

from typing import Final

from app.boundary.frame_markers import escape_frame_close

SYSTEM_NOTE_FRAME: Final[str] = "system_note"


def render_system_note(content: str) -> str:
    """``content`` inside ``<system_note>``; only this frame's closer is defused."""
    body = escape_frame_close(content, SYSTEM_NOTE_FRAME)
    return f"<system_note>\n{body}\n</system_note>"


__all__ = ["SYSTEM_NOTE_FRAME", "render_system_note"]
