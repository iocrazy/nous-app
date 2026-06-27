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
# _consolidate_context: non-dup draft is written (personal scope)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidate_pair_writes_non_dup_drafts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-dup draft flows through write_memory_row with scope='agent_user'.

    Updated for C0: tests _consolidate_context directly (the unit that writes)
    rather than _consolidate_pair (which now enumerates contexts and delegates).
    Personal context (team_id=None, project_id=None) → scope='agent_user'.
    """
    from app.workflows.consolidate_agent_memory import (
        MIN_NEW_MESSAGES,
        _consolidate_context,
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
        team_id=None,
        project_id=None,
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

    result = await _consolidate_context("u1", "a1", team_id=None, project_id=None)

    assert result["written"] == 1
    assert result["skipped"] == 0
    assert len(written_calls) == 1
    kw = written_calls[0]
    assert kw["scope"] == "agent_user"
    assert kw["owner_user_id"] == "u1"
    assert kw["agent_id"] == "a1"

    # Assert fingerprint passthrough — must equal make_fingerprint("u1", "a1", draft)
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
# _consolidate_context: dup fingerprint is skipped (write_memory_row not called)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidate_pair_skips_dup_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A draft whose fingerprint already exists must NOT be written.

    Updated for C0: tests _consolidate_context (the direct writer).
    """
    from app.services.ai.memory.agent_memory_consolidation import (
        MemoryDraft,
        make_fingerprint,
    )
    from app.workflows.consolidate_agent_memory import (
        MIN_NEW_MESSAGES,
        _consolidate_context,
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

    result = await _consolidate_context("u1", "a1", team_id=None, project_id=None)

    # consolidate_pair returns [] after dedup → loop has 0 iterations
    assert result["written"] == 0
    # write_memory_row must NOT have been called for the dup
    assert len(written_calls) == 0


# ---------------------------------------------------------------------------
# _consolidate_context: too few messages → early return, no write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidate_pair_too_few_messages_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contexts below MIN_NEW_MESSAGES skip consolidation entirely.

    Updated for C0: tests _consolidate_context (the direct writer).
    """
    from app.workflows.consolidate_agent_memory import (
        MIN_NEW_MESSAGES,
        _consolidate_context,
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

    result = await _consolidate_context("u1", "a1", team_id=None, project_id=None)

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
        new=AsyncMock(return_value={"written": 2, "skipped": 1, "contexts": 1}),
    ):
        auth = MagicMock()
        result = await trigger_consolidation(
            ConsolidateRequest(user_id="u1", agent_id="a1"), auth
        )

    assert result.written == 2
    assert result.skipped == 1
    assert result.contexts == 1


# ---------------------------------------------------------------------------
# Phase C0 (Task 2): _consolidate_context — scope derivation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidate_context_project_scope_writes_with_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_consolidate_context with project_id=55, team_id=10 writes scope='project'."""
    from app.workflows.consolidate_agent_memory import (
        MIN_NEW_MESSAGES,
        _consolidate_context,
    )

    messages = [
        {"role": "user", "content": f"msg {i}"} for i in range(MIN_NEW_MESSAGES)
    ]

    written_calls: list[dict] = []
    call_idx = [0]

    async def fake_fetch_all(sql: str, params=None) -> list:
        idx = call_idx[0]
        call_idx[0] += 1
        if idx == 0:
            return messages
        return []

    async def fake_existing_fps(
        *, owner_user_id: str, agent_id: str, scope: str, team_id, project_id
    ) -> set:
        return set()

    # project scope now uses write_memory_row_returning_id — returns memory id
    async def fake_write_returning_id(
        *,
        owner_user_id: str,
        agent_id: str,
        scope: str,
        kind: str,
        title: str,
        body_md: str,
        when_to_use: str,
        fingerprint: str,
        team_id=None,
        project_id=None,
    ):
        written_calls.append(
            {
                "scope": scope,
                "team_id": team_id,
                "project_id": project_id,
                "owner_user_id": owner_user_id,
                "agent_id": agent_id,
            }
        )
        return 1  # return a fake memory id

    async def fake_consolidator(prompt: str, model: str = "") -> str:
        return (
            '[{"title":"Deploy service","body_md":"kubectl apply",'
            '"when_to_use":"when deploying","kind":"procedure"}]'
        )

    # gate functions return None so no proposal is created (keeps test focused)
    async def fake_resolve(*args, **kwargs):
        return None

    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.existing_fingerprints",
        fake_existing_fps,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.write_memory_row_returning_id",
        fake_write_returning_id,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.default_consolidator",
        fake_consolidator,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.resolve_promotion_target",
        fake_resolve,
    )

    result = await _consolidate_context("u1", "a1", team_id=10, project_id=55)

    assert result["written"] == 1
    assert result["skipped"] == 0
    assert result.get("proposed", 0) == 0
    assert len(written_calls) == 1
    kw = written_calls[0]
    assert kw["scope"] == "project"
    assert kw["team_id"] == 10
    assert kw["project_id"] == 55


