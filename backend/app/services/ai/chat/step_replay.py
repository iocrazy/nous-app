"""Recognise a turn that DBOS recovery is running for the second time.

``run_session_turn`` persists the user message before it calls the model. When
the worker dies mid-turn, recovery re-executes the issue turn step and the
same task text used to be appended again (prod conversation 352662635985085:
identical ``Task: …`` rows 20 s apart). The issue turn now stamps the step's
``dbos_step_key`` on the user message it writes; on a re-execution that
message is already in history, so the turn reuses it instead of appending.

History is cut just before that message: it becomes this turn's user message
again, and anything the killed attempt persisted after it belongs to the
superseded run, not to the context the model should see twice.
"""

from __future__ import annotations

from typing import Any, Optional

STEP_KEY_FIELD = "dbos_step_key"


def _step_key_of(message: dict[str, Any]) -> Optional[str]:
    meta = message.get("metadata_json")
    return meta.get(STEP_KEY_FIELD) if isinstance(meta, dict) else None


def split_replayed_turn(
    history: list[dict[str, Any]], step_key: Optional[str]
) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]]]:
    """``(prior_user_message, history_before_it)`` when a user message in
    ``history`` carries ``step_key``; otherwise ``(None, history)``. The
    newest match wins. Returns a new list; ``history`` is not modified."""
    if not step_key:
        return None, history
    for index in range(len(history) - 1, -1, -1):
        message = history[index]
        if message.get("role") == "user" and _step_key_of(message) == step_key:
            return message, list(history[:index])
    return None, history


def user_message_metadata(
    message_source: Optional[dict[str, Any]], step_key: Optional[str]
) -> Optional[dict[str, Any]]:
    """Provenance stored on the turn's user message; None when there is none
    (keeps plain turns byte-for-byte what they were)."""
    meta: dict[str, Any] = {}
    if message_source:
        meta["source"] = message_source
    if step_key:
        meta[STEP_KEY_FIELD] = step_key
    return meta or None


__all__ = ["split_replayed_turn", "user_message_metadata"]
