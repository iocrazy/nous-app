"""Unit tests for upload write-access guards (app/core/scope_guards.py).

Covers the 2026-05-14 horizontal-authz fix. Each guard must 403 a caller
who does not own the target (scope_id / project_id / resource_id) and pass
a caller who does. Runs against a fake Supabase admin client — no DB.

The guards exist because the real upload path uses the service-role client,
which bypasses RLS — so the ownership check has to live in app code, and
these tests pin it.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core.deps import AuthContext
from app.core.scope_guards import (
    verify_project_write_access,
    verify_resource_write_access,
    verify_scope_access,
)

pytestmark = pytest.mark.unit


# ─── Fake Supabase client/query plumbing ──────────────────────────────


class _FakeQuery:
    def __init__(self, data: list) -> None:
        self._data = data

    def __getattr__(self, name: str):
        def _capture(*args, **kwargs) -> "_FakeQuery":
            return self

        return _capture

    async def execute(self):
        class _R:
            data = self._data

        return _R()


class _FakeClient:
    """Returns per-table canned data, so a guard that queries two tables
    (projects, then team_members) gets the right rows for each call."""

    def __init__(self, tables: dict[str, list]) -> None:
        self._tables = tables

    def table(self, name: str) -> _FakeQuery:
        return _FakeQuery(self._tables.get(name, []))


@pytest.fixture
def patch_admin(monkeypatch):
    def _install(tables: dict[str, list]) -> None:
        async def _fake_admin():
            return _FakeClient(tables)

        monkeypatch.setattr(
            "app.core.scope_guards.get_async_supabase_admin", _fake_admin
        )

    return _install


def _auth(user_id: str = "user-1") -> AuthContext:
    return AuthContext(user_id=user_id, auth_type="jwt")


# ─── verify_scope_access ──────────────────────────────────────────────


async def test_scope_member_passes(patch_admin):
    # After Spec 1 PR-C, scope_id is always a teams.id snowflake (personal
    # scopes resolve to the user's personal-team snowflake). PR-E Phase 3
    # dropped the scope_type param entirely — authorization is purely a
    # team_members check on scope_id.
    patch_admin({"team_members": [{"team_id": "pt-u1"}]})
    await verify_scope_access(auth=_auth("u1"), scope_id="pt-u1")


async def test_scope_non_member_403(patch_admin):
    patch_admin({"team_members": []})
    with pytest.raises(HTTPException) as ei:
        await verify_scope_access(auth=_auth("u1"), scope_id="pt-other")
    assert ei.value.status_code == 403


# ─── verify_project_write_access ──────────────────────────────────────


async def test_project_missing_404(patch_admin):
    patch_admin({"projects": []})
    with pytest.raises(HTTPException) as ei:
        await verify_project_write_access(project_id="p1", auth=_auth("u1"))
    assert ei.value.status_code == 404


async def test_project_owner_passes(patch_admin):
    patch_admin({"projects": [{"owner_id": "u1", "team_id": None}]})
    await verify_project_write_access(project_id="p1", auth=_auth("u1"))


async def test_project_team_member_passes(patch_admin):
    patch_admin(
        {
            "projects": [{"owner_id": "owner", "team_id": "t1"}],
            "team_members": [{"team_id": "t1"}],
        }
    )
    await verify_project_write_access(project_id="p1", auth=_auth("u1"))


async def test_project_non_owner_non_member_403(patch_admin):
    patch_admin(
        {
            "projects": [{"owner_id": "owner", "team_id": "t1"}],
            "team_members": [],
        }
    )
    with pytest.raises(HTTPException) as ei:
        await verify_project_write_access(project_id="p1", auth=_auth("u1"))
    assert ei.value.status_code == 403


async def test_project_non_owner_no_team_403(patch_admin):
    patch_admin({"projects": [{"owner_id": "owner", "team_id": None}]})
    with pytest.raises(HTTPException) as ei:
        await verify_project_write_access(project_id="p1", auth=_auth("u1"))
    assert ei.value.status_code == 403


# ─── verify_resource_write_access ─────────────────────────────────────


async def test_resource_missing_404(patch_admin):
    patch_admin({"resources": []})
    with pytest.raises(HTTPException) as ei:
        await verify_resource_write_access(resource_id="r1", auth=_auth("u1"))
    assert ei.value.status_code == 404


async def test_resource_creator_passes(patch_admin):
    patch_admin({"resources": [{"creator_id": "u1"}]})
    await verify_resource_write_access(resource_id="r1", auth=_auth("u1"))


async def test_resource_non_creator_403(patch_admin):
    patch_admin({"resources": [{"creator_id": "owner"}]})
    with pytest.raises(HTTPException) as ei:
        await verify_resource_write_access(resource_id="r1", auth=_auth("u1"))
    assert ei.value.status_code == 403
