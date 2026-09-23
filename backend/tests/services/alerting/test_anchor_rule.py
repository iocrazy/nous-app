"""``ensure_anchor_rule``: the alert_rules row system alert_history writers
hang off is created on first use and reused after that."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

import app.db.session as db_session
from app.services.alerting.anchor_rule import ensure_anchor_rule


class _Result:
    def __init__(self, scalar: Any = None) -> None:
        self._scalar = scalar

    def scalar(self) -> Any:
        return self._scalar


class _Session:
    """In-memory stand-in: remembers rules by name across calls."""

    def __init__(self) -> None:
        self.rules: dict[str, int] = {}
        self.inserts: list[dict[str, Any]] = []

    async def execute(self, stmt: Any) -> _Result:
        compiled = stmt.compile(dialect=postgresql.dialect())
        params = dict(compiled.params)
        if stmt.is_insert:
            self.inserts.append(params)
            rule_id = 100 + len(self.rules)
            self.rules[params["name"]] = rule_id
            return _Result(rule_id)
        name = next(v for v in params.values() if isinstance(v, str))
        return _Result(self.rules.get(name))


class _CM:
    def __init__(self, session: _Session) -> None:
        self._session = session

    async def __aenter__(self) -> _Session:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> _Session:
    s = _Session()
    monkeypatch.setattr(db_session, "read_scope", lambda: _CM(s))
    monkeypatch.setattr(db_session, "write_scope", lambda: _CM(s))
    return s


@pytest.mark.asyncio
async def test_created_once_then_reused(session: _Session) -> None:
    kwargs = dict(
        name="Scope denied (system)",
        metric_type="scope_denied",
        threshold=0.0,
        is_active=True,
    )
    first = await ensure_anchor_rule(**kwargs)
    second = await ensure_anchor_rule(**kwargs)

    assert first == second
    assert len(session.inserts) == 1
    created = session.inserts[0]
    assert created["name"] == "Scope denied (system)"
    assert created["metric_type"] == "scope_denied"
    assert created["is_active"] is True
    assert created["condition"] == "gte"


@pytest.mark.asyncio
async def test_rules_are_keyed_by_name(session: _Session) -> None:
    a = await ensure_anchor_rule(
        name="Scope denied (system)",
        metric_type="scope_denied",
        threshold=0.0,
        is_active=True,
    )
    b = await ensure_anchor_rule(
        name="Agent cost anomaly (system)",
        metric_type="agent_cost_zscore",
        threshold=3.0,
        is_active=False,
    )
    assert a != b
    assert len(session.inserts) == 2


class _LostRaceSession(_Session):
    """The first read misses, then another writer commits the same name before
    our insert lands: ON CONFLICT DO NOTHING returns no row."""

    def __init__(self, winner_id: int) -> None:
        super().__init__()
        self.winner_id = winner_id
        self.selects = 0
        self.insert_sql: list[str] = []

    async def execute(self, stmt: Any) -> _Result:
        if stmt.is_insert:
            compiled = stmt.compile(dialect=postgresql.dialect())
            self.inserts.append(dict(compiled.params))
            self.insert_sql.append(str(compiled))
            return _Result(None)
        self.selects += 1
        return _Result(None if self.selects == 1 else self.winner_id)


@pytest.mark.asyncio
async def test_lost_race_reselects_the_winner(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _LostRaceSession(winner_id=777)
    monkeypatch.setattr(db_session, "read_scope", lambda: _CM(s))
    monkeypatch.setattr(db_session, "write_scope", lambda: _CM(s))

    rule_id = await ensure_anchor_rule(
        name="Scope denied (system)",
        metric_type="scope_denied",
        threshold=0.0,
        is_active=True,
    )

    assert rule_id == 777
    assert len(s.inserts) == 1
    assert s.selects == 2
    sql = s.insert_sql[0]
    assert "ON CONFLICT (name) WHERE created_by IS NULL DO NOTHING" in sql


class _SqlSpySession(_Session):
    """Records the compiled SQL of every SELECT so the predicate can be asserted."""

    def __init__(self) -> None:
        super().__init__()
        self.selects: list[str] = []

    async def execute(self, stmt: Any) -> _Result:
        if not stmt.is_insert:
            self.selects.append(str(stmt.compile(dialect=postgresql.dialect())))
        return await super().execute(stmt)


async def test_lookup_only_matches_system_anchor_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The partial UNIQUE index allows a user-created rule to share the anchor's
    name; the lookup must therefore be scoped to ``created_by IS NULL`` on both
    the fast path and the post-conflict re-select, or system history rows
    would silently attach to an admin's rule and the anchor would never be
    created."""
    spy = _SqlSpySession()
    monkeypatch.setattr(db_session, "read_scope", lambda: _CM(spy))
    monkeypatch.setattr(db_session, "write_scope", lambda: _CM(spy))
    await ensure_anchor_rule(
        name="Scope denied (system)",
        metric_type="scope_denied",
        threshold=1,
        is_active=False,
    )
    assert spy.selects, "expected at least one lookup"
    for sql in spy.selects:
        assert "created_by IS NULL" in sql, sql