@pytest.mark.asyncio
async def test_consolidate_context_team_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_consolidate_context with team_id=10, project_id=None writes scope='team'."""
    from app.workflows.consolidate_agent_memory import (
        MIN_NEW_MESSAGES,
        _consolidate_context,
    )

    messages = [
        {"role": "user", "content": f"msg {i}"} for i in range(MIN_NEW_MESSAGES)
    ]

    written_calls: list[dict] = []
    call_idx = [0]

    async def fake_fetch_all(sql: str, params=None) -> list:
        idx = call_idx[0]
        call_idx[0] += 1
        if idx == 0:
            return messages
        return []

    async def fake_existing_fps(
        *, owner_user_id: str, agent_id: str, scope: str, team_id, project_id
    ) -> set:
        return set()

    # team scope now uses write_memory_row_returning_id — returns memory id
    async def fake_write_returning_id(
        *,
        owner_user_id: str,
        agent_id: str,
        scope: str,
        kind: str,
        title: str,
        body_md: str,
        when_to_use: str,
        fingerprint: str,
        team_id=None,
        project_id=None,
    ):
        written_calls.append(
            {"scope": scope, "team_id": team_id, "project_id": project_id}
        )
        return 2  # return a fake memory id

    async def fake_consolidator(prompt: str, model: str = "") -> str:
        return (
            '[{"title":"Team meeting","body_md":"notes",'
            '"when_to_use":"team context","kind":"fact"}]'
        )

    # gate returns None so no proposal (keeps test focused on write behaviour)
    async def fake_resolve(*args, **kwargs):
        return None

    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.existing_fingerprints",
        fake_existing_fps,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.write_memory_row_returning_id",
        fake_write_returning_id,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.default_consolidator",
        fake_consolidator,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.resolve_promotion_target",
        fake_resolve,
    )

    result = await _consolidate_context("u1", "a1", team_id=10, project_id=None)

    assert result["written"] == 1
    assert result.get("proposed", 0) == 0
    assert len(written_calls) == 1
    kw = written_calls[0]
    assert kw["scope"] == "team"
    assert kw["team_id"] == 10
    assert kw["project_id"] is None


@pytest.mark.asyncio
async def test_consolidate_context_personal_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_consolidate_context with both None writes scope='agent_user' + NULL ids."""
    from app.workflows.consolidate_agent_memory import (
        MIN_NEW_MESSAGES,
        _consolidate_context,
    )

    messages = [
        {"role": "user", "content": f"msg {i}"} for i in range(MIN_NEW_MESSAGES)
    ]

    written_calls: list[dict] = []
    call_idx = [0]

    async def fake_fetch_all(sql: str, params=None) -> list:
        idx = call_idx[0]
        call_idx[0] += 1
        if idx == 0:
            return messages
        return []

    async def fake_existing_fps(
        *, owner_user_id: str, agent_id: str, scope: str, team_id, project_id
    ) -> set:
        return set()

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
        team_id=None,
        project_id=None,
    ) -> bool:
        written_calls.append(
            {"scope": scope, "team_id": team_id, "project_id": project_id}
        )
        return True

    async def fake_consolidator(prompt: str, model: str = "") -> str:
        return (
            '[{"title":"Personal note","body_md":"body",'
            '"when_to_use":"personal","kind":"fact"}]'
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

    result = await _consolidate_context("u1", "a1", team_id=None, project_id=None)

    assert result["written"] == 1
    assert len(written_calls) == 1
    kw = written_calls[0]
    assert kw["scope"] == "agent_user"
    assert kw["team_id"] is None
    assert kw["project_id"] is None


# ---------------------------------------------------------------------------
# Phase C0 (Task 2): enumerate_active_pairs_step — context columns returned
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enumerate_active_pairs_includes_context_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """enumerate_active_pairs_step passes through team_id / project_id from SQL."""
    from app.workflows.consolidate_agent_memory import (
        MIN_NEW_MESSAGES,
        enumerate_active_pairs_step,
    )

    rows = [
        {
            "user_id": "u1",
            "agent_id": "a1",
            "team_id": 10,
            "project_id": 55,
            "msg_count": MIN_NEW_MESSAGES,
        },
        {
            "user_id": "u2",
            "agent_id": "a2",
            "team_id": None,
            "project_id": None,
            "msg_count": MIN_NEW_MESSAGES + 3,
        },
        # Below threshold — filtered out
        {
            "user_id": "u3",
            "agent_id": "a3",
            "team_id": 99,
            "project_id": None,
            "msg_count": MIN_NEW_MESSAGES - 1,
        },
    ]

    async def fake_fetch_all(sql: str, params=None) -> list:
        return rows

    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)

    pairs = await enumerate_active_pairs_step()

    assert len(pairs) == 2
    pair_map = {p["user_id"]: p for p in pairs}
    assert pair_map["u1"]["team_id"] == 10
    assert pair_map["u1"]["project_id"] == 55
    assert pair_map["u2"]["team_id"] is None
    assert pair_map["u2"]["project_id"] is None
    assert "u3" not in pair_map


