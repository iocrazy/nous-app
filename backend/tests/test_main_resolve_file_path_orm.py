"""ORM equivalence tests for ``app.main._resolve_file_path`` /
``_fetch_team_ids`` (Phase A raw-SQL-to-ORM migration,
docs/decisions/2026-08-04-raw-sql-to-orm-full-migration.md).

Legacy behaviour these pin (previously plain ``db_engine.fetch_one`` /
``fetch_all`` text() SQL, now ``select(...)`` over an ORM session):

  * resources hit (file_type="file") → (file_path, creator_id, team_ids),
    team_ids resolved via a second resource_items lookup.
  * resources miss / no file_path → falls through to parsed_media, keyed by
    the file_type-selected column (download_path / cover_download_path).
  * file_type="cover" skips the resources lookup entirely (cover-only assets
    live on parsed_media).
  * neither table has a row/path → HTTPException 404.
  * a non-numeric media_id degrades to a warning + 404 (the ``int(media_id)``
    ValueError is swallowed by the same broad except the legacy code had).
  * resource_items scope_id lookup drops falsy (0/None) values, exactly like
    the legacy ``if r.get("scope_id")`` filter.

No real DB: ``app.db.session.read_scope`` / ``app.db.scope.system_request_scope``
are monkeypatched to yield a fake session whose ``execute()`` returns
canned ``Result``-shaped objects, so these exercise the ORM statement's
outcome handling without touching Postgres.
"""

from __future__ import annotations

import importlib
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException

import app.db.scope as scope_module
import app.db.session as session_module
import app.main as main_module
from app.core.config import settings

pytestmark = pytest.mark.unit


class _FakeMappingsResult:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _FakeResult:
    """Stands in for a SQLAlchemy ``Result``: supports both the
    ``.mappings().first()`` shape (_resolve_file_path) and the plain
    ``.all()`` row-tuple shape (_fetch_team_ids selecting one column)."""

    def __init__(self, mapping_rows: list[dict] | None = None, tuple_rows=None):
        self._mapping_rows = mapping_rows or []
        self._tuple_rows = tuple_rows if tuple_rows is not None else []

    def mappings(self):
        return _FakeMappingsResult(self._mapping_rows)

    def all(self):
        return self._tuple_rows


class _FakeSession:
    """Returns queued results in call order; records the # of executes."""

    def __init__(self, results: list[_FakeResult]):
        self._results = list(results)
        self.execute_count = 0

    async def execute(self, _stmt):
        self.execute_count += 1
        return self._results.pop(0)


def _session_cm(session: _FakeSession):
    @asynccontextmanager
    async def _cm():
        yield session

    return _cm


@pytest.fixture
def media_module(tmp_path, monkeypatch):
    """Reload app.main with a writable DOWNLOAD_PATH so the /media/* routes
    (and the helper functions under test) register — mirrors the existing
    cover_module fixture in test_media_cover_serve_dispatch.py."""
    original = settings.DOWNLOAD_PATH
    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    importlib.reload(main_module)
    assert hasattr(
        main_module, "_resolve_file_path"
    ), "media routes did not register with a valid, writable DOWNLOAD_PATH"
    try:
        yield main_module
    finally:
        monkeypatch.setattr(settings, "DOWNLOAD_PATH", original)
        importlib.reload(main_module)


def _install_read_scope_sequence(monkeypatch, sessions: list[_FakeSession]) -> None:
    """Monkeypatch app.db.session.read_scope to hand out fake sessions in
    call order — one queued session per read_scope() invocation, matching
    the sequence _resolve_file_path/_fetch_team_ids open them in."""
    it = iter(sessions)

    def _read_scope():
        return _session_cm(next(it))()

    monkeypatch.setattr(session_module, "read_scope", _read_scope)


def _install_noop_system_request_scope(monkeypatch) -> None:
    @asynccontextmanager
    async def _system_request_scope(reason: str):
        yield None

    monkeypatch.setattr(scope_module, "system_request_scope", _system_request_scope)


