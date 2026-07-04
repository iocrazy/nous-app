"""Regression test: ``soft_delete_resource`` must set a real tz-aware timestamp.

Post-port ``soft_delete_resource`` runs on the ORM: ``resources`` is a
scope-mixin model, so the write goes through load-then-modify (``session.get``
+ attribute update), NOT a Core UPDATE. The original regression this pins —
``trashed_at`` must be a real timestamp, never the literal string ``"NOW()"``
that the retired PostgREST body could have mis-sent — still holds: the ORM
binds a tz-aware ``datetime`` object.
"""

from contextlib import asynccontextmanager
from datetime import datetime as _dt
from datetime import timezone as _tz

import pytest

from app.models import Resources
from app.repositories import resources_repository as repo_mod
from app.repositories.resources_repository import ResourcesRepository


class _CapSession:
    """Minimal fake write session: hands back a queued ``get`` result and
    records ``flush`` calls."""

    def __init__(self, get_obj):
        self._get_obj = get_obj
        self.gets = []
        self.flushed = 0

    async def get(self, model, pk):
        self.gets.append((model, pk))
        return self._get_obj

    async def flush(self):
        self.flushed += 1


@asynccontextmanager
async def _fake_write(session):
    yield session


@pytest.mark.asyncio
async def test_soft_delete_resource_sets_tzaware_datetime(monkeypatch):
    """is_trashed → True and trashed_at → a tz-aware datetime (not 'NOW()')."""
    obj = Resources(
        id=101,
        creator_id="11111111-2222-3333-4444-555555555555",
        source_type="web",
        filename="clip.mp4",
        is_trashed=False,
    )
    session = _CapSession(get_obj=obj)
    monkeypatch.setattr(repo_mod, "write_scope", lambda: _fake_write(session))

    repo = ResourcesRepository()
    await repo.soft_delete_resource("101")

    # Loaded by bigint-coerced PK, then mutated in place + flushed.
    assert session.gets == [(Resources, 101)]
    assert session.flushed == 1
    assert obj.is_trashed is True
    assert isinstance(obj.trashed_at, _dt)
    assert obj.trashed_at.tzinfo is not None
    # Never the literal 'NOW()' string the PostgREST body risked sending.
    assert obj.trashed_at != "NOW()"


@pytest.mark.asyncio
async def test_soft_delete_resource_missing_id_is_noop(monkeypatch):
    """A missing id (get → None) is a silent no-op — no flush, no raise —
    matching the legacy ``.update().eq("id", ...)`` zero-row behavior."""
    session = _CapSession(get_obj=None)
    monkeypatch.setattr(repo_mod, "write_scope", lambda: _fake_write(session))

    repo = ResourcesRepository()
    await repo.soft_delete_resource("999")

    assert session.gets == [(Resources, 999)]
    assert session.flushed == 0
