"""Tests for the LLM-driven head summarizer (Phase 2 of #199).

Pin the data-shape contract between summarizer and compactor:
  - return value is a non-empty string (the summary text)
  - provider routes via ``ai_provider_helpers.resolve_db_adapter`` so a new
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
    # The real wire shape. `{"content": ...}` — what this mock used to
    # return — is a shape no adapter has ever produced, and it made every
    # test here pass while production raised on every call.
    fake_adapter.call = AsyncMock(
        return_value={
            "choices": [
                {
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ]
        }
    )
    return fake_adapter


def _read_scope_returning_scalar(value):
    """A read_scope() stand-in whose session.execute().scalar() returns
    ``value`` — the summarizer reads compaction_provider as a single scalar."""
    from contextlib import asynccontextmanager

    class _Res:
        def scalar(self):
            return value

    class _Session:
        async def execute(self, *_a, **_kw):
            return _Res()

    @asynccontextmanager
    async def _scope():
        yield _Session()

    return _scope


async def test_default_provider_is_maintenance_model():
    """If no env override and no system_settings override, fall back to
    the maintenance-tier catalog default — a name that actually exists in
    the platform catalog (DB-only credentials; the old Haiku literal
    resolved to nothing). Pin it so a silent default change diffs loudly."""
    from app.services.ai.providers.ai_provider_helpers import (
        DEFAULT_MAINTENANCE_MODEL,
    )

    assert DEFAULT_MAINTENANCE_MODEL == "mediahub-doubao-seed-2-0-lite"


async def test_env_override_wins_over_system_settings(monkeypatch):
    """COMPACTION_PROVIDER env var is the CI / debug knob and must
    short-circuit the system_settings DB read entirely."""
    monkeypatch.setenv("COMPACTION_PROVIDER", "qwen-plus")
    fake_adapter = _adapter_returning("summary text")

    with (
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_db_adapter",
            new=AsyncMock(return_value=fake_adapter),
        ) as mock_factory,
        patch("app.db.supabase_client.get_async_supabase_admin") as mock_db,
    ):
        result = await summarize(_make_msgs())

    mock_db.assert_not_called()  # env override skipped DB
    mock_factory.assert_called_once()
    args, _ = mock_factory.call_args
    assert args[0] == "qwen-plus"
    assert result == "summary text"


async def test_system_settings_override_used_when_no_env(monkeypatch):
    """When env is empty, summarizer reads compaction_provider from
    system_settings via the ORM read_scope() session. That's the
    admin-facing config seam."""
    monkeypatch.delenv("COMPACTION_PROVIDER", raising=False)
    fake_adapter = _adapter_returning("summary text")

    import app.db.session as db_session_mod

    monkeypatch.setattr(
        db_session_mod,
        "read_scope",
        _read_scope_returning_scalar("claude-haiku-4-5"),
    )

    with patch(
        "app.services.ai.providers.ai_provider_helpers.resolve_db_adapter",
        new=AsyncMock(return_value=fake_adapter),
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

    with (
        patch(
            "app.db.supabase_client.get_async_supabase_admin",
            side_effect=RuntimeError("supabase down"),
        ),
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_db_adapter",
            new=AsyncMock(return_value=fake_adapter),
        ) as mock_factory,
    ):
        result = await summarize(_make_msgs())

    from app.services.ai.providers.ai_provider_helpers import (
        DEFAULT_MAINTENANCE_MODEL,
    )

    args, _ = mock_factory.call_args
    assert args[0] == DEFAULT_MAINTENANCE_MODEL
    assert result == "summary text"


async def test_empty_messages_returns_empty(monkeypatch):
    """No-op short-circuit: don't pay for an LLM call when there's
    nothing to summarize."""
    monkeypatch.setenv("COMPACTION_PROVIDER", "claude-haiku-4-5")
    with patch(
        "app.services.ai.providers.ai_provider_helpers.resolve_db_adapter",
        new=AsyncMock(),
    ) as mock_factory:
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

    with (
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_db_adapter",
            new=AsyncMock(return_value=fake_adapter),
        ),
        patch("app.agent_framework.summarizer.SUMMARIZE_TIMEOUT_S", 0.05),
    ):
        with pytest.raises(RuntimeError, match="timed out"):
            await summarize(_make_msgs())


async def test_provider_returns_empty_raises(monkeypatch):
    """Empty / whitespace-only response is treated as a provider
    failure — prevents a downstream agent from getting an empty
    [Earlier conversation summary] block.

    The message must blame the MODEL, not the response shape: a well-formed
    envelope carrying no text is a provider outcome, while a malformed
    envelope is our own bug. Conflating them is what hid the flat-dict
    misread for months, so the wording is part of the contract here."""
    monkeypatch.setenv("COMPACTION_PROVIDER", "claude-haiku-4-5")
    fake_adapter = _adapter_returning("")

    with patch(
        "app.services.ai.providers.ai_provider_helpers.resolve_db_adapter",
        new=AsyncMock(return_value=fake_adapter),
    ):
        with pytest.raises(RuntimeError, match="empty text") as ei:
            await summarize(_make_msgs())
    assert "choices" not in str(ei.value), (
        "a well-formed envelope with no text must not be reported as a shape "
        f"problem: {ei.value}"
    )


async def test_unsupported_provider_raises(monkeypatch):
    """Misconfigured admin (typoed model name) should fail loudly,
    not silently fall through. compactor catches RuntimeError and
    still has its emergency-cap fallback so the agent keeps working."""
    monkeypatch.setenv("COMPACTION_PROVIDER", "bogus-model-name")

    with patch(
        "app.services.ai.providers.ai_provider_helpers.resolve_db_adapter",
        new=AsyncMock(side_effect=ValueError("unsupported")),
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
        "app.services.ai.providers.ai_provider_helpers.resolve_db_adapter",
        new=AsyncMock(return_value=fake_adapter),
    ):
        await summarize(_make_msgs())

    fake_adapter.call.assert_awaited_once()
    _composed, msgs = fake_adapter.call.await_args.args
    assert len(msgs) == 1 and msgs[0]["role"] == "user"
    body = msgs[0]["content"]
    assert "/tmp/foo.txt" in body  # path preserved into the prompt
    assert "7637245285990747435" in body  # ID preserved into the prompt
