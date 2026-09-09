"""Every tool-handler await in agent_runner goes through ``self._timed`` —
a site that awaits a handler directly has no timeout and no ``timed_out``
flag (phase 2b-1 §3). Both loops (run_turn / stream_turn) are covered."""

import pathlib
import re

import pytest

pytestmark = pytest.mark.unit
SRC = (
    pathlib.Path(__file__).resolve().parents[2]
    / "app/services/ai/runner/agent_runner.py"
)
HANDLERS = [
    r"self\.skill_tool\.execute\(",
    r"self\.resource_fetch_handler\(",
    r"self\.generate_image_handler\(",
    r"self\.generate_video_handler\(",
    r"self\.mcp_registry\.call\(",
    r"self\.delegate_tool\.execute\(",
    r"self\._dispatch_screenwriting\(",
    r"self\._dispatch_finish_issue\(",
    r"self\._dispatch_ask_user\(",
]


def test_every_tool_await_is_wrapped():
    text = SRC.read_text()
    bare = []
    for pat in HANDLERS:
        for m in re.finditer(r"await\s+" + pat, text):
            bare.append((pat, text[: m.start()].count("\n") + 1))
    assert bare == [], f"tool handlers awaited without _timed: {bare}"
    # 9 handler sites per loop × 2 loops
    assert (
        text.count("self._timed(") >= 18
    ), "both loops must route every handler through _timed"
