"""conversation_memory_service — flag gating, block rendering, compaction."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.chat import conversation_memory_service as svc

CONV = {"id": 10, "scope_id": 99, "last_seq": 60}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_block_empty_when_flag_off(monkeypatch):
    monkeypatch.setattr(svc.settings, "FEATURE_GROUP_AGENT_MEMORY", False)
    out = await svc.build_memory_block(
        conversation=CONV, user_query="q", summoner_user_id="u1", agent={"id": "a1"}
    )
    assert out == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_block_renders_summary_and_memories(monkeypatch):
    monkeypatch.setattr(svc.settings, "FEATURE_GROUP_AGENT_MEMORY", True)
    monkeypatch.setattr(svc.settings, "FEATURE_AGENT_MEMORY", True)
    with (
        patch.object(
            svc,
            "get_conversation_memory_repository",
            return_value=AsyncMock(
                load=AsyncMock(
                    return_value={"summary_md": "OLD STUFF", "last_seq_summarized": 30}
                )
            ),
        ),
        patch.object(
            svc,
            "recall",
            AsyncMock(
                return_value=[
                    svc.MemoryHit(id=1, title="T", body_md="B", kind="fact", score=1.0)
                ]
            ),
        ),
    ):
        out = await svc.build_memory_block(
            conversation=CONV, user_query="q", summoner_user_id="u1", agent={"id": "a1"}
        )
    assert "## Conversation summary (older messages)" in out and "OLD STUFF" in out
    assert "## Relevant memories" in out and "fact: T — B" in out


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_block_never_raises(monkeypatch):
    monkeypatch.setattr(svc.settings, "FEATURE_GROUP_AGENT_MEMORY", True)
    with patch.object(
        svc,
        "get_conversation_memory_repository",
        side_effect=Exception("db down"),
    ):
        out = await svc.build_memory_block(
            conversation=CONV, user_query="q", summoner_user_id="u1", agent={"id": "a1"}
        )
    assert out == ""  # degraded, not raised


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_block_malformed_conversation_returns_empty(monkeypatch):
    monkeypatch.setattr(svc.settings, "FEATURE_GROUP_AGENT_MEMORY", True)
    out = await svc.build_memory_block(
        conversation={}, user_query="q", summoner_user_id="u1", agent={"id": "a1"}
    )
    assert out == ""  # never raises, degrades to empty


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compact_below_threshold_is_noop(monkeypatch):
    monkeypatch.setattr(svc.settings, "FEATURE_GROUP_AGENT_MEMORY", True)
    summarize = AsyncMock()
    with (
        patch.object(svc, "_summarize", summarize),
        patch.object(
            svc,
            "get_conversation_memory_repository",
            return_value=AsyncMock(
                load=AsyncMock(
                    return_value={"summary_md": "", "last_seq_summarized": 20}
                )
            ),
        ),
        patch.object(
            svc,
            "_fresh_conversation",
            AsyncMock(return_value={"id": 10, "last_seq": 60}),
        ),
    ):
        # 60 - 20 = 40 unsummarized < TRIGGER(30) + KEEP_TAIL(20) = 50 → no-op
        await svc.maybe_compact(conversation=CONV)
    summarize.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compact_above_threshold_upserts(monkeypatch):
    monkeypatch.setattr(svc.settings, "FEATURE_GROUP_AGENT_MEMORY", True)
    mem_repo = AsyncMock(
        load=AsyncMock(return_value={"summary_md": "PREV", "last_seq_summarized": 0})
    )
    conv_repo = AsyncMock(
        messages_in_range=AsyncMock(
            return_value=[
                {
                    "seq": 1,
                    "sender_type": "user",
                    "type": "text",
                    "body": {"text": "hi"},
                }
            ]
        )
    )
    with (
        patch.object(svc, "get_conversation_memory_repository", return_value=mem_repo),
        patch.object(svc, "get_conversation_repository", return_value=conv_repo),
        patch.object(
            svc,
            "_fresh_conversation",
            AsyncMock(return_value={"id": 10, "last_seq": 60}),
        ),
        patch.object(svc, "_summarize", AsyncMock(return_value="NEW SUMMARY")),
    ):
        # 60 - 0 = 60 unsummarized >= 50 → compact span 1..40
        await svc.maybe_compact(conversation=CONV)
    conv_repo.messages_in_range.assert_awaited_once_with(
        conversation_id=10, from_seq=1, to_seq=40
    )
    mem_repo.upsert.assert_awaited_once()
    kwargs = mem_repo.upsert.await_args.kwargs
    assert kwargs["last_seq_summarized"] == 40
    assert kwargs["summary_md"] == "NEW SUMMARY"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compact_summarizer_empty_means_no_upsert(monkeypatch):
    monkeypatch.setattr(svc.settings, "FEATURE_GROUP_AGENT_MEMORY", True)
    mem_repo = AsyncMock(load=AsyncMock(return_value=None))
    conv_repo = AsyncMock(
        messages_in_range=AsyncMock(
            return_value=[{"seq": 1, "sender_type": "user", "type": "text", "body": {}}]
        )
    )
    with (
        patch.object(svc, "get_conversation_memory_repository", return_value=mem_repo),
        patch.object(svc, "get_conversation_repository", return_value=conv_repo),
        patch.object(
            svc,
            "_fresh_conversation",
            AsyncMock(return_value={"id": 10, "last_seq": 60}),
        ),
        patch.object(svc, "_summarize", AsyncMock(return_value="")),
    ):
        await svc.maybe_compact(conversation=CONV)
    mem_repo.upsert.assert_not_awaited()
