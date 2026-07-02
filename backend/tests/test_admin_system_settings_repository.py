"""Unit tests for SystemSettingsRepository + AdminTablePreferencesRepository.

Both are now SQLAlchemy 2.0 ORM implementations (the legacy supabase-py REST
paths were retired with USE_ORM_ADMIN_SYSTEM_SETTINGS and
USE_ORM_ADMIN_TABLE_PREFERENCES respectively). Their tests mock
``read_scope``/``write_scope`` with a fake session that captures every emitted
``(compiled sql, binds)`` pair and returns configured ORM row objects / scalar
values, so the compiled SQL shape + bind params AND (for system_settings) the
value-type parity sweep (updated_by uuid → str, updated_at timestamptz → ISO
str, value/options jsonb → native dict) are asserted WITHOUT a live database
(the DSN-gated integration suites in
``tests/integration/test_system_settings_repository_orm.py`` and
``tests/integration/test_table_preferences_repository_orm.py`` exercise the real
round-trip).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

import app.repositories.admin.system_settings_repository as settings_mod
import app.repositories.admin.table_preferences_repository as prefs_mod
from app.models import SystemSettings
from app.repositories.admin.system_settings_repository import (
    SystemSettingsRepository,
)
from app.repositories.admin.table_preferences_repository import (
    AdminTablePreferencesRepository,
)

# ─── ORM fake session (SystemSettingsRepository) ───────────────────


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Captures execute/scalar (compiled sql, binds); returns configured rows /
    scalar."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.rows: list[Any] = []
        self.scalar_value: Any = None
        self.raise_on_scalar: Exception | None = None

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(_compile(stmt))
        return _FakeResult(self.rows)

    async def scalar(self, stmt: Any) -> Any:
        self.calls.append(_compile(stmt))
        if self.raise_on_scalar is not None:
            raise self.raise_on_scalar
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
    monkeypatch.setattr(settings_mod, "read_scope", lambda: _ScopeCM(session))
    monkeypatch.setattr(settings_mod, "write_scope", lambda: _ScopeCM(session))
    return session


@pytest.fixture
def settings_repo() -> SystemSettingsRepository:
    return SystemSettingsRepository()


# ─── SystemSettingsRepository (ORM) ────────────────────────────────


@pytest.mark.asyncio
async def test_list_non_transcode_excludes_transcode_prefix(
    settings_repo: SystemSettingsRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [
        SystemSettings(key="transcode_enabled", value=True),
        SystemSettings(key="transcode_tiers", value="720p"),
        SystemSettings(key="something_else", value="ok"),
        SystemSettings(key="another_setting", value=42),
    ]
    rows = await settings_repo.list_non_transcode()
    keys = [r["key"] for r in rows]
    assert "something_else" in keys
    assert "another_setting" in keys
    assert "transcode_enabled" not in keys
    assert "transcode_tiers" not in keys

    sql, _ = fake_session.calls[-1]
    assert "system_settings" in sql
    assert "ORDER BY public.system_settings.key" in sql  # ordered by key


@pytest.mark.asyncio
async def test_list_non_transcode_parity_sweep(
    settings_repo: SystemSettingsRepository, fake_session: _FakeSession
) -> None:
    admin = uuid.uuid4()
    updated_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    fake_session.rows = [
        SystemSettings(
            key="visible",
            value={"a": 1},
            updated_by=admin,
            updated_at=updated_at,
        ),
    ]
    rows = await settings_repo.list_non_transcode()
    row = rows[0]
    assert row["value"] == {"a": 1}  # jsonb → native dict
    assert row["updated_by"] == str(admin)  # uuid → str
    assert type(row["updated_by"]) is str
    assert row["updated_at"] == updated_at.isoformat()  # tstz → ISO str
    assert type(row["updated_at"]) is str


@pytest.mark.asyncio
async def test_exists_true_when_scalar_present(
    settings_repo: SystemSettingsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = "x"
    assert await settings_repo.exists("x") is True

    sql, binds = fake_session.calls[-1]
    assert "system_settings.key = " in sql
    assert "x" in binds.values()


@pytest.mark.asyncio
async def test_exists_false_on_none(
    settings_repo: SystemSettingsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = None
    assert await settings_repo.exists("x") is False


@pytest.mark.asyncio
async def test_exists_false_on_exception(
    settings_repo: SystemSettingsRepository, fake_session: _FakeSession
) -> None:
    fake_session.raise_on_scalar = RuntimeError("boom")
    assert await settings_repo.exists("x") is False


@pytest.mark.asyncio
async def test_update_returns_row_and_binds(
    settings_repo: SystemSettingsRepository, fake_session: _FakeSession
) -> None:
    admin = uuid.uuid4()
    fake_session.rows = [
        SystemSettings(key="x", value={"v": 1}, updated_by=admin),
    ]
    row = await settings_repo.update("x", {"v": 1}, str(admin))
    assert row["key"] == "x"
    assert row["value"] == {"v": 1}
    assert row["updated_by"] == str(admin)

    sql, binds = fake_session.calls[-1]
    assert "UPDATE public.system_settings SET" in sql
    assert "RETURNING" in sql
    assert {"v": 1} in binds.values()
    assert str(admin) in binds.values()


@pytest.mark.asyncio
async def test_update_returns_none_on_empty(
    settings_repo: SystemSettingsRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    row = await settings_repo.update("x", "v", "admin")
    assert row is None


@pytest.mark.asyncio
async def test_upsert_uses_on_conflict_and_returns_row(
    settings_repo: SystemSettingsRepository, fake_session: _FakeSession
) -> None:
    admin = uuid.uuid4()
    fake_session.rows = [
        SystemSettings(key="x", value={"v": 2}, updated_by=admin),
    ]
    row = await settings_repo.upsert_setting("x", {"v": 2}, str(admin))
    assert row["key"] == "x"
    assert row["value"] == {"v": 2}

    sql, binds = fake_session.calls[-1]
    assert "INSERT INTO public.system_settings" in sql
    assert "ON CONFLICT (key) DO UPDATE" in sql
    assert "RETURNING" in sql
    assert "x" in binds.values()
    assert {"v": 2} in binds.values()


@pytest.mark.asyncio
async def test_upsert_falls_back_when_no_row_returned(
    settings_repo: SystemSettingsRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    row = await settings_repo.upsert_setting("x", {"v": 3}, "admin")
    assert row == {"key": "x", "value": {"v": 3}}  # REST-parity fallback shape


# ─── AdminTablePreferencesRepository (ORM) ──────────────────────────


@pytest.fixture
def prefs_fake_session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    session = _FakeSession()
    monkeypatch.setattr(prefs_mod, "read_scope", lambda: _ScopeCM(session))
    monkeypatch.setattr(prefs_mod, "write_scope", lambda: _ScopeCM(session))
    return session


@pytest.fixture
def prefs_repo() -> AdminTablePreferencesRepository:
    return AdminTablePreferencesRepository()


def _prefs_row(
    table_key: str = "users",
    filters: Any = None,
    sorts: Any = None,
    visible_columns: Any = None,
    column_order: Any = None,
) -> SimpleNamespace:
    """A fake Row exposing the 5 projected attrs via getattr (like a real
    SQLAlchemy Row keyed by the _PROJECTION column labels)."""
    return SimpleNamespace(
        table_key=table_key,
        filters=filters if filters is not None else [],
        sorts=sorts if sorts is not None else [],
        visible_columns=visible_columns,
        column_order=column_order,
    )


@pytest.mark.asyncio
async def test_get_filters_user_and_table_key(
    prefs_repo: AdminTablePreferencesRepository, prefs_fake_session: _FakeSession
) -> None:
    prefs_fake_session.rows = [_prefs_row(table_key="users")]
    row = await prefs_repo.get("user-1", "users")
    assert row["table_key"] == "users"

    sql, binds = prefs_fake_session.calls[-1]
    assert "admin_table_preferences" in sql
    assert "user_id" in sql and "table_key" in sql
    assert "user-1" in binds.values()
    assert "users" in binds.values()


@pytest.mark.asyncio
async def test_get_returns_none_on_empty(
    prefs_repo: AdminTablePreferencesRepository, prefs_fake_session: _FakeSession
) -> None:
    prefs_fake_session.rows = []
    row = await prefs_repo.get("user-1", "users")
    assert row is None


@pytest.mark.asyncio
async def test_upsert_uses_on_conflict(
    prefs_repo: AdminTablePreferencesRepository, prefs_fake_session: _FakeSession
) -> None:
    prefs_fake_session.rows = [
        _prefs_row(
            table_key="users",
            filters=[{"field": "status"}],
            sorts=[{"field": "created_at"}],
            visible_columns=["id", "email"],
            column_order=["id", "email"],
        )
    ]
    row = await prefs_repo.upsert(
        user_id="user-1",
        table_key="users",
        filters=[{"field": "status"}],
        sorts=[{"field": "created_at"}],
        visible_columns=["id", "email"],
        column_order=["id", "email"],
    )
    assert row["table_key"] == "users"
    assert row["filters"] == [{"field": "status"}]

    sql, binds = prefs_fake_session.calls[-1]
    assert "INSERT INTO" in sql and "admin_table_preferences" in sql
    assert "ON CONFLICT (user_id, table_key) DO UPDATE" in sql
    assert "RETURNING" in sql
    assert "user-1" in binds.values()
    assert "users" in binds.values()
    assert [{"field": "status"}] in binds.values()


@pytest.mark.asyncio
async def test_upsert_returns_none_on_empty(
    prefs_repo: AdminTablePreferencesRepository, prefs_fake_session: _FakeSession
) -> None:
    prefs_fake_session.rows = []
    row = await prefs_repo.upsert(
        user_id="user-1",
        table_key="users",
        filters=[],
        sorts=[],
        visible_columns=None,
        column_order=None,
    )
    assert row is None


@pytest.mark.asyncio
async def test_delete_targets_user_and_table_key(
    prefs_repo: AdminTablePreferencesRepository, prefs_fake_session: _FakeSession
) -> None:
    await prefs_repo.delete("user-1", "users")

    sql, binds = prefs_fake_session.calls[-1]
    assert "DELETE FROM" in sql and "admin_table_preferences" in sql
    assert "user-1" in binds.values()
    assert "users" in binds.values()
