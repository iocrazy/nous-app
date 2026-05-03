"""C1 — ToolCallLoopGuard."""
from __future__ import annotations

import pytest

from app.agent_framework.loop_guard import ToolCallLoopGuard


@pytest.mark.unit
def test_constructor_validates_threshold():
    with pytest.raises(ValueError):
        ToolCallLoopGuard(repeat_threshold=0)


@pytest.mark.unit
def test_constructor_validates_window():
    with pytest.raises(ValueError, match="window"):
        ToolCallLoopGuard(repeat_threshold=5, window=3)


@pytest.mark.unit
def test_no_loop_when_under_threshold():
    g = ToolCallLoopGuard(repeat_threshold=3, window=5)
    g.observe("read", '{"x": 1}')
    g.observe("read", '{"x": 1}')
    assert g.is_looping() is False


@pytest.mark.unit
def test_loop_detected_at_threshold():
    g = ToolCallLoopGuard(repeat_threshold=3, window=5)
    g.observe("read", '{"x": 1}')
    g.observe("read", '{"x": 1}')
    g.observe("read", '{"x": 1}')
    assert g.is_looping() is True


@pytest.mark.unit
def test_different_args_dont_count_as_loop():
    """Same tool, different args → not a loop."""
    g = ToolCallLoopGuard(repeat_threshold=3, window=5)
    g.observe("read", '{"x": 1}')
    g.observe("read", '{"x": 2}')
    g.observe("read", '{"x": 3}')
    assert g.is_looping() is False


@pytest.mark.unit
def test_different_tools_dont_count_as_loop():
    g = ToolCallLoopGuard(repeat_threshold=3, window=5)
    g.observe("read", '{"x": 1}')
    g.observe("write", '{"x": 1}')
    g.observe("delete", '{"x": 1}')
    assert g.is_looping() is False


@pytest.mark.unit
def test_window_slides():
    """3 same calls in old window, then different calls → no longer loop."""
    g = ToolCallLoopGuard(repeat_threshold=3, window=5)
    for _ in range(3):
        g.observe("read", '{"x": 1}')
    assert g.is_looping() is True
    # Add 5 different calls to push the offenders out of window
    for i in range(5):
        g.observe("misc", str(i))
    assert g.is_looping() is False


@pytest.mark.unit
def test_loop_within_window_still_caught():
    """3 offenders interleaved with 2 different calls but all within
    window=5 — still a loop."""
    g = ToolCallLoopGuard(repeat_threshold=3, window=5)
    g.observe("read", '{"x": 1}')
    g.observe("misc", "a")
    g.observe("read", '{"x": 1}')
    g.observe("misc", "b")
    g.observe("read", '{"x": 1}')
    # All 5 in window; 3 of them are read+x:1
    assert g.is_looping() is True


@pytest.mark.unit
def test_looping_signature_returns_offender():
    g = ToolCallLoopGuard(repeat_threshold=3, window=5)
    for _ in range(3):
        g.observe("read", '{"path": "a.py"}')
    sig = g.looping_signature()
    assert sig is not None
    assert sig[0] == "read"


@pytest.mark.unit
def test_looping_signature_none_when_no_loop():
    g = ToolCallLoopGuard()
    g.observe("read", "x")
    assert g.looping_signature() is None


@pytest.mark.unit
def test_reset_clears_window():
    g = ToolCallLoopGuard(repeat_threshold=3, window=5)
    for _ in range(3):
        g.observe("x", "y")
    assert g.is_looping() is True
    g.reset()
    assert g.is_looping() is False


@pytest.mark.unit
def test_render_warning_includes_tool_name():
    g = ToolCallLoopGuard(repeat_threshold=3, window=5)
    for _ in range(3):
        g.observe("dangerous_tool", "any")
    msg = g.render_warning()
    assert "dangerous_tool" in msg
    assert "loop_guard" in msg


@pytest.mark.unit
def test_render_warning_empty_when_no_loop():
    g = ToolCallLoopGuard()
    g.observe("x", "y")
    assert g.render_warning() == ""


@pytest.mark.unit
def test_handles_dict_args():
    """args=dict (not just str) — hashing should work via repr."""
    g = ToolCallLoopGuard(repeat_threshold=2, window=3)
    g.observe("tool", {"k": "v"})
    g.observe("tool", {"k": "v"})
    assert g.is_looping() is True
