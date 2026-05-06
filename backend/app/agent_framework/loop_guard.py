"""Tool-call loop guard — detect agent stuck calling same tool repeatedly.

Wave 5c (C1). The AgentRunner has ``max_tool_iterations`` as a hard
ceiling, but it doesn't catch the typical loop pattern where the agent
keeps calling the SAME tool with the SAME args N times in a row,
expecting a different result each time. That burns the budget while
making no progress.

This module is a per-run sliding-window observer:

  guard = ToolCallLoopGuard(repeat_threshold=3, window=5)
  for each tool dispatch:
      guard.observe(tool_name, args_hash)
  before next LLM turn:
      if guard.is_looping():
          inject "stop calling X — try a different approach" hint
          force next turn to be tool-free

Design choices:
  - Per-run state (not per-process) — different runs are independent
  - Sliding window (not total count) — agent can legitimately call
    same tool 5x throughout a session if interleaved with other work
  - Args hash, not raw args — short circuit on identical calls without
    string-comparing potentially huge JSON
  - Guard ONLY warns; doesn't kill the run — false positives (legitimate
    paginated reads) shouldn't abort. The runner's max_tool_iterations
    is the kill switch.
"""
from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass
from typing import Optional


def _hash_call(tool_name: str, args: object) -> str:
    """Stable short hash of (tool_name, args). Args may be string,
    dict, or arbitrary JSON-serializable thing."""
    raw = f"{tool_name}\x00{args!r}".encode("utf-8")
    return hashlib.sha1(raw, usedforsecurity=False).hexdigest()[:16]


@dataclass
class _CallRecord:
    tool_name: str
    args_hash: str


class ToolCallLoopGuard:
    """Sliding-window observer of tool calls. Per-run instance.

    ``repeat_threshold``: how many times the same (tool, args_hash)
        must appear in the last ``window`` calls to count as looping.
    ``window``: how many recent calls to consider.

    Example: threshold=3, window=5 → "same tool+args 3+ times in last 5
    calls" triggers."""

    def __init__(self, *, repeat_threshold: int = 3, window: int = 5) -> None:
        if repeat_threshold < 1:
            raise ValueError("repeat_threshold must be >= 1")
        if window < repeat_threshold:
            raise ValueError("window must be >= repeat_threshold")
        self._threshold = repeat_threshold
        self._window = window
        self._recent: deque[_CallRecord] = deque(maxlen=window)

    def observe(self, tool_name: str, args: object = "") -> None:
        """Record one tool call."""
        self._recent.append(_CallRecord(tool_name, _hash_call(tool_name, args)))

    def is_looping(self) -> bool:
        """True if any (tool, args_hash) appears ``threshold`` times in
        the current window."""
        if len(self._recent) < self._threshold:
            return False
        counts: dict[str, int] = {}
        for rec in self._recent:
            key = rec.tool_name + "|" + rec.args_hash
            counts[key] = counts.get(key, 0) + 1
            if counts[key] >= self._threshold:
                return True
        return False

    def looping_signature(self) -> Optional[tuple[str, str]]:
        """Returns (tool_name, args_hash) of the offending call when
        looping; None otherwise. Useful for the hint message."""
        if len(self._recent) < self._threshold:
            return None
        counts: dict[str, _CallRecord] = {}
        count_n: dict[str, int] = {}
        for rec in self._recent:
            key = rec.tool_name + "|" + rec.args_hash
            counts[key] = rec
            count_n[key] = count_n.get(key, 0) + 1
            if count_n[key] >= self._threshold:
                return (rec.tool_name, rec.args_hash)
        return None

    def reset(self) -> None:
        """Clear the window. Use after the agent acknowledges the loop
        warning — gives it a fresh chance to escape."""
        self._recent.clear()

    def render_warning(self) -> str:
        """Default warning text the runner can inject as a system msg."""
        sig = self.looping_signature()
        if sig is None:
            return ""
        tool_name, _ = sig
        return (
            f"[loop_guard] You have called '{tool_name}' with the same "
            f"arguments {self._threshold}+ times in the last "
            f"{len(self._recent)} tool calls. Stop repeating it — try "
            "a different approach (different args, different tool, or "
            "answer the user directly). The next turn must NOT call "
            f"'{tool_name}' with these arguments again."
        )


__all__ = ["ToolCallLoopGuard"]
