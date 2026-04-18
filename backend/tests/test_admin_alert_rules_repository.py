"""Unit tests for AlertRulesRepository."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.repositories.admin.alert_rules_repository import AlertRulesRepository


class _FakeQuery:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._data: Any = []
        self._count: int | None = None

    def __getattr__(self, name: str):
        def _capture(*args: Any, **kwargs: Any) -> "_FakeQuery":
            self.calls.append((name, args, kwargs))
            return self
        return _capture

    async def execute(self) -> Any:
        class _R:
            data = self._data
            count = self._count
        return _R()


class _FakeClient:
    def __init__(self, query: _FakeQuery) -> None:
        self._query = query

    def table(self, name: str) -> _FakeQuery:
        self._query.calls.append(("table", (name,), {}))
        return self._query


@pytest.fixture
def fake_query() -> _FakeQuery:
    return _FakeQuery()


@pytest.fixture
def repo(fake_query: _FakeQuery) -> AlertRulesRepository:
    r = AlertRulesRepository()

    async def _client():
        return _FakeClient(fake_query)

    r._client = _client  # type: ignore[method-assign]
    return r


# ─── Rules ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_rules_orders_desc_by_created(
    repo: AlertRulesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "r1"}]
    fake_query._count = 1

    rows, total = await repo.list_rules()
    assert rows == [{"id": "r1"}]
    assert total == 1

    order = next(c for c in fake_query.calls if c[0] == "order")
    assert order[1] == ("created_at",)
    assert order[2] == {"desc": True}


@pytest.mark.asyncio
async def test_list_active_rules_filters_is_active(
    repo: AlertRulesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "r1"}]
    await repo.list_active_rules()

    eq = next(c for c in fake_query.calls if c[0] == "eq")
    assert eq[1] == ("is_active", True)


@pytest.mark.asyncio
async def test_create_rule_returns_first_row(
    repo: AlertRulesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "r1", "name": "New"}]
    row = await repo.create_rule({"name": "New"})
    assert row == {"id": "r1", "name": "New"}


@pytest.mark.asyncio
async def test_create_rule_returns_none_on_empty(
    repo: AlertRulesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    row = await repo.create_rule({"name": "x"})
    assert row is None


@pytest.mark.asyncio
async def test_update_rule_always_stamps_updated_at(
    repo: AlertRulesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "r1"}]
    await repo.update_rule("r1", {"threshold": 5.0})

    update = next(c for c in fake_query.calls if c[0] == "update")
    payload = update[1][0]
    assert payload["threshold"] == 5.0
    # updated_at must be auto-injected
    assert "updated_at" in payload


@pytest.mark.asyncio
async def test_delete_rule_hits_correct_id(
    repo: AlertRulesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.delete_rule("r1")

    eq = next(c for c in fake_query.calls if c[0] == "eq")
    assert eq[1] == ("id", "r1")


@pytest.mark.asyncio
async def test_auto_unmute_clears_mute_fields(
    repo: AlertRulesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "r1"}]
    await repo.auto_unmute_rule("r1")

    update = next(c for c in fake_query.calls if c[0] == "update")
    payload = update[1][0]
    assert payload["is_muted"] is False
    assert payload["mute_until"] is None


# ─── History ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_history_filters_and_pagination(
    repo: AlertRulesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
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

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("rule_id", "r1") in eq_values
    assert ("resolved", False) in eq_values

    range_call = next(c for c in fake_query.calls if c[0] == "range")
    assert range_call[1] == (25, 49)


@pytest.mark.asyncio
async def test_insert_history_sends_payload(
    repo: AlertRulesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.insert_history({"rule_id": "r1", "metric_value": 99})

    insert = next(c for c in fake_query.calls if c[0] == "insert")
    assert insert[1] == ({"rule_id": "r1", "metric_value": 99},)


@pytest.mark.asyncio
async def test_resolve_history_stamps_resolved_at(
    repo: AlertRulesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.resolve_history("alert-1")

    update = next(c for c in fake_query.calls if c[0] == "update")
    payload = update[1][0]
    assert payload["resolved"] is True
    assert "resolved_at" in payload


# ─── Metric queries (for alert evaluation) ─────────────────────────


@pytest.mark.asyncio
async def test_request_status_codes_filters_since(
    repo: AlertRulesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"status_code": 200}, {"status_code": 500}]
    rows = await repo.request_status_codes("2026-04-17")
    assert len(rows) == 2

    gte = next(c for c in fake_query.calls if c[0] == "gte")
    assert gte[1] == ("timestamp", "2026-04-17")

    tables = [c for c in fake_query.calls if c[0] == "table"]
    assert tables[0][1] == ("api_request_logs",)


@pytest.mark.asyncio
async def test_request_response_times_selects_column(
    repo: AlertRulesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"response_time_ms": 42}]
    await repo.request_response_times("2026-04-17")

    select = next(c for c in fake_query.calls if c[0] == "select")
    assert select[1] == ("response_time_ms",)


@pytest.mark.asyncio
async def test_app_log_count_by_levels_uses_in_filter(
    repo: AlertRulesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._count = 7
    count = await repo.app_log_count_by_levels(
        ["ERROR", "CRITICAL"], "2026-04-17"
    )
    assert count == 7

    in_call = next(c for c in fake_query.calls if c[0] == "in_")
    assert in_call[1] == ("level", ["ERROR", "CRITICAL"])


@pytest.mark.asyncio
async def test_app_log_count_by_level_uses_eq_filter(
    repo: AlertRulesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._count = 3
    count = await repo.app_log_count_by_level("CRITICAL", "2026-04-17")
    assert count == 3

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("level", "CRITICAL") in eq_values
