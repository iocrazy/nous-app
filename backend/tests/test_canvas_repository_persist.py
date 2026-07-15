"""Tests for CanvasRepository.patch_node_run_results (Phase 6d M4a).

Pins the load-bearing realtime-closure behaviour: persisting a node's
run_result must ALSO advance base_updated_at to the freshly-bumped updated_at,
otherwise the frontend's applyRemoteUpdate staleness guard
(``if row.base_updated_at <= s.baseUpdatedAt return``) drops the realtime
event as a self-echo and the result never surfaces in open tabs.

Boundary-stub style: the repo module's write_scope is monkeypatched with a
capturing fake session; the repository code (statement construction, two-step
token advance) really runs. No live Postgres is needed.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Dict, List

import pytest

from app.repositories.canvas_repository import CanvasRepository

_NEW_UPDATED_AT = "2026-06-10T12:00:05+00:00"
_OLD_TOKEN = "2026-06-10T12:00:00+00:00"


class _FakeSession:
    """Records every UPDATE's compiled params; returns the queued scalar for
    the first (RETURNING updated_at) statement — mimicking the DB trigger
    handing back the bumped updated_at."""

    def __init__(self, first_scalar: Any) -> None:
        self._first_scalar = first_scalar
        self.updates: List[Dict[str, Any]] = []

    async def execute(self, stmt: Any) -> Any:
        compiled = stmt.compile()
        # Statement values (SET clause) only — filter out WHERE binds by
        # keeping keys that are actual table columns being written.
        set_cols = (
            {c.name for c in stmt.table.columns} if hasattr(stmt, "table") else set()
        )
        values = {k: v for k, v in compiled.params.items() if k in set_cols}
        self.updates.append(values)

        scalar_value = self._first_scalar if len(self.updates) == 1 else None

        class _Result:
            def scalar(self_inner) -> Any:
                return scalar_value

        return _Result()


def _write_scope_with(session: _FakeSession):
    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


def _repo_with(
    monkeypatch, *, row: Dict[str, Any] | None, session: _FakeSession
) -> CanvasRepository:
    import app.repositories.canvas_repository as mod

    repo = CanvasRepository()

    async def _fake_get_by_id(_canvas_id: str) -> Dict[str, Any] | None:
        return row

    monkeypatch.setattr(repo, "get_by_id", _fake_get_by_id)
    monkeypatch.setattr(mod, "write_scope", _write_scope_with(session))
    return repo


def _seed_row() -> Dict[str, Any]:
    return {
        "id": "123",
        "nodes_json": [
            {"id": "n1", "type": "llm", "data": {"prompt": "hello"}},
            {"id": "n2", "type": "prompt", "data": {"prompt": "src"}},
        ],
        "base_updated_at": _OLD_TOKEN,
        "updated_at": _OLD_TOKEN,
    }


@pytest.mark.asyncio
class TestPatchNodeRunResults:
    async def test_advances_base_updated_at(self, monkeypatch):
        """The persist write must advance base_updated_at to the new updated_at.

        Two UPDATEs are expected: (1) nodes_json, (2) base_updated_at set to the
        bumped updated_at — never a Python-computed timestamp.
        """
        session = _FakeSession(first_scalar=_NEW_UPDATED_AT)
        repo = _repo_with(monkeypatch, row=_seed_row(), session=session)

        ok = await repo.patch_node_run_results(
            "123", {"n1": {"run_result": {"text": "out"}, "run_status": "succeeded"}}
        )
        assert ok is True

        # Exactly two UPDATEs: nodes_json then base_updated_at.
        assert len(session.updates) == 2, (
            f"expected 2 UPDATEs (nodes_json + base_updated_at), got "
            f"{session.updates}"
        )
        nodes_update, token_update = session.updates

        assert "nodes_json" in nodes_update
        assert "base_updated_at" not in nodes_update

        # Second UPDATE advances base_updated_at to the bumped updated_at.
        assert token_update == {"base_updated_at": _NEW_UPDATED_AT}, (
            "second UPDATE must set base_updated_at to the freshly-bumped "
            f"updated_at ({_NEW_UPDATED_AT}), got {token_update}"
        )

    async def test_base_updated_at_value_is_db_side_not_python(self, monkeypatch):
        """base_updated_at must equal the updated_at returned by the DB, proving
        it comes from the trigger's now() rather than a Python value."""
        session = _FakeSession(first_scalar=_NEW_UPDATED_AT)
        repo = _repo_with(monkeypatch, row=_seed_row(), session=session)

        await repo.patch_node_run_results(
            "123", {"n1": {"run_result": {"text": "x"}, "run_status": "succeeded"}}
        )

        token_update = session.updates[1]
        # The value is exactly the DB-returned updated_at, not now()/uuid/etc.
        assert token_update["base_updated_at"] == _NEW_UPDATED_AT

    async def test_nodes_json_carries_merged_run_result(self, monkeypatch):
        """Sanity: the nodes_json UPDATE still merges run_result into node.data
        and leaves other nodes untouched (regression guard alongside the token)."""
        session = _FakeSession(first_scalar=_NEW_UPDATED_AT)
        repo = _repo_with(monkeypatch, row=_seed_row(), session=session)

        await repo.patch_node_run_results(
            "123",
            {"n1": {"run_result": {"text": "merged"}, "run_status": "succeeded"}},
        )

        nodes_update = session.updates[0]
        patched_nodes = nodes_update["nodes_json"]
        by_id = {n["id"]: n for n in patched_nodes}
        # n1 got run_result + run_status merged into data; prompt preserved.
        assert by_id["n1"]["data"]["run_result"] == {"text": "merged"}
        assert by_id["n1"]["data"]["run_status"] == "succeeded"
        assert by_id["n1"]["data"]["prompt"] == "hello"
        # n2 untouched.
        assert by_id["n2"]["data"] == {"prompt": "src"}

    async def test_missing_canvas_returns_false_no_update(self, monkeypatch):
        """When the canvas row is absent, no UPDATE is attempted."""
        session = _FakeSession(first_scalar=None)
        repo = _repo_with(monkeypatch, row=None, session=session)

        ok = await repo.patch_node_run_results("123", {"n1": {"run_status": "x"}})
        assert ok is False
        assert session.updates == []

    async def test_no_token_means_no_row_returns_false(self, monkeypatch):
        """If the first UPDATE returns no updated_at (RETURNING scalar None =
        zero rows matched — the canvas vanished between read and write), the
        write reports failure and base_updated_at is never touched.

        (Legacy PostgREST note: a matched row ALWAYS carried updated_at in the
        representation, so "row updated but no token" was unreachable there
        too — this pins the equivalent ORM behaviour.)
        """
        session = _FakeSession(first_scalar=None)
        repo = _repo_with(monkeypatch, row=_seed_row(), session=session)

        ok = await repo.patch_node_run_results(
            "123", {"n1": {"run_status": "succeeded"}}
        )
        assert ok is False
        # Only the nodes_json UPDATE was attempted; no base_updated_at write.
        assert len(session.updates) == 1
        assert "nodes_json" in session.updates[0]
