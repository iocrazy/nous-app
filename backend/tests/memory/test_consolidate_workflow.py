"""Workflow-level tests for agent-memory consolidation (Phase B — Task 3).

ORM (Phase B4): the raw ``app.db.engine.fetch_all`` reads (messages +
existing titles + active pairs + pair contexts) moved to SQLAlchemy Core
through ``app.db.session.read_scope()``. The harness patches ``read_scope``
with a small recording/queueing fake session instead of the raw engine call —
each queued ``_FakeResult`` corresponds to one ``session.execute(...)`` call,
in the same order the legacy ``fake_fetch_all`` counter used to branch on.

Patches applied at the consolidate_agent_memory module boundary:
  - app.db.session.read_scope               (SQL: messages + existing titles)
  - app.workflows.consolidate_agent_memory.existing_fingerprints
  - app.workflows.consolidate_agent_memory.write_memory_row
  - app.workflows.consolidate_agent_memory.default_consolidator

All tests run without a real DB or LLM.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.db.session as db_session


class _FakeResult:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = rows if rows is not None else []

    def mappings(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows


class _RecordingSession:
    """Pops one queued ``_FakeResult`` per ``execute()`` call, in order —
    mirrors the legacy ``call_idx`` counter the raw ``fake_fetch_all``
    functions used, but driven by ``app.db.session.read_scope()`` instead."""

    def __init__(self, results: list[_FakeResult] | None = None) -> None:
        self.calls: list[Any] = []
        self._results = list(results or [])
        self._default = _FakeResult()

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(stmt)
        return self._results.pop(0) if self._results else self._default


def _patch_read_scope(
    monkeypatch: pytest.MonkeyPatch, results: list[_FakeResult] | None = None
) -> _RecordingSession:
    session = _RecordingSession(results)

    @asynccontextmanager
    async def fake_read_scope():
        yield session

    monkeypatch.setattr(db_session, "read_scope", fake_read_scope)
    return session


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

    _patch_read_scope(monkeypatch, [_FakeResult(rows=messages), _FakeResult(rows=[])])

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

    _patch_read_scope(monkeypatch, [_FakeResult(rows=messages), _FakeResult(rows=[])])

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

    _patch_read_scope(monkeypatch, [_FakeResult(rows=few_messages)])

    async def fake_write(**kwargs) -> bool:
        write_called[0] = True
        return True

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

    _patch_read_scope(monkeypatch, [_FakeResult(rows=rows)])

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

    _patch_read_scope(monkeypatch, [_FakeResult(rows=messages), _FakeResult(rows=[])])

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

    _patch_read_scope(monkeypatch, [_FakeResult(rows=messages), _FakeResult(rows=[])])

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

    _patch_read_scope(monkeypatch, [_FakeResult(rows=messages), _FakeResult(rows=[])])

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
    """enumerate_active_pairs_step passes through team_id / project_id from
    the query."""
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

    _patch_read_scope(monkeypatch, [_FakeResult(rows=rows)])

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

    _patch_read_scope(monkeypatch, [_FakeResult(rows=context_rows)])

    async def fake_consolidate_context(
        user_id: str, agent_id: str, team_id, project_id
    ) -> dict:
        context_calls.append((user_id, agent_id, team_id, project_id))
        return {"written": 1, "skipped": 0}

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
# Phase C0 (Task 2 — Important finding): the existing-titles read must be
# context-scoped so project/team consolidation runs do NOT see titles from
# the user's personal (or another team's) memories as already covered.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_titles_query_is_context_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SECOND read_scope call (existing-titles query) must carry
    team_id=10 and project_id=55 in its compiled params when called with a
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

    session = _patch_read_scope(
        monkeypatch, [_FakeResult(rows=messages), _FakeResult(rows=[])]
    )

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

    # Must have made exactly 2 read_scope().execute() calls:
    #   [0] = recent-messages statement (message load)
    #   [1] = existing-titles statement (title context for /dream prompt)
    assert (
        len(session.calls) == 2
    ), f"Expected 2 read_scope calls but got {len(session.calls)}"

    from sqlalchemy.dialects import postgresql

    title_stmt = session.calls[1]
    compiled = title_stmt.compile(dialect=postgresql.dialect())
    params = dict(compiled.params)

    assert (
        10 in params.values()
    ), f"existing-titles query must be scoped by team_id=10; got params={params}"
    assert (
        55 in params.values()
    ), f"existing-titles query must be scoped by project_id=55; got params={params}"


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

    _patch_read_scope(monkeypatch, [_FakeResult(rows=messages), _FakeResult(rows=[])])

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

    _patch_read_scope(monkeypatch, [_FakeResult(rows=messages), _FakeResult(rows=[])])

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

    _patch_read_scope(monkeypatch, [_FakeResult(rows=context_rows)])

    async def fake_consolidate_context(
        user_id: str, agent_id: str, team_id, project_id
    ) -> dict:
        if team_id == 10:
            return {"written": 1, "skipped": 0, "proposed": 1}
        return {"written": 1, "skipped": 0, "proposed": 0}

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
#
# Out of B4 Task 1 scope: agent_memory_repository.py itself is not one of
# the 6 migrated files, so this repository call still goes through its own
# write_scope() usage — untouched by this batch.
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
