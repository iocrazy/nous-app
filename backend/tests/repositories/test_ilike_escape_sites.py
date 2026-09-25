"""Every ILIKE that takes user text escapes it (``like_escape.py``).

A typed ``%`` must match a literal ``%``, not everything; ``_`` must match
itself, not any one character. The pattern is a bound parameter, so this is
not injection — it is a silent match-everything scan (the same defect
``rpc_user_media_text_search`` carried for months, migration 463).

Four call sites had no escaping: the inspiration-notes ``q``, the points
transaction ``search``, the resource picker ``q`` and the smart-folder
``contains`` / ``starts_with`` rules. ``media_repository`` /
``user_logs_repository`` / ``issue_repository`` already had it and are pinned
by their own tests. Each assertion here compiles the real statement and reads
the bound value, so a regression to a bare f-string turns it red.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

ESCAPED = "%100\\%%"  # what ``"%" + escape_like("100%") + "%"`` must bind


def _params(clause: Any) -> set[Any]:
    return set(clause.compile(dialect=postgresql.dialect()).params.values())


def _sql(clause: Any) -> str:
    return str(clause.compile(dialect=postgresql.dialect()))


# ── inspiration notes q ─────────────────────────────────────────────────────


class _ScalarResult:
    def scalars(self):
        return self

    def all(self):
        return []


class _CapturingSession:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, stmt: Any, params: Any = None) -> _ScalarResult:
        self.statements.append(stmt)
        return _ScalarResult()


def _cm(session: _CapturingSession):
    @asynccontextmanager
    async def scope():
        yield session

    return scope


@pytest.mark.asyncio
async def test_inspiration_q_is_escaped(monkeypatch):
    from app.repositories import inspiration_repository as mod

    session = _CapturingSession()
    monkeypatch.setattr(mod, "read_scope", _cm(session))
    await mod.InspirationNotesRepository().list("u1", q="100%", limit=20)
    stmt = session.statements[0]
    assert ESCAPED in _params(stmt)
    assert "ESCAPE" in _sql(stmt)


# ── points transaction search ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_points_transaction_search_is_escaped(monkeypatch):
    from app.repositories import points_repository as mod

    session = _CapturingSession()
    monkeypatch.setattr(mod, "read_scope", _cm(session))
    # team_id is a Snowflake BIGINT (``int(team_id)``); a bad one is swallowed
    # into ``[]`` by the repository, which would hide the assertion.
    await mod.PointsRepository().get_transactions("123", search="100%")
    stmt = session.statements[0]
    assert ESCAPED in _params(stmt)
    assert "ESCAPE" in _sql(stmt)


# ── resource picker q ───────────────────────────────────────────────────────


def test_picker_q_is_escaped():
    from app.repositories.resources_repository import _picker_visibility_filters

    conditions = _picker_visibility_filters(user_id="u1", q="100%", scope_team_id=None)
    like = conditions[-1]
    assert ESCAPED in _params(like)
    assert "ESCAPE" in _sql(like)


# ── smart-folder rules ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "op, expected",
    [("contains", "%100\\%%"), ("starts_with", "100\\%%")],
)
def test_smart_rule_text_ops_are_escaped(op: str, expected: str):
    from app.repositories.resources_repository import ResourcesRepository

    expr = ResourcesRepository()._condition_to_sql_expr(
        {"field": "filename", "op": op, "value": "100%"}
    )
    assert expected in _params(expr)
    assert "ESCAPE" in _sql(expr)
