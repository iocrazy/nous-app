"""Tests for the LLM-driven head summarizer (Phase 2 of #199).

Pin the data-shape contract between summarizer and compactor:
  - return value is a non-empty string (the summary text)
  - provider routes via ``ai.adapters.factory.get_adapter`` so a new
    model added there works here automatically
  - timeouts and provider failures raise RuntimeError with provider
    name + reason in the message (compactor catches and falls back)
  - admin override via system_settings + env override take precedence
    over the default
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agent_framework.summarizer import (
    DEFAULT_COMPACTION_PROVIDER,
    SUMMARIZE_SYSTEM_PROMPT,
    SUMMARIZE_TIMEOUT_S,
    summarize,
)


def _make_msgs() -> list[dict]:
    return [
        {"role": "user", "content": "Look up file /tmp/foo.txt and show line 42"},
        {"role": "assistant", "content": "I read /tmp/foo.txt; line 42 is x=1"},
        {"role": "user", "content": "Now check video ID 7637245285990747435"},
    ]


def _adapter_returning(text: str):
    """Build a fake adapter whose .call returns the given text."""
    fake_adapter = AsyncMock()
    fake_adapter.call = AsyncMock(return_value={"content": text})
    return fake_adapter


async def test_default_provider_is_haiku():
    """If no env override and no system_settings override, fall back
    to DEFAULT_COMPACTION_PROVIDER. The default must be a cheap model
    (Haiku 4.5) — pin the value so a future "let's switch to Sonnet"
    PR makes a loud diff in the test output."""
    assert "haiku" in DEFAULT_COMPACTION_PROVIDER.lower()


async def test_env_override_wins_over_system_settings(monkeypatch):
    """COMPACTION_PROVIDER env var is the CI / debug knob and must
    short-circuit the system_settings DB read entirely."""
    monkeypatch.setenv("COMPACTION_PROVIDER", "qwen-plus")
    fake_adapter = _adapter_returning("summary text")

    with patch(
        "app.services.ai.adapters.factory.get_adapter", return_value=fake_adapter
    ) as mock_factory, patch(
        "app.db.supabase_client.get_async_supabase_admin"
    ) as mock_db:
        result = await summarize(_make_msgs())

    mock_db.assert_not_called()  # env override skipped DB
    mock_factory.assert_called_once()
    args, _ = mock_factory.call_args
    assert args[0] == "qwen-plus"
    assert result == "summary text"


async def test_system_settings_override_used_when_no_env(monkeypatch):
    """When env is empty, summarizer reads compaction_provider from
    system_settings. That's the admin-facing config seam."""
    monkeypatch.delenv("COMPACTION_PROVIDER", raising=False)
    fake_adapter = _adapter_returning("summary text")

    fake_db = AsyncMock()
    fake_db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute = AsyncMock(
        return_value=type("R", (), {"data": [{"value": "claude-haiku-4-5"}]})()
    )

    with patch(
        "app.db.supabase_client.get_async_supabase_admin", return_value=fake_db
    ), patch(
        "app.services.ai.adapters.factory.get_adapter", return_value=fake_adapter
    ) as mock_factory:
        await summarize(_make_msgs())

    args, _ = mock_factory.call_args
    assert args[0] == "claude-haiku-4-5"


async def test_db_failure_falls_back_to_default(monkeypatch):
    """If system_settings can't be read (Postgres down / table missing),
    summarizer must still work using the default provider. Otherwise
    a DB hiccup would brick the agent loop."""
    monkeypatch.delenv("COMPACTION_PROVIDER", raising=False)
    fake_adapter = _adapter_returning("summary text")

    with patch(
        "app.db.supabase_client.get_async_supabase_admin",
        side_effect=RuntimeError("supabase down"),
    ), patch(
        "app.services.ai.adapters.factory.get_adapter", return_value=fake_adapter
    ) as mock_factory:
        result = await summarize(_make_msgs())

    args, _ = mock_factory.call_args
    assert args[0] == DEFAULT_COMPACTION_PROVIDER
    assert result == "summary text"


