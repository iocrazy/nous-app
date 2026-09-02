# backend/tests/test_canvas_asset_refs_repository.py
"""Statement-level pins for ``CanvasAssetRefsRepository``.

Every pin DRIVES THE REAL PRODUCTION METHOD (via a capturing fake
``read_scope``/``write_scope``) and inspects what that method actually built —
never a statement re-assembled inside the test, which would pin the test's own
copy rather than the code that ships.

Three properties are load-bearing and invisible to a unit test with a stubbed
session, so they are pinned on the compiled SQL / the construct itself:

  1. ``replace_for_canvas`` must upsert with ``DO UPDATE SET loadout_id``.
     ``loadout_id`` is OUTSIDE the primary key, so ``DO NOTHING`` (what the
     resource-refs sibling uses, where every column IS in the key) would let a
     stale loadout survive a re-save and report success.
  2. ``list_canvases_for_asset`` must aggregate with DISTINCT at all. Two
     nodes bound to the same loadout would otherwise repeat that id in
     ``loadout_ids``. (The spelling is not the point: ``func.distinct(x)``
     compiles to ``distinct(x)``, which Postgres accepts inside an aggregate —
     it parses as the keyword plus a parenthesised expression. Verified on
     pg17, so do not describe the other spelling as a runtime error.)
  3. ``list_canvases_for_asset`` must apply the scope filter. Without it, a
     system-preset asset (readable from every scope) would list every team's
     canvases to anyone who could see it.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, List

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories.canvas_asset_refs_repository import CanvasAssetRefsRepository

pytestmark = pytest.mark.asyncio

_A = 727145299382534145
_LO = 727145299382534200


class _FakeResult:
    def __init__(self, rows: List[dict]) -> None:
        self._rows = rows

    def mappings(self) -> "_FakeResult":
        return self

    def all(self) -> List[dict]:
        return self._rows


class _CapturingSession:
    def __init__(self, rows: List[dict] | None = None) -> None:
        self.statements: List[Any] = []
        self._rows = rows or []

    async def execute(self, stmt: Any) -> _FakeResult:
        self.statements.append(stmt)
        return _FakeResult(self._rows)


def _fake_scope(session: _CapturingSession):
    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


def _sql(stmt) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def _patch(monkeypatch, session):
    import app.repositories.canvas_asset_refs_repository as repo_mod

    monkeypatch.setattr(repo_mod, "read_scope", _fake_scope(session))
    monkeypatch.setattr(repo_mod, "write_scope", _fake_scope(session))


# ── 1. the upsert clause ───────────────────────────────────────────────────


async def test_replace_for_canvas_upserts_the_loadout_instead_of_ignoring_it(
    monkeypatch,
):
    session = _CapturingSession()
    _patch(monkeypatch, session)

    await CanvasAssetRefsRepository().replace_for_canvas(
        "5001",
        [
            {"asset_id": _A, "node_id": "n1", "loadout_id": _LO},
            {"asset_id": _A, "node_id": "n2", "loadout_id": None},
        ],
    )

    assert len(session.statements) == 2, "expected a DELETE then one INSERT"
    delete_sql, insert_sql = (_sql(s) for s in session.statements)

    assert delete_sql.startswith("DELETE FROM public.canvas_asset_refs")
    assert "canvas_id = 5001" in delete_sql

    assert "ON CONFLICT (canvas_id, asset_id, node_id)" in insert_sql, (
        "the conflict target must be the real PK — a wrong/missing index "
        f"makes Postgres reject the statement. SQL={insert_sql!r}"
    )
    assert "DO UPDATE SET loadout_id = excluded.loadout_id" in insert_sql, (
        "loadout_id is outside the PK, so DO NOTHING would leave a STALE "
        "loadout on a re-saved node and still report success. "
        f"SQL={insert_sql!r}"
    )
    assert "DO NOTHING" not in insert_sql
    # Both rows in ONE statement — a per-ref INSERT would be N round trips on
    # the canvas-save hot path.
    assert insert_sql.count("VALUES") == 1
    assert "'n1'" in insert_sql and "'n2'" in insert_sql
    assert "NULL" in insert_sql, "a ref with no loadout must still be inserted"


async def test_replace_for_canvas_with_no_refs_deletes_and_stops(monkeypatch):
    """Empty is a real desired state (the user removed the last asset node), so
    the DELETE must still run — but an INSERT with zero VALUES rows is a syntax
    error, so it must not be built."""
    session = _CapturingSession()
    _patch(monkeypatch, session)

    await CanvasAssetRefsRepository().replace_for_canvas("5001", [])

    assert len(session.statements) == 1
    assert _sql(session.statements[0]).startswith(
        "DELETE FROM public.canvas_asset_refs"
    )


# ── 2/3. the reverse lookup ────────────────────────────────────────────────


async def test_list_canvases_for_asset_aggregates_distinctly(monkeypatch):
    """Both aggregates must be DISTINCT.

    ``loadout_ids`` is the one with observable consequences — two nodes bound
    to the same loadout repeat the id without it — and the E2 case proves that
    end to end. This pin is the cheap static half, and it covers ``node_ids``
    too, where the PK happens to make duplicates unreachable today.
    """
    session = _CapturingSession()
    _patch(monkeypatch, session)

    await CanvasAssetRefsRepository().list_canvases_for_asset("77", "88")

    sql = _sql(session.statements[0])
    assert "array_agg(DISTINCT public.canvas_asset_refs.node_id)" in sql, sql
    assert "array_agg(DISTINCT CAST(public.canvas_asset_refs.loadout_id" in sql, sql


async def test_list_canvases_for_asset_filters_to_the_callers_scope(monkeypatch):
    session = _CapturingSession()
    _patch(monkeypatch, session)

    await CanvasAssetRefsRepository().list_canvases_for_asset("77", "88")

    sql = _sql(session.statements[0])
    assert "coalesce(public.projects.team_id, personal_team.id) = 88" in sql, (
        "the scope filter must resolve a NULL projects.team_id to the OWNER's "
        "personal team — the same rule _project_scope_id applies — or a "
        "personal-project canvas would be invisible to its own owner. "
        f"SQL={sql!r}"
    )
    assert "public.teams.kind = 'personal'" in sql
    assert "public.canvases.deleted_at IS NULL" in sql, "trashed canvases must not list"
    assert "public.canvas_asset_refs.asset_id = 77" in sql


async def test_list_canvases_for_asset_without_scope_is_unfiltered(monkeypatch):
    """The unscoped call exists for system callers (the backfill's inverse, and
    admin diagnostics). Pinned so the difference is deliberate and visible:
    every ROUTE passes a scope."""
    session = _CapturingSession()
    _patch(monkeypatch, session)

    await CanvasAssetRefsRepository().list_canvases_for_asset("77")

    assert "coalesce(public.projects.team_id" not in _sql(session.statements[0])


async def test_list_canvases_for_asset_drops_null_loadouts_and_sorts(monkeypatch):
    """``array_agg(DISTINCT loadout_id)`` keeps a NULL element when some node
    carries no loadout. Passing that through would make the client filter a null
    out of a list of ids — so the repo does it, once."""
    session = _CapturingSession(
        [{"canvas_id": "1", "node_ids": ["b", "a"], "loadout_ids": [None, "9", "7"]}]
    )
    _patch(monkeypatch, session)

    rows = await CanvasAssetRefsRepository().list_canvases_for_asset("77", "88")

    assert rows[0]["node_ids"] == ["a", "b"]
    assert rows[0]["loadout_ids"] == ["7", "9"]


async def test_list_for_canvas_hides_soft_deleted_assets_but_keeps_loadoutless_refs(
    monkeypatch,
):
    """The asset join is INNER + ``deleted_at IS NULL`` (a soft-deleted asset's
    refs survive — the FK only cascades on a HARD delete), while the loadout
    join must be OUTER: ``loadout_id`` is nullable, so an INNER join there would
    hide every ref that has no loadout."""
    session = _CapturingSession()
    _patch(monkeypatch, session)

    await CanvasAssetRefsRepository().list_for_canvas("5001")

    sql = _sql(session.statements[0])
    assert "JOIN public.assets ON" in sql and "LEFT OUTER JOIN public.assets" not in sql
    assert "public.assets.deleted_at IS NULL" in sql
    assert "LEFT OUTER JOIN public.asset_loadouts" in sql
