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
    # The daemon rejects image URLs that are not on a nous host (spec §4.3,
    # ``index.mjs`` ``ref_rejected``). 400 because the request itself is what
    # is wrong — nothing about the user's machine needs fixing, they just have
    # to attach a different image.
    "ref_rejected": 400,
    # The user's daemon is older than the build that learned to run text jobs
    # safely (sandbox flags, prompt on stdin, chunked results). 426 Upgrade
    # Required says exactly that, and like every other code here it is 4xx —
    # so the retry middleware calls it non-retryable and the call never falls
    # through to a paid model.
    "daemon_outdated": 426,
    # The model answered, but declined to draw: it returned prose (why, plus a
    # rewrite that would work) instead of calling the image tool. 422 because
    # the REQUEST is what needs changing — nothing is broken, retrying the same
    # words will be declined the same way, and like every code here it is 4xx
    # so the retry middleware never falls through to a model the user pays for.
    "content_refused": 422,
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
