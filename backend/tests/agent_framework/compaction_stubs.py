"""Shared stubs for the context-compactor tests.

Why this exists: the compactor tests used to patch ``count_messages_tokens``
with ``side_effect=[a, b, c]`` — a list keyed on CALL ORDER. That encodes how
many times the implementation happens to count, so adding one internal count
(the shrink check, 2026-08-23) broke four unrelated tests with
``StopIteration`` and told you nothing about what actually changed.

Keying the stub on WHAT is being measured survives internal refactors, and a
test that then fails is telling you about behaviour rather than about
bookkeeping.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

SUMMARY_MARKER = "[Earlier conversation summary]"


def token_stub(
    tier_counts: Iterable[int],
    *,
    summary_tokens: int,
    head_tokens: int = 300,
) -> Callable[..., int]:
    """A ``count_messages_tokens`` stand-in.

    ``tier_counts`` feeds the first N calls in order — those are the ones that
    decide which tier the compactor enters, which every test does care about.
    Anything after that is answered by content:

    * a list containing the summary message → ``summary_tokens``
    * anything else (the head being measured) → ``head_tokens``

    Set ``summary_tokens < head_tokens`` for a summary that is accepted, and
    ``>`` for one the shrink check must reject.
    """
    pending = iter(tier_counts)

    def _count(messages: list[dict[str, Any]], model: Any = None) -> int:
        if any(SUMMARY_MARKER in str(m.get("content") or "") for m in messages):
            return summary_tokens
        return next(pending, head_tokens)

    return _count


__all__ = ["SUMMARY_MARKER", "token_stub"]
