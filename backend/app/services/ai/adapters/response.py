"""Reading an `AIAdapter.call()` response.

Every adapter returns the OpenAI chat-completions envelope::

    {"choices": [{"message": {"role": ..., "content": ..., "tool_calls": [...]},
                  "finish_reason": ...}], "usage": {...}}

`claude.py` normalizes the Anthropic body into it; `openai_compat.py` — and
every adapter deriving from it — returns the provider body, which already is
it. There is exactly one shape, so there is exactly one right way to read it.

Six call sites nonetheless read ``resp.get("content")``, which is ``None`` on
that envelope, and four of them coerced it with ``or ""``. Ground truth
2026-08-23: the single ``ai_session_memory`` row holds an empty ``body_md``
with every ``sections_json`` field ``""`` at ``version = 2``, written off a
39-turn / 13773-token session. It ran twice and stored nothing, without an
exception, a log line, or a failing test — the tests mocked ``call`` as
``{"content": ...}``, a shape no adapter has ever produced.

The one distinction this module must keep sharp:

===========  ==========================================================
wrong shape  raise ``AdapterResponseShapeError``. Nobody downstream can
             act on it, and silence is exactly how it survived.
empty text   return ``""``. A tool-only reply carries ``content: None``,
             and some models return an empty string outright — that is
             the model's answer, not a defect in the reader.
===========  ==========================================================

Collapsing those two is what made the original bug invisible for months.
"""

from __future__ import annotations

from typing import Any


class AdapterResponseShapeError(ValueError):
    """An adapter response did not carry the chat-completions envelope.

    Raised rather than absorbed on purpose: a caller that silently treats it
    as "the model said nothing" reproduces the defect this module exists to
    end. Callers that must not crash should catch it AND log it — never
    swallow it into an empty return.
    """


def _message(resp: Any) -> dict[str, Any]:
    if not isinstance(resp, dict):
        raise AdapterResponseShapeError(
            f"expected the chat-completions envelope, got {type(resp).__name__}"
        )
    choices = resp.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AdapterResponseShapeError(
            "response has no non-empty 'choices' list; "
            f"top-level keys={sorted(resp)} — this is the flat-dict "
            'misread (`resp.get("content")`), not a provider outage'
        )
    first = choices[0]
    if not isinstance(first, dict):
        raise AdapterResponseShapeError(
            f"choices[0] is {type(first).__name__}, expected a dict"
        )
    message = first.get("message")
    if not isinstance(message, dict):
        raise AdapterResponseShapeError(
            f"choices[0].message is {type(message).__name__}, expected a dict; "
            f"choices[0] keys={sorted(first)}"
        )
    return message


def adapter_text(resp: Any) -> str:
    """The assistant text, or ``""`` when the model produced none.

    Never returns ``None``, and never turns a malformed envelope into ``""``.
    Does not strip — a caller that wants trimming does it itself, so this
    stays lossless for callers that don't.
    """
    content = _message(resp).get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    # Some providers emit content as a block list even on the chat route.
    if isinstance(content, list):
        return "".join(
            str(b.get("text") or "")
            for b in content
            if isinstance(b, dict) and b.get("type") in (None, "text")
        )
    return str(content)


def adapter_tool_calls(resp: Any) -> list[dict[str, Any]]:
    """Tool calls on the reply, or ``[]``. Same shape discipline as above."""
    calls = _message(resp).get("tool_calls")
    return list(calls) if isinstance(calls, list) else []


__all__ = ["AdapterResponseShapeError", "adapter_text", "adapter_tool_calls"]
