"""Typed failures for the codex-local (per-user daemon) LLM path.

Every code maps to a 4xx ``status_code`` on purpose: the retry middleware's
``classify_error`` reads that attribute and returns ``non_retryable``, which
becomes ``LLMCallError`` and therefore NEVER falls through to another model
in the fallback chain. Falling back would silently change who pays for the
call (the user's own ChatGPT subscription vs a platform key) — the user did
not agree to that.

The ``[codex-local:<code>]`` marker in ``str(exc)`` is what
``error_catalog`` pattern-matches, so it survives the ``LLMCallError``
wrap (which only keeps the message).
"""

from __future__ import annotations

_STATUS_BY_CODE: dict[str, int] = {
    "daemon_offline": 424,
    "timeout": 424,
    "tools_unsupported": 422,
    "codex_not_logged_in": 401,
    "cli_missing": 424,
    "codex_no_output": 424,
    "codex_failed": 424,
}


class CodexLocalError(RuntimeError):
    code: str
    status_code: int

    def __init__(self, code: str, message: str) -> None:
        self.code = code if code in _STATUS_BY_CODE else "codex_failed"
        self.status_code = _STATUS_BY_CODE[self.code]
        super().__init__(f"[codex-local:{self.code}] {message}")


def from_daemon_error(raw: str) -> CodexLocalError:
    """``dispatch_to_daemon`` raises ``RuntimeError("<code>: <message>")`` for a
    daemon-reported failure — split it back into a typed error."""
    code, _, message = str(raw).partition(":")
    return CodexLocalError(code.strip(), message.strip() or code.strip())


__all__ = ["CodexLocalError", "from_daemon_error"]
