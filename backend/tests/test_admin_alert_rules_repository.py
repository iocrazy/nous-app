"""Unit tests for AlertRulesRepository (ORM 2.0, text() over PG).

Post-rollout the repository is the SQLAlchemy 2.0 implementation — reads go
through ``read_scope()`` and writes through ``write_scope()`` with parameterized
``text()`` statements. These tests mock those scopes with a fake session that
captures every ``(sql, binds)`` pair, so the emitted SQL shape + bind params are
asserted WITHOUT a live database (the DSN-gated integration suite in
``tests/integration/test_admin_alert_rules_repository_orm.py`` exercises the real
round-trip). This keeps fast, always-run coverage of the collapsed ORM bodies.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

import app.repositories.admin.alert_rules_repository as mod
from app.repositories.admin.alert_rules_repository import AlertRulesRepository


class _FakeMappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _FakeMappings:
        return _FakeMappings(self._rows)


class _FakeSession:
    """Captures execute/scalar calls; returns configured rows / scalar value."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.rows: list[dict[str, Any]] = []
        self.scalar_value: int = 0

    async def execute(self, stmt: Any, binds: dict[str, Any] | None = None) -> Any:
        self.calls.append((str(stmt), dict(binds or {})))
        return _FakeResult(self.rows)

    async def scalar(self, stmt: Any, binds: dict[str, Any] | None = None) -> Any:
        self.calls.append((str(stmt), dict(binds or {})))
        return self.scalar_value


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
    monkeypatch.setattr(mod, "read_scope", lambda: _ScopeCM(session))
    monkeypatch.setattr(mod, "write_scope", lambda: _ScopeCM(session))
    return session


@pytest.fixture
def repo() -> AlertRulesRepository:
    return AlertRulesRepository()


# ─── Rules ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_rules_orders_desc_by_created(
    repo: AlertRulesRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [{"id": 1}]

    rows, total = await repo.list_rules()
    assert rows == [{"id": 1}]
    assert total == 1

    sql, _ = fake_session.calls[0]
    assert "ORDER BY created_at DESC" in sql
    assert repo.RULES_TABLE in sql


