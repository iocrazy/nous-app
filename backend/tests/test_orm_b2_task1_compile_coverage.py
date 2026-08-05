"""Compile-level coverage for Phase B2 Task 1 (settings/logs 域 — AI 系 11
文件裸 SQL → ORM).

Highest-risk item in this batch: ``provider_health.persist_provider_health``'s
nested ``jsonb_set`` UPDATE (``jsonb_set(jsonb_set(coalesce(...), '{path}',
coalesce(...), true), '{path,pk}', CAST(...), true)``) — the only write
(the other 10 files are single-column ``SELECT value WHERE key = ...``
reads) and the only place a wrong operator/nesting order would silently
write to the wrong jsonb path or clobber sibling provider entries. Same
technique as ``tests/test_orm_b1_compile_coverage.py`` /
``tests/test_orm_b1_task2_compile_coverage.py``: capture the compiled
statement handed to the session and assert with MUTUALLY EXCLUSIVE
assertions that the right operator/shape survived the rewrite.

The remaining 10 files share one trivial shape (``SELECT value FROM
system_settings WHERE key = :k`` → ``select(SystemSettings.value).where(...)``
via ``session.scalar()``); per the migration brief only 2-3 representative
call sites need dedicated coverage, picked for shape variety:
  - ``ai_governance._read_raw``            — the plain single-key scalar read.
  - ``embedding_config._read_settings``    — the one multi-key ``IN (...)``
    read in this batch (``.in_()`` + ``.mappings()``, not ``.scalar()``).
  - ``ai_provider_helpers.resolve_transcription_config`` — the one
    ``user_settings`` (not ``system_settings``) read, reached through the
    real public function (governance mocked out) to prove the wiring, not
    just the shape.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.core import secure_settings
from app.db import engine as db_engine
from app.db import session as db_session
from app.services.ai import provider_health
from app.services.ai.governance import ai_governance
from app.services.ai.providers import ai_provider_helpers, embedding_config


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeResult:
    def __init__(
        self, scalar: Any = None, rows: list[Any] | None = None, rowcount: int = 1
    ) -> None:
        self._scalar = scalar
        self._rows = rows or []
        self.rowcount = rowcount

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def mappings(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """Captures every (compiled_sql, binds) pair handed to execute()/scalar()."""

    def __init__(self, result: _FakeResult | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._result = result or _FakeResult()

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(_compile(stmt))
        return self._result

    async def scalar(self, stmt: Any) -> Any:
        self.calls.append(_compile(stmt))
        return self._result._scalar


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _all_sql(session: _FakeSession) -> str:
    return "\n".join(sql for sql, _ in session.calls)


def _all_binds(session: _FakeSession) -> list[Any]:
    values: list[Any] = []
    for _sql, params in session.calls:
        values.extend(params.values())
    return values


# ─── provider_health.persist_provider_health — nested jsonb_set UPDATE ─────


async def test_persist_provider_health_writes_nested_jsonb_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_engine, "is_configured", lambda: True)

    class _FakeCache:
        def invalidate(self, _user_id: str) -> None:
            return None

    monkeypatch.setattr(
        "app.core.cache.user_settings_cache", _FakeCache(), raising=False
    )

    session = _FakeSession(_FakeResult(rowcount=1))
    monkeypatch.setattr(db_session, "write_scope", lambda: _ScopeCM(session))

    ok = await provider_health.persist_provider_health(
        "11111111-1111-4111-8111-111111111111", "openai", "ok", "reachable"
    )

    assert ok is True
    sql = _all_sql(session)

    # Top-level shape: UPDATE ... SET settings_json = jsonb_set(jsonb_set(...
    assert "UPDATE public.user_settings SET settings_json=jsonb_set(jsonb_set(" in sql
    assert "WHERE public.user_settings.user_id = " in sql

    # Inner jsonb_set: coalesce(existing settings_json, '{}') as the target,
    # ARRAY['ai_provider_health'] as the path, coalesce(settings_json->key, '{}')
    # as the ensure-object-exists value.
    assert "coalesce(public.user_settings.settings_json, CAST(" in sql
    assert "coalesce(public.user_settings.settings_json -> " in sql
    # MUTUALLY EXCLUSIVE: the extraction must be the JSONB-typed `->`, never
    # the text-returning `->>` (a `->>` here would jsonb_set a string, not an
    # object, and blow up create_missing semantics).
    assert "->>" not in sql

    # Outer jsonb_set: two-element ARRAY path (['ai_provider_health', pk]) and
    # a CAST(... AS JSONB) new-value — never a bare string assignment.
    assert sql.count("ARRAY[") == 2
    assert "CAST(" in sql

    binds = _all_binds(session)
    assert "ai_provider_health" in binds
    assert "openai" in binds  # the provider_key bound into the ARRAY path
    assert True in binds  # create_missing=true, twice (inner + outer jsonb_set)


async def test_persist_provider_health_skips_when_engine_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_engine, "is_configured", lambda: False)

    session = _FakeSession()
    monkeypatch.setattr(db_session, "write_scope", lambda: _ScopeCM(session))

    ok = await provider_health.persist_provider_health("u1", "openai", "ok", "")

    assert ok is False
    assert session.calls == []  # never touched the DB


# ─── ai_governance._read_raw — plain single-key scalar read ───────────────


async def test_read_raw_selects_value_by_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db_engine, "is_configured", lambda: True)
    monkeypatch.setattr(secure_settings, "reveal", lambda v: v)

    session = _FakeSession(_FakeResult(scalar=True))
    monkeypatch.setattr(db_session, "read_scope", lambda: _ScopeCM(session))

    value = await ai_governance._read_raw("ai_module.chat.user_allowed")

    assert value is True  # native JSONB bool, not the string "true"
    sql = _all_sql(session)
    assert "SELECT public.system_settings.value" in sql
    assert "WHERE public.system_settings.key = " in sql
    assert "ai_module.chat.user_allowed" in _all_binds(session)


# ─── embedding_config._read_settings — multi-key IN (...) read ────────────


async def test_read_settings_uses_in_clause_over_all_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_engine, "is_configured", lambda: True)
    monkeypatch.setattr(secure_settings, "reveal", lambda v: v)

    rows = [
        {"key": "graph_embedder_base_url", "value": "http://x"},
        {"key": "graph_embedder_model", "value": "m1"},
    ]
    session = _FakeSession(_FakeResult(rows=rows))
    monkeypatch.setattr(db_session, "read_scope", lambda: _ScopeCM(session))

    out = await embedding_config._read_settings()

    assert out == {"graph_embedder_base_url": "http://x", "graph_embedder_model": "m1"}
    sql = _all_sql(session)
    assert "SELECT public.system_settings.key, public.system_settings.value" in sql
    assert "WHERE public.system_settings.key IN (" in sql
    # all 4 _KEYS bound (as the IN-clause list param), not just the ones
    # present in the fake result rows
    binds = _all_binds(session)
    in_clause_values = next(v for v in binds if isinstance(v, list))
    for key in embedding_config._KEYS:
        assert key in in_clause_values


# ─── ai_provider_helpers.resolve_transcription_config — user_settings read ─


async def test_resolve_transcription_config_selects_settings_json_by_user_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Governance not locked -> falls through to the user_settings read.
    # ai_provider_helpers imports resolve_locked_module_config INSIDE the
    # function body each call, so patching the SOURCE module's attribute
    # (ai_governance, already imported above) is what takes effect.
    monkeypatch.setattr(
        ai_governance, "resolve_locked_module_config", AsyncMock(return_value=None)
    )

    session = _FakeSession(_FakeResult(scalar=None))
    monkeypatch.setattr(db_session, "read_scope", lambda: _ScopeCM(session))

    with pytest.raises(RuntimeError, match="no user_settings for u1"):
        await ai_provider_helpers.resolve_transcription_config("u1")

    sql = _all_sql(session)
    assert "SELECT public.user_settings.settings_json" in sql
    assert "WHERE public.user_settings.user_id = " in sql
    assert "u1" in _all_binds(session)
