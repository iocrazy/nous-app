"""ORM migration of the inspiration attachments + token repos.

Boundary-stub style: only the read_scope/write_scope session is faked; the
SQLAlchemy statement (table, filters, bind coercion, RETURNING) is built for
real, so a cross-transport regression fails here rather than in prod.

Parity contract these lock in: the old PostgREST dicts had BIGINT ids as int,
uuid user_id as str, timestamps as ISO str. The ORM ``_serialize`` reproduces
that (uuid → str, datetime → ISO, ids stay int).
"""

from __future__ import annotations

import datetime
import uuid
from contextlib import asynccontextmanager

import pytest


class _MappingResult:
    def __init__(self, mapping):
        self._mapping = mapping

    def mappings(self):
        return self

    def first(self):
        return self._mapping


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _RowResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeSession:
    def __init__(self, result=None):
        self.statements = []
        self._result = result

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        return self._result


def _cm(session):
    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


# ── _serialize parity (both repos share the shape) ─────────────────────────


def test_serialize_uuid_and_datetime_parity():
    from app.repositories.inspiration_token_repository import _serialize

    uid = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
    dt = datetime.datetime(2026, 7, 14, tzinfo=datetime.timezone.utc)
    out = _serialize(
        {"id": 9007199254740995, "user_id": uid, "created_at": dt, "revoked_at": None}
    )
    assert out["id"] == 9007199254740995  # bigint stays native int
    assert out["user_id"] == "00000000-0000-0000-0000-0000000000aa"
    assert out["created_at"] == "2026-07-14T00:00:00+00:00"
    assert out["revoked_at"] is None


# ── attachments ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_attachment_create_returns_serialized_row(monkeypatch):
    from app.repositories import inspiration_attachments_repository as mod
    from app.repositories.inspiration_attachments_repository import (
        InspirationAttachmentsRepository,
    )

    uid = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
    dt = datetime.datetime(2026, 7, 14, tzinfo=datetime.timezone.utc)
    row = {"id": 5, "note_id": 7, "user_id": uid, "created_at": dt, "mime": "image/png"}
    session = _FakeSession(_MappingResult(row))
    monkeypatch.setattr(mod, "write_scope", _cm(session))

    out = await InspirationAttachmentsRepository().create(
        "7", "u1", "inspiration", "p/x.png", "image/png", 12, "x.png"
    )

    assert out["id"] == 5 and out["note_id"] == 7
    assert out["user_id"] == "00000000-0000-0000-0000-0000000000bb"
    assert out["created_at"] == "2026-07-14T00:00:00+00:00"
    stmt = session.statements[0]
    sql = str(stmt).lower()
    assert "insert into public.inspiration_attachments" in sql
    assert "returning" in sql
    # note_id str→int coercion.
    assert 7 in stmt.compile().params.values()


@pytest.mark.asyncio
async def test_attachment_list_for_notes_empty_short_circuits(monkeypatch):
    from app.repositories import inspiration_attachments_repository as mod
    from app.repositories.inspiration_attachments_repository import (
        InspirationAttachmentsRepository,
    )

    def _explode():  # pragma: no cover
        raise AssertionError("read_scope must not open for empty input")

    monkeypatch.setattr(mod, "read_scope", _explode)
    assert await InspirationAttachmentsRepository().list_for_notes([]) == []


@pytest.mark.asyncio
async def test_attachment_delete_true_when_row_returned(monkeypatch):
    from app.repositories import inspiration_attachments_repository as mod
    from app.repositories.inspiration_attachments_repository import (
        InspirationAttachmentsRepository,
    )

    session = _FakeSession(_RowResult((5,)))
    monkeypatch.setattr(mod, "write_scope", _cm(session))
    assert await InspirationAttachmentsRepository().delete("5") is True
    sql = str(session.statements[0])
    assert "DELETE FROM public.inspiration_attachments" in sql
    assert "RETURNING" in sql