def _patch_scopes(monkeypatch, *, resources_session, other_session) -> None:
    """system_request_scope() wraps the resources query (no-op stub — the
    fake session doesn't care about ambient scope); read_scope() is called
    once for the resources query and again for whichever branch runs next
    (team_ids lookup on a hit, parsed_media fallback on a miss)."""
    _install_noop_system_request_scope(monkeypatch)
    _install_read_scope_sequence(monkeypatch, [resources_session, other_session])


@pytest.mark.asyncio
async def test_resources_hit_returns_file_path_and_team_ids(media_module, monkeypatch):
    resources_session = _FakeSession(
        [
            _FakeResult(
                mapping_rows=[{"id": 42, "creator_id": "u1", "file_path": "a.mp4"}]
            )
        ]
    )
    team_session = _FakeSession([_FakeResult(tuple_rows=[(7,), (8,)])])
    _patch_scopes(
        monkeypatch, resources_session=resources_session, other_session=team_session
    )

    result = await media_module._resolve_file_path("777001", "file")
    assert result == ("a.mp4", "u1", ("7", "8"))


@pytest.mark.asyncio
async def test_resources_miss_falls_back_to_parsed_media(media_module, monkeypatch):
    resources_session = _FakeSession([_FakeResult(mapping_rows=[])])
    parsed_media_session = _FakeSession(
        [_FakeResult(mapping_rows=[{"download_path": "legacy/path"}])]
    )
    _patch_scopes(
        monkeypatch,
        resources_session=resources_session,
        other_session=parsed_media_session,
    )

    result = await media_module._resolve_file_path("777002", "file")
    assert result == ("legacy/path", None, ())


@pytest.mark.asyncio
async def test_cover_file_type_skips_resources_lookup(media_module, monkeypatch):
    """file_type='cover' must never touch resources — only parsed_media,
    keyed by cover_download_path (matches the legacy media_col selection)."""
    parsed_media_session = _FakeSession(
        [_FakeResult(mapping_rows=[{"cover_download_path": "cover.jpg"}])]
    )
    _install_read_scope_sequence(monkeypatch, [parsed_media_session])

    def _must_not_be_called(reason: str):
        raise AssertionError("cover lookup must not open system_request_scope")

    monkeypatch.setattr(scope_module, "system_request_scope", _must_not_be_called)

    result = await media_module._resolve_file_path("777003", "cover")
    assert result == ("cover.jpg", None, ())


@pytest.mark.asyncio
async def test_neither_table_has_row_raises_404(media_module, monkeypatch):
    resources_session = _FakeSession([_FakeResult(mapping_rows=[])])
    parsed_media_session = _FakeSession([_FakeResult(mapping_rows=[])])
    _patch_scopes(
        monkeypatch,
        resources_session=resources_session,
        other_session=parsed_media_session,
    )

    with pytest.raises(HTTPException) as exc_info:
        await media_module._resolve_file_path("777004", "file")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_non_numeric_media_id_degrades_to_404_not_raise(
    media_module, monkeypatch
):
    """int(media_id) raises ValueError while building the SELECT (inside the
    open session/scope) — caught by the same broad except the legacy code
    had, degrading to the 404 fallback rather than propagating a raw
    exception. The session is entered but ``execute`` must never be
    reached (the statement never finishes building)."""

    class _MustNotExecuteSession:
        async def execute(self, _stmt):
            raise AssertionError("must not query DB for a non-numeric id")

    _install_noop_system_request_scope(monkeypatch)
    _install_read_scope_sequence(monkeypatch, [_MustNotExecuteSession()])

    with pytest.raises(HTTPException) as exc_info:
        await media_module._resolve_file_path("not-a-number", "file")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_fetch_team_ids_drops_falsy_scope_ids(media_module, monkeypatch):
    session = _FakeSession([_FakeResult(tuple_rows=[(0,), (None,), (55,)])])
    monkeypatch.setattr(session_module, "read_scope", _session_cm(session))

    result = await media_module._fetch_team_ids(123)
    assert result == ("55",)


@pytest.mark.asyncio
async def test_fetch_team_ids_db_error_returns_empty_tuple(media_module, monkeypatch):
    class _BoomSession:
        async def execute(self, _stmt):
            raise RuntimeError("boom")

    monkeypatch.setattr(session_module, "read_scope", _session_cm(_BoomSession()))

    result = await media_module._fetch_team_ids(123)
    assert result == ()
