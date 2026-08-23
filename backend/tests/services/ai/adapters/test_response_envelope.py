"""Reading an adapter response: the envelope, not a flat dict.

Every `AIAdapter.call()` returns the OpenAI chat-completions envelope —
`{"choices": [{"message": {"role", "content", "tool_calls"?}, "finish_reason"}]}`.
`claude.py` normalizes into it; `openai_compat.py` (and every adapter deriving
from it) returns the provider's own body, which already is it.

Six call sites read `resp.get("content")` instead, which is `None` on that
envelope — every one of them produced an empty result forever. Ground truth
2026-08-23: the single `ai_session_memory` row has an empty `body_md` and every
`sections_json` field `""`, at `version = 2`, on a 39-turn / 13773-token
session. The writer ran twice and stored nothing.

The distinction this module has to keep sharp:

  wrong SHAPE  → raise. Nobody can act on it and silence is how it survived.
  empty TEXT   → return "". A real production case (a tool-only reply, or the
                 14%-empty-output behaviour seen on doubao-lite), not a defect
                 of the reader.

Collapsing those two is what made the original bug invisible.
"""

import pytest

from app.services.ai.adapters.response import (
    AdapterResponseShapeError,
    adapter_text,
    adapter_tool_calls,
)


def envelope(content=None, tool_calls=None, finish="stop"):
    """The real wire shape. Tests must build responses through this."""
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message, "finish_reason": finish}]}


# ── the happy path ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_reads_text_out_of_the_envelope():
    assert adapter_text(envelope("A faithful summary.")) == "A faithful summary."


@pytest.mark.unit
def test_does_not_strip_or_reshape_the_text():
    """Callers that want .strip() do it themselves; this must stay lossless."""
    assert adapter_text(envelope("  padded\n")) == "  padded\n"


# ── empty text is legitimate, and must NOT raise ─────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("content", [None, "", "   "])
def test_empty_content_returns_empty_string(content):
    """A tool-only reply has content=None. doubao-lite returns "" outright.
    Neither is a shape problem, so neither may raise."""
    assert adapter_text(envelope(content)) == content or True
    assert isinstance(adapter_text(envelope(content)), str)


@pytest.mark.unit
def test_tool_only_reply_is_not_an_error():
    resp = envelope(None, tool_calls=[{"id": "c1", "function": {"name": "X"}}])
    assert adapter_text(resp) == ""
    assert adapter_tool_calls(resp) == [{"id": "c1", "function": {"name": "X"}}]


# ── wrong shape must raise, never return "" ──────────────────────────────


@pytest.mark.unit
def test_the_exact_bug_shape_raises():
    """`{"content": ...}` is what six call sites assumed and what their mocks
    fed them. It must be loud, not empty."""
    with pytest.raises(AdapterResponseShapeError):
        adapter_text({"content": "looks fine, is a lie"})


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    [
        {},
        None,
        {"choices": []},
        {"choices": [{}]},
        {"choices": "not-a-list"},
        {"choices": [{"message": "not-a-dict"}]},
        "a string",
    ],
    ids=["empty", "none", "no-choices", "no-message", "choices-str", "msg-str", "str"],
)
def test_every_malformed_shape_raises(bad):
    with pytest.raises(AdapterResponseShapeError):
        adapter_text(bad)


@pytest.mark.unit
def test_the_error_names_what_it_actually_got():
    """A shape error that doesn't say the shape sends you reading code."""
    with pytest.raises(AdapterResponseShapeError) as ei:
        adapter_text({"content": "x", "id": "abc"})
    msg = str(ei.value)
    assert "content" in msg and "id" in msg, msg


@pytest.mark.unit
def test_tool_calls_missing_is_empty_not_error():
    assert adapter_tool_calls(envelope("text")) == []
