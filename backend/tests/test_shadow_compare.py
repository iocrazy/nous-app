"""Tests for the ORM 2.0 shadow-compare parity harness.

Covers the four guarantees the rollout depends on:
  * reads return the REST result unchanged (caller never sees ORM output);
  * a REST/ORM divergence is diff-logged (but the REST value is still returned);
  * writes pass through to REST only (ORM is never invoked → no double-mutation);
  * an ORM-side exception is swallowed (request still returns REST) + logged;
plus ``shadow_enabled`` parsing of ``SHADOW_ORM_DOMAINS``.

No DB needed — REST/ORM repos are fakes, and the loguru sink is asserted via a
captured-record handler.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.db import shadow_compare
from app.db.shadow_compare import (
    ShadowRepo,
    diff_results,
    is_read_method,
    shadow_enabled,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeRestRepo:
    """REST repo: records which methods were called + returns canned values."""

    def __init__(self, read_value: Any) -> None:
        self._read_value = read_value
        self.calls: list[str] = []

    async def get_thing(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append("get_thing")
        return self._read_value

    async def list_things(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append("list_things")
        return self._read_value

    async def create_thing(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append("create_thing")
        return {"created": True}

    async def update_thing(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append("update_thing")
        return {"updated": True}


class FakeOrmRepo:
    """ORM repo: records calls; may diverge or raise per construction."""

    def __init__(self, read_value: Any = None, raises: bool = False) -> None:
        self._read_value = read_value
        self._raises = raises
        self.calls: list[str] = []

    async def get_thing(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append("get_thing")
        if self._raises:
            raise RuntimeError("orm boom")
        return self._read_value

    async def list_things(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append("list_things")
        if self._raises:
            raise RuntimeError("orm boom")
        return self._read_value

    async def create_thing(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append("create_thing")
        return {"created": "ORM"}

    async def update_thing(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append("update_thing")
        return {"updated": "ORM"}


@pytest.fixture
def shadow_logs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture every record emitted by the shadow module's patched logger."""
    captured: list[dict[str, Any]] = []

    def _sink(message: Any) -> None:
        rec = message.record
        captured.append(
            {
                "level": rec["level"].name,
                "message": str(rec["message"]),
                "name": rec.get("name"),
                "extra": dict(rec.get("extra", {})),
            }
        )

    handler_id = shadow_compare.logger.add(_sink, level="WARNING")
    yield captured
    shadow_compare.logger.remove(handler_id)


async def _drain_tasks() -> None:
    """Let the fire-and-forget shadow task run to completion."""
    # Yield control twice so create_task'd coroutine schedules + finishes.
    await asyncio.sleep(0)
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# shadow_enabled / prefix parsing
# ---------------------------------------------------------------------------


