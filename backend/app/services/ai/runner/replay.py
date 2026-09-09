"""Phase 2b-1 replay/fork: rebuild the model-visible history from a run's
transcript.

Only what ``build_history_messages`` would show a fresh turn — user /
assistant / system — comes back; ``tool_call`` rows never do (they are not
in cross-turn history either). A ``compaction_summary`` that carries its
``summary`` text replaces everything before it, exactly as the runtime
compaction does; a metrics-only row (emergency cap, or runs recorded before
the text was stamped) has nothing to replace with and leaves the messages
alone — never wipe history on a missing summary. Pure functions; the fork
service (issue_fork) and its tests are the consumers.
"""

from __future__ import annotations

from typing import Any

BOUNDARY_TYPES = ("step_start", "turn_end")
# Mirrors ContextCompactor._compact_with_summary's system message verbatim.
SUMMARY_PREFIX = "[Earlier conversation summary]\n"


def events_upto(events: list[dict[str, Any]], at_seq: int) -> list[dict[str, Any]]:
    """``events[:at_seq]`` by SEQ (inclusive), not by list index — the two
    only agree while seqs are dense from 1, which no caller may assume."""
    return [ev for ev in events if int(ev.get("seq", -1)) <= int(at_seq)]


def messages_from_events(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    """``events`` are ``{seq, event_type, payload}`` rows in seq order."""
    out: list[dict[str, str]] = []
    for ev in events:
        t = ev.get("event_type")
        p = ev.get("payload") or {}
        if t == "compaction_summary":
            summary = str(p.get("summary") or "").strip()
            if summary:
                out = [{"role": "system", "content": SUMMARY_PREFIX + summary}]
            continue
        if t in ("user", "assistant"):
            content = str(p.get("content") or "").strip()
            if content:
                out.append({"role": t, "content": content})
    return out


def is_step_boundary(events: list[dict[str, Any]], at_seq: int) -> bool:
    """A fork point must be a step boundary so the last message is whole."""
    for ev in events:
        if int(ev.get("seq", -1)) == int(at_seq):
            return ev.get("event_type") in BOUNDARY_TYPES
    return False


__all__ = [
    "BOUNDARY_TYPES",
    "SUMMARY_PREFIX",
    "events_upto",
    "is_step_boundary",
    "messages_from_events",
]
