"""Workflow-level tests for agent-memory consolidation (Phase B — Task 3).

Patches applied at the consolidate_agent_memory module boundary:
  - app.db.engine.fetch_all               (SQL: messages + existing titles)
  - app.workflows.consolidate_agent_memory.existing_fingerprints
  - app.workflows.consolidate_agent_memory.write_memory_row
  - app.workflows.consolidate_agent_memory.default_consolidator

All tests run without a real DB or LLM.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# _consolidate_pair: non-dup draft is written
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidate_pair_writes_non_dup_drafts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-dup draft flows through to write_memory_row with scope='agent_user'."""
    from app.workflows.consolidate_agent_memory import (
        MIN_NEW_MESSAGES,
        _consolidate_pair,
    )

    # Enough messages to pass the cost guard
    messages = [
        {"role": "user", "content": f"msg {i}"} for i in range(MIN_NEW_MESSAGES)
    ]

    written_calls: list[dict] = []
    call_idx = [0]

    async def fake_fetch_all(sql: str, params=None) -> list:
        idx = call_idx[0]
        call_idx[0] += 1
        if idx == 0:
            return messages  # first call = recent messages
        return []  # second call = existing titles

    async def fake_existing_fps(
        *, owner_user_id: str, agent_id: str, scope: str, team_id, project_id
    ) -> set:
        return set()  # nothing stored yet

    async def fake_write(
        *,
        owner_user_id: str,
        agent_id: str,
        scope: str,
        kind: str,
        title: str,
        body_md: str,
        when_to_use: str,
        fingerprint: str,
    ) -> bool:
        written_calls.append(
            {
                "owner_user_id": owner_user_id,
                "agent_id": agent_id,
                "scope": scope,
                "kind": kind,
                "title": title,
                "fingerprint": fingerprint,
            }
        )
        return True

    async def fake_consolidator(prompt: str, model: str = "") -> str:
        return (
            '[{"title":"Deploy service","body_md":"kubectl apply",'
            '"when_to_use":"when deploying","kind":"procedure"}]'
        )

    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.existing_fingerprints",
        fake_existing_fps,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.write_memory_row",
        fake_write,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.default_consolidator",
        fake_consolidator,
    )

    result = await _consolidate_pair("u1", "a1")

    assert result["written"] == 1
    assert result["skipped"] == 0
    assert len(written_calls) == 1
    kw = written_calls[0]
    assert kw["scope"] == "agent_user"
    assert kw["owner_user_id"] == "u1"
    assert kw["agent_id"] == "a1"

    # Fix 2: assert fingerprint passthrough — must equal make_fingerprint("u1", "a1", draft)
    from app.services.ai.memory.agent_memory_consolidation import (
        MemoryDraft,
        make_fingerprint,
    )

    expected_draft = MemoryDraft(
        title="Deploy service",
        body_md="kubectl apply",
        when_to_use="when deploying",
        kind="procedure",
    )
    expected_fp = make_fingerprint("u1", "a1", "agent_user", "", expected_draft)
    assert kw["fingerprint"] == expected_fp


