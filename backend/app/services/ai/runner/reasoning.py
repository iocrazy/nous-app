"""Strip Qwen3-style chain-of-thought from LLM output.

Qwen3 *thinking* models (e.g. Qwen3-30B-A3B, served as ``qwen3-6-35b`` on the
self-hosted nous-center box) emit their reasoning then the answer in ONE
``content`` string. The chat template consumes the opening ``<think>`` token, so
the wire format is::

    <reasoning prose ...></think>
    <the actual answer>

There is no separate ``reasoning_content`` field on this serving, so we split on
``</think>`` and keep only the answer. Non-thinking models never emit
``</think>``, so the helpers are no-ops for them.

Two entry points:
  - ``strip_reasoning(text)`` — buffered (whole-response) case.
  - ``ReasoningStreamFilter`` — streaming case (suppress deltas until the
    thinking block closes, then forward the answer).

``MIN_REASONING_MAX_TOKENS`` is a floor the adapter applies for reasoning
models: the thinking block alone can be 1-2k tokens, so a small ``max_tokens``
would truncate mid-thought and the response would contain NO answer (worse than
the raw thinking). The floor guarantees room for thinking + answer.
"""

from __future__ import annotations

from typing import Optional

THINK_CLOSE = "</think>"
MIN_REASONING_MAX_TOKENS = 2048


def model_uses_reasoning(model: Optional[str]) -> bool:
    """True for model names that stream a Qwen3-style thinking block.

    Kept deliberately narrow (currently the qwen3 family) so non-thinking
    models keep streaming token-by-token. Extend as new reasoning models land.
    """
    if not model:
        return False
    return "qwen3" in model.lower()


def strip_reasoning(text: Optional[str]) -> str:
    """Return only the answer that follows the final ``</think>``.

    No ``</think>`` present → return ``text`` unchanged (covers non-thinking
    models AND the truncated case where thinking never closed, so the caller
    still surfaces *something* rather than an empty string).
    """
    if not text:
        return text or ""
    idx = text.rfind(THINK_CLOSE)
    if idx == -1:
        return text
    return text[idx + len(THINK_CLOSE) :].lstrip("\n").lstrip()


class ReasoningStreamFilter:
    """Stateful per-stream filter that suppresses a leading thinking block.

    Feed each delta; receive the text to forward downstream (or ``None`` while
    still inside the thinking block). When the stream ends, call ``flush()`` to
    surface any buffered remainder — this only happens when the block never
    closed (truncation), where showing the partial thought beats showing
    nothing.

    ``enabled=False`` makes it a transparent pass-through (used for
    non-reasoning models so live streaming is unaffected).
    """

    def __init__(self, enabled: bool) -> None:
        self._open = enabled  # inside (suppressing) the thinking block
        self._strip_lead = enabled  # trim whitespace before the first answer char
        self._emitted_any = False
        self._buf = ""

    def feed(self, delta: Optional[str]) -> Optional[str]:
        if not self._open:
            # Disabled filter → pure pass-through. Enabled + past the thinking
            # block → forward the answer (leading whitespace trimmed once).
            if not self._strip_lead:
                return delta
            return self._lead(delta)
        if not delta:
            return None
        self._buf += delta
        idx = self._buf.find(THINK_CLOSE)
        if idx == -1:
            return None  # still inside the thinking block — emit nothing yet
        after = self._buf[idx + len(THINK_CLOSE) :]
        self._buf = ""
        self._open = False
        return self._lead(after)

    def _lead(self, text: Optional[str]) -> Optional[str]:
        """Trim whitespace up to the first real answer char (the answer often
        starts with ``\\n\\n`` after ``</think>``); pass through afterwards."""
        if self._emitted_any:
            return text or None
        if not text:
            return None
        stripped = text.lstrip()
        if not stripped:
            return None
        self._emitted_any = True
        return stripped

    def flush(self) -> str:
        """Stream ended; surface any buffered (un-closed/truncated) remainder."""
        if self._open and self._buf:
            out = self._buf
            self._buf = ""
            return out
        return ""
