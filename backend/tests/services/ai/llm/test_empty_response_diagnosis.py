"""An empty reply must say where the tokens went.

Production, 30 days to 2026-08-23: `doubao-seed-2-0-lite-260428` finished 36
runs, **8 of them (22%) with no text and no tool call**, and 7 of those billed
completion tokens — one for 649 of them. The model generated output; we stored
none of it, and the run closed as `completed` with `error_code=EMPTY_OUTPUT`.

Then the trail stops. No transcript event is written for those runs, so
nothing records which fields the response actually carried. Two very different
causes are indistinguishable from what we keep:

* the provider put the text somewhere we do not read (`reasoning_content` is
  the obvious suspect for a "seed-2.0" reasoning model), or
* the model genuinely returned nothing.

They call for opposite fixes — read another field, versus retry — so guessing
between them is how a fix gets built on a coin flip. This diagnosis records
the message's key names and value sizes (never the content) so the NEXT
occurrence answers the question instead of raising it again.
"""

import pytest

from app.services.ai.llm.empty_response import diagnose_empty_response


def envelope(message, *, finish="stop", usage=None):
    body = {"choices": [{"message": message, "finish_reason": finish}]}
    if usage:
        body["usage"] = usage
    return body


@pytest.mark.unit
def test_a_reply_with_text_is_not_empty():
    assert diagnose_empty_response(envelope({"content": "hello"})) is None


@pytest.mark.unit
def test_a_tool_only_reply_is_not_empty():
    """No prose but a tool call is a working turn, not a defect. Conflating
    the two would retry perfectly good turns."""
    resp = envelope({"content": None, "tool_calls": [{"id": "c1"}]})
    assert diagnose_empty_response(resp) is None


@pytest.mark.unit
def test_a_truly_empty_reply_is_reported():
    d = diagnose_empty_response(envelope({"content": ""}))
    assert d is not None


@pytest.mark.unit
def test_it_names_the_field_that_carried_the_tokens():
    """The whole point: `reasoning_content` present and non-trivial is the
    answer to 'where did 649 completion tokens go'."""
    d = diagnose_empty_response(
        envelope(
            {"content": "", "reasoning_content": "x" * 400},
            usage={"completion_tokens": 649},
        )
    )
    assert "reasoning_content" in d["message_keys"]
    assert d["message_keys"]["reasoning_content"] == 400
    assert d["completion_tokens"] == 649


@pytest.mark.unit
def test_it_never_records_the_content_itself():
    """Sizes and key names only — a diagnostic must not become a covert
    transcript of user data."""
    secret = "PATIENT NAME: Ada Reyes"
    d = diagnose_empty_response(envelope({"content": "", "reasoning_content": secret}))
    assert secret not in str(d)


@pytest.mark.unit
def test_it_carries_finish_reason():
    """`length` on an empty reply means the cap cut it off before any text —
    a different problem from the model declining to answer."""
    d = diagnose_empty_response(envelope({"content": ""}, finish="length"))
    assert d["finish_reason"] == "length"


@pytest.mark.unit
def test_billed_but_empty_is_flagged_explicitly():
    """The pairing that makes this worth chasing: we paid and stored nothing."""
    d = diagnose_empty_response(
        envelope({"content": ""}, usage={"completion_tokens": 102})
    )
    assert d["billed_but_empty"] is True

    d2 = diagnose_empty_response(
        envelope({"content": ""}, usage={"completion_tokens": 0})
    )
    assert d2["billed_but_empty"] is False


@pytest.mark.unit
def test_a_malformed_envelope_does_not_crash_the_diagnosis():
    """A diagnostic that raises turns a recoverable turn into an outage."""
    for bad in [None, {}, {"choices": []}, {"choices": [{"message": None}]}, "x"]:
        diagnose_empty_response(bad)  # must not raise