# ---------------------------------------------------------------------------
# Phase C0 (Task 2): _consolidate_pair — enumerates contexts and sums results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidate_pair_enumerates_contexts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_consolidate_pair queries contexts for the pair and consolidates each."""
    from app.workflows.consolidate_agent_memory import _consolidate_pair

    # Two contexts for the same (u1, a1) pair
    context_rows = [
        {"team_id": None, "project_id": None},
        {"team_id": 10, "project_id": 55},
    ]

    context_calls: list[tuple] = []

    async def fake_fetch_all(sql: str, params=None) -> list:
        return context_rows

    async def fake_consolidate_context(
        user_id: str, agent_id: str, team_id, project_id
    ) -> dict:
        context_calls.append((user_id, agent_id, team_id, project_id))
        return {"written": 1, "skipped": 0}

    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory._consolidate_context",
        fake_consolidate_context,
    )

    result = await _consolidate_pair("u1", "a1")

    assert result["contexts"] == 2
    assert result["written"] == 2
    assert result["skipped"] == 0
    assert len(context_calls) == 2


# ---------------------------------------------------------------------------
# Phase C0 (Task 2): admin endpoint maps contexts field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_consolidation_endpoint_maps_contexts() -> None:
    """Admin endpoint ConsolidateResponse includes contexts count."""
    from app.api.admin.settings_router import trigger_consolidation
    from app.schemas.admin import ConsolidateRequest

    with patch(
        "app.workflows.consolidate_agent_memory._consolidate_pair",
        new=AsyncMock(return_value={"written": 3, "skipped": 0, "contexts": 2}),
    ):
        auth = MagicMock()
        result = await trigger_consolidation(
            ConsolidateRequest(user_id="u1", agent_id="a1"), auth
        )

    assert result.written == 3
    assert result.skipped == 0
    assert result.contexts == 2