# ---------------------------------------------------------------------------
# _consolidate_pair: dup fingerprint is skipped (write_memory_row not called)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidate_pair_skips_dup_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A draft whose fingerprint already exists must NOT be written."""
    from app.services.ai.memory.agent_memory_consolidation import (
        MemoryDraft,
        make_fingerprint,
    )
    from app.workflows.consolidate_agent_memory import (
        MIN_NEW_MESSAGES,
        _consolidate_pair,
    )

    messages = [
        {"role": "user", "content": f"msg {i}"} for i in range(MIN_NEW_MESSAGES)
    ]

    # Pre-compute the fingerprint of the draft the LLM will return
    dup_draft = MemoryDraft(
        title="Deploy service",
        body_md="kubectl apply",
        when_to_use="when deploying",
        kind="procedure",
    )
    dup_fp = make_fingerprint("u1", "a1", "agent_user", "", dup_draft)

    written_calls: list = []
    call_idx = [0]

    async def fake_fetch_all(sql: str, params=None) -> list:
        idx = call_idx[0]
        call_idx[0] += 1
        return messages if idx == 0 else []

    async def fake_existing_fps(
        *, owner_user_id: str, agent_id: str, scope: str, team_id, project_id
    ) -> set:
        return {dup_fp}  # already stored — should be deduped

    async def fake_write(**kwargs) -> bool:
        written_calls.append(kwargs)
        return True

    async def fake_consolidator(prompt: str, model: str = "") -> str:
        return (
            '[{"title":"Deploy service","body_md":"kubectl apply",'
            '"when_to_use":"when deploying","kind":"procedure"}]'
        )

    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.existing_fingerprints",
        fake_existing_fps,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.write_memory_row",
        fake_write,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.default_consolidator",
        fake_consolidator,
    )

    result = await _consolidate_pair("u1", "a1")

    # consolidate_pair returns [] after dedup → loop has 0 iterations
    assert result["written"] == 0
    # write_memory_row must NOT have been called for the dup
    assert len(written_calls) == 0


# ---------------------------------------------------------------------------
# _consolidate_pair: too few messages → early return, no write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidate_pair_too_few_messages_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pairs below MIN_NEW_MESSAGES skip consolidation entirely."""
    from app.workflows.consolidate_agent_memory import (
        MIN_NEW_MESSAGES,
        _consolidate_pair,
    )

    # One fewer message than the threshold
    few_messages = [{"role": "user", "content": "hi"}] * (MIN_NEW_MESSAGES - 1)

    write_called = [False]

    async def fake_fetch_all(sql: str, params=None) -> list:
        return few_messages

    async def fake_write(**kwargs) -> bool:
        write_called[0] = True
        return True

    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.write_memory_row",
        fake_write,
    )

    result = await _consolidate_pair("u1", "a1")

    assert result["written"] == 0
    assert not write_called[0]


# ---------------------------------------------------------------------------
# enumerate_active_pairs_step: MIN_NEW_MESSAGES filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enumerate_active_pairs_filters_by_min_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """enumerate_active_pairs_step returns only pairs with >= MIN_NEW_MESSAGES."""
    from app.workflows.consolidate_agent_memory import (
        MIN_NEW_MESSAGES,
        enumerate_active_pairs_step,
    )

    rows = [
        {"user_id": "u1", "agent_id": "a1", "msg_count": MIN_NEW_MESSAGES},
        {"user_id": "u2", "agent_id": "a2", "msg_count": MIN_NEW_MESSAGES - 1},
        {"user_id": "u3", "agent_id": "a3", "msg_count": MIN_NEW_MESSAGES + 5},
    ]

    async def fake_fetch_all(sql: str, params=None) -> list:
        return rows

    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)

    pairs = await enumerate_active_pairs_step()

    # Only u1 (exactly threshold) and u3 (above threshold) should pass
    assert len(pairs) == 2
    user_ids = {p["user_id"] for p in pairs}
    assert "u1" in user_ids
    assert "u3" in user_ids
    assert "u2" not in user_ids


# ---------------------------------------------------------------------------
# Task 4: admin trigger endpoint — trigger_consolidation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_consolidation_endpoint_returns_counts() -> None:
    """Admin endpoint delegates to _consolidate_pair and maps written/skipped."""
    from app.api.admin.settings_router import trigger_consolidation
    from app.schemas.admin import ConsolidateRequest

    with patch(
        "app.workflows.consolidate_agent_memory._consolidate_pair",
        new=AsyncMock(return_value={"written": 2, "skipped": 1}),
    ):
        auth = MagicMock()
        result = await trigger_consolidation(
            ConsolidateRequest(user_id="u1", agent_id="a1"), auth
        )

    assert result.written == 2
    assert result.skipped == 1
