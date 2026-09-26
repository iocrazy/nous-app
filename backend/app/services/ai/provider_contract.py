"""The provider contract — what an adapter's ``call()`` may hand back.

THE RULE (this docstring is the contract's home; call sites do not restate it)
------------------------------------------------------------------------------
* **Success** → the chat-completions envelope, returned **byte-identical**
  (the very same dict the provider produced, or the adapter's own
  normalised envelope). Nothing is added, renamed or dropped.
* **Any error form** → **raised** as :class:`ProviderResponseError`, a
  subclass of ``LLMCallError``. An error is never *returned* as a value and
  callers never inspect a ``kind`` — they either get an envelope they can
  read, or an exception that every existing error path (retry middleware,
  fallback chain, ``provider_errors`` HTTP mapping, workflow
  ``record_ai_error_code``, issue ``blocked`` routing) already handles.

Why raise and not ``return {"kind": "error"}``: roughly a dozen callers read
an adapter's reply directly (summariser, canvas runs, tasklets, memory
consolidation, health probes …). A returned error value makes each of them
responsible for checking it, and every one that forgets is a new silent drop
— the exact "caller has to guess" failure CLAUDE.md 「公共契约两侧都要遵守」
bans. Before this module the three real adapters passed in-band errors
through as success: a 200 body carrying ``{"error": …}`` surfaced later as an
untyped ``KeyError('choices')`` with no retry and no fallback, and a
content-filtered or token-less empty reply ended the turn "completed".

Where it is enforced: :func:`normalize_envelope` is the last expression of
every real adapter ``call()`` (``openai_compat`` — and every thin subclass —
``claude``, ``codex_daemon``). A source-scan rule test pins that.

Mapping (source shape → error code, same-model retry policy):

=====================================================  =========================  =============
source shape                                           error_code                 policy
=====================================================  =========================  =============
200 body with ``error`` / no usable ``choices``        body-classified, else      per code;
                                                       ``PROVIDER_BAD_RESPONSE``  bad → once
finish ``content_filter`` / ``sensitive`` / refusal    ``PROVIDER_CONTENT_FILTER`` fallback only
finish ``error`` (and DeepSeek's resource-exhausted)   ``PROVIDER_BAD_RESPONSE``  retry
no text, no tool call, nothing billed                  ``PROVIDER_EMPTY_RESPONSE`` retry
=====================================================  =========================  =============

Kept as success on purpose: ``length`` (an honest partial answer — turn_end
files it ``provider_length``) and "empty but billed" (the text went somewhere
we do not read; ``diagnose_empty_response`` records the evidence and fh2 T4's
workflow policy owns what happens next). Unknown finish words stay success
too: refusing a reply because a provider coined a new word would be the
opposite failure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Mapping, Optional

from app.services.ai.error_catalog import (
    PROVIDER_AUTH,
    PROVIDER_BAD_MODEL,
    PROVIDER_BAD_RESPONSE,
    PROVIDER_CONTENT_FILTER,
    PROVIDER_EMPTY_RESPONSE,
    PROVIDER_UNREACHABLE,
    classify_ai_error,
    error_code_for,
    error_code_marker,
)
from app.services.ai.llm.llm_retry_middleware import (
    LLMCallError,
    classify_error,
    classify_status,
)


class FinishReason(str, Enum):
    """Closed vocabulary for why a reply ended. ``UNKNOWN`` = a raw word we
    do not map (kept as success, see module docstring)."""

    STOP = "stop"
    TOOL_CALLS = "tool_calls"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"
    EMPTY = "empty"
    UNKNOWN = "unknown"


# Raw provider finish words → the closed vocabulary. OpenAI-compatible words
# first; Anthropic ``stop_reason`` words are here too so a direct Claude
# envelope classifies the same way even before its adapter maps them.
_RAW_FINISH: Mapping[str, FinishReason] = {
    "stop": FinishReason.STOP,
    "end_turn": FinishReason.STOP,
    "stop_sequence": FinishReason.STOP,
    "tool_calls": FinishReason.TOOL_CALLS,
    "function_call": FinishReason.TOOL_CALLS,
    "tool_use": FinishReason.TOOL_CALLS,
    "length": FinishReason.LENGTH,
    "max_tokens": FinishReason.LENGTH,
    "content_filter": FinishReason.CONTENT_FILTER,
    "sensitive": FinishReason.CONTENT_FILTER,
    "refusal": FinishReason.CONTENT_FILTER,
    "error": FinishReason.ERROR,
    # DeepSeek: the request was cut because the backend ran out of capacity.
    "insufficient_system_resource": FinishReason.ERROR,
}

# What the retry middleware does with the error on the SAME model:
#   retry          — normal backoff budget, then fall back to the next model
#   retry_once     — one more try, then fall back
#   fallback_only  — no same-model retry (it would say the same thing), but
#                    the next model may well answer
#   fail           — neither retry nor fallback (bad key, missing model)
RetryPolicy = Literal["retry", "retry_once", "fallback_only", "fail"]

_POLICY_BY_CODE: Mapping[str, RetryPolicy] = {
    PROVIDER_AUTH: "fail",
    PROVIDER_BAD_MODEL: "fail",
    PROVIDER_BAD_RESPONSE: "retry_once",
    PROVIDER_CONTENT_FILTER: "fallback_only",
    PROVIDER_EMPTY_RESPONSE: "retry",
}

_DETAIL_MAX = 240


def parse_finish_reason(raw: Any) -> FinishReason:
    """A raw finish word → the closed vocabulary. ``None`` / ``""`` is STOP
    (plenty of servers omit it on a normal answer)."""
    if raw is None or raw == "":
        return FinishReason.STOP
    return _RAW_FINISH.get(str(raw).strip().lower(), FinishReason.UNKNOWN)


def _has_text(content: Any) -> bool:
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return bool(content)
    return False


def _billed_tokens(usage: Any) -> int:
    if not isinstance(usage, Mapping):
        return 0
    try:
        return int(usage.get("completion_tokens") or 0)
    except (TypeError, ValueError):
        return 0


def _snippet(value: Any) -> str:
    from app.boundary.log_redact import redact

    try:
        text = value if isinstance(value, str) else json.dumps(value, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    return redact(" ".join(text.split()))[:_DETAIL_MAX]


def _body_status(body: Mapping[str, Any]) -> Optional[int]:
    """An HTTP-like status carried INSIDE a 200 error body, if any."""
    err = body.get("error")
    candidates = [body.get("status"), body.get("status_code")]
    if isinstance(err, Mapping):
        candidates += [err.get("status"), err.get("code"), err.get("status_code")]
    for c in candidates:
        try:
            n = int(c)
        except (TypeError, ValueError):
            continue
        if 100 <= n <= 599:
            return n
    return None


@dataclass(frozen=True)
class ProviderOutcome:
    """Typed read of one provider reply. ``kind="error"`` is never returned
    to a caller — :func:`normalize_envelope` raises it instead."""

    kind: Literal["ok", "error"]
    finish_reason: FinishReason
    content: str = ""
    tool_calls: tuple[Mapping[str, Any], ...] = ()
    usage: Optional[Mapping[str, Any]] = None
    error_code: Optional[str] = None
    retry_policy: Optional[RetryPolicy] = None
    status_code: Optional[int] = None
    detail: str = ""
    raw: Optional[Mapping[str, Any]] = field(default=None, repr=False, compare=False)

    @property
    def retryable(self) -> bool:
        return self.retry_policy in ("retry", "retry_once")

    @classmethod
    def error(
        cls,
        code: str,
        detail: str,
        *,
        finish: FinishReason = FinishReason.ERROR,
        status_code: Optional[int] = None,
        policy: Optional[RetryPolicy] = None,
        raw: Optional[Mapping[str, Any]] = None,
    ) -> "ProviderOutcome":
        return cls(
            kind="error",
            finish_reason=finish,
            error_code=code,
            retry_policy=policy or _POLICY_BY_CODE.get(code, "retry"),
            status_code=status_code,
            detail=detail,
            raw=raw,
        )

    @classmethod
    def from_envelope(cls, resp: Any) -> "ProviderOutcome":
        """Classify one reply. Never raises."""
        if not isinstance(resp, Mapping):
            return cls.error(
                PROVIDER_BAD_RESPONSE,
                f"reply is {type(resp).__name__}, not a JSON object",
            )
        if resp.get("error"):
            return cls._from_error_body(resp)
        choices = resp.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, Mapping) else None
        if not isinstance(message, Mapping):
            return cls.error(
                PROVIDER_BAD_RESPONSE,
                f"reply has no usable choices[0].message; keys={sorted(resp)[:8]}",
                raw=resp,
            )
        return cls._from_message(resp, first, message)

    @classmethod
    def _from_error_body(cls, resp: Mapping[str, Any]) -> "ProviderOutcome":
        status = _body_status(resp)
        detail = _snippet(resp.get("error"))
        text = (f"HTTP {status} " if status else "") + detail
        code = classify_ai_error(text) or PROVIDER_BAD_RESPONSE
        if code == PROVIDER_BAD_RESPONSE:
            policy: Optional[RetryPolicy] = "retry_once"
        elif status is not None:
            policy = _policy_from_status(status)
        else:
            policy = None
        return cls.error(
            code,
            f"200 body carried an error: {detail}",
            status_code=status,
            policy=policy,
            raw=resp,
        )

    @classmethod
    def _from_message(
        cls,
        resp: Mapping[str, Any],
        first: Mapping[str, Any],
        message: Mapping[str, Any],
    ) -> "ProviderOutcome":
        raw_finish = first.get("finish_reason")
        finish = parse_finish_reason(raw_finish)
        content = message.get("content")
        calls = message.get("tool_calls")
        tool_calls = tuple(calls) if isinstance(calls, list) else ()
        usage = resp.get("usage") if isinstance(resp.get("usage"), Mapping) else None
        if finish is FinishReason.CONTENT_FILTER:
            return cls.error(
                PROVIDER_CONTENT_FILTER,
                f"reply withheld by the provider (finish_reason={raw_finish})",
                finish=finish,
                raw=resp,
            )
        if finish is FinishReason.ERROR:
            return cls.error(
                PROVIDER_BAD_RESPONSE,
                f"provider ended the reply with finish_reason={raw_finish}",
                finish=finish,
                policy="retry",
                raw=resp,
            )
        if not _has_text(content) and not tool_calls and _billed_tokens(usage) == 0:
            return cls.error(
                PROVIDER_EMPTY_RESPONSE,
                "reply carried no text, no tool call and no billed tokens",
                finish=FinishReason.EMPTY,
                raw=resp,
            )
        text = content if isinstance(content, str) else ""
        return cls(
            kind="ok",
            finish_reason=finish,
            content=text,
            tool_calls=tool_calls,
            usage=usage,
            raw=resp,
        )

    @classmethod
    def from_exception(cls, exc: BaseException) -> "ProviderOutcome":
        """Typed read of a transport / SDK exception (never raises)."""
        if isinstance(exc, ProviderResponseError):
            return exc.outcome
        code = error_code_for(exc)
        klass = classify_error(exc)
        policy: RetryPolicy = {
            "retryable": "retry",
            "retry_once": "retry_once",
            "fallback_only": "fallback_only",
            "non_retryable": "fail",
        }.get(klass, "retry_once")
        status = getattr(exc, "status_code", None)
        if not isinstance(status, int):
            status = getattr(getattr(exc, "response", None), "status_code", None)
        return cls.error(
            code,
            _snippet(f"{type(exc).__name__}: {exc}"),
            status_code=status if isinstance(status, int) else None,
            policy=policy,
        )


def _policy_from_status(status: int) -> RetryPolicy:
    """Same split the retry middleware makes for a transport status."""
    return "fail" if classify_status(status) == "non_retryable" else "retry"


class ProviderResponseError(LLMCallError):
    """An error the provider reported in-band (or a transport outcome typed
    through the contract). Subclasses ``LLMCallError`` so every caller that
    already catches that — or ``AllModelsFailed`` further up — needs no change.

    The message starts with an ``[error_code:X]`` marker: DBOS pickles only an
    exception's ``args``, and the marker is what lets ``classify_ai_error``
    recover the code from a bare string on the far side.
    """

    def __init__(self, outcome: ProviderOutcome, *, model: Optional[str] = None):
        self.outcome = outcome
        self.model = model
        where = f" (model={model})" if model else ""
        super().__init__(
            f"{error_code_marker(outcome.error_code)} {outcome.detail}{where}"
        )

    @property
    def error_code(self) -> Optional[str]:
        return self.outcome.error_code

    @property
    def retry_policy(self) -> Optional[RetryPolicy]:
        return self.outcome.retry_policy

    @property
    def retryable(self) -> bool:
        return self.outcome.retryable

    @property
    def status_code(self) -> Optional[int]:
        return self.outcome.status_code

    def __reduce__(self):  # keep the typed fields across pickling
        return (_restore, (str(self), self.error_code, self.retry_policy, self.model))


def _restore(message: str, code: Optional[str], policy, model) -> ProviderResponseError:
    outcome = ProviderOutcome.error(code or PROVIDER_BAD_RESPONSE, "", policy=policy)
    err = ProviderResponseError(outcome, model=model)
    err.args = (message,)
    return err


def normalize_envelope(resp: Any, *, model: Optional[str] = None) -> Any:
    """The last expression of every real adapter ``call()``.

    Returns ``resp`` itself (same object, byte-identical) when it is a
    success; raises :class:`ProviderResponseError` for every error form.
    """
    outcome = ProviderOutcome.from_envelope(resp)
    if outcome.kind == "error":
        raise ProviderResponseError(outcome, model=model)
    return resp


def stream_ended_without_finish(model: Optional[str] = None) -> ProviderResponseError:
    """A true stream whose connection closed before any finish_reason."""
    return ProviderResponseError(
        ProviderOutcome.error(
            PROVIDER_UNREACHABLE,
            "stream closed before the provider sent a finish_reason",
            policy="retry",
        ),
        model=model,
    )


__all__ = [
    "FinishReason",
    "ProviderOutcome",
    "ProviderResponseError",
    "RetryPolicy",
    "normalize_envelope",
    "parse_finish_reason",
    "stream_ended_without_finish",
]
