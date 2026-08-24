"""Unit tests for MediahubModelRepository (ORM 2.0, model-backed).

Post-rollout the repository is the SQLAlchemy 2.0 implementation — reads go
through ``read_scope()`` and writes through ``write_scope()`` with
``select`` / ``insert`` / ``update`` / ``delete`` statements, and row objects are
converted to SELECT *-shaped dicts by the ``_parity`` value-type sweep. These
tests mock ``read_scope`` / ``write_scope`` with a fake session that captures
every emitted ``(sql, binds)`` pair and returns in-memory model instances, so the
compiled SQL shape + bind params AND the STRATEGY-C value-type parity (BIGINT id
→ native int; Numeric pricing_value → native Decimal; timestamptz → ISO str) are
asserted WITHOUT a live database (the DSN-gated integration suite in
``tests/integration/test_mediahub_model_repository_orm.py`` exercises the real round-trip).
"""

from __future__ import annotations

import datetime as _dt
import decimal as _decimal
from typing import Any

import pytest

import app.repositories.mediahub_model_repository as mod
from app.models import MediahubModels
from app.repositories.mediahub_model_repository import MediahubModelRepository


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeResult:
    """Supports ``.scalars().all()`` / ``.scalars().first()`` (model reads +
    RETURNING) and ``.mappings().all()`` (the list_enabled partial-column SELECT)."""

    def __init__(self, scalar_rows: list[Any], mapping_rows: list[Any]) -> None:
        self._scalar_rows = scalar_rows
        self._mapping_rows = mapping_rows

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._scalar_rows)

    def mappings(self) -> _FakeScalars:
        return _FakeScalars(self._mapping_rows)


