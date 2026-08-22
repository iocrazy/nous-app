"""The one scenario that pins the system message VERBATIM.

Every other prompt test in this repo tokenizes — it asserts that a marker or a
phrase is present, so editing one sentence of prompt prose churns one line in
one test. That is the right default; it is also why no test anywhere would show
a reviewer what the model actually receives, end to end, as one artifact.

This file is the deliberate exception: exactly ONE composition is pinned in
full, against a checked-in snapshot. Editing composer prose changes this file
and nothing else, and the diff IS the review — you read the assembled prompt
the way the model reads it.

Do not add a second pinned scenario. Two full pins means every prose edit
produces two diffs saying the same thing, and reviewers start refreshing
snapshots without reading them — which is exactly how a pin stops being a
control and becomes a chore.

Refresh after an intended change:
    PIN_REFRESH=1 uv run pytest tests/services/ai/prompts/test_system_message_pin.py
then READ the diff before committing it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.services.ai.prompts.prompt_composer import PromptComposer

SNAPSHOT = Path(__file__).parent / "snapshots" / "system_message_text_turn.txt"

# Frozen inputs: no uuid4, no clock, no DB. A snapshot that moves on its own
# teaches reviewers to ignore it.
AGENT = {
    "id": "1000000000000000001",
    "slug": "script_ai",
    "name": "Script AI",
    "model": "qwen-max",
    "identity_md": "I am the Script AI.",
    "soul_md": "I write crisp cinematic prose.",
    "agent_md": "When asked, produce HTML script output.",
}
SKILLS = [
    {"slug": "script-outline", "description": "Produce a 3-act outline."},
    {"slug": "script-expand", "description": "Expand a beat into a scene."},
]
WORKERS = [
    {"slug": "summarize", "description": "Summarize media.", "model": "qwen-max"}
]
GRAPH_FACTS = ["User is writing a heist series."]
USER_CONTEXT = "Prefers terse feedback."
REQUEST_INSTRUCTIONS = "Draft scene 3."


@pytest.fixture
def frozen_runtime_line(monkeypatch):
    """The runtime line carries wall-clock time — the one moving part."""
    monkeypatch.setattr(
        PromptComposer,
        "_render_runtime_line",
        lambda self, agent: f"# Runtime\nModel: {agent.get('model')} | Time: <FROZEN>",
    )


def _render() -> str:
    composer = PromptComposer(agent_repo=None, skill_repo=None)
    return composer._assemble_system_message(
        agent=AGENT,
        skills=SKILLS,
        request_instructions=REQUEST_INSTRUCTIONS,
        workers=WORKERS,
        graph_facts=GRAPH_FACTS,
        user_context=USER_CONTEXT,
    )


@pytest.mark.unit
def test_system_message_matches_the_pinned_snapshot(frozen_runtime_line):
    actual = _render()
    if os.environ.get("PIN_REFRESH"):
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(actual)
        pytest.skip(
            "snapshot refreshed — read the diff, then re-run without PIN_REFRESH"
        )
    assert SNAPSHOT.exists(), (
        f"missing snapshot {SNAPSHOT.name} — create it with "
        "PIN_REFRESH=1 and review the generated file before committing"
    )
    assert actual == SNAPSHOT.read_text(), (
        "The assembled system message changed. If the change was intended, "
        "refresh with PIN_REFRESH=1 and READ the diff — this pin exists so a "
        "prompt edit is reviewed as prompt text, not inferred from code diff."
    )


@pytest.mark.unit
def test_the_pin_is_reachable_and_non_trivial(frozen_runtime_line):
    """A snapshot test that renders almost nothing passes for the wrong reason.

    Without this, deleting a whole section from the composer AND refreshing
    the snapshot would still be green, and the pin would silently cover less
    each time.
    """
    actual = _render()
    for required in (
        "# Identity",
        "# Soul",
        "# Agent Instructions",
        "<available_skills>",
        "<available_workers>",
        "<graph_facts>",
        "<user_context>",
        "# Request Instructions",
        "# Runtime",
    ):
        assert required in actual, f"pinned scenario stopped covering {required}"