# ---------------------------------------------------------------------------
# Phase C0 (Task 2 — Important finding): _EXISTING_TITLES_SQL must be
# context-scoped so project/team consolidation runs do NOT see titles from
# the user's personal (or another team's) memories as already covered.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_titles_query_is_context_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SECOND fetch_all call (existing-titles query) must carry
    team_id=10 and project_id=55 in its params dict when called with a
    project context.  Without the fix the params only had user_id/agent_id,
    causing cross-context title bleed.
    """
    from app.workflows.consolidate_agent_memory import (
        MIN_NEW_MESSAGES,
        _consolidate_context,
    )

    messages = [
        {"role": "user", "content": f"msg {i}"} for i in range(MIN_NEW_MESSAGES)
    ]

    # Capture every (sql, params) pair passed to db_engine.fetch_all.
    fetch_calls: list[dict] = []
    call_idx = [0]

    async def fake_fetch_all(sql: str, params=None) -> list:
        fetch_calls.append({"sql": sql, "params": dict(params) if params else {}})
        idx = call_idx[0]
        call_idx[0] += 1
        if idx == 0:
            return messages  # first call = recent messages
        return []  # second call = existing titles (what we're testing)

    async def fake_existing_fps(
        *, owner_user_id: str, agent_id: str, scope: str, team_id, project_id
    ) -> set:
        return set()

    # project scope uses write_memory_row_returning_id
    async def fake_write_returning_id(**kwargs):
        return 1

    async def fake_consolidator(prompt: str, model: str = "") -> str:
        return (
            '[{"title":"Project tip","body_md":"body",'
            '"when_to_use":"in project","kind":"fact"}]'
        )

    async def fake_resolve(*args, **kwargs):
        return None

    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.existing_fingerprints",
        fake_existing_fps,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.write_memory_row_returning_id",
        fake_write_returning_id,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.default_consolidator",
        fake_consolidator,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.resolve_promotion_target",
        fake_resolve,
    )

    await _consolidate_context("u1", "a1", team_id=10, project_id=55)

    # Must have made exactly 2 fetch_all calls:
    #   [0] = _RECENT_MESSAGES_SQL  (message load)
    #   [1] = _EXISTING_TITLES_SQL  (title context for /dream prompt)
    assert len(fetch_calls) == 2, (
        f"Expected 2 fetch_all calls but got {len(fetch_calls)}: "
        f"{[c['sql'][:40] for c in fetch_calls]}"
    )

    title_call_params = fetch_calls[1]["params"]

    assert title_call_params.get("team_id") == 10, (
        f"_EXISTING_TITLES_SQL must be scoped by team_id=10; "
        f"got params={title_call_params}"
    )
    assert title_call_params.get("project_id") == 55, (
        f"_EXISTING_TITLES_SQL must be scoped by project_id=55; "
        f"got params={title_call_params}"
    )


# ---------------------------------------------------------------------------
# Phase C1 (Task 3): promotion proposal wiring — team-scope context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidate_context_team_scope_creates_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_consolidate_context with team-scope context calls insert_proposal once.

    Conditions:
    - team_id=10, project_id=None → scope='team'
    - write_memory_row_returning_id succeeds → returns memory_id=42
    - resolve_promotion_target → (10, None)
    - evaluate_promotion → PromotionVerdict(shareable=True, confidence=0.9, ...)
    - insert_proposal called once with memory_id=42, proposed_scope='team',
      target_team_id=10, scrubbed_body_md from verdict
    - result["proposed"] == 1
    """
    from unittest.mock import AsyncMock

    from app.services.ai.memory.promotion_gate import PromotionVerdict
    from app.workflows.consolidate_agent_memory import (
        MIN_NEW_MESSAGES,
        _consolidate_context,
    )

    messages = [
        {"role": "user", "content": f"msg {i}"} for i in range(MIN_NEW_MESSAGES)
    ]
    call_idx = [0]

    async def fake_fetch_all(sql: str, params=None) -> list:
        idx = call_idx[0]
        call_idx[0] += 1
        if idx == 0:
            return messages
        return []

    async def fake_existing_fps(
        *, owner_user_id: str, agent_id: str, scope: str, team_id, project_id
    ) -> set:
        return set()

    # write_memory_row_returning_id returns the new memory_id=42
    async def fake_write_returning_id(
        *,
        owner_user_id: str,
        agent_id: str,
        scope: str,
        kind: str,
        title: str,
        body_md: str,
        when_to_use: str,
        fingerprint: str,
        team_id=None,
        project_id=None,
    ):
        return 42

    async def fake_consolidator(prompt: str, model: str = "") -> str:
        return (
            '[{"title":"Team deploy","body_md":"kubectl apply -n team",'
            '"when_to_use":"team deployments","kind":"procedure"}]'
        )

    verdict = PromotionVerdict(
        shareable=True,
        confidence=0.9,
        justification="Durable team procedure.",
        scrubbed_body_md="kubectl apply -n team",
    )
    mock_resolve = AsyncMock(return_value=(10, None))
    mock_evaluate = AsyncMock(return_value=verdict)
    mock_insert = AsyncMock(return_value=True)

    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.existing_fingerprints",
        fake_existing_fps,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.write_memory_row_returning_id",
        fake_write_returning_id,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.default_consolidator",
        fake_consolidator,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.resolve_promotion_target",
        mock_resolve,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.evaluate_promotion",
        mock_evaluate,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.insert_proposal",
        mock_insert,
    )

    result = await _consolidate_context("u1", "a1", team_id=10, project_id=None)

    assert result["written"] == 1
    assert result["proposed"] == 1

    # insert_proposal must have been called exactly once with the right args
    mock_insert.assert_called_once()
    call_kwargs = mock_insert.call_args.kwargs
    assert call_kwargs["memory_id"] == 42
    assert call_kwargs["proposed_scope"] == "team"
    assert call_kwargs["target_team_id"] == 10
    assert call_kwargs["target_project_id"] is None
    assert call_kwargs["scrubbed_body_md"] == verdict.scrubbed_body_md
    assert call_kwargs["confidence"] == verdict.confidence
    assert call_kwargs["justification"] == verdict.justification

    # resolve_promotion_target must have been called with correct args
    mock_resolve.assert_called_once_with(
        owner_user_id="u1",
        scope="team",
        team_id=10,
        project_id=None,
    )


