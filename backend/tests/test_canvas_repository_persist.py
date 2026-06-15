"""Tests for CanvasRepository.patch_node_run_results (Phase 6d M4a).

Pins the load-bearing realtime-closure behaviour: persisting a node's
run_result must ALSO advance base_updated_at to the freshly-bumped updated_at,
otherwise the frontend's applyRemoteUpdate staleness guard
(``if row.base_updated_at <= s.baseUpdatedAt return``) drops the realtime
event as a self-echo and the result never surfaces in open tabs.

These use a fake supabase-py query builder so no live Postgres is needed.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.repositories.canvas_repository import CanvasRepository

_NEW_UPDATED_AT = "2026-06-10T12:00:05+00:00"
_OLD_TOKEN = "2026-06-10T12:00:00+00:00"


class _FakeResult:
    def __init__(self, data: Any) -> None:
        self.data = data


class _FakeQuery:
    """Records update() payloads + eq() filters; chainable, awaitable execute().

    On execute() of an update, returns a row carrying the bumped updated_at so
    the repo's step-2 read-back works exactly like the real DB trigger path.
    """

    def __init__(self, recorder: "_FakeClient") -> None:
        self._rec = recorder
        self._op: str | None = None
        self._payload: Dict[str, Any] | None = None

    def update(self, payload: Dict[str, Any]) -> "_FakeQuery":
        self._op = "update"
        self._payload = payload
        self._rec.updates.append(dict(payload))
        return self

    def eq(self, *_args: Any, **_kwargs: Any) -> "_FakeQuery":
        return self

    async def execute(self) -> _FakeResult:
        if self._op == "update":
            # Mimic the trigger: any UPDATE returns the row with a bumped
            # updated_at (SQL now()).  base_updated_at write doesn't re-bump.
            return _FakeResult([{"id": "123", "updated_at": _NEW_UPDATED_AT}])
        return _FakeResult([])


class _FakeClient:
    def __init__(self) -> None:
        self.updates: List[Dict[str, Any]] = []

    def table(self, _name: str) -> _FakeQuery:
        return _FakeQuery(self)


def _repo_with(
    monkeypatch, *, row: Dict[str, Any], client: _FakeClient
) -> CanvasRepository:
    repo = CanvasRepository()

    async def _fake_get_by_id(_canvas_id: str) -> Dict[str, Any]:
        return row

    async def _fake_client() -> _FakeClient:
        return client

    monkeypatch.setattr(repo, "get_by_id", _fake_get_by_id)
    monkeypatch.setattr(repo, "_client", _fake_client)
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
        client = _FakeClient()
        repo = _repo_with(monkeypatch, row=_seed_row(), client=client)

        ok = await repo.patch_node_run_results(
            "123", {"n1": {"run_result": {"text": "out"}, "run_status": "succeeded"}}
        )
        assert ok is True

        # Exactly two UPDATEs: nodes_json then base_updated_at.
        assert len(client.updates) == 2, (
            f"expected 2 UPDATEs (nodes_json + base_updated_at), got "
            f"{client.updates}"
        )
        nodes_update, token_update = client.updates

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
        client = _FakeClient()
        repo = _repo_with(monkeypatch, row=_seed_row(), client=client)

        await repo.patch_node_run_results(
            "123", {"n1": {"run_result": {"text": "x"}, "run_status": "succeeded"}}
        )

        token_update = client.updates[1]
        # The value is exactly the DB-returned updated_at, not now()/uuid/etc.
        assert token_update["base_updated_at"] == _NEW_UPDATED_AT

    async def test_nodes_json_carries_merged_run_result(self, monkeypatch):
        """Sanity: the nodes_json UPDATE still merges run_result into node.data
        and leaves other nodes untouched (regression guard alongside the token)."""
        client = _FakeClient()
        repo = _repo_with(monkeypatch, row=_seed_row(), client=client)

        await repo.patch_node_run_results(
            "123",
            {"n1": {"run_result": {"text": "merged"}, "run_status": "succeeded"}},
        )

        nodes_update = client.updates[0]
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
        client = _FakeClient()
        repo = CanvasRepository()

        async def _none(_cid: str):
            return None

        async def _fake_client():
            return client

        monkeypatch.setattr(repo, "get_by_id", _none)
        monkeypatch.setattr(repo, "_client", _fake_client)

        ok = await repo.patch_node_run_results("123", {"n1": {"run_status": "x"}})
        assert ok is False
        assert client.updates == []

    async def test_no_token_skips_second_update(self, monkeypatch):
        """If the first UPDATE returns no updated_at, base_updated_at is not
        written (defensive: still returns True for the nodes_json write)."""

        class _NoTokenQuery(_FakeQuery):
            async def execute(self) -> _FakeResult:
                if self._op == "update":
                    return _FakeResult([{"id": "123"}])  # no updated_at
                return _FakeResult([])

        class _NoTokenClient(_FakeClient):
            def table(self, _name: str) -> _FakeQuery:
                return _NoTokenQuery(self)

        client = _NoTokenClient()
        repo = _repo_with(monkeypatch, row=_seed_row(), client=client)

        ok = await repo.patch_node_run_results(
            "123", {"n1": {"run_status": "succeeded"}}
        )
        assert ok is True
        # Only the nodes_json UPDATE happened; no base_updated_at write.
        assert len(client.updates) == 1
        assert "nodes_json" in client.updates[0]