@pytest.mark.asyncio
async def test_list_active_rules_filters_is_active(
    repo: AlertRulesRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [{"id": 1}]
    await repo.list_active_rules()

    sql, _ = fake_session.calls[0]
    assert "is_active = true" in sql


@pytest.mark.asyncio
async def test_create_rule_returns_first_row(
    repo: AlertRulesRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [{"id": 1, "name": "New"}]
    row = await repo.create_rule({"name": "New"})
    assert row == {"id": 1, "name": "New"}

    sql, binds = fake_session.calls[0]
    assert "INSERT INTO" in sql and "RETURNING *" in sql
    assert binds == {"name": "New"}


@pytest.mark.asyncio
async def test_create_rule_returns_none_on_empty(
    repo: AlertRulesRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    row = await repo.create_rule({"name": "x"})
    assert row is None


@pytest.mark.asyncio
async def test_create_rule_drops_phantom_keys(
    repo: AlertRulesRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [{"id": 1}]
    await repo.create_rule({"name": "n", "bogus": "drop-me"})

    _, binds = fake_session.calls[0]
    assert "bogus" not in binds
    assert binds == {"name": "n"}


@pytest.mark.asyncio
async def test_update_rule_always_stamps_updated_at(
    repo: AlertRulesRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [{"id": 1}]
    await repo.update_rule("r1", {"threshold": 5.0})

    sql, binds = fake_session.calls[0]
    assert "UPDATE" in sql and "WHERE id = :rule_id" in sql
    assert binds["threshold"] == 5.0
    # updated_at must be auto-injected (native datetime bind for the ORM path)
    assert "updated_at" in binds
    assert isinstance(binds["updated_at"], datetime)
    assert binds["rule_id"] == "r1"


@pytest.mark.asyncio
async def test_delete_rule_hits_correct_id(
    repo: AlertRulesRepository, fake_session: _FakeSession
) -> None:
    await repo.delete_rule("r1")

    sql, binds = fake_session.calls[0]
    assert sql.startswith("DELETE FROM")
    assert binds == {"rule_id": "r1"}


@pytest.mark.asyncio
async def test_auto_unmute_clears_mute_fields(
    repo: AlertRulesRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [{"id": 1}]
    await repo.auto_unmute_rule("r1")

    _, binds = fake_session.calls[0]
    assert binds["is_muted"] is False
    assert binds["mute_until"] is None


# ─── History ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_history_filters_and_pagination(
    repo: AlertRulesRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    fake_session.scalar_value = 0
    start = datetime(2026, 4, 10, tzinfo=timezone.utc)
    end = datetime(2026, 4, 17, tzinfo=timezone.utc)

    await repo.list_history(
        page=2,
        page_size=25,
        rule_id="r1",
        resolved=False,
        start_date=start,
        end_date=end,
    )

    # calls[0] = count scalar, calls[1] = list execute
    count_sql, count_binds = fake_session.calls[0]
    list_sql, list_binds = fake_session.calls[1]
    assert count_sql.startswith("SELECT count(*)")
    assert count_binds["rule_id"] == "r1"
    assert count_binds["resolved"] is False
    assert count_binds["start_date"] == start
    assert count_binds["end_date"] == end

    assert "LIMIT :limit OFFSET :offset" in list_sql
    assert list_binds["limit"] == 25
    assert list_binds["offset"] == 25  # page 2, page_size 25 → offset 25


@pytest.mark.asyncio
async def test_insert_history_sends_payload(
    repo: AlertRulesRepository, fake_session: _FakeSession
) -> None:
    await repo.insert_history({"rule_id": "r1", "metric_value": 99})

    sql, binds = fake_session.calls[0]
    assert "INSERT INTO" in sql
    assert binds == {"rule_id": "r1", "metric_value": 99}


@pytest.mark.asyncio
async def test_resolve_history_stamps_resolved_at(
    repo: AlertRulesRepository, fake_session: _FakeSession
) -> None:
    await repo.resolve_history("alert-1")

    sql, binds = fake_session.calls[0]
    assert "resolved = true" in sql
    assert "resolved_at" in binds
    assert binds["alert_id"] == "alert-1"


# ─── Metric queries (for alert evaluation) ─────────────────────────


@pytest.mark.asyncio
async def test_request_status_codes_filters_since(
    repo: AlertRulesRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [{"status_code": 200}, {"status_code": 500}]
    rows = await repo.request_status_codes("2026-04-17")
    assert len(rows) == 2

    sql, binds = fake_session.calls[0]
    assert "FROM api_request_logs" in sql
    assert "timestamp >= :since" in sql
    # since bound as a NATIVE tz-aware datetime (v3 rule)
    assert isinstance(binds["since"], datetime)
    assert binds["since"].tzinfo is not None


@pytest.mark.asyncio
async def test_request_response_times_selects_column(
    repo: AlertRulesRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [{"response_time_ms": 42}]
    await repo.request_response_times("2026-04-17")

    sql, _ = fake_session.calls[0]
    assert "SELECT response_time_ms" in sql


@pytest.mark.asyncio
async def test_app_log_count_by_levels_uses_any_filter(
    repo: AlertRulesRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 7
    count = await repo.app_log_count_by_levels(["ERROR", "CRITICAL"], "2026-04-17")
    assert count == 7

    sql, binds = fake_session.calls[0]
    assert "level = ANY(:levels)" in sql
    assert binds["levels"] == ["ERROR", "CRITICAL"]


@pytest.mark.asyncio
async def test_app_log_count_by_level_uses_eq_filter(
    repo: AlertRulesRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 3
    count = await repo.app_log_count_by_level("CRITICAL", "2026-04-17")
    assert count == 3

    sql, binds = fake_session.calls[0]
    assert "level = :level" in sql
    assert binds["level"] == "CRITICAL"
