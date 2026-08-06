"""RLS 第三层 (PR-2b) integration — proves ``caller_scope()`` actually makes
migration 408's team-tenant policies filter the ORM/agent-tool path, against a
live nous-db.

Gated on ``SUPAVISOR_DATABASE_URL``: skips (never fails) when unset, so the
engine-less unit suite and CI stay green. Run it against nous-db with the DSN
exported to exercise the real privilege drop end to end::

    SUPAVISOR_DATABASE_URL=postgresql://postgres:...@127.0.0.1:55436/postgres \
        uv run pytest tests/test_screenwriting_tools_rls.py -q

``real_caller_scope`` opts these tests out of conftest's inert-stub fixture so
the REAL context manager (SET LOCAL ROLE authenticated + injected
request.jwt.claims) runs. Raw ``text()`` here is test-only data discovery, not
app code.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.real_caller_scope,
    pytest.mark.skipif(
        not os.getenv("SUPAVISOR_DATABASE_URL"),
        reason="needs a live SUPAVISOR_DATABASE_URL (nous-db)",
    ),
]

_NOBODY = "00000000-0000-0000-0000-000000000000"


async def _scalar(session, sql, **params):
    return (await session.execute(text(sql), params)).scalar()


async def test_caller_scope_enforces_tenant_isolation_on_script_scenes():
    """Same-tenant team member reads its scene; a non-member reads zero — the
    exact cross-tenant boundary mig-408 exists to enforce, now reached through
    the ORM connection because caller_scope drops it off the postgres
    superuser to the (non-BYPASSRLS) authenticated role."""
    from app.db import session as session_mod

    # Discover a team-scoped scene and a member of its owning team, on postgres.
    async with session_mod.read_scope() as s:
        row = (
            await s.execute(
                text(
                    "SELECT sc.id AS scene_id, tm.user_id::text AS member "
                    "FROM script_scenes sc "
                    "JOIN script_projects sp ON sp.id = sc.script_id "
                    "JOIN team_members tm ON tm.team_id = sp.team_id "
                    "WHERE sp.team_id IS NOT NULL LIMIT 1"
                )
            )
        ).first()
    if row is None:
        pytest.skip("no team-scoped scene present in this db to exercise")
    scene_id, member = row.scene_id, row.member

    # The member of the owning team SEES the scene under caller_scope, and the
    # transaction really is running as the non-superuser authenticated role.
    async with session_mod.caller_scope(member) as s:
        assert await _scalar(s, "SELECT current_user") == "authenticated"
        seen = await _scalar(
            s, "SELECT count(*) FROM script_scenes WHERE id = :i", i=scene_id
        )
    assert seen == 1, "same-tenant member could not read its own scene under RLS"

    # A random non-member sees ZERO rows for the same id — RLS filtered it out.
    async with session_mod.caller_scope(_NOBODY) as s:
        seen = await _scalar(
            s, "SELECT count(*) FROM script_scenes WHERE id = :i", i=scene_id
        )
    assert seen == 0, "cross-tenant read was NOT filtered by RLS"


async def test_caller_scope_blocks_cross_tenant_shot_insert_via_with_check():
    """A non-member's INSERT into script_shots for someone else's scene is
    refused by mig-408's WITH CHECK — the write half of the same boundary."""
    from asyncpg.exceptions import InsufficientPrivilegeError
    from sqlalchemy.exc import DBAPIError

    from app.db import session as session_mod

    async with session_mod.read_scope() as s:
        scene_id = (
            await s.execute(
                text(
                    "SELECT sc.id FROM script_scenes sc "
                    "JOIN script_projects sp ON sp.id = sc.script_id "
                    "WHERE sp.team_id IS NOT NULL LIMIT 1"
                )
            )
        ).scalar()
    if scene_id is None:
        pytest.skip("no team-scoped scene present in this db to exercise")

    with pytest.raises((DBAPIError, InsufficientPrivilegeError)):
        async with session_mod.caller_scope(_NOBODY) as s:
            await s.execute(
                text(
                    "INSERT INTO script_shots (scene_id, shot_number, sort_order) "
                    "VALUES (:sid, 999, 999000)"
                ),
                {"sid": scene_id},
            )
