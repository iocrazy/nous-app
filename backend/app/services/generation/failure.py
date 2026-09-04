"""How one generation failure is split across two columns.

A failure can carry an explanation written for the user to read — today, the
image model's own words when it declines a prompt: what it objected to, and a
rewrite that works. That text is the model's prose, so it is routinely
non-ASCII, and it therefore cannot ride the exception.

``task_tracking.error_msg`` is derived from the pickled exception by
``public.dbos_error_to_text()`` (migration 219), which escape-renders the
pickle, treats every non-printable byte and every byte >= 0x80 as a delimiter,
and keeps only the LONGEST surviving chunk. A Chinese sentence arrives there as
a fragment; a multi-line message arrives as its longest line. (That is exactly
how the 2026-09-04 failure reached the user as
``RuntimeError: "message": "The response did not include an
image_generation_call result."`` — one line lifted out of the daemon's JSON.)

So: one ASCII line goes on the exception, and the prose goes to ``metadata``,
which is jsonb and business decoration the workflow owns (route C §3).

Deliberately duck-typed on ``code`` / ``detail`` rather than tied to one
exception class: the daemon path raises ``DaemonJobFailedError`` and the
in-container subprocess path raises ``CodexCliError``, both codex image
generation, and a user cannot tell which one ran.
"""

from __future__ import annotations

from typing import Any

CONTENT_REFUSED = "content_refused"

# One line, pure ASCII, and it names its own code — all three load-bearing.
# The marker is what the frontend still has to match on after the extractor
# above has had its way with the string.
REFUSAL_MESSAGE = (
    "[content_refused] The image model declined this prompt and answered with an "
    "explanation instead of an image. Open this task's details to read its own "
    "wording and the rewrite it suggests."
)

GENERIC_MESSAGE = "The generation failed. Open this task's details."


def _ascii_line(text: str) -> str:
    """Fold ``text`` into one line of pure ASCII, or give up and say so.

    A message that is mostly non-ASCII would arrive downstream as a fragment,
    which is worse than a truthful generic line — the real words are in
    ``metadata`` either way.
    """
    folded = " ".join(str(text or "").split())
    ascii_only = "".join(c for c in folded if 32 <= ord(c) < 127)
    if len(ascii_only) < 12:
        return GENERIC_MESSAGE
    return ascii_only[:400]


def describe_generation_failure(exc: BaseException) -> tuple[str, dict[str, Any]]:
    """Return ``(message_to_raise, metadata_patch)`` for one failed generation.

    A failure is only ever described as a refusal when the provider SAID it
    was one. Telling someone whose daemon crashed that "the model declined
    you" is both wrong and unactionable.
    """
    code = str(getattr(exc, "code", "") or "job_failed")
    detail = getattr(exc, "detail", "") or ""
    patch: dict[str, Any] = {"failure": {"code": code, "detail": detail}}
    message = REFUSAL_MESSAGE if code == CONTENT_REFUSED else _ascii_line(str(exc))
    return message, patch


__all__ = ["CONTENT_REFUSED", "REFUSAL_MESSAGE", "describe_generation_failure"]
