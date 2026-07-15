"""Tests for ``_ensure_personal_team_bootstrap`` — the backend's
defensive copy of `handle_new_user`'s setup.

The on_auth_user_created DB trigger was dropped from prod once before
(mig 239 history). When that happens, this helper has to pick up the
slack so the welcome-bonus path further down has a team to attach to.

Tests focus on the three branches (now on the ORM session boundary):
  - happy path: team_members ⋈ teams already has the personal team →
    returns it without touching INSERT paths
  - cold-start path: auth.users present, no team_members → INSERTs
    user_profiles (upsert) + teams (team_members lands via trigger)
  - missing-user path: auth.users row not found → returns None, no writes
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.dml import Insert

from app.api.supabase_auth_router import _ensure_personal_team_bootstrap


class _Result:
    """Stands in for a SQLAlchemy Result over the queued value."""

    def __init__(self, value):
        self._v = value

    def scalar(self):
        return self._v

    def mappings(self):
        return self

    def first(self):
        return self._v

    def all(self):
        return self._v


class _Session:
    """Returns queued results in call order; classifies INSERT/upsert
    statements (by on-conflict presence) for payload assertions."""

    def __init__(self, results):
        self._results = list(results)
        self.inserts: list = []
        self.upserts: list = []

    async def execute(self, stmt, params=None):
        if isinstance(stmt, Insert):
            table = "public." + stmt.table.name
            payload = stmt.compile(dialect=postgresql.dialect()).params
            if getattr(stmt, "_post_values_clause", None) is not None:
                self.upserts.append((table, payload))
            else:
                self.inserts.append((table, payload))
        return _Result(self._results.pop(0) if self._results else None)


@pytest.fixture
def patch_scopes(monkeypatch):
    def _install(results) -> _Session:
        session = _Session(results)

        @asynccontextmanager
        async def _scope():
            yield session

        import app.db.session as dbs

        monkeypatch.setattr(dbs, "read_scope", _scope)
        monkeypatch.setattr(dbs, "write_scope", _scope)
        return session

    return _install


@pytest.mark.asyncio
async def test_returns_existing_team_id_without_inserts(patch_scopes):
    # First (and only) execute: the team_members ⋈ teams probe → scalar id.
    session = patch_scopes([310812366953241])

    result = await _ensure_personal_team_bootstrap(
        "8e1584e3-9c29-4a5b-90fe-125b74259f7f"
    )

    assert result == "310812366953241"
    assert session.inserts == []
    assert session.upserts == []


@pytest.mark.asyncio
async def test_cold_start_inserts_profile_and_team(patch_scopes):
    """No team_members row → look up auth.users, upsert user_profiles +
    INSERT teams. The trigger adds team_members."""
    # Order: probe(None), auth.users(row), upsert(unused), teams insert(id).
    session = patch_scopes(
        [
            None,
            {
                "id": "9f3c0eaa-...",
                "email": "newbie@example.com",
                "raw_user_meta_data": {"username": "newbie"},
            },
            None,
            311999999900001,
        ]
    )

    result = await _ensure_personal_team_bootstrap("9f3c0eaa-...")

    assert result == "311999999900001"
    upsert_tables = {t for t, _ in session.upserts}
    insert_tables = {t for t, _ in session.inserts}
    assert "public.user_profiles" in upsert_tables
    assert "public.teams" in insert_tables
    team_payload = next(p for t, p in session.inserts if t == "public.teams")
    assert team_payload["kind"] == "personal"
    assert team_payload["name"] == "newbie's Workspace"
    assert team_payload["owner_id"] == "9f3c0eaa-..."


@pytest.mark.asyncio
async def test_missing_auth_user_returns_none_no_writes(patch_scopes):
    # probe(None), auth.users(None) → returns None before any write.
    session = patch_scopes([None, None])

    result = await _ensure_personal_team_bootstrap("ghost-uuid")

    assert result is None
    assert session.inserts == []
    assert session.upserts == []


@pytest.mark.asyncio
async def test_falls_back_to_email_prefix_when_username_missing(patch_scopes):
    """raw_user_meta_data has no username key — use email local-part."""
    session = patch_scopes(
        [
            None,
            {
                "id": "abc",
                "email": "alice@corp.com",
                "raw_user_meta_data": {},
            },
            None,
            999,
        ]
    )

    await _ensure_personal_team_bootstrap("abc")

    team_payload = next(p for t, p in session.inserts if t == "public.teams")
    assert team_payload["name"] == "alice's Workspace"
    upsert_payload = next(p for t, p in session.upserts if t == "public.user_profiles")
    assert upsert_payload["username"] == "alice"
