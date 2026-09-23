"""The legacy summary path carries the user, so codex-local can route.

``resolve_db_adapter`` takes ``user_id`` as routing context: a ``codex-local``
maintenance model runs on the user's own paired machine and refuses to build
without one. The warm path never needed it (it reuses the conversation's own,
already user-routed adapter), but the legacy fallback built a fresh adapter
with no user — so a codex-local maintenance model could never summarize, and
every such compaction silently degraded to the lossy emergency cap.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.agent_framework import summarizer
from app.agent_framework.context_compactor import ContextCompactor
from tests.agent_framework.compaction_stubs import token_stub


def _adapter(text="a summary"):
    a = AsyncMock()
    a.call = AsyncMock(
        return_value={
            "choices": [
                {
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ]
        }
    )
    return a


@pytest.mark.unit
async def test_summarize_passes_user_id_to_adapter_resolution():
    resolve = AsyncMock(return_value=_adapter())
    with patch(
        "app.services.ai.providers.ai_provider_helpers.resolve_db_adapter", new=resolve
    ):
        text = await summarizer.summarize(
            [{"role": "user", "content": "hello"}],
            provider_override="codex:gpt-5",
            user_id="user-123",
        )
    assert text == "a summary"
    assert resolve.await_args.kwargs.get("user_id") == "user-123"


@pytest.mark.unit
async def test_compactor_legacy_path_threads_user_id():
    legacy = AsyncMock(return_value="legacy summary")
    with (
        patch(
            "app.agent_framework.context_compactor.count_messages_tokens",
            new=token_stub([300], summary_tokens=10),
        ),
        patch("app.agent_framework.summarizer.summarize", legacy),
    ):
        await ContextCompactor()._compact_with_summary(
            messages=[
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "latest"},
            ],
            keep_recent_turns=1,
            model="qwen-max",
            user_id="user-123",
        )
    assert legacy.await_args.kwargs.get("user_id") == "user-123"


@pytest.mark.unit
def test_runner_threads_the_user_into_the_compactor():
    """helper 存在 ≠ helper 被调：the runner's preflight must hand over the
    user, or the legacy path is still user-less on every real route."""
    src = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "services"
        / "ai"
        / "runner"
        / "agent_runner.py"
    ).read_text()
    start = src.index("maybe_compact(")
    call = src[start : src.index("\n        )", start)]
    assert "user_id=" in call, call
