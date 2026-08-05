"""Unit tests for gallery-as-a-first-class-entity repo methods (PR-A).

Covers:
- ``get_resource_items`` hides gallery children by default (NOT EXISTS clause)
  and opts out with ``include_gallery_children=True``; the ``gallery_count``
  computed column is always projected.
- ``set_gallery_items`` resets the junction (delete + positional insert) in one
  write scope.
- ``get_gallery_items`` ordered read shape.
- ``validate_scope_image_ids`` scope+mime gate.
- ``_build_mime_sql`` gains a ``gallery`` category and folds the gallery mime
  into the ``other`` known-clause.

Mirrors the scope-mock capture harness in ``test_resources_repository_filters``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import pytest

from app.repositories import resources_repository as repo_mod
from app.repositories.resources_repository import ResourcesRepository

# ─── capture harness ──────────────────────────────────────────────────


class _Mappings:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = rows

    def all(self) -> List[Dict[str, Any]]:
        return self._rows

    def first(self) -> Optional[Dict[str, Any]]:
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Mappings:
        return _Mappings(self._rows)

    def all(self) -> List[Any]:
        """Bare (non-``.mappings()``) row access — the shape a real ORM
        ``select(col)`` result returns (tuple-like rows indexable by
        position), used by ``validate_scope_image_ids`` post Phase C task 3."""
        return self._rows


class _CapSession:
    """Records ``(stmt, params)`` and hands back queued rowsets (default [])."""

    def __init__(self, rowsets: Optional[List[List[Dict[str, Any]]]] = None) -> None:
        self._rowsets = list(rowsets or [])
        self.calls: List[tuple[Any, Dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        self.calls.append((stmt, dict(params or {})))
        rows = self._rowsets.pop(0) if self._rowsets else []
        return _Result(rows)


@asynccontextmanager
async def _fake_scope(session: _CapSession):
    yield session


# ─── get_resource_items: gallery-children exclusion + gallery_count ───


@pytest.mark.asyncio
async def test_list_excludes_gallery_children_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CapSession()
    monkeypatch.setattr(repo_mod, "read_scope", lambda: _fake_scope(session))
    repo = ResourcesRepository()

    await repo.get_resource_items(scope_type="personal", scope_id="user-1")
    stmt, _ = session.calls[-1]
    sql = str(stmt)
    assert "NOT EXISTS (SELECT 1 FROM gallery_items gi" in sql
    assert "gi.image_id = i.resource_id" in sql


@pytest.mark.asyncio
async def test_list_include_gallery_children_drops_exclusion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CapSession()
    monkeypatch.setattr(repo_mod, "read_scope", lambda: _fake_scope(session))
    repo = ResourcesRepository()

    await repo.get_resource_items(
        scope_type="personal", scope_id="user-1", include_gallery_children=True
    )
    sql = str(session.calls[-1][0])
    assert "NOT EXISTS (SELECT 1 FROM gallery_items" not in sql


@pytest.mark.asyncio
async def test_list_projects_gallery_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CapSession()
    monkeypatch.setattr(repo_mod, "read_scope", lambda: _fake_scope(session))
    repo = ResourcesRepository()

    await repo.get_resource_items(scope_type="personal", scope_id="user-1")
    sql = str(session.calls[-1][0])
    assert "gallery_items gc" in sql
    assert "AS gallery_count" in sql


@pytest.mark.asyncio
async def test_list_injects_gallery_count_into_resource_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One row: gallery with 3 children. The count rides inside `resource`.
    row = {
        "id": 1,
        "resource_id": 2,
        "scope_id": 3,
        "folder_id": None,
        "added_by": None,
        "library_id": None,
        "i_created_at": None,
        "resource": {"id": 2, "filename": "My Gallery"},
        "gallery_count": 3,
    }
    session = _CapSession(rowsets=[[row]])
    monkeypatch.setattr(repo_mod, "read_scope", lambda: _fake_scope(session))
    repo = ResourcesRepository()

    out = await repo.get_resource_items(scope_type="personal", scope_id="user-1")
    assert out[0]["resource"]["gallery_count"] == 3
    # The top-level scratch column is consumed, not leaked.
    assert "gallery_count" not in out[0]


# ─── set_gallery_items (delete + positional insert) ───────────────────


@pytest.mark.asyncio
async def test_set_gallery_items_resets_and_positions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # rowset for the INSERT ... RETURNING (delete result is unused).
    returning = [
        {"gallery_id": 100, "image_id": 10, "position": 0, "created_at": None},
        {"gallery_id": 100, "image_id": 20, "position": 1, "created_at": None},
    ]
    session = _CapSession(rowsets=[[], returning])
    monkeypatch.setattr(repo_mod, "write_scope", lambda: _fake_scope(session))
    repo = ResourcesRepository()

    out = await repo.set_gallery_items("100", ["10", "20"])
    assert len(session.calls) == 2  # delete + insert
    delete_stmt = session.calls[0][0]
    insert_stmt = session.calls[1][0]
    delete_sql = str(delete_stmt)
    assert delete_sql.startswith("DELETE FROM")
    assert "gallery_items" in delete_sql
    # Positional values: enumerate order preserved, ids coerced to int.
    multi = insert_stmt._multi_values[0]  # type: ignore[attr-defined]
    # Keys are Column objects — normalise to column-name dicts.
    rows = [{k.name: v for k, v in row.items()} for row in multi]
    assert rows[0] == {"gallery_id": 100, "image_id": 10, "position": 0}
    assert rows[1] == {"gallery_id": 100, "image_id": 20, "position": 1}
    assert len(out) == 2


@pytest.mark.asyncio
async def test_set_gallery_items_empty_only_deletes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CapSession(rowsets=[[]])
    monkeypatch.setattr(repo_mod, "write_scope", lambda: _fake_scope(session))
    repo = ResourcesRepository()

    out = await repo.set_gallery_items("100", [])
    assert out == []
    assert len(session.calls) == 1  # delete only, no insert
    only_sql = str(session.calls[0][0])
    assert only_sql.startswith("DELETE FROM")
    assert "gallery_items" in only_sql


# ─── get_gallery_items (ordered read) ─────────────────────────────────


@pytest.mark.asyncio
async def test_get_gallery_items_ordered_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {"id": 10, "filename": "a.jpg", "thumbnail_path": "t/a", "position": 0},
        {"id": 20, "filename": "b.jpg", "thumbnail_path": "t/b", "position": 1},
    ]
    session = _CapSession(rowsets=[rows])
    monkeypatch.setattr(repo_mod, "read_scope", lambda: _fake_scope(session))
    repo = ResourcesRepository()

    out = await repo.get_gallery_items("100")
    stmt, params = session.calls[-1]
    sql = str(stmt)
    assert "FROM gallery_items gi" in sql
    assert "ORDER BY gi.position ASC" in sql
    assert params["gid"] == 100  # bigint-coerced
    assert out[0]["filename"] == "a.jpg" and out[1]["position"] == 1


# ─── validate_scope_image_ids (scope + mime gate) ─────────────────────


@pytest.mark.asyncio
async def test_validate_scope_image_ids_empty_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CapSession()
    monkeypatch.setattr(repo_mod, "read_scope", lambda: _fake_scope(session))
    repo = ResourcesRepository()
    assert await repo.validate_scope_image_ids("s1", []) == set()
    assert session.calls == []


@pytest.mark.asyncio
async def test_validate_scope_image_ids_maps_back_to_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase C task 3: ``validate_scope_image_ids`` is now a real ORM
    INNER JOIN (``select(Resources.id).distinct().join(ResourceItems, ...)``)
    instead of raw ``text()`` — the fake session hands back tuple rows
    (``.all()``, not ``.mappings().all()``) and assertions compile the
    captured statement instead of substring-matching SQL text."""
    # DB says 10 and 30 are valid images in scope; 20 is not.
    session = _CapSession(rowsets=[[(10,), (30,)]])
    monkeypatch.setattr(repo_mod, "read_scope", lambda: _fake_scope(session))
    repo = ResourcesRepository()

    valid = await repo.validate_scope_image_ids("500", ["10", "20", "30"])
    assert valid == {"10", "30"}  # returned as the caller's str reps

    stmt, _ = session.calls[-1]
    compiled = stmt.compile()
    sql = str(compiled)
    params = dict(compiled.params)
    assert "resources" in sql
    assert "resource_items" in sql
    assert "mime_type" in sql
    # scope_id bound literally; the id list bound as an expanding IN() param.
    assert 500 in params.values()
    assert any(
        isinstance(v, (list, tuple)) and set(v) == {10, 20, 30} for v in params.values()
    )


# ─── _build_mime_sql: gallery category ────────────────────────────────


def test_build_mime_sql_gallery_category() -> None:
    expr = ResourcesRepository._build_mime_sql(["gallery"])
    assert expr == "(r.mime_type = 'application/x-mediahub-gallery')"


def test_build_mime_sql_other_excludes_gallery_mime() -> None:
    """'other' = NOT any known prefix — the gallery mime must be a known
    prefix so galleries never leak into the 'other' bucket."""
    expr = ResourcesRepository._build_mime_sql(["other"])
    assert expr is not None
    assert "r.mime_type = 'application/x-mediahub-gallery'" in expr
