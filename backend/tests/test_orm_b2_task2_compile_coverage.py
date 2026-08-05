"""Compile-level coverage for Phase B2 Task 2 (settings/logs 域 infra/media/
workflows — 9 文件).

Highest-risk items in this batch, none of which Task 1's coverage touched:

  - ``temp_ttl_settings._upsert_settings_key`` (personal branch) is the
    first ``INSERT ... ON CONFLICT DO UPDATE`` in this migration series that
    also does a jsonb ``||`` MERGE inside the ``DO UPDATE SET`` (Task 1's
    ``provider_health`` write was a plain ``UPDATE``, not an upsert). The
    legacy SQL merged against ``public.user_settings.settings_json`` — the
    EXISTING (pre-conflict) row — not ``excluded.settings_json`` (the
    proposed INSERT value); getting this wrong would silently switch the
    merge from "preserve other keys in the existing row" to "there is no
    existing row to preserve, this VALUES tuple is a mostly-empty jsonb
    object" on every conflict.
  - ``temp_ttl_settings._upsert_settings_key`` (team branch) / the CAST
    used by ``_resolve_personal_user_id`` — both compare
    ``CAST(<bigint column> AS VARCHAR)`` against a bound TEXT param
    (never ``CAST(:param AS BIGINT)``) — the documented asyncpg strict-typing
    trap (binding a Python ``str`` to a bigint-cast bind raises DataError).
  - ``secrets_selfheal._heal_user_settings_ai_providers`` — the nested
    ``->`` (JSONB-typed, never ``->>``) two-level read, and the single-path
    ``jsonb_set`` write. Same operator-equivalence risk Task 1 pinned for
    ``provider_health``, but a different call site/shape (single jsonb_set,
    not nested).
  - The remaining ``secrets_selfheal`` write paths
    (``_heal_system_settings_flat`` / ``_heal_platform_providers`` /
    ``_heal_mediahub_models`` / ``_heal_user_mcp_servers``) are simple
    UPDATEs, but per the migration brief this module is a STARTUP GATE
    (``readyz``) — every write path gets dedicated coverage regardless of
    shape simplicity.

Same technique as ``tests/test_orm_b2_task1_compile_coverage.py``: capture
the compiled statement(s) handed to the session and assert with MUTUALLY
EXCLUSIVE assertions that the right operator/shape survived the rewrite.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.db import session as db_session
from app.services.infra import secrets_selfheal
from app.services.library import temp_ttl_settings


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeResult:
    def __init__(self, scalar: Any = None, rows: list[Any] | None = None) -> None:
        self._scalar = scalar
        self._rows = rows if rows is not None else []

    def mappings(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows


class _RecordingSession:
    """Appends every compiled (sql, binds) pair to a SHARED ``calls`` list
    so a function that opens read_scope() once and write_scope() N times
    (once per healed row) can be asserted on as one combined trace."""

    def __init__(self, calls: list[Any], result: _FakeResult | None = None) -> None:
        self.calls = calls
        self._result = result or _FakeResult()

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(_compile(stmt))
        return self._result

    async def scalar(self, stmt: Any) -> Any:
        self.calls.append(_compile(stmt))
        return self._result._scalar


class _ScopeCM:
    def __init__(self, session: _RecordingSession) -> None:
        self._session = session

    async def __aenter__(self) -> _RecordingSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _all_sql(calls: list[Any]) -> str:
    return "\n".join(sql for sql, _ in calls)


def _all_binds(calls: list[Any]) -> list[Any]:
    values: list[Any] = []
    for _sql, params in calls:
        values.extend(params.values())
    return values


def _patch_scopes(
    monkeypatch: pytest.MonkeyPatch, read_result: _FakeResult | None = None
) -> list[Any]:
    """Patch db_session.read_scope/write_scope to share one ``calls`` list.
    The first read_scope() call returns ``read_result``; every write_scope()
    call (there may be several, one per healed row) returns a fresh
    no-op-result recording session sharing the same list."""
    calls: list[Any] = []
    monkeypatch.setattr(
        db_session,
        "read_scope",
        lambda: _ScopeCM(_RecordingSession(calls, read_result)),
    )
    monkeypatch.setattr(
        db_session, "write_scope", lambda: _ScopeCM(_RecordingSession(calls))
    )
    return calls


# ─── temp_ttl_settings._upsert_settings_key — personal (ON CONFLICT + ||) ──


async def test_upsert_personal_on_conflict_merges_against_existing_row_not_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_scopes(monkeypatch)

    # A UUID (non-digit) scope_id short-circuits _resolve_personal_user_id
    # without touching the DB, isolating this test to the upsert statement.
    user_id = "11111111-1111-4111-8111-111111111111"
    await temp_ttl_settings._upsert_settings_key(
        "personal", user_id, "chat_temp_ttl_days", 30
    )

    sql = _all_sql(calls)
    assert "INSERT INTO public.user_settings (user_id, settings_json) VALUES" in sql
    assert "ON CONFLICT (user_id) DO UPDATE SET" in sql
    # The merge target must be the EXISTING row (fully-qualified column ref),
    # never `excluded.settings_json` (the proposed INSERT value) — that
    # would merge against an empty/mostly-empty tuple instead of the row
    # already in the table (the #485 clobber rule, upsert edition).
    assert "coalesce(public.user_settings.settings_json, CAST(" in sql
    assert "excluded.settings_json" not in sql
    # jsonb_build_object appears twice: once for the INSERT VALUES, once
    # for the ON CONFLICT DO UPDATE SET merge operand.
    assert sql.count("jsonb_build_object(") == 2
    assert " || jsonb_build_object(" in sql
    assert "to_jsonb(CAST(" in sql and "AS INTEGER))" in sql

    binds = _all_binds(calls)
    assert "chat_temp_ttl_days" in binds
    assert 30 in binds
    assert user_id in binds


# ─── temp_ttl_settings._upsert_settings_key — team (plain UPDATE + ||) ─────


async def test_upsert_team_updates_via_cast_id_to_text_not_param_to_bigint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_scopes(monkeypatch)

    await temp_ttl_settings._upsert_settings_key("team", "123", "chat_temp_ttl_days", 7)

    sql = _all_sql(calls)
    assert "UPDATE public.teams SET settings_json=" in sql
    assert "coalesce(public.teams.settings_json, CAST(" in sql
    assert " || jsonb_build_object(" in sql
    # asyncpg-safe id comparison: CAST the COLUMN to text, never CAST the
    # bound scope_id param to bigint (binding a str to a bigint-typed bind
    # raises DataError under asyncpg — feedback_asyncpg_bigint_str_strict).
    assert "CAST(public.teams.id AS VARCHAR) = " in sql
    assert "CAST(" in sql and "AS BIGINT)" not in sql

    binds = _all_binds(calls)
    assert "123" in binds
    assert 7 in binds


# ─── temp_ttl_settings._resolve_personal_user_id — CAST both sides ─────────


async def test_resolve_personal_user_id_casts_column_not_param_to_bigint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_scopes(monkeypatch, _FakeResult(scalar="owner-uuid"))

    uid = await temp_ttl_settings._resolve_personal_user_id("999")

    assert uid == "owner-uuid"
    sql = _all_sql(calls)
    assert "CAST(public.teams.owner_id AS VARCHAR)" in sql
    assert "CAST(public.teams.id AS VARCHAR) = " in sql
    assert "public.teams.kind = " in sql
    assert "AS BIGINT)" not in sql  # never casts the :id param to bigint
    binds = _all_binds(calls)
    assert "999" in binds
    assert "personal" in binds


# ─── secrets_selfheal._heal_system_settings_flat ───────────────────────────


async def test_heal_system_settings_flat_reads_in_clause_writes_healed_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        secrets_selfheal, "_heal_marked_value", lambda v: "enc:v1:HEALED"
    )
    calls = _patch_scopes(
        monkeypatch,
        _FakeResult(rows=[{"key": "some.secret.key", "value": "plaintext"}]),
    )

    rewritten = await secrets_selfheal._heal_system_settings_flat()

    assert rewritten == 1
    sql = _all_sql(calls)
    assert "SELECT public.system_settings.key, public.system_settings.value" in sql
    assert "WHERE public.system_settings.key IN (" in sql
    assert "UPDATE public.system_settings SET value=" in sql
    assert "WHERE public.system_settings.key = " in sql
    binds = _all_binds(calls)
    assert "enc:v1:HEALED" in binds
    assert "some.secret.key" in binds


# ─── secrets_selfheal._heal_platform_providers ─────────────────────────────


async def test_heal_platform_providers_writes_merged_dict_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        secrets_selfheal, "_heal_marked_value", lambda v: "enc:v1:HEALED"
    )
    calls = _patch_scopes(
        monkeypatch,
        _FakeResult(scalar={"openai": {"api_key": "plaintext", "app_id": "x"}}),
    )

    rewritten = await secrets_selfheal._heal_platform_providers()

    assert rewritten >= 1
    sql = _all_sql(calls)
    assert "SELECT public.system_settings.value" in sql
    assert "UPDATE public.system_settings SET value=" in sql
    assert "WHERE public.system_settings.key = " in sql
    binds = _all_binds(calls)
    assert secrets_selfheal.PLATFORM_PROVIDERS_KEY in binds
    # the merged dict is bound directly (JSONB column bind-processor
    # serializes it) — never a manually json.dumps()'d string + CAST.
    assert any(isinstance(b, dict) for b in binds)


# ─── secrets_selfheal._heal_mediahub_models ────────────────────────────────


async def test_heal_mediahub_models_guards_null_and_empty_then_writes_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        secrets_selfheal, "_heal_marked_value", lambda v: "enc:v1:HEALED"
    )
    calls = _patch_scopes(
        monkeypatch, _FakeResult(rows=[{"id": 42, "api_key": "plaintext"}])
    )

    rewritten = await secrets_selfheal._heal_mediahub_models()

    assert rewritten == 1
    sql = _all_sql(calls)
    assert "SELECT public.mediahub_models.id, public.mediahub_models.api_key" in sql
    assert "public.mediahub_models.api_key IS NOT NULL" in sql
    assert "public.mediahub_models.api_key != " in sql
    assert "UPDATE public.mediahub_models SET api_key=" in sql
    assert "WHERE public.mediahub_models.id = " in sql
    binds = _all_binds(calls)
    assert "enc:v1:HEALED" in binds
    assert 42 in binds


# ─── secrets_selfheal._heal_user_mcp_servers ───────────────────────────────


async def test_heal_user_mcp_servers_guards_null_and_empty_then_writes_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        secrets_selfheal, "_heal_bearer_token", lambda v: "gAAAAAHEALED"
    )
    calls = _patch_scopes(
        monkeypatch, _FakeResult(rows=[{"id": "mcp-1", "bearer_token": "plaintext"}])
    )

    rewritten = await secrets_selfheal._heal_user_mcp_servers()

    assert rewritten == 1
    sql = _all_sql(calls)
    assert (
        "SELECT public.user_mcp_servers.id, public.user_mcp_servers.bearer_token" in sql
    )
    assert "public.user_mcp_servers.bearer_token IS NOT NULL" in sql
    assert "public.user_mcp_servers.bearer_token != " in sql
    assert "UPDATE public.user_mcp_servers SET bearer_token=" in sql
    assert "WHERE public.user_mcp_servers.id = " in sql
    binds = _all_binds(calls)
    assert "gAAAAAHEALED" in binds
    assert "mcp-1" in binds


# ─── secrets_selfheal._heal_user_settings_ai_providers ─────────────────────


async def test_heal_user_settings_ai_providers_nested_arrow_read_and_jsonb_set_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The nested read must use the JSONB-typed ``->`` (never ``->>`` — a
    ``->>`` here would hand back a serialized STRING for the whole
    ai_providers blob, and ``isinstance(raw, dict)`` would then always be
    False, silently skipping every row). The write is a TARGETED single-path
    ``jsonb_set`` — never a whole-``settings_json`` replace."""
    monkeypatch.setattr(
        secrets_selfheal,
        "_heal_byok_field",
        lambda value, user_id: "enc:v1:HEALED-BOUND",
    )
    calls = _patch_scopes(
        monkeypatch,
        _FakeResult(
            rows=[
                {
                    "user_id": "22222222-2222-4222-8222-222222222222",
                    "ai_providers": {"openai": {"api_key": "plaintext"}},
                }
            ]
        ),
    )

    rewritten = await secrets_selfheal._heal_user_settings_ai_providers()

    assert rewritten == 1
    sql = _all_sql(calls)
    # nested read: two `->` hops, JSONB-typed throughout.
    assert "(public.user_settings.settings_json -> " in sql and ") -> " in sql
    # MUTUALLY EXCLUSIVE: never the text-returning ->> extraction.
    assert "->>" not in sql
    assert "IS NOT NULL" in sql
    # targeted write: jsonb_set on the two-element path, not a bare replace.
    assert "UPDATE public.user_settings SET settings_json=jsonb_set(" in sql
    assert "ARRAY[" in sql
    assert "CAST(" in sql and "AS JSONB)" in sql
    assert "WHERE public.user_settings.user_id = " in sql

    binds = _all_binds(calls)
    assert "ai_settings" in binds
    assert "ai_providers" in binds
    assert "22222222-2222-4222-8222-222222222222" in binds
