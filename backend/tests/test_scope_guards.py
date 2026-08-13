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
    can_write_project,
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


# ─── can_write_project: non-raising twin of the write guard ───────────
#
# The canvas GET ships this verdict to the client as ``can_edit`` so a
# read-only viewer never has to learn its rights by firing a PUT that is
# guaranteed to 403. The predicate and the guard MUST agree on every cell
# — they share ``_resolve_project_access``, and these tests pin that by
# asserting both against the same fixtures.


@pytest.mark.parametrize(
    "tables, expected",
    [
        # owner (personal project, no team, no member rows)
        ({"projects": [("u1", None)]}, True),
        # team member — any team role is a project writer here
        ({"projects": [("owner", "t1")], "team_members": [("t1",)]}, True),
        # explicit project_members rows
        ({"projects": [("owner", None)], "project_members": [("manager",)]}, True),
        ({"projects": [("owner", None)], "project_members": [("editor",)]}, True),
        ({"projects": [("owner", None)], "project_members": [("viewer",)]}, False),
        ({"projects": [("owner", None)], "project_members": [("external",)]}, False),
        # non-member of a team project
        (
            {"projects": [("owner", "t1")], "team_members": [], "project_members": []},
            False,
        ),
    ],
)
async def test_can_write_project_matrix(patch_session, tables, expected):
    patch_session(tables)
    assert await can_write_project("1", "u1") is expected


@pytest.mark.parametrize(
    "tables",
    [
        {"projects": [("u1", None)]},
        {"projects": [("owner", "t1")], "team_members": [("t1",)]},
        {"projects": [("owner", None)], "project_members": [("editor",)]},
        {"projects": [("owner", None)], "project_members": [("viewer",)]},
        {"projects": [("owner", "t1")], "team_members": [], "project_members": []},
    ],
)
async def test_can_write_project_agrees_with_the_write_guard(patch_session, tables):
    """No drift: whatever the predicate says, the guard must do — otherwise
    the UI's read-only state and the server's 403 disagree."""
    patch_session(tables)
    predicted = await can_write_project("1", "u1")

    patch_session(tables)
    try:
        await verify_project_write_access(project_id="1", auth=_auth("u1"))
        guard_allowed = True
    except HTTPException as exc:
        assert exc.status_code == 403
        guard_allowed = False

    assert predicted is guard_allowed


async def test_can_write_project_missing_project_is_false_not_404(patch_session):
    """The guard 404s a missing project; the predicate has no HTTP status to
    return, so 'nobody can write a row that isn't there' → False."""
    patch_session({"projects": []})
    assert await can_write_project("1", "u1") is False


# ─── script/scene/shot family: read-access project_members fallback ───
#
# 2026-08-12 finding: script_projects.team_id is NOT projects.team_id — it
# is NOT NULL on every row, including scripts under a personal
# (projects.team_id NULL) project, which get the OWNER's auto-created
# single-member personal team. So an invited project_members row (e.g. a
# viewer added to someone else's personal project) was never a member of
# THAT team and always 403'd, even after #1742 folded "explicit member row
# wins" into resolve_effective_role — this family never consulted it. The
# *_read_access variants now fall back to _check_project_access once the
# team check fails; the write variants are deliberately unchanged (this
# family has never graded project_members roles for writes).

_PERSONAL_TEAM = "800001001"  # the owner's auto-created single-member team
_SHARED_TEAM = "800001002"
_SCRIPT_PROJECT_ID = "500001"


async def test_script_read_access_personal_project_member_passes(
    patch_session, monkeypatch
):
    from app.core.scope_guards import verify_script_read_access
    from app.repositories.script_repository import ScriptProjectRepository

    async def fake_script_get(self, script_id):
        return {
            "id": script_id,
            "team_id": _PERSONAL_TEAM,
            "project_id": _SCRIPT_PROJECT_ID,
        }

    monkeypatch.setattr(ScriptProjectRepository, "get_by_id", fake_script_get)
    patch_session(
        {
            "team_members": [],  # caller not in the owner's personal team
            "projects": [("owner-uuid", None)],  # personal project
            "project_members": [("viewer",)],  # explicit invited row
        }
    )
    await verify_script_read_access(script_id="1", auth=_auth("u1"))


async def test_script_write_access_personal_project_viewer_still_403(
    patch_session, monkeypatch
):
    """The WRITE variant keeps the team-only gate — no project_members
    fallback — so a viewer-role member is still denied writes. Extending
    the fallback to writes would silently widen access, not fix a read
    gap."""
    from app.core.scope_guards import verify_script_access
    from app.repositories.script_repository import ScriptProjectRepository

    async def fake_script_get(self, script_id):
        return {
            "id": script_id,
            "team_id": _PERSONAL_TEAM,
            "project_id": _SCRIPT_PROJECT_ID,
        }

    monkeypatch.setattr(ScriptProjectRepository, "get_by_id", fake_script_get)
    patch_session({"team_members": []})

    with pytest.raises(HTTPException) as ei:
        await verify_script_access(script_id="1", auth=_auth("u1"))
    assert ei.value.status_code == 403


async def test_script_read_access_team_member_passes_without_project_lookup(
    patch_session, monkeypatch
):
    """Team membership still short-circuits — the fallback is additive."""
    from app.core.scope_guards import verify_script_read_access
    from app.repositories.script_repository import ScriptProjectRepository

    async def fake_script_get(self, script_id):
        return {
            "id": script_id,
            "team_id": _SHARED_TEAM,
            "project_id": _SCRIPT_PROJECT_ID,
        }

    monkeypatch.setattr(ScriptProjectRepository, "get_by_id", fake_script_get)
    patch_session({"team_members": [(_SHARED_TEAM,)]})
    await verify_script_read_access(script_id="1", auth=_auth("u1"))


async def test_script_read_access_non_member_403(patch_session, monkeypatch):
    from app.core.scope_guards import verify_script_read_access
    from app.repositories.script_repository import ScriptProjectRepository

    async def fake_script_get(self, script_id):
        return {
            "id": script_id,
            "team_id": _PERSONAL_TEAM,
            "project_id": _SCRIPT_PROJECT_ID,
        }

    monkeypatch.setattr(ScriptProjectRepository, "get_by_id", fake_script_get)
    patch_session(
        {
            "team_members": [],
            "projects": [("owner-uuid", None)],
            "project_members": [],
        }
    )
    with pytest.raises(HTTPException) as ei:
        await verify_script_read_access(script_id="1", auth=_auth("u1"))
    assert ei.value.status_code == 403


async def test_scene_read_access_personal_project_member_passes(
    patch_session, monkeypatch
):
    """One hop further: scene → script → project. Confirms the fallback
    propagates through verify_scene_read_access, not just the script-scoped
    entry point."""
    from app.core.scope_guards import verify_scene_read_access
    from app.repositories.script_repository import ScriptProjectRepository
    from app.repositories.script_scene_repository import ScriptSceneRepository

    async def fake_scene_get(self, scene_id):
        return {"id": scene_id, "script_id": "1"}

    async def fake_script_get(self, script_id):
        return {
            "id": script_id,
            "team_id": _PERSONAL_TEAM,
            "project_id": _SCRIPT_PROJECT_ID,
        }

    monkeypatch.setattr(ScriptSceneRepository, "get_by_id", fake_scene_get)
    monkeypatch.setattr(ScriptProjectRepository, "get_by_id", fake_script_get)
    patch_session(
        {
            "team_members": [],
            "projects": [("owner-uuid", None)],
            "project_members": [("viewer",)],
        }
    )
    await verify_scene_read_access(scene_id="9", auth=_auth("u1"))