class _FakeSession:
    """Captures execute (compiled sql, binds); returns configured rows."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.scalar_rows: list[Any] = []
        self.mapping_rows: list[Any] = []
        self.raises: Exception | None = None

    async def execute(self, stmt: Any, params: Any = None) -> _FakeResult:
        if self.raises is not None:
            raise self.raises
        self.calls.append((str(stmt), stmt.compile().params))
        return _FakeResult(self.scalar_rows, self.mapping_rows)


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
def repo() -> MediahubModelRepository:
    return MediahubModelRepository()


# ─── list_enabled ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_enabled_filters_is_enabled_true(
    repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    fake_session.mapping_rows = [{"id": 1, "name": "nous-base"}]
    rows = await repo.list_enabled()
    assert rows == [{"id": 1, "name": "nous-base", "is_local": False}]

    sql, _ = fake_session.calls[-1]
    assert "mediahub_models" in sql
    assert "is_enabled" in sql
    assert "ORDER BY" in sql and "sort_order" in sql


@pytest.mark.asyncio
async def test_list_enabled_with_type_filter_applies_extra_where(
    repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    fake_session.mapping_rows = []
    await repo.list_enabled("llm")

    sql, binds = fake_session.calls[-1]
    assert "type" in sql
    assert "llm" in binds.values()


@pytest.mark.asyncio
async def test_list_enabled_hides_secret_columns(
    repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    """The public projection selects display columns only — never api_key, and
    never ``description`` (admin-internal ops notes: private IPs / BYOK refs)."""
    fake_session.mapping_rows = []
    await repo.list_enabled()

    sql, _ = fake_session.calls[-1]
    assert "api_key" not in sql
    assert "app_id" not in sql
    assert "base_url" not in sql
    assert "description" not in sql


@pytest.mark.asyncio
async def test_list_enabled_returns_empty_on_error(
    repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    fake_session.raises = RuntimeError("boom")
    assert await repo.list_enabled() == []


# ─── get_by_name ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_by_name_returns_full_row(
    repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = [
        MediahubModels(id=123456789012345678, name="nous-base", api_key="sk")
    ]
    row = await repo.get_by_name("nous-base")
    assert row is not None
    assert row["api_key"] == "sk"
    # bigint id stays native int (5.3 trap).
    assert row["id"] == 123456789012345678 and type(row["id"]) is int

    sql, binds = fake_session.calls[-1]
    assert "mediahub_models" in sql
    assert "nous-base" in binds.values()


@pytest.mark.asyncio
async def test_get_by_name_none_on_error(
    repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    fake_session.raises = RuntimeError("boom")
    assert await repo.get_by_name("nous-base") is None


# ─── list_all ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_all_orders_by_sort_order(
    repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = [MediahubModels(id=1, name="m1")]
    await repo.list_all()

    sql, _ = fake_session.calls[-1]
    assert "mediahub_models" in sql
    assert "ORDER BY" in sql and "sort_order" in sql


@pytest.mark.asyncio
async def test_list_all_no_is_enabled_filter(
    repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    await repo.list_all()

    sql, _ = fake_session.calls[-1]
    assert "WHERE" not in sql  # list_all includes disabled rows


@pytest.mark.asyncio
async def test_list_all_empty(
    repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    assert await repo.list_all() == []


# ─── CRUD ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_filters_to_mapped_attrs_and_returns_row(
    repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = [MediahubModels(id=1, name="nous-new")]
    created = await repo.create({"name": "nous-new", "not_a_column": "dropped"})
    assert created == {"id": 1, "name": "nous-new"} or created["name"] == "nous-new"

    sql, binds = fake_session.calls[-1]
    assert "INSERT INTO public.mediahub_models" in sql
    values = set(binds.values())
    assert "nous-new" in values
    assert "dropped" not in values  # phantom column never bound


@pytest.mark.asyncio
async def test_create_none_on_empty(
    repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    assert await repo.create({"name": "x"}) is None


@pytest.mark.asyncio
async def test_update_drops_now_sentinel_uses_func_now(
    repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    """The legacy 'updated_at=now()' string sentinel is dropped; updated_at is
    set via SQL func.now() (binding the literal string would error)."""
    fake_session.scalar_rows = [MediahubModels(id=1, name="m1")]
    await repo.update("1", {"display_name": "Renamed", "updated_at": "now()"})

    sql, binds = fake_session.calls[-1]
    assert "UPDATE public.mediahub_models" in sql
    values = list(binds.values())
    assert "Renamed" in values
    assert 1 in values  # str model_id "1" → int (bigint bind)
    assert "now()" not in values  # the raw REST sentinel string is NOT bound
    # updated_at rendered as a SQL now() call, not a bound param.
    assert "now()" in sql.lower()


@pytest.mark.asyncio
async def test_update_none_on_empty(
    repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    assert await repo.update("1", {"display_name": "y"}) is None


@pytest.mark.asyncio
async def test_record_test_result_writes_test_columns_only(
    repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    """record_test_result sets last_test_* + last_tested_at, NOT updated_at."""
    fake_session.scalar_rows = [MediahubModels(id=1, name="m1")]
    await repo.record_test_result("1", "ok", "200 OK")

    sql, binds = fake_session.calls[-1]
    assert "UPDATE public.mediahub_models" in sql
    values = list(binds.values())
    assert "ok" in values and "200 OK" in values
    assert 1 in values  # str model_id → int (bigint bind)
    # A connectivity probe is not an edit — updated_at is NOT in the SET clause
    # (it still appears in RETURNING, so scope the check to the SET portion).
    set_clause = sql.split("WHERE")[0]
    assert "updated_at" not in set_clause
    assert "last_test_status" in set_clause and "last_tested_at" in set_clause


@pytest.mark.asyncio
async def test_record_test_result_persists_the_reason_code(
    repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    """The classified failure reason must actually reach the column — the
    classifier being correct is worth nothing if the value stops at the repo."""
    fake_session.scalar_rows = [MediahubModels(id=1, name="m1")]
    await repo.record_test_result(
        "1", "fail", "HTTP 429: SetLimitExceeded", "rate_limit"
    )

    sql, binds = fake_session.calls[-1]
    assert "last_test_code" in sql.split("WHERE")[0]
    assert "rate_limit" in list(binds.values())


@pytest.mark.asyncio
async def test_record_test_result_clears_a_stale_code_on_success(
    repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    """A recovered model must not keep showing last week's reason.

    The column is written on EVERY probe, so the pass path binds NULL rather
    than omitting the column — omitting it would leave 'rate limited' sitting
    next to a green light indefinitely, since the hourly poll is the only
    writer most rows ever get.
    """
    fake_session.scalar_rows = [MediahubModels(id=1, name="m1")]
    await repo.record_test_result("1", "ok", "chat ok")

    sql, binds = fake_session.calls[-1]
    assert "last_test_code" in sql.split("WHERE")[0]
    assert None in list(binds.values())


@pytest.mark.asyncio
async def test_update_parity_bigint_numeric_timestamp(
    repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    """Returned row honours strategy-C parity: bigint id native int, Numeric
    pricing_value native Decimal, timestamptz ISO str."""
    fake_session.scalar_rows = [
        MediahubModels(
            id=123456789012345678,
            name="m1",
            pricing_value=_decimal.Decimal("8"),
            created_at=_dt.datetime(2026, 4, 1, tzinfo=_dt.timezone.utc),
            updated_at=_dt.datetime(2026, 4, 2, tzinfo=_dt.timezone.utc),
        )
    ]
    row = await repo.update("123456789012345678", {"display_name": "X"})
    assert row is not None
    assert row["id"] == 123456789012345678 and type(row["id"]) is int
    assert isinstance(row["pricing_value"], _decimal.Decimal)
    assert float(row["pricing_value"]) == 8.0
    assert row["created_at"] == "2026-04-01T00:00:00+00:00"
    assert row["updated_at"] == "2026-04-02T00:00:00+00:00"


@pytest.mark.asyncio
async def test_delete_returns_true_on_success(
    repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    assert await repo.delete("1") is True

    sql, binds = fake_session.calls[-1]
    assert "DELETE FROM public.mediahub_models" in sql
    assert 1 in binds.values()  # str model_id "1" → int (bigint bind)


@pytest.mark.asyncio
async def test_delete_returns_false_on_exception(
    repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    fake_session.raises = RuntimeError("boom")
    assert await repo.delete("1") is False


# ─── Factory ───────────────────────────────────────────────────────


def test_factory_returns_collapsed_repo() -> None:
    """Post-rollout the factory unconditionally returns the collapsed ORM repo."""
    repo = mod.get_mediahub_model_repository()
    assert type(repo) is MediahubModelRepository


# ---------------------------------------------------------------------------
# Owner scoping (owner_user_id, migration 431)
# ---------------------------------------------------------------------------
async def test_list_enabled_without_viewer_excludes_owned_rows(fake_session):
    """No viewer → fail-closed: only rows with owner_user_id IS NULL."""
    repo = MediahubModelRepository()
    await repo.list_enabled("image")
    sql, _binds = fake_session.calls[0]
    assert "owner_user_id IS NULL" in sql
    assert "OR" not in sql.split("WHERE", 1)[1].split("ORDER BY")[0].replace(
        "owner_user_id IS NULL", ""
    )


async def test_list_enabled_with_viewer_includes_own_rows(fake_session):
    viewer = "8e1584e3-9c29-4a5b-90fe-125b74259f7f"
    repo = MediahubModelRepository()
    await repo.list_enabled("image", viewer_user_id=viewer)
    sql, binds = fake_session.calls[0]
    assert "owner_user_id IS NULL" in sql
    assert "owner_user_id =" in sql
    assert any(str(v) == viewer for v in binds.values())


def test_public_projection_still_hides_owner_user_id():
    from app.repositories.mediahub_model_repository import _PUBLIC_COLS

    assert "owner_user_id" not in {c.key for c in _PUBLIC_COLS}
