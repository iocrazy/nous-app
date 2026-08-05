"""Compile-level regression pins for the two ``canvas_refs_repository.py``
methods migrated off raw ``text()`` SQL in the Phase C final review
(canvas_refs sweep, 2026-08-05).

Both pins DRIVE THE REAL PRODUCTION STATEMENT CONSTRUCTOR (the actual
``CanvasRefsRepository`` methods, via a capturing fake ``read_scope`` — not a
statement independently reconstructed here) and inspect the resulting
SQLAlchemy construct STRUCTURALLY (join onclause / distinct_on / order_by
objects), not by substring-matching compiled SQL text — the same
mutually-exclusive-assertion rigor as the rest of this batch's regression
tests.

  1. ``tree_for_projects``: ``Resources.is_trashed`` must be part of the
     JOIN's ON clause, and must NOT appear in the statement's WHERE clause.
     Moving it to WHERE would silently turn the LEFT JOIN into an INNER JOIN
     (a canvas with zero live resources would vanish from the tree instead
     of reporting ``asset_count=0``) — exactly the kind of "simplification"
     a future edit could make without realizing it changes semantics.
  2. ``list_assets_for_canvas``: the inner DISTINCT ON subquery's leading
     ORDER BY column must structurally match its ``DISTINCT ON`` column.
     Postgres requires ``DISTINCT ON`` expressions to be the LEADING
     ``ORDER BY`` expressions — if a future edit reorders the ``.order_by()``
     call (e.g. to put ``created_at`` first "for clarity"), Postgres would
     reject the query at runtime; this pin catches the mismatch statically.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.repositories.canvas_refs_repository import CanvasRefsRepository

pytestmark = pytest.mark.asyncio


class _FakeExecuteResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> "_FakeExecuteResult":
        return self

    def all(self) -> list[dict]:
        return self._rows


class _CapturingSession:
    """Records every statement passed to ``execute()`` — no rows needed for
    these pins, only the statement object itself."""

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> _FakeExecuteResult:
        self.statements.append(stmt)
        return _FakeExecuteResult([])


def _fake_read_scope(session: _CapturingSession):
    @asynccontextmanager
    async def _read_scope():
        yield session

    return _read_scope


async def test_tree_for_projects_is_trashed_stays_in_join_on_not_where(monkeypatch):
    """Drives the real ``tree_for_projects`` production statement builder."""
    import app.repositories.canvas_refs_repository as repo_mod

    session = _CapturingSession()
    monkeypatch.setattr(repo_mod, "read_scope", _fake_read_scope(session))

    await CanvasRefsRepository().tree_for_projects(["1", "2"])

    assert len(session.statements) == 1
    stmt = session.statements[0]

    froms = stmt.get_final_froms()
    assert froms, "expected at least one FROM/JOIN element"
    resources_join = froms[0]  # the (possibly nested) join tree
    onclause_sql = str(resources_join.onclause)
    assert "is_trashed" in onclause_sql, (
        "Resources.is_trashed must be part of the resources JOIN's ON "
        f"clause — got onclause={onclause_sql!r}"
    )

    where_sql = str(stmt.whereclause) if stmt.whereclause is not None else ""
    assert "is_trashed" not in where_sql, (
        "Resources.is_trashed must NOT appear in the WHERE clause — moving "
        "it there would silently turn the LEFT JOIN into an INNER JOIN "
        f"(a canvas with zero live resources would vanish). WHERE={where_sql!r}"
    )


async def test_list_assets_for_canvas_distinct_on_matches_leading_order_by(
    monkeypatch,
):
    """Drives the real ``list_assets_for_canvas`` production statement
    builder and inspects the inner DISTINCT ON subquery it wraps."""
    import app.repositories.canvas_refs_repository as repo_mod

    session = _CapturingSession()
    monkeypatch.setattr(repo_mod, "read_scope", _fake_read_scope(session))

    await CanvasRefsRepository().list_assets_for_canvas("5")

    assert len(session.statements) == 1
    outer_stmt = session.statements[0]

    froms = outer_stmt.get_final_froms()
    assert froms, "expected the outer SELECT to select FROM the inner subquery"
    inner_select = froms[0].element  # Subquery.element → the original Select

    distinct_on = inner_select._distinct_on
    order_by = inner_select._order_by_clauses
    assert distinct_on, "inner query must be a DISTINCT ON select"
    assert len(order_by) >= len(
        distinct_on
    ), "ORDER BY must have at least as many expressions as DISTINCT ON"
    for i, distinct_col in enumerate(distinct_on):
        assert distinct_col.compare(order_by[i]), (
            f"DISTINCT ON column #{i} does not match the leading ORDER BY "
            f"column #{i} — Postgres requires DISTINCT ON expressions to be "
            "the LEADING ORDER BY expressions (a mismatch here compiles "
            "fine in this test but is REJECTED by Postgres at runtime): "
            f"distinct_on={distinct_col!r} order_by={order_by[i]!r}"
        )
