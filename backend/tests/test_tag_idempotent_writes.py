"""Race-safe tag creation + idempotent resource-tag attach.

Prod incident 2026-07-19: a double-fired "Mark to publish" toggle raced its
own lazy tag creation — both requests passed the router's exists-check, the
loser hit ``unique_tag_per_scope`` unhandled (500); the repeat attach then hit
``resource_tags_pkey`` (500). Fix: ``create_tag`` is ON CONFLICT DO NOTHING +
re-select (concurrent same-intent creation returns the winner's row) and
``add_resource_tag`` is ON CONFLICT DO NOTHING + re-select (repeat attach
returns the existing junction row).

Boundary-stub style (see test_get_tags_by_ids.py): only the write_scope
session is faked; the INSERT/SELECT statements are built for real, so the
on_conflict clause is asserted on the actual compiled statement.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.models import Tags
from app.repositories.resources_repository import ResourcesRepository
from app.repositories.tags_repository import TagsRepository


class _Result:
    """Wraps one statement's rows for both .scalars().first() and
    .mappings().first() access patterns."""

    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Returns queued results, one per execute() call, recording statements."""

    def __init__(self, results):
        self._results = list(results)
        self.statements = []

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        return _Result(self._results.pop(0))

    async def flush(self):
        return None


def _patch_write_scope(monkeypatch, module, session):
    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(module, "write_scope", _scope)


@pytest.mark.asyncio
async def test_create_tag_conflict_returns_existing(monkeypatch):
    """Losing the create race re-selects and returns the winner's row."""
    from app.repositories import tags_repository as mod

    existing = Tags(
        id=329310560607659,
        name="To Publish",
        type="user",
        user_id="8e1584e3-9c29-4a5b-90fe-125b74259f7f",
        color="#6366f1",
    )
    # 1st execute: INSERT ... ON CONFLICT DO NOTHING → no row (lost the race)
    # 2nd execute: re-select → existing row
    session = _FakeSession([[], [existing]])
    _patch_write_scope(monkeypatch, mod, session)

    out = await TagsRepository().create_tag(
        name="To Publish",
        user_id="8e1584e3-9c29-4a5b-90fe-125b74259f7f",
    )

    assert str(out["id"]) == str(existing.id)
    assert out["name"] == "To Publish"
    insert_sql = str(session.statements[0]).upper()
    assert "ON CONFLICT" in insert_sql and "DO NOTHING" in insert_sql


@pytest.mark.asyncio
async def test_create_tag_normal_insert_still_returns_new_row(monkeypatch):
    from app.repositories import tags_repository as mod

    created = Tags(id=7, name="Fresh", type="user", user_id="u-1", color="#000")
    session = _FakeSession([[created]])
    _patch_write_scope(monkeypatch, mod, session)

    out = await TagsRepository().create_tag(name="Fresh", user_id="u-1")

    assert out["name"] == "Fresh"
    assert len(session.statements) == 1  # no fallback select needed


@pytest.mark.asyncio
async def test_create_tag_conflict_without_row_raises(monkeypatch):
    """Conflict but the winner's row is not visible → loud failure, not None."""
    from app.repositories import tags_repository as mod

    session = _FakeSession([[], []])
    _patch_write_scope(monkeypatch, mod, session)

    with pytest.raises(RuntimeError, match="existing row not found"):
        await TagsRepository().create_tag(name="Ghost", user_id="u-1")


@pytest.mark.asyncio
async def test_add_resource_tag_repeat_attach_returns_existing(monkeypatch):
    """Attaching an already-attached tag is success, not a duplicate-key 500."""
    from app.repositories import resources_repository as mod

    existing_row = {
        "resource_id": 314463923573949,
        "tag_id": 329310560607659,
        "tagged_by": "8e1584e3-9c29-4a5b-90fe-125b74259f7f",
    }
    # 1st execute: INSERT ... ON CONFLICT DO NOTHING → no row (already attached)
    # 2nd execute: re-select → existing junction row
    session = _FakeSession([[], [existing_row]])
    _patch_write_scope(monkeypatch, mod, session)

    out = await ResourcesRepository().add_resource_tag(
        "314463923573949", "329310560607659", "8e1584e3-9c29-4a5b-90fe-125b74259f7f"
    )

    assert str(out["resource_id"]) == "314463923573949"
    insert_sql = str(session.statements[0]).upper()
    assert "ON CONFLICT" in insert_sql and "DO NOTHING" in insert_sql


@pytest.mark.asyncio
async def test_add_resource_tag_first_attach_returns_inserted(monkeypatch):
    from app.repositories import resources_repository as mod

    inserted = {"resource_id": 1, "tag_id": 2, "tagged_by": "u-1"}
    session = _FakeSession([[inserted]])
    _patch_write_scope(monkeypatch, mod, session)

    out = await ResourcesRepository().add_resource_tag("1", "2", "u-1")

    assert str(out["tag_id"]) == "2"
    assert len(session.statements) == 1