# ---------------------------------------------------------------------------
# Phase C1 (Task 3): isolation invariant — personal context NEVER hits gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidate_context_personal_scope_never_runs_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Personal-scope (team_id=None, project_id=None) must NEVER call
    resolve_promotion_target, evaluate_promotion, or insert_proposal.

    This is the cross-tenant isolation invariant: agent_user memories are
    always private and must never enter the promotion pipeline.
    """
    from unittest.mock import AsyncMock

    from app.workflows.consolidate_agent_memory import (
        MIN_NEW_MESSAGES,
        _consolidate_context,
    )

    messages = [
        {"role": "user", "content": f"msg {i}"} for i in range(MIN_NEW_MESSAGES)
    ]
    call_idx = [0]

    async def fake_fetch_all(sql: str, params=None) -> list:
        idx = call_idx[0]
        call_idx[0] += 1
        if idx == 0:
            return messages
        return []

    async def fake_existing_fps(
        *, owner_user_id: str, agent_id: str, scope: str, team_id, project_id
    ) -> set:
        return set()

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
        team_id=None,
        project_id=None,
    ) -> bool:
        return True

    async def fake_consolidator(prompt: str, model: str = "") -> str:
        return (
            '[{"title":"Personal note","body_md":"body",'
            '"when_to_use":"personal","kind":"fact"}]'
        )

    mock_resolve = AsyncMock()
    mock_evaluate = AsyncMock()
    mock_insert = AsyncMock()

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
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.resolve_promotion_target",
        mock_resolve,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.evaluate_promotion",
        mock_evaluate,
    )
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory.insert_proposal",
        mock_insert,
    )

    result = await _consolidate_context("u1", "a1", team_id=None, project_id=None)

    assert result["written"] == 1
    assert result.get("proposed", 0) == 0

    # Isolation invariant: no gate functions may be called for agent_user scope
    mock_resolve.assert_not_called()
    mock_evaluate.assert_not_called()
    mock_insert.assert_not_called()


# ---------------------------------------------------------------------------
# Phase C1 (Task 3): _consolidate_pair sums proposed count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidate_pair_sums_proposed_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_consolidate_pair sums the proposed field from each context result."""
    from app.workflows.consolidate_agent_memory import _consolidate_pair

    context_rows = [
        {"team_id": 10, "project_id": None},
        {"team_id": None, "project_id": None},
    ]

    call_count = [0]

    async def fake_fetch_all(sql: str, params=None) -> list:
        return context_rows

    async def fake_consolidate_context(
        user_id: str, agent_id: str, team_id, project_id
    ) -> dict:
        call_count[0] += 1
        if team_id == 10:
            return {"written": 1, "skipped": 0, "proposed": 1}
        return {"written": 1, "skipped": 0, "proposed": 0}

    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)
    monkeypatch.setattr(
        "app.workflows.consolidate_agent_memory._consolidate_context",
        fake_consolidate_context,
    )

    result = await _consolidate_pair("u1", "a1")

    assert result["written"] == 2
    assert result["skipped"] == 0
    assert result["contexts"] == 2
    assert result["proposed"] == 1