def test_shadow_enabled_parses_comma_list(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(
        settings, "SHADOW_ORM_DOMAINS", "admin_audit_logs, Admin_Monitoring ,"
    )
    assert shadow_enabled("admin_audit_logs") is True
    assert shadow_enabled("admin_monitoring") is True  # case-insensitive
    assert shadow_enabled("ADMIN_MONITORING") is True
    assert shadow_enabled("admin_stats") is False


def test_shadow_enabled_empty_is_all_false(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "SHADOW_ORM_DOMAINS", "")
    assert shadow_enabled("admin_audit_logs") is False
    assert shadow_enabled("anything") is False


def test_is_read_method_prefixes() -> None:
    for name in (
        "get_x",
        "list_x",
        "find_x",
        "count_x",
        "search_x",
        "fetch_x",
        "exists_x",
    ):
        assert is_read_method(name) is True
    for name in ("create_x", "update_x", "delete_x", "save_x", "upsert_x"):
        assert is_read_method(name) is False


# ---------------------------------------------------------------------------
# diff_results
# ---------------------------------------------------------------------------


def test_diff_results_equal_returns_none() -> None:
    assert diff_results({"a": 1, "b": "x"}, {"a": 1, "b": "x"}) is None
    assert diff_results([{"id": 1}], [{"id": 1}]) is None
    assert diff_results(5, 5) is None


def test_diff_results_dict_mismatch() -> None:
    diff = diff_results({"a": 1}, {"a": 2})
    assert diff is not None and "a:" in diff


def test_diff_results_list_length() -> None:
    diff = diff_results([1, 2], [1])
    assert diff is not None and "length" in diff


def test_diff_results_order_sensitive() -> None:
    # Same rows, different order → a real finding (REST order is the contract).
    diff = diff_results([{"id": 1}, {"id": 2}], [{"id": 2}, {"id": 1}])
    assert diff is not None


def test_diff_results_caps_size() -> None:
    big_rest = {"k": "a" * 10000}
    big_orm = {"k": "b" * 10000}
    diff = diff_results(big_rest, big_orm)
    assert diff is not None
    assert len(diff) <= shadow_compare._MAX_DIFF_CHARS


# ---------------------------------------------------------------------------
# ShadowRepo — read parity (happy path)
# ---------------------------------------------------------------------------


async def test_read_returns_rest_result_unchanged(
    shadow_logs: list[dict[str, Any]],
) -> None:
    rest = FakeRestRepo(read_value={"id": 1, "name": "x"})
    orm = FakeOrmRepo(read_value={"id": 1, "name": "x"})  # parity
    shadow = ShadowRepo(rest, orm, "admin_audit_logs")

    result = await shadow.get_thing(7, foo="bar")
    await _drain_tasks()

    assert result == {"id": 1, "name": "x"}
    assert result is rest._read_value  # the REST object, not the ORM one
    assert rest.calls == ["get_thing"]
    assert orm.calls == ["get_thing"]  # ORM ran in the background
    # Parity held → no mismatch logged.
    mismatches = [r for r in shadow_logs if "mismatch" in r["message"]]
    assert mismatches == []


async def test_read_mismatch_is_logged_but_rest_returned(
    shadow_logs: list[dict[str, Any]],
) -> None:
    rest = FakeRestRepo(read_value={"id": 1, "status": "active"})
    orm = FakeOrmRepo(read_value={"id": 1, "status": "ACTIVE"})  # diverges
    shadow = ShadowRepo(rest, orm, "admin_audit_logs")

    result = await shadow.get_thing()
    await _drain_tasks()

    # Caller still gets the REST value.
    assert result == {"id": 1, "status": "active"}
    # A mismatch was logged at WARNING with module=orm_shadow.
    mismatches = [r for r in shadow_logs if "mismatch" in r["message"]]
    assert len(mismatches) == 1
    rec = mismatches[0]
    assert rec["level"] == "WARNING"
    assert rec["name"] == "orm_shadow"  # → application_logs.module
    assert rec["extra"].get("domain") == "admin_audit_logs"
    assert rec["extra"].get("method") == "get_thing"
    assert "status" in (rec["extra"].get("diff") or "")


# ---------------------------------------------------------------------------
# ShadowRepo — writes pass through to REST only
# ---------------------------------------------------------------------------


async def test_write_passes_through_to_rest_only(
    shadow_logs: list[dict[str, Any]],
) -> None:
    rest = FakeRestRepo(read_value=None)
    orm = FakeOrmRepo()
    shadow = ShadowRepo(rest, orm, "admin_audit_logs")

    created = await shadow.create_thing({"x": 1})
    updated = await shadow.update_thing(1, {"x": 2})
    await _drain_tasks()

    assert created == {"created": True}  # REST result
    assert updated == {"updated": True}
    assert rest.calls == ["create_thing", "update_thing"]
    assert orm.calls == []  # ORM NEVER invoked on writes → no double-mutation


# ---------------------------------------------------------------------------
# ShadowRepo — ORM exception does not propagate
# ---------------------------------------------------------------------------


async def test_orm_exception_does_not_propagate(
    shadow_logs: list[dict[str, Any]],
) -> None:
    rest = FakeRestRepo(read_value=[{"id": 1}])
    orm = FakeOrmRepo(raises=True)
    shadow = ShadowRepo(rest, orm, "admin_audit_logs")

    # Must NOT raise even though the ORM side blows up.
    result = await shadow.list_things()
    await _drain_tasks()

    assert result == [{"id": 1}]  # REST result returned
    failures = [r for r in shadow_logs if "failure" in r["message"]]
    assert len(failures) == 1
    assert failures[0]["level"] == "WARNING"
    assert failures[0]["name"] == "orm_shadow"


# ---------------------------------------------------------------------------
# ShadowRepo — non-callable + unknown attribute passthrough
# ---------------------------------------------------------------------------


async def test_non_callable_attribute_passthrough() -> None:
    rest = FakeRestRepo(read_value=None)
    rest.TABLE = "audit_logs"  # type: ignore[attr-defined]
    orm = FakeOrmRepo()
    shadow = ShadowRepo(rest, orm, "admin_audit_logs")
    assert shadow.TABLE == "audit_logs"


# ---------------------------------------------------------------------------
# ShadowRepo — background task is strongly held then discarded (no GC race)
# ---------------------------------------------------------------------------


async def test_bg_task_is_tracked_then_discarded(
    shadow_logs: list[dict[str, Any]],
) -> None:
    rest = FakeRestRepo(read_value={"id": 1})
    orm = FakeOrmRepo(read_value={"id": 1})
    shadow = ShadowRepo(rest, orm, "admin_audit_logs")

    assert shadow_compare._BG_TASKS == set()
    await shadow.get_thing()
    # A strong ref is held while the shadow task is in flight (GC can't cancel).
    assert len(shadow_compare._BG_TASKS) == 1

    await _drain_tasks()
    # Done-callback discards it → no leak.
    assert shadow_compare._BG_TASKS == set()
