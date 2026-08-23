"""An unknown model must not silently borrow a generic context window.

`model_window_size()` answers every model — a name it does not know falls
back to `settings.LLM_MAX_CONTEXT_TOKENS` (28000). That default is a fine
safety net and a terrible silent one: every tier decision downstream divides
by it, so an unrecognised model gets confidently-wrong compaction with no
signal anywhere that the denominator was a guess.

Not hypothetical. Production, 2026-08-23: of 20 configured agents, 12 run
models absent from the table — `doubao-seed-2-0-lite-260428` (11 agents) and
`nous-qwen3-llm` (1) — so all 12 have been compacted against 28000 tokens
regardless of what their real window is.

This is the repo's recurring shape: a fallback whose output is indistinguishable
from a correct answer. The fix is not to remove the fallback — it is to make
using it say so.
"""

from unittest.mock import patch

import pytest

from app.agent_framework.context_compactor import ContextCompactor
from app.agent_framework.context_window import model_window_size


@pytest.fixture
def compactor():
    return ContextCompactor()


@pytest.mark.unit
def test_a_known_model_is_not_reported_as_a_guess():
    assert model_window_size("claude-sonnet-4-6") == 200_000


@pytest.mark.unit
@pytest.mark.parametrize(
    "model", ["doubao-seed-2-0-lite-260428", "nous-qwen3-llm", "totally-made-up"]
)
async def test_an_unknown_model_says_so_in_the_stats(compactor, model):
    """The note is the only place a reader can learn the window was guessed."""
    msgs = [{"role": "user", "content": "x"}]
    with (
        patch("app.agent_framework.context_compactor.count_tokens", return_value=0),
        patch(
            "app.agent_framework.context_compactor.count_messages_tokens",
            return_value=10,
        ),
    ):
        _, stats = await compactor.maybe_compact(
            system_message="", user_messages=msgs, model=model
        )
    assert any("window is a fallback" in n for n in stats.notes), (
        f"{model} is not in the window table, so every tier decision for it "
        f"divided by a default — that must be visible. notes={stats.notes}"
    )


@pytest.mark.unit
async def test_a_known_model_carries_no_such_note(compactor):
    """The negative half: a note on every turn is a note nobody reads."""
    msgs = [{"role": "user", "content": "x"}]
    with (
        patch("app.agent_framework.context_compactor.count_tokens", return_value=0),
        patch(
            "app.agent_framework.context_compactor.count_messages_tokens",
            return_value=10,
        ),
    ):
        _, stats = await compactor.maybe_compact(
            system_message="", user_messages=msgs, model="claude-sonnet-4-6"
        )
    assert not any("window is a fallback" in n for n in stats.notes), stats.notes


@pytest.mark.unit
async def test_the_note_survives_the_green_tier(compactor):
    """Green returns early. If the note only rode the compaction paths, the
    models most likely to be mis-measured — the ones sitting comfortably under
    a wrong threshold — would never report it."""
    msgs = [{"role": "user", "content": "x"}]
    with (
        patch("app.agent_framework.context_compactor.count_tokens", return_value=0),
        patch(
            "app.agent_framework.context_compactor.count_messages_tokens",
            return_value=1,  # ~0% used → green
        ),
    ):
        _, stats = await compactor.maybe_compact(
            system_message="", user_messages=msgs, model="unknown-model-xyz"
        )
    from app.agent_framework.context_compactor import CompactionTier

    assert stats.tier == CompactionTier.GREEN
    assert any("window is a fallback" in n for n in stats.notes), stats.notes