# ---------------------------------------------------------------------------
# Phase C1 (Task 3): ConsolidateResponse includes proposed field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_consolidation_endpoint_maps_proposed() -> None:
    """Admin endpoint maps proposed from _consolidate_pair result."""
    from app.api.admin.settings_router import trigger_consolidation
    from app.schemas.admin import ConsolidateRequest

    with patch(
        "app.workflows.consolidate_agent_memory._consolidate_pair",
        new=AsyncMock(
            return_value={"written": 2, "skipped": 0, "contexts": 1, "proposed": 1}
        ),
    ):
        auth = MagicMock()
        result = await trigger_consolidation(
            ConsolidateRequest(user_id="u1", agent_id="a1"), auth
        )

    assert result.written == 2
    assert result.skipped == 0
    assert result.contexts == 1
    assert result.proposed == 1


# ---------------------------------------------------------------------------
# Phase C1 (Task 3): write_memory_row_returning_id repository unit test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_memory_row_returning_id_returns_new_id() -> None:
    """write_memory_row_returning_id executes INSERT RETURNING id and returns int."""
    from unittest.mock import patch

    class _ScalarResult:
        def __init__(self, val):
            self._val = val

        def scalar_one_or_none(self):
            return self._val

    class _Session:
        def __init__(self):
            self.calls = []

        async def execute(self, stmt, params=None):
            self.calls.append({"sql": str(stmt), "params": params})
            return _ScalarResult(99)

    class _Scope:
        def __init__(self, session):
            self._session = session

        async def __aenter__(self):
            return self._session

        async def __aexit__(self, *a):
            return False

    session = _Session()

    with patch(
        "app.repositories.agent_memory_repository.write_scope",
        return_value=_Scope(session),
    ):
        from app.repositories.agent_memory_repository import (
            write_memory_row_returning_id,
        )

        result = await write_memory_row_returning_id(
            owner_user_id="u1",
            agent_id="a1",
            scope="team",
            kind="fact",
            title="Test title",
            body_md="body text",
            when_to_use="in team context",
            fingerprint="fp-abc",
            team_id=10,
            project_id=None,
        )

    assert result == 99
    assert len(session.calls) == 1
    sql_upper = session.calls[0]["sql"].upper()
    assert "INSERT" in sql_upper
    assert "RETURNING" in sql_upper
    params = session.calls[0]["params"]
    assert params["owner_user_id"] == "u1"
    assert params["scope"] == "team"
    assert params["team_id"] == 10
    assert params["fingerprint"] == "fp-abc"


@pytest.mark.asyncio
async def test_write_memory_row_returning_id_returns_none_on_error() -> None:
    """write_memory_row_returning_id returns None on any exception (never raises)."""
    from unittest.mock import patch

    with patch(
        "app.repositories.agent_memory_repository.write_scope",
        side_effect=RuntimeError("db gone"),
    ):
        from app.repositories.agent_memory_repository import (
            write_memory_row_returning_id,
        )

        result = await write_memory_row_returning_id(
            owner_user_id="u1",
            agent_id="a1",
            scope="team",
            kind="fact",
            title="t",
            body_md="b",
            when_to_use="w",
            fingerprint="fp",
            team_id=5,
            project_id=None,
        )

    assert result is None
