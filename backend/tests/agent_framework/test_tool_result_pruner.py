"""A4 — tool_result dedupe + aging passes."""
from __future__ import annotations

import pytest

from app.agent_framework.tool_result_pruner import (
    AGING_GIST,
    DUPLICATE_REFERENCE,
    age_old_tool_results,
    dedupe_tool_results,
    prune,
)


# ─── Fixtures ─────────────────────────────────────────────────────────


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _assistant_with_tool_call(tcid: str, name: str = "read", args: str = '{}') -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": tcid, "type": "function", "function": {"name": name, "arguments": args}}
        ],
    }


def _tool_reply(tcid: str, body: str) -> dict:
    return {"role": "tool", "tool_call_id": tcid, "name": "read", "content": body}


# ─── dedupe ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_dedupe_no_duplicates_no_change():
    msgs = [
        _user("hi"),
        _assistant_with_tool_call("c1", args='{"path": "a.py"}'),
        _tool_reply("c1", "file contents A"),
        _assistant_with_tool_call("c2", args='{"path": "b.py"}'),
        _tool_reply("c2", "file contents B"),
    ]
    new, stats = dedupe_tool_results(msgs)
    assert stats.duplicates_replaced == 0
    assert new == msgs


@pytest.mark.unit
def test_dedupe_replaces_second_occurrence():
    """Same args → second result body replaced with reference to first."""
    msgs = [
        _assistant_with_tool_call("c1", args='{"path": "a.py"}'),
        _tool_reply("c1", "first body of a.py"),
        _user("re-check"),
        _assistant_with_tool_call("c2", args='{"path": "a.py"}'),
        _tool_reply("c2", "second body of a.py — same content"),
    ]
    new, stats = dedupe_tool_results(msgs)
    assert stats.duplicates_replaced == 1
    # First occurrence preserved
    assert new[1]["content"] == "first body of a.py"
    # Second replaced with reference to c1
    second_reply = new[4]
    assert "c1" in second_reply["content"]
    assert "duplicate" in second_reply["content"]


@pytest.mark.unit
def test_dedupe_handles_multiple_duplicate_groups():
    """Two distinct (tool, args) groups, each duplicated → both pruned independently."""
    msgs = [
        _assistant_with_tool_call("c1", args='{"path": "a.py"}'),
        _tool_reply("c1", "A1"),
        _assistant_with_tool_call("c2", args='{"path": "b.py"}'),
        _tool_reply("c2", "B1"),
        _assistant_with_tool_call("c3", args='{"path": "a.py"}'),
        _tool_reply("c3", "A2 (dup)"),
        _assistant_with_tool_call("c4", args='{"path": "b.py"}'),
        _tool_reply("c4", "B2 (dup)"),
    ]
    new, stats = dedupe_tool_results(msgs)
    assert stats.duplicates_replaced == 2
    # First-of-each preserved
    assert new[1]["content"] == "A1"
    assert new[3]["content"] == "B1"
    # Both subsequent ones replaced
    assert "duplicate" in new[5]["content"]
    assert "c1" in new[5]["content"]
    assert "duplicate" in new[7]["content"]
    assert "c2" in new[7]["content"]


@pytest.mark.unit
def test_dedupe_does_not_mutate_input():
    msgs = [
        _assistant_with_tool_call("c1", args='{"path": "a.py"}'),
        _tool_reply("c1", "body 1"),
        _assistant_with_tool_call("c2", args='{"path": "a.py"}'),
        _tool_reply("c2", "body 2"),
    ]
    original_body = msgs[3]["content"]
    dedupe_tool_results(msgs)
    assert msgs[3]["content"] == original_body  # untouched


# ─── aging ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_aging_squashes_old_long_tool_results():
    """tool_result older than aging_after_turns AND > max_body_chars → squashed."""
    big = "x" * 5000
    msgs: list[dict] = []
    # Old tool result (will be aged)
    msgs.append(_assistant_with_tool_call("c1"))
    msgs.append(_tool_reply("c1", big))
    # Pad to push it past aging window
    for i in range(15):
        msgs.append(_user(f"t{i}"))
    new, stats = age_old_tool_results(msgs, aging_after_turns=10, max_body_chars=1000)
    assert stats.aged_results == 1
    # Aged content has gist marker
    aged_body = new[1]["content"]
    assert "aged:" in aged_body
    assert "5000 chars" in aged_body  # original size noted


@pytest.mark.unit
def test_aging_preserves_recent_tool_results():
    """tool_result inside aging_after_turns window → untouched."""
    big = "y" * 5000
    msgs: list[dict] = [
        _user("recent question"),
        _assistant_with_tool_call("c1"),
        _tool_reply("c1", big),
    ]
    new, stats = age_old_tool_results(msgs, aging_after_turns=10)
    assert stats.aged_results == 0
    assert new[2]["content"] == big


@pytest.mark.unit
def test_aging_preserves_short_old_results():
    """Short bodies even when old — no aging needed."""
    msgs: list[dict] = [_assistant_with_tool_call("c1"), _tool_reply("c1", "ok")]
    for i in range(15):
        msgs.append(_user(f"t{i}"))
    new, stats = age_old_tool_results(msgs, aging_after_turns=10, max_body_chars=1000)
    assert stats.aged_results == 0
    assert new[1]["content"] == "ok"


# ─── prune (combined) ─────────────────────────────────────────────────


@pytest.mark.unit
def test_prune_combines_stats():
    big = "z" * 5000
    msgs: list[dict] = [
        _assistant_with_tool_call("c1", args='{"path": "x"}'),
        _tool_reply("c1", big),
        _assistant_with_tool_call("c2", args='{"path": "x"}'),
        _tool_reply("c2", "duplicate body"),
    ]
    for i in range(15):
        msgs.append(_user(f"pad{i}"))
    new, stats = prune(msgs, aging_after_turns=10, max_body_chars=1000)
    # c2 was a dup → replaced; c1 was old + long → aged
    assert stats.duplicates_replaced == 1
    assert stats.aged_results == 1
    assert stats.chars_dropped > 0


@pytest.mark.unit
def test_prune_empty_input():
    new, stats = prune([])
    assert new == []
    assert stats.duplicates_replaced == 0
    assert stats.aged_results == 0