@pytest.mark.asyncio
async def test_attachment_delete_false_when_no_row(monkeypatch):
    from app.repositories import inspiration_attachments_repository as mod
    from app.repositories.inspiration_attachments_repository import (
        InspirationAttachmentsRepository,
    )

    monkeypatch.setattr(mod, "write_scope", _cm(_FakeSession(_RowResult(None))))
    assert await InspirationAttachmentsRepository().delete("5") is False


# ── tokens ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_token_list_filters_revoked_and_orders_desc(monkeypatch):
    from app.repositories import inspiration_token_repository as mod
    from app.repositories.inspiration_token_repository import (
        InspirationTokenRepository,
    )

    session = _FakeSession(_ScalarResult([]))
    monkeypatch.setattr(mod, "read_scope", _cm(session))

    await InspirationTokenRepository().list_by_user("u1")

    sql = str(session.statements[0])
    assert "FROM public.inspiration_api_tokens" in sql
    assert "revoked_at IS NULL" in sql  # non-revoked filter by default
    assert "ORDER BY" in sql and "DESC" in sql


@pytest.mark.asyncio
async def test_token_list_include_revoked_drops_filter(monkeypatch):
    from app.repositories import inspiration_token_repository as mod
    from app.repositories.inspiration_token_repository import (
        InspirationTokenRepository,
    )

    session = _FakeSession(_ScalarResult([]))
    monkeypatch.setattr(mod, "read_scope", _cm(session))

    await InspirationTokenRepository().list_by_user("u1", include_revoked=True)

    assert "revoked_at IS NULL" not in str(session.statements[0])


@pytest.mark.asyncio
async def test_find_active_by_hash_filters_non_revoked(monkeypatch):
    from app.repositories import inspiration_token_repository as mod
    from app.repositories.inspiration_token_repository import (
        InspirationTokenRepository,
    )

    session = _FakeSession(_ScalarResult([]))
    monkeypatch.setattr(mod, "read_scope", _cm(session))

    out = await InspirationTokenRepository().find_active_by_hash("deadbeef")

    assert out is None
    sql = str(session.statements[0])
    assert "token_hash" in sql and "revoked_at IS NULL" in sql


@pytest.mark.asyncio
async def test_revoke_scopes_by_owner_and_unrevoked(monkeypatch):
    from app.repositories import inspiration_token_repository as mod
    from app.repositories.inspiration_token_repository import (
        InspirationTokenRepository,
    )

    session = _FakeSession(_RowResult((9,)))
    monkeypatch.setattr(mod, "write_scope", _cm(session))

    ok = await InspirationTokenRepository().revoke("9", "owner-1")

    assert ok is True
    stmt = session.statements[0]
    sql = str(stmt).lower()
    assert "update public.inspiration_api_tokens" in sql
    assert "revoked_at is null" in sql  # idempotence guard
    assert "user_id" in sql  # ownership guard
    assert 9 in stmt.compile().params.values()


@pytest.mark.asyncio
async def test_revoke_false_when_not_owned(monkeypatch):
    from app.repositories import inspiration_token_repository as mod
    from app.repositories.inspiration_token_repository import (
        InspirationTokenRepository,
    )

    monkeypatch.setattr(mod, "write_scope", _cm(_FakeSession(_RowResult(None))))
    assert await InspirationTokenRepository().revoke("9", "owner-1") is False


@pytest.mark.asyncio
async def test_touch_last_used_binds_datetime_not_isostring(monkeypatch):
    from app.repositories import inspiration_token_repository as mod
    from app.repositories.inspiration_token_repository import (
        InspirationTokenRepository,
    )

    session = _FakeSession(_RowResult(None))
    monkeypatch.setattr(mod, "write_scope", _cm(session))

    await InspirationTokenRepository().touch_last_used("9")

    params = session.statements[0].compile().params
    # asyncpg wants a datetime object, never an ISO string.
    assert any(isinstance(v, datetime.datetime) for v in params.values())


def test_repos_have_no_supabase_client():
    import inspect

    from app.repositories import (
        inspiration_attachments_repository,
        inspiration_token_repository,
    )

    for mod in (inspiration_attachments_repository, inspiration_token_repository):
        src = inspect.getsource(mod)
        assert "get_async_supabase_admin" not in src, mod.__name__
        assert "client.table" not in src, mod.__name__
