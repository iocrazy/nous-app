"""The one renderer of the compaction-summary system message.

The summary is written by a model reading the user's conversation, so it is
user-controllable text: a literal ``</conversation_summary>`` typed by the
user can be echoed into it. The body therefore goes through
``escape_frame_body`` inside the owned ``<conversation_summary>`` frame.

Two producers must emit the identical message: ``ContextCompactor`` (live
compaction) and ``runner.replay`` (rebuilds it from the persisted
``compaction_summary`` event, which stores the RAW summary text — framing
happens here, exactly once, on both paths).

``SUMMARY_PREFIX`` stays the first line: replay and ``issue_fork`` recognise
a summary message by ``startswith(SUMMARY_PREFIX)``.
"""

from __future__ import annotations

from typing import Final

from app.boundary.frame_markers import escape_frame_body

SUMMARY_PREFIX: Final[str] = "[Earlier conversation summary]\n"


def frame_summary(summary_text: str) -> str:
    """``SUMMARY_PREFIX`` + the escaped summary inside its owned frame.

    Stripped here, not by callers: replay strips the persisted text, so the
    live path must too or a trailing newline breaks byte-identity.
    """
    body = escape_frame_body(str(summary_text or "").strip())
    return f"{SUMMARY_PREFIX}<conversation_summary>\n{body}\n</conversation_summary>"


def render_summary_message(summary_text: str) -> dict[str, str]:
    """The system message that replaces the summarized head."""
    return {"role": "system", "content": frame_summary(summary_text)}


__all__ = ["SUMMARY_PREFIX", "frame_summary", "render_summary_message"]
