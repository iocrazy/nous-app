"""Unit tests for upload write-access guards (app/core/scope_guards.py).

Covers the 2026-05-14 horizontal-authz fix. Each guard must 403 a caller
who does not own the target (scope_id / project_id / resource_id) and pass
a caller who does. Runs against a fake ORM read session — no DB.

The guards exist because the real upload path uses the service-role
connection, which bypasses RLS — so the ownership check has to live in app
code, and these tests pin it. After the supabase-py → SQLAlchemy ORM
transport swap the guards issue ``select(...)`` statements through
``read_scope()``; the fake session below returns per-table canned rows so
the guards' real statement-construction + branch logic runs unchanged.

Note: ids are numeric strings here (the guards coerce ``int(str(id))`` for
the BIGINT snowflake columns, exactly as the real routes pass them).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException

from app.core.deps import AuthContext
from app.core.scope_guards import (
    verify_project_write_access,
    verify_resource_write_access,
    verify_scope_access,
)

pytestmark = pytest.mark.unit


# ─── Fake ORM read-session plumbing ───────────────────────────────────
#
# The guards run several ``select()`` statements per call (projects, then
# team_members, then project_members). The fake session inspects the
# rendered SQL to pick the table, returning that table's canned rows as
# tuple "Row"s — which unpack (``owner_id, team_id = row``) and index
# (``row[0]``) just like a SQLAlchemy Row.


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Returns per-table canned rows, keyed by table name found in the SQL."""

    # Most specific names first so ``project_members`` wins over ``projects``
    # and ``team_members`` over ``teams``.
    _TABLES = (
        "project_members",
        "team_members",
        "user_profiles",
        "projects",
        "resources",
        "teams",
    )

    def __init__(self, tables: dict[str, list]) -> None:
        self._tables = tables

    async def execute(self, stmt):
        sql = str(stmt)
        for name in self._TABLES:
            if name in sql:
                return _Result(self._tables.get(name, []))
        return _Result([])


@pytest.fixture
def patch_session(monkeypatch):
    def _install(tables: dict[str, list]) -> None:
        @asynccontextmanager
        async def _fake_read_scope():
            yield _FakeSession(tables)

        # Guards import read_scope locally from app.db.session, so patching the
        # module attribute is seen at call time.
        import app.db.session as db_session_mod

        monkeypatch.setattr(db_session_mod, "read_scope", _fake_read_scope)

    return _install


def _auth(user_id: str = "user-1") -> AuthContext:
    return AuthContext(user_id=user_id, auth_type="jwt")


# ─── verify_scope_access ──────────────────────────────────────────────


async def test_scope_member_passes(patch_session):
    # After Spec 1 PR-C, scope_id is always a teams.id snowflake (personal
    # scopes resolve to the user's personal-team snowflake). PR-E Phase 3
    # dropped the scope_type param entirely — authorization is purely a
    # team_members check on scope_id.
    patch_session({"team_members": [("100",)]})
    await verify_scope_access(auth=_auth("u1"), scope_id="100")


async def test_scope_non_member_403(patch_session):
    patch_session({"team_members": []})
    with pytest.raises(HTTPException) as ei:
        await verify_scope_access(auth=_auth("u1"), scope_id="200")
    assert ei.value.status_code == 403


# ─── verify_project_write_access ──────────────────────────────────────


async def test_project_missing_404(patch_session):
    patch_session({"projects": []})
    with pytest.raises(HTTPException) as ei:
        await verify_project_write_access(project_id="1", auth=_auth("u1"))
    assert ei.value.status_code == 404


async def test_project_owner_passes(patch_session):
    patch_session({"projects": [("u1", None)]})
    await verify_project_write_access(project_id="1", auth=_auth("u1"))


async def test_project_team_member_passes(patch_session):
    patch_session(
        {
            "projects": [("owner", "t1")],
            "team_members": [("t1",)],
        }
    )
    await verify_project_write_access(project_id="1", auth=_auth("u1"))


async def test_project_non_owner_non_member_403(patch_session):
    patch_session(
        {
            "projects": [("owner", "t1")],
            "team_members": [],
        }
    )
    with pytest.raises(HTTPException) as ei:
        await verify_project_write_access(project_id="1", auth=_auth("u1"))
    assert ei.value.status_code == 403


async def test_project_non_owner_no_team_403(patch_session):
    patch_session({"projects": [("owner", None)]})
    with pytest.raises(HTTPException) as ei:
        await verify_project_write_access(project_id="1", auth=_auth("u1"))
    assert ei.value.status_code == 403


# ─── verify_resource_write_access ─────────────────────────────────────


async def test_resource_missing_404(patch_session):
    patch_session({"resources": []})
    with pytest.raises(HTTPException) as ei:
        await verify_resource_write_access(resource_id="1", auth=_auth("u1"))
    assert ei.value.status_code == 404


async def test_resource_creator_passes(patch_session):
    patch_session({"resources": [("u1",)]})
    await verify_resource_write_access(resource_id="1", auth=_auth("u1"))


async def test_resource_non_creator_403(patch_session):
    patch_session({"resources": [("owner",)]})
    with pytest.raises(HTTPException) as ei:
        await verify_resource_write_access(resource_id="1", auth=_auth("u1"))
    assert ei.value.status_code == 403


# ─── verify_project_read_access + project_members branch ──────────────


async def test_read_project_member_viewer_passes(patch_session):
    from app.core.scope_guards import verify_project_read_access

    patch_session(
        {
            "projects": [("owner", None)],
            "project_members": [("viewer",)],
        }
    )
    await verify_project_read_access(project_id="1", auth=_auth("u1"))


async def test_read_non_member_403(patch_session):
    from app.core.scope_guards import verify_project_read_access

    patch_session(
        {
            "projects": [("owner", None)],
            "project_members": [],
        }
    )
    with pytest.raises(HTTPException) as ei:
        await verify_project_read_access(project_id="1", auth=_auth("u1"))
    assert ei.value.status_code == 403


async def test_read_project_missing_404(patch_session):
    from app.core.scope_guards import verify_project_read_access

    patch_session({"projects": []})
    with pytest.raises(HTTPException) as ei:
        await verify_project_read_access(project_id="1", auth=_auth("u1"))
    assert ei.value.status_code == 404


async def test_write_project_member_editor_passes(patch_session):
    patch_session(
        {
            "projects": [("owner", None)],
            "project_members": [("editor",)],
        }
    )
    await verify_project_write_access(project_id="1", auth=_auth("u1"))


async def test_write_project_member_viewer_403(patch_session):
    patch_session(
        {
            "projects": [("owner", None)],
            "project_members": [("viewer",)],
        }
    )
    with pytest.raises(HTTPException) as ei:
        await verify_project_write_access(project_id="1", auth=_auth("u1"))
    assert ei.value.status_code == 403