async def test_empty_messages_returns_empty(monkeypatch):
    """No-op short-circuit: don't pay for an LLM call when there's
    nothing to summarize."""
    monkeypatch.setenv("COMPACTION_PROVIDER", "claude-haiku-4-5")
    with patch("app.services.ai.adapters.factory.get_adapter") as mock_factory:
        result = await summarize([])
    assert result == ""
    mock_factory.assert_not_called()


async def test_provider_timeout_raises_runtime_error(monkeypatch):
    """A slow provider must NOT block the main agent's turn beyond
    SUMMARIZE_TIMEOUT_S. Compactor catches RuntimeError + falls back."""
    monkeypatch.setenv("COMPACTION_PROVIDER", "claude-haiku-4-5")

    async def hang(*_a, **_kw):
        import asyncio
        await asyncio.sleep(SUMMARIZE_TIMEOUT_S * 2)

    fake_adapter = AsyncMock()
    fake_adapter.call = hang

    with patch(
        "app.services.ai.adapters.factory.get_adapter", return_value=fake_adapter
    ), patch(
        "app.agent_framework.summarizer.SUMMARIZE_TIMEOUT_S", 0.05
    ):
        with pytest.raises(RuntimeError, match="timed out"):
            await summarize(_make_msgs())


async def test_provider_returns_empty_raises(monkeypatch):
    """Empty / whitespace-only response is treated as a provider
    failure — prevents a downstream agent from getting an empty
    [Earlier conversation summary] block."""
    monkeypatch.setenv("COMPACTION_PROVIDER", "claude-haiku-4-5")
    fake_adapter = _adapter_returning("")

    with patch(
        "app.services.ai.adapters.factory.get_adapter", return_value=fake_adapter
    ):
        with pytest.raises(RuntimeError, match="empty content"):
            await summarize(_make_msgs())


async def test_unsupported_provider_raises(monkeypatch):
    """Misconfigured admin (typoed model name) should fail loudly,
    not silently fall through. compactor catches RuntimeError and
    still has its emergency-cap fallback so the agent keeps working."""
    monkeypatch.setenv("COMPACTION_PROVIDER", "bogus-model-name")

    with patch(
        "app.services.ai.adapters.factory.get_adapter",
        side_effect=ValueError("unsupported"),
    ):
        with pytest.raises(RuntimeError, match="unsupported provider"):
            await summarize(_make_msgs())


async def test_system_prompt_demands_id_preservation():
    """Pin the prompt's #1 rule. If a future PR loosens the "preserve
    IDs / URLs / paths" requirement, the head summary will start losing
    file references the agent then can't look up. Treat this as a
    contract."""
    assert "URL" in SUMMARIZE_SYSTEM_PROMPT
    assert "file path" in SUMMARIZE_SYSTEM_PROMPT
    assert "ID" in SUMMARIZE_SYSTEM_PROMPT


async def test_call_passes_flattened_text(monkeypatch):
    """Verify the user message handed to the adapter contains the
    flattened conversation text. The summarizer prompt operates on
    text — if it ever started receiving structured messages by accident
    the cheap model would refuse / confuse."""
    monkeypatch.setenv("COMPACTION_PROVIDER", "claude-haiku-4-5")
    fake_adapter = _adapter_returning("ok")

    with patch(
        "app.services.ai.adapters.factory.get_adapter", return_value=fake_adapter
    ):
        await summarize(_make_msgs())

    fake_adapter.call.assert_awaited_once()
    _composed, msgs = fake_adapter.call.await_args.args
    assert len(msgs) == 1 and msgs[0]["role"] == "user"
    body = msgs[0]["content"]
    assert "/tmp/foo.txt" in body  # path preserved into the prompt
    assert "7637245285990747435" in body  # ID preserved into the prompt
