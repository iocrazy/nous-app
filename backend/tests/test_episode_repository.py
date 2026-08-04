"""Unit tests for EpisodeRepository.create's sort_order assignment (pre-ship
review fix, PR-10b).

Before this fix every new episode landed at the ``episodes.sort_order`` DB
default of 0 unless the caller passed an explicit value, so a Move
up/Move down PATCH swap between two freshly-created episodes was a
same-value 0<->0 no-op — visually nothing moved. The fix: when the caller
omits ``sort_order``, the repository now assigns
``COALESCE(MAX(sort_order)+1, 1)`` for the project via a correlated
subquery baked into the same INSERT statement.

Mocks ``write_scope`` with a fake session that captures every emitted
(compiled sql, binds) pair, same fake-session shape as
``tests/test_agent_repository.py`` — no live database needed to assert the
SQL shape. The auto-Ep1 project-creation path (which always passes
``sort_order=1`` explicitly) is covered separately by
``tests/test_create_project_default_episode.py`` and is not re-tested
here beyond confirming an explicit value bypasses the subquery.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

import app.repositories.episode_repository as mod
from app.models import Episodes
from app.repositories.episode_repository import EpisodeRepository


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    try:
        params = dict(compiled.params)
    except Exception:  # pragma: no cover - text() with unbound params
        params = {}
    return str(compiled), params


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.rows: list[Any] = []

    async def execute(self, stmt: Any, params: Any = None) -> _FakeResult:
        sql, binds = _compile(stmt)
        if params:
            binds = {**binds, **params}
        self.calls.append((sql, binds))
        return _FakeResult(self.rows)


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    session = _FakeSession()
    monkeypatch.setattr(mod, "write_scope", lambda: _ScopeCM(session))
    return session


@pytest.fixture
def repo() -> EpisodeRepository:
    return EpisodeRepository()


@pytest.mark.asyncio
async def test_create_without_sort_order_uses_max_plus_one_subquery(
    repo: EpisodeRepository, fake_session: _FakeSession
) -> None:
    """No explicit sort_order => the INSERT carries a correlated
    COALESCE(MAX(sort_order)+1, 1) subquery scoped to the project, not a
    bound literal (and not the DB default of 0)."""
    fake_session.rows = [Episodes(id=3001, project_id=42, title="Ep 3", sort_order=3)]

    await repo.create({"project_id": 42, "title": "Ep 3"})

    sql, binds = fake_session.calls[-1]
    assert "INSERT INTO" in sql and "episodes" in sql
    assert "coalesce" in sql.lower()
    assert "max(" in sql.lower() and "sort_order" in sql.lower()
    # The project scoping in the subquery's WHERE binds to 42; no literal
    # sort_order bind is present since it's computed server-side.
    assert 42 in binds.values()
    assert "sort_order" not in binds


@pytest.mark.asyncio
async def test_create_without_sort_order_empty_project_defaults_to_one(
    repo: EpisodeRepository, fake_session: _FakeSession
) -> None:
    """Same subquery shape regardless of whether the project already has
    episodes — COALESCE(..., 1) is what makes an empty project start at 1;
    the real MAX() value is resolved server-side, not asserted here."""
    fake_session.rows = [Episodes(id=4001, project_id=99, title="Ep 1", sort_order=1)]

    await repo.create({"project_id": 99, "title": "Ep 1"})

    sql, binds = fake_session.calls[-1]
    assert "coalesce" in sql.lower()
    assert 99 in binds.values()


@pytest.mark.asyncio
async def test_create_with_explicit_sort_order_bypasses_subquery(
    repo: EpisodeRepository, fake_session: _FakeSession
) -> None:
    """The auto-Ep1 project-creation path passes sort_order=1 explicitly —
    that value must be bound as-is, no MAX() subquery involved."""
    fake_session.rows = [
        Episodes(id=5001, project_id=7, title="Episode 1", sort_order=1)
    ]

    await repo.create({"project_id": 7, "title": "Episode 1", "sort_order": 1})

    sql, binds = fake_session.calls[-1]
    assert "coalesce" not in sql.lower()
    assert binds.get("sort_order") == 1


@pytest.mark.asyncio
async def test_create_without_project_id_skips_subquery(
    repo: EpisodeRepository, fake_session: _FakeSession
) -> None:
    """Defensive: if project_id is somehow absent, don't emit a subquery
    with a None-scoped WHERE (which would silently match nothing under
    normal comparison semantics) — fall through to the DB default."""
    fake_session.rows = [Episodes(id=6001, title="Untitled")]

    await repo.create({"title": "Untitled"})

    sql, _ = fake_session.calls[-1]
    assert "coalesce" not in sql.lower()


# ── set_current_node_id: per-episode workflow cursor (mig 402, B1) ──────────
#
# Sibling of ProjectStageNodesRepository.set_current_node_id, but writes
# episodes.current_node_id instead of the legacy projects.current_node_id —
# B1 only lands this accessor, B2 is what wires advance_service to call it.


@pytest.mark.asyncio
async def test_set_current_node_id_updates_episode_cursor(
    repo: EpisodeRepository, fake_session: _FakeSession
) -> None:
    await repo.set_current_node_id("777", "888")

    sql, binds = fake_session.calls[-1]
    assert "UPDATE" in sql and "episodes" in sql.lower()
    assert "current_node_id" in sql
    assert binds.get("current_node_id") == 888
    assert binds.get("id_1") == 777


@pytest.mark.asyncio
async def test_set_current_node_id_none_clears_cursor(
    repo: EpisodeRepository, fake_session: _FakeSession
) -> None:
    """node_id=None binds an explicit SQL NULL (clearing the cursor), not a
    no-op — same "assign None explicitly" contract as
    ProjectStageNodesRepository.set_current_node_id."""
    await repo.set_current_node_id("777", None)

    sql, binds = fake_session.calls[-1]
    assert "UPDATE" in sql
    assert "current_node_id" in binds
    assert binds["current_node_id"] is None
