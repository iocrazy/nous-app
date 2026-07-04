"""Scope-mock unit tests for the ported REST-straggler methods on
``ResourcesRepository``.

These methods used to run legacy supabase-py REST bodies (tenant-scope
bypasses); they are now rewritten on the ORM session scopes. This suite pins,
with a capturing fake session (no DB), that:

  - the compiled SQL / binds carry the right filters + bigint coercion, and
  - Strategy-C value parity holds (uuid → str, datetime → ISO str, bigint → int)
    so the dicts stay byte-identical to the retired REST bodies.

``execute_smart_rules`` gets one test per supported operator, the relative-date
resolution, and the tag-filter (contains / not_contains × AND / OR) + match /
exclude combinations. Live end-to-end parity is covered by the DSN-gated
integration suite (``tests/integration/test_resources_stragglers_orm.py``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from contextlib import asynccontextmanager
from typing import Any, List, Optional

import pytest
from sqlalchemy.dialects import postgresql

from app.models import Folders, ResourceItems, Resources, ResourceTags, Tags
from app.repositories import resources_repository as repo_mod
from app.repositories.resources_repository import ResourcesRepository

_UUID = uuid.UUID("11111111-2222-3333-4444-555555555555")
_DT = dt.datetime(2026, 3, 18, 6, 9, 32, 314912, tzinfo=dt.timezone.utc)


# ─── Fake session plumbing ────────────────────────────────────────────────────


class _Scalars:
    def __init__(self, rows: List[Any]) -> None:
        self._rows = rows

    def all(self) -> List[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _Mappings:
    def __init__(self, rows: List[Any]) -> None:
        self._rows = rows

    def all(self) -> List[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, rows: List[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _Scalars:
        return _Scalars(self._rows)

    def mappings(self) -> _Mappings:
        return _Mappings(self._rows)

    def all(self) -> List[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _CapSession:
    """Capturing fake session. Hands back queued rowsets (in FIFO order) and
    records executed statements, PK gets, adds, deletes, and flushes."""

    def __init__(
        self,
        rowsets: Optional[List[List[Any]]] = None,
        get_returns: Optional[List[Any]] = None,
    ) -> None:
        self._rowsets = list(rowsets or [])
        self._get_returns = list(get_returns or [])
        self.statements: List[Any] = []
        self.exec_params: List[Any] = []
        self.gets: List[Any] = []
        self.added: List[Any] = []
        self.deleted: List[Any] = []
        self.flushed = 0

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        self.statements.append(stmt)
        self.exec_params.append(params)
        rows = self._rowsets.pop(0) if self._rowsets else []
        return _Result(rows)

    async def get(self, model: Any, pk: Any) -> Any:
        self.gets.append((model, pk))
        return self._get_returns.pop(0) if self._get_returns else None

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    async def flush(self) -> None:
        self.flushed += 1

    async def refresh(self, obj: Any) -> None:
        pass


@asynccontextmanager
async def _fake_scope(session: _CapSession):
    yield session


def _patch_read(monkeypatch: pytest.MonkeyPatch, session: _CapSession) -> None:
    monkeypatch.setattr(repo_mod, "read_scope", lambda: _fake_scope(session))


def _patch_write(monkeypatch: pytest.MonkeyPatch, session: _CapSession) -> None:
    monkeypatch.setattr(repo_mod, "write_scope", lambda: _fake_scope(session))


def _sql(session: _CapSession, idx: int = 0):
    """(sql_text, bind_params) for the idx-th captured statement."""
    compiled = session.statements[idx].compile(dialect=postgresql.dialect())
    return str(compiled), compiled.params


def _item(resource_id: int, item_id: int = 1) -> ResourceItems:
    return ResourceItems(
        id=item_id, scope_id=999, resource_id=resource_id, created_at=_DT
    )


def _resource(res_id: int) -> Resources:
    return Resources(
        id=res_id,
        creator_id=_UUID,
        source_type="web",
        filename=f"clip-{res_id}.mp4",
        is_trashed=False,
        created_at=_DT,
        updated_at=_DT,
    )


# ─── add_resource_tag ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_resource_tag_inserts_bigint_coerced(monkeypatch) -> None:
    session = _CapSession(
        rowsets=[
            [
                {
                    "resource_id": 5,
                    "tag_id": 7,
                    "tagged_by": _UUID,
                    "created_at": _DT,
                    "source": "user",
                    "confidence": None,
                }
            ]
        ]
    )
    _patch_write(monkeypatch, session)
    repo = ResourcesRepository()

    result = await repo.add_resource_tag("5", "7", str(_UUID))

    sql, binds = _sql(session)
    assert "INSERT INTO public.resource_tags" in sql
    assert binds["resource_id"] == 5  # bigint-coerced from "5"
    assert binds["tag_id"] == 7
    assert binds["tagged_by"] == str(_UUID)
    # Strategy-C: RETURNING row → uuid str, datetime ISO str, bigint int.
    assert type(result["tagged_by"]) is str
    assert type(result["created_at"]) is str
    assert result["created_at"] == _DT.isoformat()
    assert type(result["resource_id"]) is int
    assert result["source"] == "user"


@pytest.mark.asyncio
async def test_add_resource_tag_empty_returning_is_empty_dict(monkeypatch) -> None:
    session = _CapSession(rowsets=[[]])
    _patch_write(monkeypatch, session)
    repo = ResourcesRepository()

    assert await repo.add_resource_tag("5", "7", str(_UUID)) == {}


@pytest.mark.asyncio
async def test_add_resource_tag_reraises_on_error(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(repo_mod, "write_scope", _boom)
    repo = ResourcesRepository()

    with pytest.raises(RuntimeError):
        await repo.add_resource_tag("5", "7", str(_UUID))


# ─── remove_resource_tag ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_remove_resource_tag_loads_composite_pk_then_deletes(monkeypatch) -> None:
    obj = ResourceTags(resource_id=5, tag_id=7)
    session = _CapSession(get_returns=[obj])
    _patch_write(monkeypatch, session)
    repo = ResourcesRepository()

    result = await repo.remove_resource_tag("5", "7")

    assert result is True
    # Composite PK dict, both ids bigint-coerced.
    assert session.gets == [(ResourceTags, {"resource_id": 5, "tag_id": 7})]
    assert session.deleted == [obj]
    assert session.flushed == 1


@pytest.mark.asyncio
async def test_remove_resource_tag_missing_row_is_true_noop(monkeypatch) -> None:
    session = _CapSession(get_returns=[None])
    _patch_write(monkeypatch, session)
    repo = ResourcesRepository()

    result = await repo.remove_resource_tag("5", "7")

    assert result is True
    assert session.deleted == []
    assert session.flushed == 0


# ─── get_resource_tags ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_resource_tags_embeds_tag_dict(monkeypatch) -> None:
    rt = ResourceTags(resource_id=5, tag_id=7, tagged_by=_UUID, created_at=_DT)
    tag = Tags(id=7, name="Funny", type="user", created_at=_DT)
    session = _CapSession(rowsets=[[(rt, tag)]])
    _patch_read(monkeypatch, session)
    repo = ResourcesRepository()

    rows = await repo.get_resource_tags("5")

    sql, binds = _sql(session)
    assert "JOIN public.tags" in sql
    assert binds["resource_id_1"] == 5  # bigint-coerced
    assert len(rows) == 1
    row = rows[0]
    # resource_tags columns present + nested full tag row.
    assert type(row["tagged_by"]) is str
    assert type(row["created_at"]) is str
    assert row["tag"]["name"] == "Funny"
    assert type(row["tag"]["id"]) is int
    assert type(row["tag"]["created_at"]) is str


@pytest.mark.asyncio
async def test_get_resource_tags_empty_and_error(monkeypatch) -> None:
    session = _CapSession(rowsets=[[]])
    _patch_read(monkeypatch, session)
    repo = ResourcesRepository()
    assert await repo.get_resource_tags("5") == []

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(repo_mod, "read_scope", _boom)
    assert await repo.get_resource_tags("5") == []  # swallowed → []


# ─── get_smart_folders ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_smart_folders_filters_and_orders(monkeypatch) -> None:
    folder = Folders(
        id=42,
        name="Recent",
        scope_id=99,
        created_by=_UUID,
        is_smart=True,
        is_trashed=False,
        created_at=_DT,
        updated_at=_DT,
    )
    session = _CapSession(rowsets=[[folder]])
    _patch_read(monkeypatch, session)
    repo = ResourcesRepository()

    rows = await repo.get_smart_folders(None, "99")

    sql, binds = _sql(session)
    assert binds["scope_id_1"] == 99  # bigint-coerced
    assert "is_smart IS true" in sql
    assert "is_trashed IS false" in sql
    assert "ORDER BY public.folders.sort_order ASC" in sql
    assert rows[0]["id"] == 42
    assert type(rows[0]["created_by"]) is str  # Strategy-C


@pytest.mark.asyncio
async def test_get_smart_folders_error_swallowed(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(repo_mod, "read_scope", _boom)
    repo = ResourcesRepository()
    assert await repo.get_smart_folders(None, "99") == []


# ─── create_smart_folder ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_smart_folder_inserts_and_returns_full_row(monkeypatch) -> None:
    session = _CapSession(
        rowsets=[
            [
                {
                    "id": 42,
                    "name": "Recent",
                    "scope_id": 99,
                    "created_by": _UUID,
                    "is_smart": True,
                    "smart_rules": {"operator": "AND", "conditions": []},
                    "sort_order": 0,
                    "created_at": _DT,
                    "updated_at": _DT,
                }
            ]
        ]
    )
    _patch_write(monkeypatch, session)
    repo = ResourcesRepository()

    data = {
        "name": "Recent",
        "scope_id": 99,
        "created_by": str(_UUID),
        "is_smart": True,
        "smart_rules": {"operator": "AND", "conditions": []},
        "icon": None,
        "color": None,
    }
    result = await repo.create_smart_folder(data)

    sql, binds = _sql(session)
    assert "INSERT INTO public.folders" in sql
    assert binds["name"] == "Recent"
    assert result["id"] == 42
    assert type(result["created_by"]) is str
    assert type(result["created_at"]) is str


@pytest.mark.asyncio
async def test_create_smart_folder_reraises_on_error(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(repo_mod, "write_scope", _boom)
    repo = ResourcesRepository()
    with pytest.raises(RuntimeError):
        await repo.create_smart_folder({"name": "x"})


# ─── execute_smart_rules: per-operator translation ────────────────────────────


def _rules(op: str, field: str = "filename", value: str = "test", **kw):
    return {"conditions": [{"field": field, "op": op, "value": value}], **kw}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "op,value,needle,expected_bind",
    [
        ("eq", "test", "filename = ", "test"),
        ("contains", "foo", "filename ILIKE ", "%foo%"),
        ("starts_with", "foo", "filename ILIKE ", "foo%"),
    ],
)
async def test_smart_rules_text_operators(
    monkeypatch, op, value, needle, expected_bind
) -> None:
    session = _CapSession(rowsets=[[]])
    _patch_read(monkeypatch, session)
    repo = ResourcesRepository()

    await repo.execute_smart_rules(None, "99", _rules(op, value=value))

    sql, binds = _sql(session)
    assert needle in sql
    assert expected_bind in binds.values()
    # Base filters always present (inner join + scope + non-trashed).
    assert "JOIN public.resources" in sql
    assert binds["scope_id_1"] == 99
    assert "resources.is_trashed IS false" in sql
    assert "ORDER BY public.resource_items.created_at DESC" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "op,symbol",
    [("gt", " > "), ("lt", " < "), ("gte", " >= "), ("lte", " <= ")],
)
async def test_smart_rules_numeric_comparison_operators(
    monkeypatch, op, symbol
) -> None:
    session = _CapSession(rowsets=[[]])
    _patch_read(monkeypatch, session)
    repo = ResourcesRepository()

    await repo.execute_smart_rules(
        None, "99", _rules(op, field="file_size_bytes", value="1000")
    )

    sql, binds = _sql(session)
    assert f"file_size_bytes{symbol}" in sql
    # int-coerced for the bigint column (PostgREST's unknown-literal cast).
    assert 1000 in binds.values()
    assert "1000" not in binds.values()


@pytest.mark.asyncio
async def test_smart_rules_in_operator_splits_and_coerces(monkeypatch) -> None:
    session = _CapSession(rowsets=[[]])
    _patch_read(monkeypatch, session)
    repo = ResourcesRepository()

    await repo.execute_smart_rules(
        None, "99", _rules("in", field="file_type", value="image,video")
    )

    sql, binds = _sql(session)
    assert "file_type IN" in sql
    flat = [x for v in binds.values() for x in (v if isinstance(v, list) else [v])]
    assert "image" in flat and "video" in flat


@pytest.mark.asyncio
async def test_smart_rules_relative_date_binds_datetime(monkeypatch) -> None:
    session = _CapSession(rowsets=[[]])
    _patch_read(monkeypatch, session)
    repo = ResourcesRepository()

    await repo.execute_smart_rules(
        None, "99", _rules("gte", field="created_at", value="relative:-7d")
    )

    _, binds = _sql(session)
    # created_at comparison binds a real tz-aware datetime ~7 days ago (not a str).
    dt_binds = [v for v in binds.values() if isinstance(v, dt.datetime)]
    assert dt_binds, "expected a datetime bind for the relative-date filter"
    now = dt.datetime.now(dt.timezone.utc)
    delta_days = (now - dt_binds[0]).total_seconds() / 86400
    assert 6.9 < delta_days < 7.1


@pytest.mark.asyncio
async def test_smart_rules_unknown_op_is_noop(monkeypatch) -> None:
    """An unsupported resource-field op (e.g. not_contains) adds no predicate —
    the base query runs unfiltered (matches the retired helpers returning the
    query unchanged / None)."""
    session = _CapSession(rowsets=[[]])
    _patch_read(monkeypatch, session)
    repo = ResourcesRepository()

    await repo.execute_smart_rules(
        None, "99", _rules("not_contains", field="filename", value="x")
    )

    sql, _ = _sql(session)
    assert "filename ILIKE" not in sql
    assert "filename =" not in sql


@pytest.mark.asyncio
async def test_smart_rules_unknown_field_returns_empty(monkeypatch) -> None:
    """A field that is not a Resources column raises inside the try → [] (parity
    with PostgREST 400-ing on an unknown column)."""
    session = _CapSession(rowsets=[[]])
    _patch_read(monkeypatch, session)
    repo = ResourcesRepository()

    result = await repo.execute_smart_rules(
        None, "99", _rules("eq", field="no_such_column", value="x")
    )
    assert result == []


@pytest.mark.asyncio
async def test_smart_rules_and_vs_or_mode(monkeypatch) -> None:
    two = {
        "operator": "OR",
        "conditions": [
            {"field": "filename", "op": "eq", "value": "a"},
            {"field": "resolution", "op": "eq", "value": "1080p"},
        ],
    }
    session = _CapSession(rowsets=[[]])
    _patch_read(monkeypatch, session)
    repo = ResourcesRepository()

    await repo.execute_smart_rules(None, "99", two)
    sql, _ = _sql(session)
    # OR mode combines the two resource predicates with OR.
    assert " OR " in sql


# ─── execute_smart_rules: tag filter + match/exclude ──────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "op,operator,keep_ids",
    [
        ("contains", "AND", {10}),
        ("not_contains", "AND", {20}),
    ],
)
async def test_smart_rules_tag_filter(monkeypatch, op, operator, keep_ids) -> None:
    items = [
        (_item(10, item_id=1), _resource(10)),
        (_item(20, item_id=2), _resource(20)),
    ]
    tag_rows = [{"resource_id": 10, "name": "funny"}]
    session = _CapSession(rowsets=[items, tag_rows])
    _patch_read(monkeypatch, session)
    repo = ResourcesRepository()

    rules = {
        "operator": operator,
        "conditions": [{"field": "tags", "op": op, "value": "Funny"}],
    }
    result = await repo.execute_smart_rules(None, "99", rules)

    assert {r["resource_id"] for r in result} == keep_ids
    # Tag query joined tags and filtered by the candidate resource ids.
    tag_sql, _ = _sql(session, idx=1)
    assert "JOIN public.tags" in tag_sql


@pytest.mark.asyncio
async def test_smart_rules_exclude_mode_subtracts_matched(monkeypatch) -> None:
    # match=False: run the rule, then return ALL in-scope items MINUS matched.
    matched = [(_item(10, item_id=1), _resource(10))]
    all_items = [
        (_item(10, item_id=1), _resource(10)),
        (_item(20, item_id=2), _resource(20)),
    ]
    session = _CapSession(rowsets=[matched, all_items])
    _patch_read(monkeypatch, session)
    repo = ResourcesRepository()

    rules = {
        "operator": "AND",
        "match": False,
        "conditions": [{"field": "filename", "op": "eq", "value": "clip-10.mp4"}],
    }
    result = await repo.execute_smart_rules(None, "99", rules)

    # item id 1 was matched → excluded; only item id 2 remains.
    assert [r["id"] for r in result] == [2]


@pytest.mark.asyncio
async def test_smart_rules_error_swallowed(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(repo_mod, "read_scope", _boom)
    repo = ResourcesRepository()
    assert await repo.execute_smart_rules(None, "99", _rules("eq")) == []


# ─── _coerce_smart_value + _resolve_value (pure helpers) ──────────────────────


def test_coerce_smart_value_by_column_type() -> None:
    coerce = ResourcesRepository._coerce_smart_value
    # int column → int()
    assert coerce(Resources.file_size_bytes, "1000") == 1000
    assert type(coerce(Resources.file_size_bytes, "1000")) is int
    # timestamptz column → datetime
    out = coerce(Resources.created_at, "2026-01-01T00:00:00+00:00")
    assert isinstance(out, dt.datetime)
    # text column → unchanged
    assert coerce(Resources.filename, "abc") == "abc"
    # non-str passes through untouched
    assert coerce(Resources.file_size_bytes, 55) == 55
    # unparseable value for a typed column falls back to the raw string
    assert coerce(Resources.file_size_bytes, "not-an-int") == "not-an-int"


def test_resolve_value_relative_units() -> None:
    repo = ResourcesRepository()
    now = dt.datetime.now(dt.timezone.utc)

    for token, unit_seconds in (
        ("relative:-7d", 7 * 86400),
        ("relative:-2h", 2 * 3600),
        ("relative:-30m", 30 * 60),
    ):
        resolved = repo._resolve_value(token)
        parsed = dt.datetime.fromisoformat(resolved)
        assert abs((now - parsed).total_seconds() - unit_seconds) < 5

    # A plain (non-relative) value is returned unchanged.
    assert repo._resolve_value("plain") == "plain"


# ─── list_resources_in_folder ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_resources_in_folder_flattens_and_skips_trashed(monkeypatch) -> None:
    rows = [
        {"resource_id": 5, "created_at": _DT, "is_trashed": False},
        {"resource_id": 6, "created_at": _DT, "is_trashed": True},
    ]
    session = _CapSession(rowsets=[rows])
    _patch_read(monkeypatch, session)
    repo = ResourcesRepository()

    out = await repo.list_resources_in_folder("42")

    sql, binds = _sql(session)
    assert "JOIN public.resources" in sql
    assert binds["folder_id_1"] == 42  # bigint-coerced
    # Non-trashed only; created_at flattened to an ISO STRING (sweeper calls
    # .replace() on it).
    assert out == [{"id": 5, "created_at": _DT.isoformat()}]
    assert type(out[0]["created_at"]) is str


@pytest.mark.asyncio
async def test_list_resources_in_folder_include_trashed(monkeypatch) -> None:
    rows = [
        {"resource_id": 5, "created_at": _DT, "is_trashed": False},
        {"resource_id": 6, "created_at": _DT, "is_trashed": True},
    ]
    session = _CapSession(rowsets=[rows])
    _patch_read(monkeypatch, session)
    repo = ResourcesRepository()

    out = await repo.list_resources_in_folder("42", include_trashed=True)

    assert {r["id"] for r in out} == {5, 6}


@pytest.mark.asyncio
async def test_list_resources_in_folder_reraises_on_error(monkeypatch) -> None:
    """Legacy contract: this helper RE-RAISES on error (not swallow-to-[])."""

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(repo_mod, "read_scope", _boom)
    repo = ResourcesRepository()
    with pytest.raises(RuntimeError):
        await repo.list_resources_in_folder("42")


# ─── get_descendant_folder_ids (include_trashed toggle) ───────────────────────


@pytest.mark.asyncio
async def test_get_descendant_folder_ids_default_excludes_trashed(monkeypatch) -> None:
    session = _CapSession(rowsets=[[{"id": 2}, {"id": 3}]])
    _patch_read(monkeypatch, session)
    repo = ResourcesRepository()

    out = await repo.get_descendant_folder_ids("1")

    assert out == ["2", "3"]  # coerced to str
    sql = str(session.statements[0])
    assert "is_trashed = false" in sql  # default filters trashed out
    assert session.exec_params[0]["fid"] == 1  # bigint-coerced


@pytest.mark.asyncio
async def test_get_descendant_folder_ids_include_trashed_drops_filter(
    monkeypatch,
) -> None:
    session = _CapSession(rowsets=[[{"id": 2}]])
    _patch_read(monkeypatch, session)
    repo = ResourcesRepository()

    out = await repo.get_descendant_folder_ids("1", include_trashed=True)

    assert out == ["2"]
    sql = str(session.statements[0])
    # The permanent-purge variant recurses through trashed folders (no filter).
    assert "is_trashed" not in sql
